#!/usr/bin/env python
"""Fetch the likely original-language transcript for YouTube videos.

Selection policy:
1. Never call translated transcript tracks.
2. Prefer a manual transcript in the same language as YouTube's generated
   source transcript when a generated source track exists.
3. If only manual tracks exist, choose the first manual track that does not
   start with common translation disclaimers.
4. Fall back to the first available track and print a low-confidence reason.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from youtube_transcript_api import YouTubeTranscriptApi


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass
class TrackSample:
    track: object
    snippets: list
    has_translation_marker: bool


@dataclass
class Selection:
    track: object
    reason: str
    confidence: str


@dataclass
class TranscriptSegment:
    start: float
    duration: float
    text: str
    speaker: str | None
    speaker_label: str | None
    speaker_confidence: str

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class TranscriptGroup:
    start: float
    end: float
    text: str
    speaker: str | None
    speaker_label: str | None
    speaker_confidence: str


SENTENCE_END_RE = re.compile(r'[.!?\u3002\uff01\uff1f\u2026]["\'\u201d\u2019)）]*$')
LEADING_SPEAKER_MARKER_RE = re.compile(r"^\s*(?P<marker>>>+|-)\s+(?P<body>.+)$")
COLON_SPEAKER_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff ._\-\u00b7]{0,23})"
    r"\s*[:\uff1a]\s*(?P<body>.+)$"
)
KNOWN_SPEAKER_LABELS = {
    "host",
    "interviewer",
    "interviewee",
    "guest",
    "speaker",
    "moderator",
    "question",
    "answer",
    "\u4e3b\u6301\u4eba",
    "\u5609\u5bbe",
    "\u8bbf\u8c08\u8005",
    "\u53d7\u8bbf\u8005",
    "\u63d0\u95ee\u8005",
    "\u56de\u7b54\u8005",
    "\u8001\u5e08",
    "\u5b66\u751f",
}
NON_SPEAKER_LABELS = {
    "http",
    "https",
    "chapter",
    "chapters",
    "section",
    "topic",
    "title",
    "note",
    "notes",
    "example",
    "warning",
    "\u7ae0\u8282",
    "\u4e3b\u9898",
    "\u6807\u9898",
    "\u6ce8\u610f",
    "\u4f8b\u5982",
}
MAX_GROUP_GAP_SECONDS = 8.0
MAX_GROUP_SPAN_SECONDS = 30.0


def parse_video_id(value: str) -> str:
    value = value.strip()
    if VIDEO_ID_RE.match(value):
        return value

    parsed = urlparse(value)
    if parsed.netloc.endswith("youtu.be"):
        candidate = parsed.path.strip("/").split("/")[0]
        if VIDEO_ID_RE.match(candidate):
            return candidate

    query_id = parse_qs(parsed.query).get("v", [""])[0]
    if VIDEO_ID_RE.match(query_id):
        return query_id

    raise ValueError(f"Cannot parse YouTube video id from: {value}")


def language_family(language_code: str) -> str:
    lowered = language_code.lower()
    if lowered.startswith("zh"):
        return "zh"
    if lowered.startswith("en"):
        return "en"
    return lowered.split("-")[0]


def has_translation_marker(snippets: Sequence[object]) -> bool:
    prefix = " ".join(getattr(item, "text", "") for item in snippets[:12]).strip()
    lowered = prefix.lower()
    ascii_markers = (
        "translated by ai",
        "translated by machine",
        "machine translated",
        "auto-translated",
        "for reference only",
    )
    unicode_markers = (
        "\u7ffb\u8bd1",
        "\u673a\u7ffb",
        "\u673a\u5668\u7ffb\u8bd1",
        "\u4ec5\u4f9b\u53c2\u8003",
    )
    return any(marker in lowered for marker in ascii_markers) or any(
        marker in prefix for marker in unicode_markers
    )


def make_api() -> YouTubeTranscriptApi:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }
    )
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return YouTubeTranscriptApi(http_client=session)


def with_retries(fn, attempts: int, label: str):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # YouTube often fails transiently with TLS EOF.
            last_error = exc
            if attempt == attempts:
                break
            wait = min(2 * attempt, 8)
            print(
                f"[retry] {label} failed on attempt {attempt}/{attempts}: {exc}",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise last_error


def fetch_sample(track: object, attempts: int) -> TrackSample:
    fetched = with_retries(
        lambda: track.fetch(preserve_formatting=False),
        attempts=attempts,
        label=f"sample {track.language_code}",
    )
    snippets = list(fetched)
    return TrackSample(
        track=track,
        snippets=snippets,
        has_translation_marker=has_translation_marker(snippets),
    )


def choose_track(tracks: Iterable[object], attempts: int) -> Selection:
    ordered = list(tracks)
    if not ordered:
        raise RuntimeError("No transcript tracks are available.")

    manual = [track for track in ordered if not track.is_generated]
    generated = [track for track in ordered if track.is_generated]

    if generated:
        source = generated[0]
        source_family = language_family(source.language_code)
        for track in manual:
            if language_family(track.language_code) == source_family:
                sample = fetch_sample(track, attempts)
                if not sample.has_translation_marker:
                    return Selection(
                        track=track,
                        reason=(
                            "manual track matches generated source language "
                            f"{source.language_code}"
                        ),
                        confidence="high",
                    )
        return Selection(
            track=source,
            reason="generated track is the only detected source-language track",
            confidence="medium",
        )

    samples = [fetch_sample(track, attempts) for track in manual]
    for sample in samples:
        if not sample.has_translation_marker:
            return Selection(
                track=sample.track,
                reason="first manual track without an initial translation marker",
                confidence="medium",
            )

    return Selection(
        track=manual[0],
        reason="fallback to first manual track; all sampled tracks looked translated",
        confidence="low",
    )


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip()


def looks_like_speaker_label(label: str, body: str) -> bool:
    normalized = normalize_text(label).strip(" -")
    lowered = normalized.lower()
    if not normalized or not body.strip() or lowered in NON_SPEAKER_LABELS:
        return False
    if re.search(r"\d", normalized):
        return False
    if lowered in KNOWN_SPEAKER_LABELS:
        return True
    if re.search(r"[\u4e00-\u9fff]", normalized):
        return len(normalized) <= 5
    if not re.match(r"^[A-Z][A-Za-z0-9 ._-]{0,23}$", normalized):
        return False
    return len(normalized.split()) <= 3


def parse_colon_speaker(text: str) -> tuple[str | None, str]:
    match = COLON_SPEAKER_RE.match(text)
    if not match:
        return None, text
    label = normalize_text(match.group("label"))
    body = normalize_text(match.group("body"))
    if not looks_like_speaker_label(label, body):
        return None, text
    return label, body


def allocate_speaker(
    key: str,
    label: str | None,
    speaker_ids: dict[str, str],
    speaker_labels: dict[str, str | None],
) -> str:
    if key in speaker_ids:
        return speaker_ids[key]
    speaker_id = f"speaker_{len(speaker_ids)}"
    speaker_ids[key] = speaker_id
    speaker_labels[speaker_id] = label
    return speaker_id


def detect_speaker(
    text: str,
    speaker_ids: dict[str, str],
    speaker_labels: dict[str, str | None],
    marker_state: dict[str, object],
) -> tuple[str, str | None, str | None, str]:
    marker_match = LEADING_SPEAKER_MARKER_RE.match(text)
    marker_found = marker_match is not None
    body = normalize_text(marker_match.group("body")) if marker_match else text

    label, body_without_label = parse_colon_speaker(body)
    if label:
        key = f"label:{label.lower()}"
        speaker_id = allocate_speaker(key, label, speaker_ids, speaker_labels)
        return body_without_label, speaker_id, label, "explicit_label"

    if marker_found:
        marker_ids = marker_state.setdefault("speaker_ids", [])
        if not isinstance(marker_ids, list):
            marker_ids = []
            marker_state["speaker_ids"] = marker_ids
        last_speaker = marker_state.get("last_speaker")
        if len(marker_ids) < 2:
            key = f"marker:{len(marker_ids)}"
            speaker_id = allocate_speaker(key, None, speaker_ids, speaker_labels)
            marker_ids.append(speaker_id)
        else:
            speaker_id = marker_ids[1] if last_speaker == marker_ids[0] else marker_ids[0]
        marker_state["last_speaker"] = speaker_id
        return body, speaker_id, None, "marker_alternating"

    return text, None, None, "none"


def sentence_finished(text: str) -> bool:
    return bool(SENTENCE_END_RE.search(text.strip()))


def should_start_group(current: TranscriptGroup, segment: TranscriptSegment) -> bool:
    if segment.speaker and current.speaker != segment.speaker:
        return True
    if segment.start - current.end > MAX_GROUP_GAP_SECONDS:
        return True
    if current.end - current.start >= MAX_GROUP_SPAN_SECONDS:
        return True
    return sentence_finished(current.text)


def build_grouped_transcript(raw_rows: Sequence[dict]) -> tuple[list[TranscriptGroup], dict]:
    speaker_ids: dict[str, str] = {}
    speaker_labels: dict[str, str | None] = {}
    marker_state: dict[str, object] = {}
    segments: list[TranscriptSegment] = []
    speaker_markers_found = 0
    active_speaker: str | None = None
    active_label: str | None = None

    for row in raw_rows:
        text = normalize_text(row.get("text", ""))
        if not text:
            continue
        clean_text, speaker, label, confidence = detect_speaker(
            text, speaker_ids, speaker_labels, marker_state
        )
        clean_text = normalize_text(clean_text)
        if not clean_text:
            continue
        if confidence != "none":
            speaker_markers_found += 1
            active_speaker = speaker
            active_label = label
        elif active_speaker:
            speaker = active_speaker
            label = active_label
            confidence = "inherited_marker"
        start = float(row.get("start", 0.0))
        duration = float(row.get("duration", 0.0))
        segments.append(
            TranscriptSegment(
                start=start,
                duration=duration,
                text=clean_text,
                speaker=speaker,
                speaker_label=label,
                speaker_confidence=confidence,
            )
        )

    groups: list[TranscriptGroup] = []
    current: TranscriptGroup | None = None
    for segment in segments:
        if current is None or should_start_group(current, segment):
            current = TranscriptGroup(
                start=segment.start,
                end=segment.end,
                text=segment.text,
                speaker=segment.speaker,
                speaker_label=segment.speaker_label,
                speaker_confidence=segment.speaker_confidence,
            )
            groups.append(current)
            continue

        current.end = max(current.end, segment.end)
        current.text = normalize_text(f"{current.text} {segment.text}")
        if not current.speaker and segment.speaker:
            current.speaker = segment.speaker
            current.speaker_label = segment.speaker_label
            current.speaker_confidence = segment.speaker_confidence

    if speaker_markers_found:
        grouping_method = "speaker-marker-and-sentence"
    else:
        grouping_method = "sentence-and-time"

    meta = {
        "speaker_detection": "heuristic",
        "grouping_method": grouping_method,
        "speaker_markers_found": speaker_markers_found,
        "speakers": [
            {"speaker": speaker_id, "label": speaker_labels.get(speaker_id)}
            for speaker_id in sorted(speaker_labels)
        ],
    }
    return groups, meta


def seconds_to_timestamp(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, rem = divmod(milliseconds, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def group_to_raw(group: TranscriptGroup) -> dict:
    return {
        "start": group.start,
        "end": group.end,
        "timestamp": seconds_to_timestamp(group.start),
        "speaker": group.speaker,
        "speaker_label": group.speaker_label,
        "speaker_confidence": group.speaker_confidence,
        "text": group.text,
    }


def markdown_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def speaker_display(group: TranscriptGroup) -> str | None:
    if group.speaker_label:
        return group.speaker_label
    return group.speaker


def write_outputs(out_dir: Path, fetched: object, selection: Selection) -> tuple[Path, Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    video_id = fetched.video_id
    language_code = safe_name(fetched.language_code)
    base = out_dir / f"{video_id}.{language_code}.original"

    raw_rows = fetched.to_raw_data()
    groups, group_meta = build_grouped_transcript(raw_rows)
    json_path = Path(f"{base}.json")
    txt_path = Path(f"{base}.txt")
    grouped_json_path = Path(f"{base}.grouped.json")
    grouped_md_path = Path(f"{base}.grouped.md")

    payload = {
        "video_id": video_id,
        "language": fetched.language,
        "language_code": fetched.language_code,
        "is_generated": fetched.is_generated,
        "selection_reason": selection.reason,
        "selection_confidence": selection.confidence,
        "snippets": raw_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    grouped_payload = {
        "video_id": video_id,
        "language": fetched.language,
        "language_code": fetched.language_code,
        "is_generated": fetched.is_generated,
        "selection_reason": selection.reason,
        "selection_confidence": selection.confidence,
        **group_meta,
        "groups": [group_to_raw(group) for group in groups],
    }
    grouped_json_path.write_text(
        json.dumps(grouped_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# video_id: {video_id}",
        f"# language: {fetched.language} ({fetched.language_code})",
        f"# generated: {fetched.is_generated}",
        f"# selection: {selection.confidence} - {selection.reason}",
        "",
    ]
    for row in raw_rows:
        stamp = seconds_to_timestamp(float(row["start"]))
        text = str(row["text"]).replace("\n", " ").strip()
        if text:
            lines.append(f"[{stamp}] {text}")
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    md_lines = [
        "---",
        f"video_id: {markdown_value(video_id)}",
        f"language: {markdown_value(fetched.language)}",
        f"language_code: {markdown_value(fetched.language_code)}",
        f"generated: {markdown_value(fetched.is_generated)}",
        f"selection_confidence: {markdown_value(selection.confidence)}",
        f"selection_reason: {markdown_value(selection.reason)}",
        f"speaker_detection: {markdown_value(group_meta['speaker_detection'])}",
        f"grouping_method: {markdown_value(group_meta['grouping_method'])}",
        f"speaker_markers_found: {group_meta['speaker_markers_found']}",
        "---",
        "",
        "# Transcript",
        "",
    ]
    for group in groups:
        stamp = seconds_to_timestamp(group.start)
        speaker = speaker_display(group)
        if speaker:
            md_lines.append(f"**[{stamp}] {speaker}**: {group.text}")
        else:
            md_lines.append(f"**[{stamp}]** {group.text}")
        md_lines.append("")
    grouped_md_path.write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")
    return json_path, txt_path, grouped_json_path, grouped_md_path


def process_video(api: YouTubeTranscriptApi, url_or_id: str, out_dir: Path, attempts: int) -> None:
    video_id = parse_video_id(url_or_id)
    transcript_list = with_retries(
        lambda: api.list(video_id), attempts=attempts, label=f"list {video_id}"
    )
    tracks = list(transcript_list)
    selection = choose_track(tracks, attempts=attempts)
    fetched = with_retries(
        lambda: selection.track.fetch(preserve_formatting=False),
        attempts=attempts,
        label=f"fetch {video_id} {selection.track.language_code}",
    )
    json_path, txt_path, grouped_json_path, grouped_md_path = write_outputs(out_dir, fetched, selection)

    track_type = "generated" if fetched.is_generated else "manual"
    print(
        f"{video_id}: {fetched.language_code} ({track_type}, {selection.confidence}) "
        f"-> {txt_path}"
    )
    print(f"  reason: {selection.reason}")
    print(f"  json: {json_path}")
    print(f"  grouped: {grouped_md_path}")
    print(f"  grouped json: {grouped_json_path}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch likely original-language YouTube transcripts."
    )
    parser.add_argument("videos", nargs="+", help="YouTube URLs or 11-char video ids.")
    parser.add_argument(
        "--out-dir",
        default="transcripts",
        help="Output directory for .txt and .json files. Default: transcripts",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=5,
        help="Retry attempts for transient YouTube/TLS failures. Default: 5",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    api = make_api()
    out_dir = Path(args.out_dir)
    failed = 0
    for video in args.videos:
        try:
            process_video(api, video, out_dir, attempts=args.attempts)
        except Exception as exc:
            failed += 1
            print(f"[error] {video}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
