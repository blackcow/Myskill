#!/usr/bin/env python
"""Archive public WeChat Channels sph preview pages.

The public sph preview endpoint exposes metadata, cover images, and engagement
counts. It does not currently expose a subtitle track or a downloadable video
URL for the tested public share links, so this tool records that boundary
explicitly instead of fabricating a transcript.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


SOURCE_TYPE = "wechat_channels_video"
API_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
OPENROUTER_TRANSCRIPTION_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_ASR_MODEL = "openai/gpt-4o-mini-transcribe"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
SHORT_URI_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
SUPPORTED_ASR_AUDIO_FORMATS = {"mp3", "wav"}
WX_CHANNEL_HEALTH_URL = "http://127.0.0.1:2025/api/health"
WX_CHANNEL_BATCH_START_URL = "http://127.0.0.1:2025/__wx_channels_api/batch_start"
WX_CHANNEL_BATCH_PROGRESS_URL = "http://127.0.0.1:2025/__wx_channels_api/batch_progress"
WX_CHANNEL_STATUS_URL = "http://127.0.0.1:2026/api/channels/status"
WX_CHANNEL_CONTACT_SEARCH_URL = "http://127.0.0.1:2026/api/channels/contact/search"
WX_CHANNEL_FEED_LIST_URL = "http://127.0.0.1:2026/api/channels/contact/feed/list"
WX_CHANNEL_PROFILE_URL = "http://127.0.0.1:2026/api/channels/feed/profile"


@dataclass
class AssetResult:
    role: str
    source_url: str
    path: str | None
    ok: bool
    warning: str | None = None


@dataclass
class AsrResult:
    status: str
    text: str
    segments: list[dict[str, Any]]
    warnings: list[str]
    files: list[str]
    metadata: dict[str, Any]


@dataclass
class MediaAcquisitionResult:
    status: str
    method: str
    media_file: Path | None
    metadata: dict[str, Any]
    warnings: list[str]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_short_uri(value: str) -> str:
    value = value.strip()
    if SHORT_URI_RE.match(value):
        return value

    parsed = urlparse(value)
    if parsed.netloc.endswith("weixin.qq.com"):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "sph" and SHORT_URI_RE.match(parts[1]):
            return parts[1]

    if parsed.netloc.endswith("channels.weixin.qq.com"):
        query = parse_qs(parsed.query)
        candidate = (query.get("id") or query.get("shortUri") or [""])[0]
        if SHORT_URI_RE.match(candidate):
            return candidate

    raise ValueError(f"Cannot parse WeChat Channels short uri from input: {value}")


def canonical_url(short_uri: str) -> str:
    return f"https://weixin.qq.com/sph/{short_uri}"


def preview_url(short_uri: str) -> str:
    return f"https://channels.weixin.qq.com/finder-preview/pages/sph?id={short_uri}"


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_feed_info(session: requests.Session, short_uri: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://channels.weixin.qq.com",
        "Referer": preview_url(short_uri),
    }
    payload = {"baseReq": {"generalToken": ""}, "shortUri": short_uri}
    response = session.post(API_URL, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("errCode") not in (0, None):
        raise RuntimeError(f"WeChat Channels API returned errCode={data.get('errCode')}: {data.get('errMsg')}")
    return data


def safe_filename(value: str, max_len: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-")
    cleaned = re.sub(r"\s+", "-", cleaned)
    if not cleaned:
        return "wechat-channel-video"
    return cleaned[:max_len].rstrip(" .-")


def text_excerpt(value: str, max_len: int = 54) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + "..."


def build_asr_context(title: str, author: str, description: str) -> str:
    parts = [
        "这是微信视频号视频的中文转写任务。请保留技术名词、英文产品名和专有名词的原文拼写。",
        f"作者：{author}" if author else "",
        f"标题：{title}" if title else "",
        f"公开视频文案：{text_excerpt(description, 280)}" if description else "",
    ]
    return "\n".join(part for part in parts if part)


def format_count(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def normalize_published_time(timestamp: Any) -> tuple[str | None, str | None]:
    if not timestamp:
        return None, None
    try:
        dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone()
    except (TypeError, ValueError, OSError):
        return None, None
    return dt.isoformat(timespec="seconds"), dt.strftime("%Y-%m-%d")


def title_from_feed(author: str, description: str, short_uri: str) -> str:
    if description:
        stem = re.split(r"[，。！？!?；;\n#]", description, maxsplit=1)[0].strip()
        if len(stem) < 8:
            stem = description
        return f"{author or '视频号'}：{text_excerpt(stem, 64)}"
    return f"{author or '视频号'}：{short_uri}"


def slug_from_feed(author: str, published_date: str | None, short_uri: str) -> str:
    date_part = published_date or datetime.now().astimezone().strftime("%Y-%m-%d")
    author_part = safe_filename(author or "wechat-channel", max_len=28)
    return safe_filename(f"{date_part}-{author_part}-{short_uri}", max_len=96)


def guess_asset_extension(url: str, content_type: str | None, fallback: str) -> str:
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        ext = mimetypes.guess_extension(media_type)
        if ext:
            if ext == ".jpe":
                return ".jpg"
            return ext
    path_suffix = Path(urlparse(url).path).suffix
    if path_suffix and len(path_suffix) <= 8:
        return path_suffix
    return fallback


def download_asset(
    session: requests.Session,
    assets_dir: Path,
    role: str,
    url: str | None,
    basename: str,
    fallback_ext: str,
) -> AssetResult | None:
    if not url:
        return None
    try:
        response = session.get(url, timeout=45)
        response.raise_for_status()
        ext = guess_asset_extension(url, response.headers.get("content-type"), fallback_ext)
        target = assets_dir / f"{basename}{ext}"
        target.write_bytes(response.content)
        return AssetResult(role=role, source_url=url, path=f"assets/{target.name}", ok=True)
    except Exception as exc:  # noqa: BLE001 - archive tools should record degradations.
        return AssetResult(role=role, source_url=url, path=None, ok=False, warning=f"{role} download failed: {exc}")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def yaml_scalar(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return json.dumps(text, ensure_ascii=False)


def frontmatter(fields: list[tuple[str, Any]]) -> str:
    lines = ["---"]
    for key, value in fields:
        lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_media_metadata(
    path: Path | None,
    filename: str | None,
    acquisition_method: str = "provided_local_media",
) -> dict[str, Any] | None:
    if not path or not filename or not path.exists():
        return None
    mime_type, _ = mimetypes.guess_type(filename)
    return {
        "filename": filename,
        "size_bytes": path.stat().st_size,
        "sha256": file_hash(path),
        "mime_type": mime_type,
        "acquisition_method": acquisition_method,
    }


def default_wx_channel_exe() -> Path | None:
    env_path = os.environ.get("WX_CHANNEL_EXE")
    if env_path:
        return Path(env_path)
    try:
        workspace_root = Path(__file__).resolve().parents[3]
    except IndexError:
        return None
    candidate = workspace_root / "_reference" / "wechat-media-tools" / "wx_channel_V5.6.2.exe"
    return candidate if candidate.exists() else None


def wx_channel_tools_dir(exe_path: Path | None) -> Path | None:
    if exe_path:
        return exe_path.resolve().parent
    try:
        workspace_root = Path(__file__).resolve().parents[3]
    except IndexError:
        return None
    candidate = workspace_root / "_reference" / "wechat-media-tools"
    return candidate if candidate.exists() else None


def json_get(session: requests.Session, url: str, **params: Any) -> dict[str, Any]:
    response = session.get(url, params={k: v for k, v in params.items() if v not in (None, "")}, timeout=30)
    response.raise_for_status()
    return response.json()


def local_json_get(url: str, timeout: float = 2.0) -> dict[str, Any]:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def json_post(session: requests.Session, url: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    response = session.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def unwrap_api_data(value: Any) -> Any:
    if isinstance(value, dict) and value.get("code") == 0 and "data" in value:
        return value.get("data")
    return value


def walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_dicts(child))
    return found


def normalize_match_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[#，。！？!?；;：:、,.\"'`~\-—_()\[\]{}<>《》|/\\]+", "", text)
    return text


def text_similarity(left: str, right: str) -> float:
    left_norm = normalize_match_text(left)
    right_norm = normalize_match_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm in right_norm or right_norm in left_norm:
        return min(len(left_norm), len(right_norm)) / max(len(left_norm), len(right_norm))
    left_chars = set(left_norm)
    right_chars = set(right_norm)
    if not left_chars or not right_chars:
        return 0.0
    return len(left_chars & right_chars) / len(left_chars | right_chars)


def wx_clean_filename(value: str) -> str:
    text = re.sub(r"<[^>]*>", "", value or "")
    for source, target in {
        "&nbsp;": " ",
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&apos;": "'",
        "&#39;": "'",
        "&#34;": '"',
    }.items():
        text = text.replace(source, target)
    text = re.sub(r"&[a-zA-Z0-9#]+;", "", text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip()
    if not text:
        text = "video_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    runes = list(text)
    if len(runes) > 50:
        text = "".join(runes[:50])
    return text


def wx_clean_folder_name(value: str) -> str:
    text = wx_clean_filename(value or "未知作者").rstrip(".")
    return text or "未知作者"


def wx_video_filename(title: str, video_id: str) -> str:
    base = wx_clean_filename(title) if title else f"video_{video_id or datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if video_id and f"_{video_id}" not in base:
        base = f"{Path(base).stem}_{video_id}"
    return f"{base}.mp4" if not base.lower().endswith(".mp4") else base


def windows_process_running(image_name: str) -> bool:
    if os.name != "nt":
        return False
    completed = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return image_name.lower() in (completed.stdout or "").lower()


def resolve_wechat_pc_exe(user_value: Path | None) -> Path | None:
    if user_value:
        return user_value
    for candidate in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Tencent" / "WeChat" / "WeChat.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Tencent" / "Weixin" / "Weixin.exe",
        Path("C:/Program Files/Tencent/WeChat/WeChat.exe"),
        Path("C:/Program Files (x86)/Tencent/WeChat/WeChat.exe"),
        Path("C:/Program Files/Tencent/Weixin/Weixin.exe"),
        Path("C:/Program Files (x86)/Tencent/Weixin/Weixin.exe"),
    ):
        if candidate.exists():
            return candidate
    found = shutil.which("WeChat.exe")
    if not found:
        found = shutil.which("Weixin.exe")
    return Path(found) if found else None


def start_wechat_pc(exe_path: Path | None) -> dict[str, Any]:
    if os.name != "nt":
        return {"attempted": False, "status": "unsupported_non_windows"}
    if windows_process_running("WeChat.exe") or windows_process_running("Weixin.exe"):
        return {"attempted": False, "status": "already_running"}
    resolved = resolve_wechat_pc_exe(exe_path)
    if not resolved or not resolved.exists():
        return {"attempted": False, "status": "wechat_exe_not_found"}
    subprocess.Popen([str(resolved)], cwd=str(resolved.parent))
    return {"attempted": True, "status": "started", "exe": str(resolved)}


def start_wx_channel(exe_path: Path | None, mode: str) -> dict[str, Any]:
    if mode == "none":
        return {"attempted": False, "status": "start_disabled"}
    if not exe_path or not exe_path.exists():
        return {"attempted": False, "status": "wx_channel_exe_not_found", "exe": str(exe_path) if exe_path else None}
    workdir = exe_path.resolve().parent
    if os.name == "nt" and mode == "admin":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Start-Process -FilePath "
                f"{json.dumps(str(exe_path))} -WorkingDirectory {json.dumps(str(workdir))} "
                "-Verb RunAs -WindowStyle Minimized"
            ),
        ]
        subprocess.run(command, check=False, capture_output=True, text=True)
        return {"attempted": True, "status": "start_requested_admin", "exe": str(exe_path)}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    subprocess.Popen([str(exe_path)], cwd=str(workdir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    return {"attempted": True, "status": "started", "exe": str(exe_path)}


def probe_wx_channel(session: requests.Session) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    health = None
    status = None
    try:
        health = local_json_get(WX_CHANNEL_HEALTH_URL)
    except Exception as exc:  # noqa: BLE001 - report service boundary.
        warnings.append(f"WX_CHANNEL_NOT_RUNNING: health check failed: {exc}")
    try:
        status = local_json_get(WX_CHANNEL_STATUS_URL)
    except Exception as exc:  # noqa: BLE001 - report service boundary.
        warnings.append(f"WX_CHANNEL_NOT_READY: channels status check failed: {exc}")
    return health, status, warnings


def wx_channel_ready(status: dict[str, Any] | None) -> bool:
    data = unwrap_api_data(status)
    if not isinstance(data, dict):
        return False
    return bool(data.get("connected")) and int(data.get("ready_clients") or 0) > 0


def ensure_wx_channel_ready(
    session: requests.Session,
    exe_path: Path | None,
    start_mode: str,
    wait_seconds: int,
    ensure_wechat_pc: bool,
    wechat_exe: Path | None,
) -> tuple[bool, dict[str, Any], list[str]]:
    details: dict[str, Any] = {
        "method": "wx_channel",
        "health_url": WX_CHANNEL_HEALTH_URL,
        "status_url": WX_CHANNEL_STATUS_URL,
        "wx_channel_exe": str(exe_path) if exe_path else None,
    }
    warnings: list[str] = []
    if ensure_wechat_pc:
        details["wechat_pc"] = start_wechat_pc(wechat_exe)

    health, status, probe_warnings = probe_wx_channel(session)
    details["initial_health"] = health
    details["initial_status"] = status
    details["initial_probe_warnings"] = probe_warnings
    if health is None:
        details["wx_channel_start"] = start_wx_channel(exe_path, start_mode)

    deadline = time.time() + max(0, wait_seconds)
    while time.time() <= deadline:
        health, status, probe_warnings = probe_wx_channel(session)
        details["health"] = health
        details["status"] = status
        if health is not None and wx_channel_ready(status):
            details["status_code"] = "READY"
            return True, details, warnings
        time.sleep(2)

    if health is None and status is None:
        details["status_code"] = "WX_CHANNEL_NOT_RUNNING"
        warnings.extend(probe_warnings or details.get("initial_probe_warnings") or [])
    else:
        details["status_code"] = "NEED_WECHAT_LOGIN_OR_CHANNEL_PAGE"
        warnings.append("NEED_WECHAT_LOGIN_OR_CHANNEL_PAGE: wx_channel is running, but no ready WeChat Channels page/client is connected")
    return False, details, warnings


def extract_contacts(value: Any) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    for item in walk_dicts(unwrap_api_data(value)):
        username = item.get("username") or item.get("userName") or item.get("id")
        nickname = item.get("nickname") or item.get("nickName") or item.get("displayName")
        if username and nickname:
            contacts.append(item)
    return contacts


def choose_contact(search_response: dict[str, Any], author: str) -> dict[str, Any] | None:
    contacts = extract_contacts(search_response)
    if not contacts:
        return None
    author_norm = normalize_match_text(author)
    scored: list[tuple[float, dict[str, Any]]] = []
    for contact in contacts:
        nickname = str(contact.get("nickname") or contact.get("nickName") or "")
        username = str(contact.get("username") or contact.get("userName") or contact.get("id") or "")
        score = 0.0
        if author_norm and normalize_match_text(nickname) == author_norm:
            score = 2.0
        else:
            score = text_similarity(author, nickname)
        if username.endswith("@finder"):
            score += 0.1
        scored.append((score, contact))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 0.2 else None


def feed_item_title(item: dict[str, Any]) -> str:
    object_desc = item.get("objectDesc") if isinstance(item.get("objectDesc"), dict) else {}
    candidates = [
        item.get("title"),
        item.get("description"),
        object_desc.get("description") if object_desc else None,
        item.get("desc"),
    ]
    return str(next((candidate for candidate in candidates if candidate), ""))


def feed_item_object_id(item: dict[str, Any]) -> str:
    for key in ("object_id", "objectId", "id", "objectID"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def feed_item_nonce_id(item: dict[str, Any]) -> str:
    for key in ("nonce_id", "nonceId", "objectNonceId", "object_nonce_id"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def extract_feed_items(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in walk_dicts(unwrap_api_data(value)):
        if feed_item_object_id(item) and feed_item_nonce_id(item) and feed_item_title(item):
            items.append(item)
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        deduped[(feed_item_object_id(item), feed_item_nonce_id(item))] = item
    return list(deduped.values())


def choose_feed_item(feed_response: dict[str, Any], description: str, title: str) -> tuple[dict[str, Any] | None, float]:
    items = extract_feed_items(feed_response)
    if not items:
        return None, 0.0
    reference = description or title
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        item_title = feed_item_title(item)
        score = max(text_similarity(reference, item_title), text_similarity(title, item_title))
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best_item = scored[0]
    return (best_item, best_score) if best_score >= 0.35 else (None, best_score)


def extract_profile_media(profile_response: dict[str, Any]) -> dict[str, Any] | None:
    for item in walk_dicts(unwrap_api_data(profile_response)):
        url = item.get("url") or item.get("originalUrl") or item.get("videoUrl")
        token = item.get("urlToken") or ""
        key = item.get("decodeKey") or item.get("decryptKey") or item.get("key")
        if url and key:
            item = dict(item)
            item["download_url"] = str(url) + str(token or "")
            item["download_key"] = str(key)
            return item
    return None


def find_downloaded_media(tools_dir: Path | None, author: str, title: str, video_id: str) -> Path | None:
    if not tools_dir:
        return None
    downloads_dir = tools_dir / "downloads"
    if not downloads_dir.exists():
        return None
    author_dir = downloads_dir / wx_clean_folder_name(author)
    expected = author_dir / wx_video_filename(title, video_id)
    if expected.exists():
        return expected
    patterns = []
    if author_dir.exists():
        patterns.extend(author_dir.glob(f"*_{video_id}.mp4"))
    patterns.extend(downloads_dir.glob(f"**/*_{video_id}.mp4"))
    existing = [path for path in patterns if path.exists()]
    if not existing:
        return None
    existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return existing[0]


def wait_for_wx_download(
    session: requests.Session,
    tools_dir: Path | None,
    author: str,
    title: str,
    video_id: str,
    timeout_seconds: int,
) -> tuple[Path | None, dict[str, Any]]:
    deadline = time.time() + max(1, timeout_seconds)
    last_progress: dict[str, Any] = {}
    while time.time() <= deadline:
        try:
            progress = json_get(session, WX_CHANNEL_BATCH_PROGRESS_URL)
            last_progress = progress
            progress_data = unwrap_api_data(progress)
            if isinstance(progress_data, dict):
                tasks = progress_data.get("tasks") or []
                failed = int(progress_data.get("failed") or 0)
                done = int(progress_data.get("done") or 0)
                total = int(progress_data.get("total") or 0)
                if failed > 0:
                    return None, {"status": "DOWNLOAD_FAILED", "progress": progress}
                if total > 0 and done >= total:
                    media_path = find_downloaded_media(tools_dir, author, title, video_id)
                    return media_path, {"status": "DOWNLOAD_DONE", "progress": progress, "tasks": tasks}
        except Exception as exc:  # noqa: BLE001 - keep polling until timeout.
            last_progress = {"error": str(exc)}
        media_path = find_downloaded_media(tools_dir, author, title, video_id)
        if media_path:
            return media_path, {"status": "DOWNLOAD_DONE_FILE_FOUND", "progress": last_progress}
        time.sleep(2)
    return None, {"status": "DOWNLOAD_TIMEOUT", "progress": last_progress}


def acquire_media_with_wx_channel(
    session: requests.Session,
    author: str,
    description: str,
    title: str,
    wx_channel_exe: Path | None,
    wx_start_mode: str,
    wait_seconds: int,
    ensure_wechat_pc: bool,
    wechat_exe: Path | None,
    download_timeout_seconds: int,
    force_redownload: bool,
) -> MediaAcquisitionResult:
    metadata: dict[str, Any] = {"method": "wx_channel"}
    warnings: list[str] = []
    tools_dir = wx_channel_tools_dir(wx_channel_exe)
    ready, ensure_details, ensure_warnings = ensure_wx_channel_ready(
        session=session,
        exe_path=wx_channel_exe,
        start_mode=wx_start_mode,
        wait_seconds=wait_seconds,
        ensure_wechat_pc=ensure_wechat_pc,
        wechat_exe=wechat_exe,
    )
    metadata["ensure"] = ensure_details
    warnings.extend(ensure_warnings)
    if not ready:
        status = str(ensure_details.get("status_code") or "WX_CHANNEL_NOT_READY")
        return MediaAcquisitionResult(status=status, method="wx_channel", media_file=None, metadata=metadata, warnings=warnings)

    search_response = json_get(session, WX_CHANNEL_CONTACT_SEARCH_URL, keyword=author)
    metadata["contact_search"] = {
        "keyword": author,
        "code": search_response.get("code"),
        "message": search_response.get("message"),
    }
    contact = choose_contact(search_response, author)
    if not contact:
        warnings.append(f"CONTACT_SEARCH_FAILED: wx_channel did not return a matching account for author={author!r}")
        return MediaAcquisitionResult(status="CONTACT_SEARCH_FAILED", method="wx_channel", media_file=None, metadata=metadata, warnings=warnings)
    username = str(contact.get("username") or contact.get("userName") or contact.get("id") or "")
    metadata["matched_contact"] = {
        "nickname": contact.get("nickname") or contact.get("nickName"),
        "username": username,
        "authProfession": contact.get("authProfession"),
    }

    feed_response = json_get(session, WX_CHANNEL_FEED_LIST_URL, username=username)
    feed_item, match_score = choose_feed_item(feed_response, description, title)
    metadata["feed_match"] = {
        "score": match_score,
        "feed_item_count": len(extract_feed_items(feed_response)),
    }
    if not feed_item:
        warnings.append(f"FEED_MATCH_FAILED: no author feed item matched public description; best_score={match_score:.3f}")
        return MediaAcquisitionResult(status="FEED_MATCH_FAILED", method="wx_channel", media_file=None, metadata=metadata, warnings=warnings)
    object_id = feed_item_object_id(feed_item)
    nonce_id = feed_item_nonce_id(feed_item)
    feed_title = feed_item_title(feed_item)
    metadata["matched_feed"] = {
        "object_id": object_id,
        "nonce_id": nonce_id,
        "title": feed_title,
        "match_score": match_score,
    }

    profile_response = json_get(session, WX_CHANNEL_PROFILE_URL, object_id=object_id, nonce_id=nonce_id)
    media = extract_profile_media(profile_response)
    if not media:
        warnings.append("PROFILE_MEDIA_NOT_FOUND: wx_channel profile response did not include downloadable media URL/key")
        return MediaAcquisitionResult(status="PROFILE_MEDIA_NOT_FOUND", method="wx_channel", media_file=None, metadata=metadata, warnings=warnings)
    video_title = text_excerpt(feed_title or title, 90).rstrip(".")
    duration_seconds = media.get("videoPlayLen") or media.get("duration")
    duration_label = format_seconds(duration_seconds) if isinstance(duration_seconds, (int, float)) else ""
    size_bytes = media.get("fileSize") or media.get("size") or 0
    size_label = f"{float(size_bytes) / 1024 / 1024:.2f}MB" if isinstance(size_bytes, (int, float)) and size_bytes else ""
    batch_video = {
        "id": object_id,
        "url": media["download_url"],
        "title": video_title,
        "authorName": author,
        "key": media["download_key"],
        "cover": media.get("coverUrl") or media.get("thumbUrl") or "",
        "duration": duration_label,
        "sizeMB": size_label,
        "resolution": "",
        "pageSource": "api_profile",
    }
    metadata["profile"] = {
        "object_id": object_id,
        "nonce_id": nonce_id,
        "key_present": bool(batch_video["key"]),
        "duration": duration_label,
        "sizeMB": size_label,
    }
    payload = {"videos": [batch_video], "forceRedownload": force_redownload, "pageSource": "api_profile"}
    start_response = json_post(session, WX_CHANNEL_BATCH_START_URL, payload, timeout=30)
    metadata["batch_start"] = {
        "code": start_response.get("code"),
        "message": start_response.get("message"),
        "forceRedownload": force_redownload,
    }
    media_path, download_details = wait_for_wx_download(
        session=session,
        tools_dir=tools_dir,
        author=author,
        title=video_title,
        video_id=object_id,
        timeout_seconds=download_timeout_seconds,
    )
    metadata["download"] = download_details
    if not media_path:
        status = str(download_details.get("status") or "DOWNLOAD_FAILED")
        warnings.append(f"{status}: wx_channel batch download did not produce a local media file")
        return MediaAcquisitionResult(status=status, method="wx_channel", media_file=None, metadata=metadata, warnings=warnings)
    metadata["downloaded_file"] = {
        "path": str(media_path),
        "size_bytes": media_path.stat().st_size,
        "sha256": file_hash(media_path),
    }
    return MediaAcquisitionResult(status="downloaded", method="wx_channel", media_file=media_path, metadata=metadata, warnings=warnings)


def canonical_terms_from_context(context_prompt: str | None) -> list[str]:
    if not context_prompt:
        return []
    terms = re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{2,}\b", context_prompt)
    seen: set[str] = set()
    canonical_terms: list[str] = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            canonical_terms.append(term)
            seen.add(key)
    return canonical_terms


def normalize_term_casing(text: str, canonical_terms: list[str]) -> str:
    for term in sorted(canonical_terms, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(term)}\b", term, text, flags=re.IGNORECASE)
    return text


def format_seconds(value: int | float | None) -> str:
    if value is None:
        return ""
    seconds = max(0, int(round(float(value))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def load_manual_transcript(path: Path | None) -> str | None:
    if not path:
        return None
    return path.read_text(encoding="utf-8").strip()


def copy_media_file(media_file: Path | None, out_dir: Path) -> str | None:
    if not media_file:
        return None
    if not media_file.exists():
        raise FileNotFoundError(f"media file does not exist: {media_file}")
    target = out_dir / f"source_media{media_file.suffix.lower() or '.bin'}"
    shutil.copy2(media_file, target)
    return target.name


def read_windows_user_env(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value) if value else None
    except OSError:
        return None


def get_openrouter_api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or read_windows_user_env("OPENROUTER_API_KEY")


def resolve_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - return a practical setup error.
        raise RuntimeError(
            "ffmpeg is required for ASR media extraction. Install ffmpeg or install the Python package imageio-ffmpeg."
        ) from exc


def run_subprocess(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}\n{detail}")


def reset_audio_chunks(audio_dir: Path) -> None:
    audio_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("chunk_*.mp3", "chunk_*.wav", "audio.mp3", "audio.wav"):
        for path in audio_dir.glob(pattern):
            path.unlink()


def extract_audio_chunks(
    media_file: Path,
    audio_dir: Path,
    chunk_seconds: int,
    audio_format: str,
) -> list[Path]:
    if audio_format not in SUPPORTED_ASR_AUDIO_FORMATS:
        raise ValueError(f"unsupported ASR audio format: {audio_format}")
    reset_audio_chunks(audio_dir)
    ffmpeg = resolve_ffmpeg()
    ext = f".{audio_format}"
    base_cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(media_file),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
    ]
    if audio_format == "mp3":
        base_cmd.extend(["-b:a", "48k"])

    if chunk_seconds > 0:
        target_pattern = audio_dir / f"chunk_%04d{ext}"
        command = [
            *base_cmd,
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-reset_timestamps",
            "1",
            str(target_pattern),
        ]
        run_subprocess(command)
        chunks = sorted(audio_dir.glob(f"chunk_*{ext}"))
    else:
        target = audio_dir / f"audio{ext}"
        run_subprocess([*base_cmd, str(target)])
        chunks = [target] if target.exists() else []

    if not chunks:
        raise RuntimeError("ffmpeg completed but no audio chunks were produced.")
    return chunks


def transcribe_audio_chunk_openrouter(
    session: requests.Session,
    api_key: str,
    chunk_path: Path,
    model: str,
    language: str | None,
    audio_format: str,
    context_prompt: str | None,
    temperature: float,
) -> dict[str, Any]:
    encoded_audio = base64.b64encode(chunk_path.read_bytes()).decode("ascii")
    payload: dict[str, Any] = {
        "input_audio": {"data": encoded_audio, "format": audio_format},
        "model": model,
    }
    if language:
        payload["language"] = language
    if context_prompt:
        payload["prompt"] = context_prompt
    payload["temperature"] = temperature
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost/openclaw-wechat-channel-asr",
        "X-Title": "OpenClaw WeChat Channel ASR",
    }
    response = session.post(OPENROUTER_TRANSCRIPTION_URL, headers=headers, json=payload, timeout=180)
    if response.status_code >= 400:
        raise RuntimeError(f"OpenRouter transcription failed: HTTP {response.status_code} {response.text[:800]}")
    result = response.json()
    if "text" not in result:
        raise RuntimeError(f"OpenRouter transcription response has no text field: {json.dumps(result)[:800]}")
    return result


def run_openrouter_asr(
    session: requests.Session,
    media_file: Path,
    assets_dir: Path,
    model: str,
    language: str | None,
    chunk_seconds: int,
    audio_format: str,
    context_prompt: str | None,
    temperature: float,
) -> AsrResult:
    api_key = get_openrouter_api_key()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required when --asr openrouter is used.")

    audio_dir = assets_dir / "audio"
    chunks = extract_audio_chunks(media_file, audio_dir, chunk_seconds, audio_format)
    segments: list[dict[str, Any]] = []
    warnings: list[str] = []
    usage_totals = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    texts: list[str] = []
    canonical_terms = canonical_terms_from_context(context_prompt)

    for index, chunk in enumerate(chunks):
        start = index * chunk_seconds if chunk_seconds > 0 else None
        end = (index + 1) * chunk_seconds if chunk_seconds > 0 else None
        result = transcribe_audio_chunk_openrouter(
            session=session,
            api_key=api_key,
            chunk_path=chunk,
            model=model,
            language=language,
            audio_format=audio_format,
            context_prompt=context_prompt,
            temperature=temperature,
        )
        text = normalize_term_casing(str(result.get("text") or "").strip(), canonical_terms)
        usage = result.get("usage") or {}
        for key in ("total_tokens", "input_tokens", "output_tokens"):
            if isinstance(usage.get(key), (int, float)):
                usage_totals[key] += int(usage[key])
        if isinstance(usage.get("cost"), (int, float)):
            usage_totals["cost"] += float(usage["cost"])
        if isinstance(usage.get("seconds"), (int, float)) and start is not None:
            end = start + float(usage["seconds"])
        if not text:
            warnings.append(f"ASR chunk {index} returned empty text: {chunk.name}")
        else:
            texts.append(text)
        segments.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "start_label": format_seconds(start),
                "end_label": format_seconds(end),
                "speaker": None,
                "speaker_confidence": "not_detected",
                "text": text,
                "source": "openrouter_asr",
                "asr_model": model,
                "audio_file": f"assets/audio/{chunk.name}",
                "usage": usage,
            }
        )

    text = "\n\n".join(texts).strip()
    status = "asr_openrouter_completed" if text and not warnings else "asr_openrouter_partial"
    metadata = {
        "engine": "openrouter",
        "endpoint": OPENROUTER_TRANSCRIPTION_URL,
        "model": model,
        "language": language or "auto",
        "audio_format": audio_format,
        "chunk_seconds": chunk_seconds,
        "chunk_count": len(chunks),
        "temperature": temperature,
        "context_prompt_chars": len(context_prompt or ""),
        "context_prompt": context_prompt or "",
        "canonical_terms": canonical_terms,
        "speaker_diarization_status": "not_available_openrouter_transcriptions",
        "timestamp_status": "approximate_chunk_boundaries" if chunk_seconds > 0 else "none_single_chunk",
        "usage": usage_totals,
        "cost_usd": round(float(usage_totals["cost"]), 8),
    }
    full_text = "\n\n".join(part for part in texts if part).strip()
    files = [f"assets/audio/{chunk.name}" for chunk in chunks]
    return AsrResult(status=status, text=full_text, segments=segments, warnings=warnings, files=files, metadata=metadata)


def build_archive(
    source: str,
    out_root: Path,
    manual_transcript: Path | None = None,
    media_file: Path | None = None,
    auto_download: str = "none",
    ensure_service: bool = False,
    ensure_wechat_pc: bool = False,
    wechat_exe: Path | None = None,
    wx_channel_exe: Path | None = None,
    wx_channel_start_mode: str = "normal",
    wx_channel_wait_seconds: int = 90,
    wx_channel_download_timeout: int = 240,
    wx_channel_force_redownload: bool = True,
    asr_engine: str = "none",
    asr_model: str = DEFAULT_ASR_MODEL,
    asr_language: str | None = None,
    asr_chunk_seconds: int = 60,
    asr_audio_format: str = "mp3",
    asr_context: str | None = None,
    asr_temperature: float = 0.0,
) -> Path:
    short_uri = parse_short_uri(source)
    session = make_session()
    captured_at = now_iso()
    feed_response = fetch_feed_info(session, short_uri)

    data = feed_response.get("data") or {}
    author_info = data.get("authorInfo") or {}
    feed_info = data.get("feedInfo") or {}
    scene_info = data.get("sceneInfo") or {}
    err_msg = data.get("errMsg") or {}

    author = str(author_info.get("nickname") or "")
    description = str(feed_info.get("description") or "").strip()
    published_at, published_date = normalize_published_time(feed_info.get("createtime"))
    title = title_from_feed(author, description, short_uri)
    slug = slug_from_feed(author, published_date, short_uri)
    out_dir = out_root / slug
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    raw_feed_path = out_dir / "feed.json"
    video_md_path = out_dir / "video.md"
    legacy_transcript_md_path = out_dir / "transcript.md"
    transcript_json_path = out_dir / "transcript.json"
    metadata_path = out_dir / "metadata.json"
    if legacy_transcript_md_path.exists():
        legacy_transcript_md_path.unlink()

    warnings: list[str] = []
    media_acquisition: dict[str, Any] = {"method": "provided_local_media" if media_file else "none", "status": "not_requested"}
    media_acquisition_method = "provided_local_media"
    effective_media_file = media_file
    if auto_download == "wx_channel" and not effective_media_file:
        wx_exe = wx_channel_exe or default_wx_channel_exe()
        acquisition = acquire_media_with_wx_channel(
            session=session,
            author=author,
            description=description,
            title=title,
            wx_channel_exe=wx_exe,
            wx_start_mode=wx_channel_start_mode if ensure_service else "none",
            wait_seconds=wx_channel_wait_seconds,
            ensure_wechat_pc=ensure_wechat_pc,
            wechat_exe=wechat_exe,
            download_timeout_seconds=wx_channel_download_timeout,
            force_redownload=wx_channel_force_redownload,
        )
        media_acquisition = {
            "method": acquisition.method,
            "status": acquisition.status,
            **acquisition.metadata,
        }
        warnings.extend(acquisition.warnings)
        if acquisition.media_file:
            effective_media_file = acquisition.media_file
            media_acquisition_method = "wx_channel"
    elif auto_download != "none" and media_file:
        media_acquisition = {
            "method": auto_download,
            "status": "skipped_media_file_provided",
        }

    media_filename = copy_media_file(effective_media_file, out_dir)
    media_path = out_dir / media_filename if media_filename else None
    manual_text = load_manual_transcript(manual_transcript)

    asset_results = [
        download_asset(session, assets_dir, "cover", feed_info.get("coverUrl"), "cover", ".jpg"),
        download_asset(session, assets_dir, "avatar", author_info.get("headImgUrl"), "avatar", ".jpg"),
        download_asset(session, assets_dir, "auth_icon", author_info.get("authIconUrl"), "auth_icon", ".png"),
    ]
    asset_results = [result for result in asset_results if result is not None]
    asset_paths = [result.path for result in asset_results if result.ok and result.path]

    video_url_candidates = [
        feed_info.get("videoUrl"),
        (feed_info.get("h264VideoInfo") or {}).get("videoUrl"),
        (feed_info.get("h265VideoInfo") or {}).get("videoUrl"),
    ]
    public_video_urls = [url for url in video_url_candidates if url]

    for asset in asset_results:
        if asset.warning:
            warnings.append(asset.warning)

    transcript_segments = []
    transcript_text = ""
    asr_result: AsrResult | None = None
    if manual_text:
        transcript_status = "manual_provided"
        transcript_text = manual_text
        transcript_segments.append(
            {
                "start": None,
                "end": None,
                "speaker": None,
                "speaker_confidence": "not_detected",
                "text": manual_text,
                "source": "manual_transcript",
            }
        )
        if asr_engine != "none":
            warnings.append("manual transcript was provided; ASR was skipped to avoid overwriting user-supplied text")
    elif asr_engine == "openrouter":
        if not media_path:
            if auto_download != "none":
                transcript_status = "unavailable_media_acquisition_failed"
                warnings.append(
                    f"ASR_SKIPPED: --asr openrouter was requested, but media acquisition status is {media_acquisition.get('status')}"
                )
            else:
                raise RuntimeError("--asr openrouter requires --media-file or --auto-download wx_channel.")
        else:
            effective_asr_context = asr_context or build_asr_context(title, author, description)
            asr_result = run_openrouter_asr(
                session=session,
                media_file=media_path,
                assets_dir=assets_dir,
                model=asr_model,
                language=asr_language,
                chunk_seconds=asr_chunk_seconds,
                audio_format=asr_audio_format,
                context_prompt=effective_asr_context,
                temperature=asr_temperature,
            )
            transcript_status = asr_result.status
            transcript_text = asr_result.text
            transcript_segments = asr_result.segments
            warnings.extend(asr_result.warnings)
            if asr_result.metadata["speaker_diarization_status"] == "not_available_openrouter_transcriptions":
                warnings.append("OpenRouter transcription API returned text only; speaker diarization is not available in this ASR path")
    else:
        transcript_status = "unavailable_no_public_subtitle_or_video_url"
        warnings.extend(
            [
                "public sph response does not include a subtitle track",
                "public sph response does not include a video or audio URL; ASR cannot run without user-provided media",
                "transcript.json records an explicit unavailability status; no transcript text was generated",
            ]
        )
    if media_filename and not manual_text and asr_engine == "none":
        warnings.append("source media was copied, but ASR was not requested; pass --asr openrouter to generate a transcript")
    if err_msg.get("type") not in (0, None):
        warnings.append(f"feed errMsg type={err_msg.get('type')}: {err_msg.get('title') or err_msg.get('content') or ''}".strip())

    counts = {
        "favorite": format_count(feed_info.get("favCountFmt")),
        "forward": format_count(feed_info.get("forwardCountFmt")),
        "like": format_count(feed_info.get("likeCountFmt")),
        "comment": format_count(feed_info.get("commentCountFmt")),
    }

    write_json(raw_feed_path, feed_response)

    transcript_json = {
        "schema_version": 1,
        "source_type": SOURCE_TYPE,
        "source_url": canonical_url(short_uri),
        "preview_url": preview_url(short_uri),
        "short_uri": short_uri,
        "author": author,
        "title": title,
        "published": published_at,
        "captured_at": captured_at,
        "transcript_status": transcript_status,
        "canonical_source": "video.md",
        "file_role": "structured_transcript",
        "asr": asr_result.metadata if asr_result else None,
        "text": transcript_text,
        "segments": transcript_segments,
        "warnings": warnings,
    }
    write_json(transcript_json_path, transcript_json)

    cover_asset = next((asset.path for asset in asset_results if asset.role == "cover" and asset.path), None)
    avatar_asset = next((asset.path for asset in asset_results if asset.role == "avatar" and asset.path), None)
    video_frontmatter = [
        ("source_type", SOURCE_TYPE),
        ("source_url", canonical_url(short_uri)),
        ("preview_url", preview_url(short_uri)),
        ("short_uri", short_uri),
        ("title", title),
        ("author", author),
        ("published", published_at or ""),
        ("captured_at", captured_at),
        ("transcript_status", transcript_status),
        ("asr_engine", (asr_result.metadata["engine"] if asr_result else "")),
        ("asr_model", (asr_result.metadata["model"] if asr_result else "")),
        ("canonical_source", "video.md"),
        ("asset_count", len(asset_paths)),
        ("status", "raw_asr" if asr_result else ("raw" if manual_text else "metadata_only")),
    ]
    video_lines = [frontmatter(video_frontmatter).rstrip(), "", f"# {title}", ""]
    if cover_asset:
        video_lines.extend([f"![cover]({cover_asset})", ""])
    video_lines.extend(
        [
            "## 基本信息",
            "",
            f"- 作者：{author or '未知'}",
            f"- 发布时间：{published_at or '未知'}",
            f"- 原始链接：{canonical_url(short_uri)}",
            f"- 预览链接：{preview_url(short_uri)}",
            f"- 收藏：{counts['favorite'] or '未知'}",
            f"- 转发：{counts['forward'] or '未知'}",
            f"- 点赞：{counts['like'] or '未知'}",
            f"- 评论：{counts['comment'] or '未知'}",
            "",
            "## 视频文案",
            "",
            description or "未获取到公开文案。",
            "",
            "## 字幕状态",
            "",
        ]
    )
    if manual_text:
        video_lines.append("已使用用户提供的字幕文本生成 `transcript.json`，逐字稿正文已合并到本文件。")
    elif asr_result:
        video_lines.append(
            f"已使用 OpenRouter `{asr_result.metadata['model']}` 对用户提供的本地媒体文件执行 ASR，结构化结果写入 `transcript.json`，逐字稿正文已合并到本文件。"
        )
        video_lines.append(
            f"ASR 切片数：{asr_result.metadata['chunk_count']}；费用：约 ${asr_result.metadata['cost_usd']:.8f}。"
        )
    else:
        video_lines.append(
            "未生成真实字幕。公开预览接口只返回文案、封面和互动数，没有返回字幕轨、音频地址或可下载视频地址。"
        )
    if manual_text or asr_result:
        video_lines.extend(["", "## 逐字稿", ""])
        if asr_result:
            video_lines.append(
                f"> ASR: OpenRouter `{asr_result.metadata['model']}`；语言：`{asr_result.metadata['language']}`；时间戳为按音频切片生成的近似边界。"
            )
            video_lines.append("")
        for segment in transcript_segments:
            label = ""
            if segment.get("start_label") and segment.get("end_label"):
                label = f"[{segment['start_label']} - {segment['end_label']}] "
            text = str(segment.get("text") or "").strip()
            if text:
                video_lines.append(f"{label}{text}")
                video_lines.append("")
    video_lines.extend(["", "## 本地资源", ""])
    if asset_paths:
        for path in asset_paths:
            role = next((asset.role for asset in asset_results if asset.path == path), "asset")
            video_lines.append(f"- {role}: `{path}`")
    else:
        video_lines.append("- 未成功下载本地资源。")
    if avatar_asset:
        video_lines.extend(["", f"![avatar]({avatar_asset})"])
    video_text = "\n".join(video_lines).rstrip() + "\n"
    video_md_path.write_text(video_text, encoding="utf-8")

    file_roles = {
        "metadata.json": "archive_index_status_and_quality_control",
        "video.md": "canonical_reading_file",
        "transcript.json": "structured_transcript_status_segments_and_asr_metadata",
        "feed.json": "wechat_preview_api_source_snapshot",
        "assets/": "localized_cover_avatar_auth_icon_and_asr_audio_chunks",
    }
    if media_filename:
        file_roles[media_filename] = "source_media_evidence"

    metadata = {
        "schema_version": 1,
        "source_type": SOURCE_TYPE,
        "input": source,
        "source_url": canonical_url(short_uri),
        "preview_url": preview_url(short_uri),
        "short_uri": short_uri,
        "api_url": API_URL,
        "title": title,
        "author": author,
        "published": published_at,
        "captured_at": captured_at,
        "fetch_method": "public-finder-preview-api",
        "parser": "wechat_channels_sph_preview",
        "transcript_status": transcript_status,
        "asr": asr_result.metadata if asr_result else None,
        "speaker_diarization_status": (
            asr_result.metadata["speaker_diarization_status"] if asr_result else "not_available_without_transcript"
        ),
        "video_url_publicly_exposed": bool(public_video_urls),
        "public_video_url_count": len(public_video_urls),
        "manual_transcript": str(manual_transcript) if manual_transcript else None,
        "source_media": media_filename,
        "source_media_metadata": source_media_metadata(media_path, media_filename, media_acquisition_method),
        "media_acquisition": media_acquisition,
        "cover_asset": cover_asset,
        "asset_count": len(asset_paths),
        "content_chars": len(video_text),
        "transcript_chars": len(transcript_text),
        "content_sha256": content_hash(video_text),
        "counts": counts,
        "scene_info": scene_info,
        "err_msg": err_msg,
        "canonical_source": "video.md",
        "agent_reading_order": [
            "metadata.json",
            "video.md",
            "transcript.json",
            "feed.json",
        ],
        "file_roles": file_roles,
        "files": [
            "metadata.json",
            "video.md",
            "transcript.json",
            "feed.json",
            "assets/",
            *asset_paths,
            *([media_filename] if media_filename else []),
            *((asr_result.files if asr_result else [])),
        ],
        "warnings": warnings,
        "notes": [
            "metadata.json is the agent entrypoint for status, warnings, file roles, and reading order.",
            "video.md is the only canonical reading file for WeChat Channels sph archives.",
            "transcript.json preserves transcript status, text, segments, ASR details, and speaker degradation.",
            "feed.json preserves the raw public preview API response for evidence.",
        ],
    }
    write_json(metadata_path, metadata)
    return out_dir


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive public WeChat Channels sph preview pages.")
    parser.add_argument("sources", nargs="+", help="weixin.qq.com/sph URL, channels preview URL, or short uri.")
    parser.add_argument(
        "--out-dir",
        default="outputs",
        help="Output root directory. Each input gets one child directory. Default: outputs",
    )
    parser.add_argument(
        "--manual-transcript",
        type=Path,
        help="Optional UTF-8 text transcript to attach to a single source.",
    )
    parser.add_argument(
        "--media-file",
        type=Path,
        help="Optional local source video/audio file to copy into the output directory.",
    )
    parser.add_argument(
        "--auto-download",
        choices=["none", "wx_channel"],
        default="none",
        help="Optional local media acquisition backend. Use wx_channel to search, match, download, and copy source_media automatically.",
    )
    parser.add_argument(
        "--ensure-service",
        action="store_true",
        help="When --auto-download wx_channel is used, start wx_channel if its local API is not already running.",
    )
    parser.add_argument(
        "--ensure-wechat-pc",
        action="store_true",
        help="When --auto-download wx_channel is used, start WeChat PC if it is not already running. Login still requires the user.",
    )
    parser.add_argument(
        "--wechat-exe",
        type=Path,
        help="Optional WeChat.exe path used by --ensure-wechat-pc.",
    )
    parser.add_argument(
        "--wx-channel-exe",
        type=Path,
        default=None,
        help="Optional wx_channel executable path. Defaults to WX_CHANNEL_EXE or the workspace _reference/wechat-media-tools copy.",
    )
    parser.add_argument(
        "--wx-channel-start-mode",
        choices=["normal", "admin", "none"],
        default="normal",
        help="How --ensure-service starts wx_channel when needed. Default: normal.",
    )
    parser.add_argument(
        "--wx-channel-wait-seconds",
        type=int,
        default=90,
        help="Seconds to wait for wx_channel and a ready WeChat Channels client. Default: 90.",
    )
    parser.add_argument(
        "--wx-channel-download-timeout",
        type=int,
        default=240,
        help="Seconds to wait for wx_channel batch download completion. Default: 240.",
    )
    parser.add_argument(
        "--no-wx-channel-force-redownload",
        action="store_true",
        help="Do not force wx_channel to redownload when a matching prior download exists.",
    )
    parser.add_argument(
        "--asr",
        choices=["none", "openrouter"],
        default="none",
        help="Optional ASR engine. Use 'openrouter' with --media-file to generate transcript.json and embed transcript text in video.md.",
    )
    parser.add_argument(
        "--asr-model",
        default=DEFAULT_ASR_MODEL,
        help=f"OpenRouter ASR model. Default: {DEFAULT_ASR_MODEL}",
    )
    parser.add_argument(
        "--asr-language",
        default=None,
        help="Optional ISO-639-1 language code for ASR, such as zh or en. Omit for auto-detection.",
    )
    parser.add_argument(
        "--asr-chunk-seconds",
        type=int,
        default=60,
        help="Audio chunk size used to create approximate transcript timestamps. Use 0 for one chunk. Default: 60",
    )
    parser.add_argument(
        "--asr-audio-format",
        choices=sorted(SUPPORTED_ASR_AUDIO_FORMATS),
        default="mp3",
        help="Intermediate audio format sent to OpenRouter. Default: mp3",
    )
    parser.add_argument(
        "--asr-context",
        default=None,
        help="Optional prompt/context for ASR domain terms. Defaults to title, author, and public description.",
    )
    parser.add_argument(
        "--asr-temperature",
        type=float,
        default=0.0,
        help="ASR sampling temperature. Default: 0.0 for stable archival output.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.manual_transcript and len(args.sources) != 1:
        print("--manual-transcript can only be used with one source at a time.", file=sys.stderr)
        return 2
    if (args.media_file or args.auto_download != "none") and len(args.sources) != 1:
        print("--media-file and --auto-download can only be used with one source at a time.", file=sys.stderr)
        return 2
    if args.asr != "none" and not args.media_file and args.auto_download == "none":
        print("--asr requires --media-file or --auto-download wx_channel because public sph links do not expose source media.", file=sys.stderr)
        return 2
    if args.asr_chunk_seconds < 0:
        print("--asr-chunk-seconds must be >= 0.", file=sys.stderr)
        return 2

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    for source in args.sources:
        started = time.time()
        out_dir = build_archive(
            source=source,
            out_root=out_root,
            manual_transcript=args.manual_transcript,
            media_file=args.media_file,
            auto_download=args.auto_download,
            ensure_service=args.ensure_service,
            ensure_wechat_pc=args.ensure_wechat_pc,
            wechat_exe=args.wechat_exe,
            wx_channel_exe=args.wx_channel_exe,
            wx_channel_start_mode=args.wx_channel_start_mode,
            wx_channel_wait_seconds=args.wx_channel_wait_seconds,
            wx_channel_download_timeout=args.wx_channel_download_timeout,
            wx_channel_force_redownload=not args.no_wx_channel_force_redownload,
            asr_engine=args.asr,
            asr_model=args.asr_model,
            asr_language=args.asr_language,
            asr_chunk_seconds=args.asr_chunk_seconds,
            asr_audio_format=args.asr_audio_format,
            asr_context=args.asr_context,
            asr_temperature=args.asr_temperature,
        )
        elapsed = time.time() - started
        print(f"Archived {source} -> {out_dir} ({elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
