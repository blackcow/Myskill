#!/usr/bin/env python
"""Archive paper PDFs into an agent-readable directory.

Output contract:
metadata.json, paper.md, paper.json, source.pdf, assets/
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import re
import shutil
import sys
import time
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote, unquote, urlparse

import requests
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ARXIV_ABS_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(?P<id>(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)",
    re.IGNORECASE,
)
ARXIV_ID_RE = re.compile(
    r"^(?P<base>(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?P<version>v\d+)?$",
    re.IGNORECASE,
)
PDF_POINTS_PER_INCH = 72
DEFAULT_IMAGE_SCALE = 4.0
FIGURE_CAPTION_RE = re.compile(r"^\s*(?:Figure|Fig\.)\s+[A-Za-z0-9]+[:.\s]", re.IGNORECASE)
TABLE_CAPTION_RE = re.compile(r"^\s*Table\s+[A-Za-z0-9]+[:.\s]", re.IGNORECASE)
MEDIA_CAPTION_RE = re.compile(
    r"^\s*(?:(?:Figure|Fig\.)|Table)\s+[A-Za-z0-9]+[:.\s]", re.IGNORECASE
)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?:\.?/)?assets/([^)]+)\)")
FIGURE_FALLBACK_IGNORED_TEXT_LABELS = {"caption", "page_footer", "page_header"}


@dataclass
class SourceInfo:
    input_value: str
    source_url: str | None
    pdf_url: str | None
    local_path: Path | None
    arxiv_id: str | None
    arxiv_version: str | None
    default_slug: str


@dataclass
class PaperInfo:
    title: str | None
    authors: list[str]
    published: str | None
    summary: str | None
    warnings: list[str]


@dataclass
class FigureFallback:
    page_no: int
    caption_text: str
    asset_name: str
    crop_bbox: dict[str, float]
    noise_texts: set[str]
    removed_markdown_lines: int = 0
    removed_image_refs: list[str] | None = None


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def split_arxiv_id(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    match = ARXIV_ID_RE.match(value)
    if not match:
        return value, None
    return match.group("base"), match.group("version")


def safe_slug(value: str, fallback: str = "paper") -> str:
    value = unquote(value).strip().lower()
    value = re.sub(r"\.pdf$", "", value)
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:120] or fallback


def normalize_source(value: str) -> SourceInfo:
    raw = value.strip().strip('"')
    local = Path(raw)
    if local.exists():
        return SourceInfo(
            input_value=raw,
            source_url=None,
            pdf_url=None,
            local_path=local,
            arxiv_id=None,
            arxiv_version=None,
            default_slug=safe_slug(local.stem),
        )

    parsed = urlparse(raw)
    if not parsed.scheme:
        raise ValueError(f"Input is neither an existing local file nor a URL: {value}")

    arxiv_match = ARXIV_ABS_RE.search(raw)
    if arxiv_match:
        arxiv_id_with_version = arxiv_match.group("id")
        arxiv_id, arxiv_version = split_arxiv_id(arxiv_id_with_version)
        pdf_id = arxiv_id_with_version
        return SourceInfo(
            input_value=raw,
            source_url=f"https://arxiv.org/abs/{arxiv_id_with_version}",
            pdf_url=f"https://arxiv.org/pdf/{pdf_id}",
            local_path=None,
            arxiv_id=arxiv_id,
            arxiv_version=arxiv_version,
            default_slug=safe_slug(arxiv_id_with_version.replace("/", "-")),
        )

    pdf_url = raw
    if "huggingface.co" in parsed.netloc and "/blob/" in parsed.path:
        pdf_url = raw.replace("/blob/", "/resolve/")
    filename = Path(unquote(urlparse(pdf_url).path)).name or "paper.pdf"
    return SourceInfo(
        input_value=raw,
        source_url=raw,
        pdf_url=pdf_url,
        local_path=None,
        arxiv_id=None,
        arxiv_version=None,
        default_slug=safe_slug(filename),
    )


def stable_url_without_query(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_or_copy_pdf(source: SourceInfo, pdf_path: Path, session: requests.Session) -> dict[str, Any]:
    if source.local_path:
        shutil.copy2(source.local_path, pdf_path)
        return {
            "method": "copy",
            "source_path": str(source.local_path),
            "status_code": None,
            "content_type": "application/pdf",
            "bytes": pdf_path.stat().st_size,
            "final_url": None,
        }

    assert source.pdf_url
    with session.get(source.pdf_url, stream=True, timeout=90) as response:
        response.raise_for_status()
        final_url = stable_url_without_query(response.url)
        with pdf_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        return {
            "method": "download",
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "bytes": pdf_path.stat().st_size,
            "final_url": final_url,
            "final_url_sanitized": final_url != response.url,
        }


def get_page_count(pdf_path: Path) -> int | None:
    try:
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return None


class ArxivMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        values = {key.lower(): value for key, value in attrs if value is not None}
        name = values.get("name") or values.get("property")
        content = values.get("content")
        if name and content:
            self.meta.setdefault(name.lower(), []).append(unescape(content).strip())

    def first(self, *names: str) -> str | None:
        for name in names:
            values = self.meta.get(name.lower()) or []
            for value in values:
                value = re.sub(r"\s+", " ", value).strip()
                if value:
                    return value
        return None

    def all(self, name: str) -> list[str]:
        return [
            re.sub(r"\s+", " ", value).strip()
            for value in self.meta.get(name.lower(), [])
            if value.strip()
        ]


def fetch_arxiv_abs_info(arxiv_id: str, session: requests.Session, warnings: list[str]) -> PaperInfo:
    url = f"https://arxiv.org/abs/{quote(arxiv_id)}"
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        warnings.append(f"arxiv_abs_metadata_failed:{type(exc).__name__}")
        return PaperInfo(title=None, authors=[], published=None, summary=None, warnings=warnings)

    parser = ArxivMetaParser()
    parser.feed(response.text)
    title = parser.first("citation_title", "og:title")
    if title and title.lower().startswith("arxiv:"):
        title = title.split(" ", 1)[1] if " " in title else title
    return PaperInfo(
        title=title,
        authors=parser.all("citation_author"),
        published=parser.first("citation_date", "citation_online_date"),
        summary=parser.first("description", "og:description"),
        warnings=warnings,
    )


def fetch_arxiv_info(arxiv_id: str | None, session: requests.Session) -> PaperInfo:
    if not arxiv_id:
        return PaperInfo(title=None, authors=[], published=None, summary=None, warnings=[])

    url = f"https://export.arxiv.org/api/query?id_list={quote(arxiv_id)}"
    warnings: list[str] = []
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        warnings.append(f"arxiv_api_metadata_failed:http_{status_code}")
        return fetch_arxiv_abs_info(arxiv_id, session, warnings)
    except (requests.RequestException, ET.ParseError) as exc:
        warnings.append(f"arxiv_api_metadata_failed:{type(exc).__name__}")
        return fetch_arxiv_abs_info(arxiv_id, session, warnings)

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        warnings.append("arxiv_api_metadata_empty")
        return fetch_arxiv_abs_info(arxiv_id, session, warnings)

    def text(path: str) -> str | None:
        node = entry.find(path, ns)
        if node is None or node.text is None:
            return None
        return re.sub(r"\s+", " ", node.text).strip()

    authors = []
    for author in entry.findall("atom:author", ns):
        name = author.find("atom:name", ns)
        if name is not None and name.text:
            authors.append(re.sub(r"\s+", " ", name.text).strip())
    return PaperInfo(
        title=text("atom:title"),
        authors=authors,
        published=text("atom:published"),
        summary=text("atom:summary"),
        warnings=warnings,
    )


def first_markdown_title(markdown: str) -> str | None:
    headings: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if not match:
            continue
        heading = match.group(1).strip()
        if heading.lower() in {"abstract", "contents", "introduction"}:
            break
        headings.append(heading)
        if len(headings) == 2:
            break
    if not headings:
        return None
    if len(headings) >= 2 and headings[0].endswith(":"):
        return f"{headings[0]} {headings[1]}"
    return headings[0]


def yaml_value(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {yaml_value(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def extract_docling(
    pdf_path: Path, markdown_body_path: Path, image_scale: float
) -> tuple[str, dict[str, Any], str, list[str]]:
    warnings: list[str] = []
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import ImageRefMode
    except Exception as exc:
        raise RuntimeError(
            "Docling is not installed. Install requirements.txt in this scheme directory."
        ) from exc

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False
    pipeline_options.do_table_structure = True
    pipeline_options.do_formula_enrichment = False
    pipeline_options.do_code_enrichment = False
    pipeline_options.do_picture_classification = False
    pipeline_options.do_picture_description = False
    pipeline_options.do_chart_extraction = False
    pipeline_options.generate_page_images = False
    pipeline_options.generate_picture_images = True
    pipeline_options.generate_table_images = False
    pipeline_options.images_scale = image_scale
    pipeline_options.document_timeout = 900

    warnings.extend(
        [
            "docling_ocr_disabled_for_born_digital_pdf",
            "docling_picture_description_disabled_use_multimodal_followup",
            "docling_formula_enrichment_disabled_math_may_be_plain_text",
            "docling_chart_extraction_disabled_chart_data_may_be_caption_only",
        ]
    )

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )
    # docling-parse on Windows can fail when its input path contains non-ASCII
    # characters. Use an ASCII temp path while preserving source.pdf in output.
    with tempfile.TemporaryDirectory(prefix="paper_pdf_docling_") as temp_dir:
        temp_pdf = Path(temp_dir) / "source.pdf"
        shutil.copy2(pdf_path, temp_pdf)
        result = converter.convert(str(temp_pdf))
    document = result.document
    result_status = getattr(result, "status", None)
    if result_status is not None:
        status_value = getattr(result_status, "value", str(result_status))
        if str(status_value).lower() not in {"success", "partial_success"}:
            warnings.append(f"docling_status:{status_value}")
    error_counts: dict[str, int] = {}
    for item in getattr(result, "errors", []) or []:
        message = getattr(item, "message", None) or str(item)
        compact_message = re.sub(r"\s+", " ", message).strip()
        if compact_message:
            compact_message = compact_message[:240]
            error_counts[compact_message] = error_counts.get(compact_message, 0) + 1
    for message, count in error_counts.items():
        if count > 1:
            warnings.append(f"docling_error_repeated_{count}:{message}")
        else:
            warnings.append(f"docling_error:{message}")

    try:
        document.save_as_markdown(
            markdown_body_path,
            artifacts_dir=Path("assets"),
            image_mode=ImageRefMode.REFERENCED,
            image_placeholder="<!-- image -->",
        )
        markdown = markdown_body_path.read_text(encoding="utf-8")
    except Exception as exc:
        warnings.append(f"docling_referenced_image_markdown_failed:{type(exc).__name__}")
        markdown = document.export_to_markdown()
    finally:
        if markdown_body_path.exists():
            markdown_body_path.unlink()
    if not markdown.strip():
        warnings.append("docling_export_to_markdown_empty")

    if hasattr(document, "export_to_dict"):
        paper_json = document.export_to_dict()
    elif hasattr(document, "model_dump"):
        paper_json = document.model_dump(mode="json")
    else:
        paper_json = {"warning": "Docling document object did not expose export_to_dict/model_dump."}
        warnings.append("docling_export_to_dict_unavailable")

    try:
        docling_version = importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        docling_version = "unknown"
    return markdown, paper_json, docling_version, warnings


def docling_error_fallback_page_count(warnings: Sequence[str], page_count: int | None) -> int:
    fallback_count = 0
    failed_pages: list[int] = []
    for warning in warnings:
        for match in re.finditer(r"Page\s+(\d+)", warning):
            failed_pages.append(int(match.group(1)))
        if warning.startswith("docling_error_repeated_"):
            match = re.match(r"docling_error_repeated_(\d+):", warning)
            if match:
                fallback_count = max(fallback_count, int(match.group(1)))
        elif warning.startswith("docling_error:"):
            fallback_count = max(fallback_count, 1)
    if failed_pages and page_count:
        earliest_failed_page = min(failed_pages)
        fallback_count = max(fallback_count, page_count - earliest_failed_page + 1)
    return min(max(fallback_count, 0), 10)


def append_pypdf_tail_fallback(markdown: str, pdf_path: Path, fallback_count: int) -> tuple[str, str | None]:
    if fallback_count <= 0:
        return markdown, None
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        return markdown, f"pypdf_tail_fallback_failed:{type(exc).__name__}"

    page_count = len(reader.pages)
    start_index = max(0, page_count - fallback_count)
    sections: list[str] = []
    for index in range(start_index, page_count):
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception as exc:
            sections.append(f"### Page {index + 1}\n\n[pypdf extract_text failed: {type(exc).__name__}]")
            continue
        text = text.strip()
        if text:
            sections.append(f"### Page {index + 1}\n\n{text}")

    if not sections:
        return markdown, f"pypdf_tail_fallback_empty_pages:{start_index + 1}-{page_count}"

    fallback_markdown = "\n\n".join(sections)
    markdown = (
        markdown.rstrip()
        + "\n\n## pypdf Tail Text Fallback\n\n"
        + "Docling reported pipeline errors near the end of the PDF, so raw text from the final pages is appended for completeness.\n\n"
        + fallback_markdown
        + "\n"
    )
    return markdown, f"pypdf_tail_fallback_appended_pages:{start_index + 1}-{page_count}"


def clear_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for item in path.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def count_picture_items(paper_json: dict[str, Any]) -> int:
    pictures = paper_json.get("pictures")
    if isinstance(pictures, list):
        return len(pictures)
    if isinstance(pictures, dict):
        return len(pictures)
    return 0


def count_markdown_image_refs(markdown: str) -> int:
    return len(re.findall(r"!\[[^\]]*\]\((?:assets/|\.?/assets/)[^)]+\)", markdown))


def normalize_markdown_asset_paths(markdown: str) -> str:
    return re.sub(r"(\]\()\.?/?assets\\", r"\1assets/", markdown)


def normalize_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def first_prov(item: dict[str, Any]) -> dict[str, Any] | None:
    prov = item.get("prov")
    if isinstance(prov, list) and prov and isinstance(prov[0], dict):
        return prov[0]
    return None


def bbox_from_item(item: dict[str, Any]) -> dict[str, float] | None:
    prov = first_prov(item)
    bbox = prov.get("bbox") if prov else None
    if not isinstance(bbox, dict):
        return None
    try:
        return {
            "l": float(bbox["l"]),
            "t": float(bbox["t"]),
            "r": float(bbox["r"]),
            "b": float(bbox["b"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def page_no_from_item(item: dict[str, Any]) -> int | None:
    prov = first_prov(item)
    if not prov:
        return None
    try:
        return int(prov["page_no"])
    except (KeyError, TypeError, ValueError):
        return None


def page_size_from_json(paper_json: dict[str, Any], page_no: int) -> tuple[float, float] | None:
    pages = paper_json.get("pages")
    page_info: Any = None
    if isinstance(pages, dict):
        page_info = pages.get(str(page_no)) or pages.get(page_no)
    elif isinstance(pages, list) and 0 <= page_no - 1 < len(pages):
        page_info = pages[page_no - 1]
    if not isinstance(page_info, dict):
        return None
    size = page_info.get("size")
    if not isinstance(size, dict):
        return None
    try:
        return float(size["width"]), float(size["height"])
    except (KeyError, TypeError, ValueError):
        return None


def horizontally_overlaps(
    left: float,
    right: float,
    anchor_left: float,
    anchor_right: float,
    tolerance: float,
) -> bool:
    return right >= anchor_left - tolerance and left <= anchor_right + tolerance


def vertically_within(
    top: float,
    bottom: float,
    anchor_top: float,
    anchor_bottom: float,
    tolerance: float,
) -> bool:
    return bottom >= anchor_bottom - tolerance and top <= anchor_top + tolerance


def item_ref(item: dict[str, Any], collection: str, index: int) -> str:
    return str(item.get("self_ref") or f"#/{collection}/{index}")


def iter_ref_values(item: dict[str, Any], fields: Sequence[str]) -> Sequence[str]:
    refs: list[str] = []
    for field in fields:
        values = item.get(field)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict) and isinstance(value.get("$ref"), str):
                refs.append(value["$ref"])
    return refs


def picture_references_caption(picture: dict[str, Any], caption_ref: str) -> bool:
    return caption_ref in iter_ref_values(picture, ("children", "captions"))


def select_related_picture_boxes(
    pictures: list[dict[str, Any]],
    page_no: int,
    caption_bbox: dict[str, float],
    caption_ref: str,
    page_size: tuple[float, float],
) -> list[dict[str, float]]:
    linked_boxes: list[dict[str, float]] = []
    adjacent_candidates: list[tuple[float, dict[str, float]]] = []
    page_width, page_height = page_size
    horizontal_tolerance = max(10.0, page_width * 0.02)
    max_caption_gap = max(36.0, page_height * 0.20)

    for picture in pictures:
        if page_no_from_item(picture) != page_no:
            continue
        bbox = bbox_from_item(picture)
        if not bbox or bbox["b"] < caption_bbox["t"] - 8:
            continue
        if picture_references_caption(picture, caption_ref):
            linked_boxes.append(bbox)
            continue

        gap_to_caption = max(0.0, bbox["b"] - caption_bbox["t"])
        if gap_to_caption > max_caption_gap:
            continue
        if not horizontally_overlaps(
            bbox["l"], bbox["r"], caption_bbox["l"], caption_bbox["r"], horizontal_tolerance
        ):
            continue
        adjacent_candidates.append((gap_to_caption, bbox))

    if linked_boxes:
        return linked_boxes

    if not adjacent_candidates:
        return []

    min_gap = min(gap for gap, _bbox in adjacent_candidates)
    return [bbox for gap, bbox in adjacent_candidates if gap <= min_gap + 12.0]


def select_nearest_chart_text_cluster(
    above_texts: list[tuple[int, str, dict[str, float]]],
    caption_bbox: dict[str, float],
    page_size: tuple[float, float],
) -> list[tuple[int, str, dict[str, float]]]:
    page_width, page_height = page_size
    max_start_gap = max(48.0, page_height * 0.18)
    max_row_gap = max(24.0, page_height * 0.06)
    horizontal_tolerance = max(36.0, page_width * 0.08)

    chart_like = [
        (text_index, text, bbox)
        for text_index, text, bbox in above_texts
        if len(text) <= 80
        and horizontally_overlaps(
            bbox["l"], bbox["r"], caption_bbox["l"], caption_bbox["r"], horizontal_tolerance
        )
    ]
    chart_like.sort(key=lambda item: (item[2]["b"], item[2]["t"]))
    if not chart_like:
        return []

    first_gap = chart_like[0][2]["b"] - caption_bbox["t"]
    if first_gap > max_start_gap:
        return []

    cluster: list[tuple[int, str, dict[str, float]]] = []
    cluster_top = chart_like[0][2]["t"]
    for item in chart_like:
        bbox = item[2]
        if cluster and bbox["b"] > cluster_top + max_row_gap:
            break
        cluster.append(item)
        cluster_top = max(cluster_top, bbox["t"])

    return cluster


def is_figure_label_like_text(text: str) -> bool:
    normalized = normalize_inline_text(text)
    if not normalized:
        return False
    if len(normalized) <= 80:
        return True
    if re.search(r"[.!?;:]", normalized):
        return False
    if len(normalized) <= 180:
        return len(normalized.split()) <= 32
    return len(normalized) <= 320 and len(normalized.split()) <= 50


def select_chart_texts_above_picture_anchor(
    above_texts: list[tuple[int, str, dict[str, float]]],
    caption_bbox: dict[str, float],
    page_size: tuple[float, float],
    anchor_left: float,
    anchor_right: float,
    anchor_top: float,
    anchored_indexes: set[int],
    barrier_top: float | None = None,
) -> list[tuple[int, str, dict[str, float]]]:
    page_width, page_height = page_size
    horizontal_tolerance = max(36.0, page_width * 0.08)
    max_above_anchor_gap = max(120.0, page_height * 0.25)
    min_bottom = anchor_top - max(18.0, page_height * 0.04)
    max_top = min(page_height, anchor_top + max_above_anchor_gap)
    if barrier_top is not None:
        max_top = min(max_top, barrier_top)

    candidates: list[tuple[int, str, dict[str, float]]] = []
    for text_index, text, bbox in above_texts:
        if text_index in anchored_indexes:
            continue
        if not is_figure_label_like_text(text):
            continue
        if bbox["b"] < min_bottom or bbox["t"] > max_top:
            continue
        if not (
            horizontally_overlaps(
                bbox["l"],
                bbox["r"],
                anchor_left,
                anchor_right,
                horizontal_tolerance,
            )
            or horizontally_overlaps(
                bbox["l"],
                bbox["r"],
                caption_bbox["l"],
                caption_bbox["r"],
                horizontal_tolerance,
            )
        ):
            continue
        candidates.append((text_index, text, bbox))

    if len(candidates) < 6:
        return []
    return candidates


def detect_figure_fallbacks(
    paper_json: dict[str, Any], image_scale: float
) -> list[FigureFallback]:
    texts = paper_json.get("texts") if isinstance(paper_json.get("texts"), list) else []
    pictures = paper_json.get("pictures") if isinstance(paper_json.get("pictures"), list) else []
    fallbacks: list[FigureFallback] = []
    used_pages: set[tuple[int, str]] = set()

    for caption_index, caption_item in enumerate(texts):
        caption_text = normalize_inline_text(
            str(caption_item.get("text") or caption_item.get("orig") or "")
        )
        if caption_item.get("label") != "caption" or not FIGURE_CAPTION_RE.match(caption_text):
            continue
        page_no = page_no_from_item(caption_item)
        caption_bbox = bbox_from_item(caption_item)
        page_size = page_size_from_json(paper_json, page_no) if page_no else None
        if page_no is None or caption_bbox is None or page_size is None:
            continue
        caption_ref = item_ref(caption_item, "texts", caption_index)

        above_texts: list[tuple[int, str, dict[str, float]]] = []
        for text_index, text_item in enumerate(texts):
            if (
                text_index == caption_index
                or text_item.get("label") in FIGURE_FALLBACK_IGNORED_TEXT_LABELS
            ):
                continue
            if page_no_from_item(text_item) != page_no:
                continue
            bbox = bbox_from_item(text_item)
            if not bbox or bbox["b"] < caption_bbox["t"] - 2:
                continue
            text = normalize_inline_text(str(text_item.get("text") or text_item.get("orig") or ""))
            if text:
                above_texts.append((text_index, text, bbox))

        if not above_texts:
            continue

        picture_boxes = select_related_picture_boxes(
            pictures=pictures,
            page_no=page_no,
            caption_bbox=caption_bbox,
            caption_ref=caption_ref,
            page_size=page_size,
        )

        # When Docling has at least a partial picture bbox, use it as a spatial
        # anchor. This prevents a figure caption from swallowing unrelated
        # same-page tables or running headers that also sit above the caption.
        candidate_texts = above_texts
        if picture_boxes:
            anchor_left = min(box["l"] for box in picture_boxes)
            anchor_right = max(box["r"] for box in picture_boxes)
            anchor_top = max(box["t"] for box in picture_boxes)
            anchor_bottom = min(box["b"] for box in picture_boxes)
            text_anchor_bottom = min(anchor_bottom, caption_bbox["t"] + 2)
            horizontal_tolerance = max(24.0, page_size[0] * 0.04)
            vertical_tolerance = max(4.0, (anchor_top - anchor_bottom) * 0.02)
            anchored_texts = [
                (text_index, text, bbox)
                for text_index, text, bbox in above_texts
                if horizontally_overlaps(
                    bbox["l"], bbox["r"], anchor_left, anchor_right, horizontal_tolerance
                )
                and vertically_within(
                    bbox["t"], bbox["b"], anchor_top, text_anchor_bottom, vertical_tolerance
                )
            ]
            anchored_indexes = {text_index for text_index, _text, _bbox in anchored_texts}
            barrier_top: float | None = None
            for other_index, other_item in enumerate(texts):
                if other_index == caption_index or other_item.get("label") != "caption":
                    continue
                if page_no_from_item(other_item) != page_no:
                    continue
                other_bbox = bbox_from_item(other_item)
                if not other_bbox or other_bbox["b"] < anchor_top - 2:
                    continue
                if not (
                    horizontally_overlaps(
                        other_bbox["l"],
                        other_bbox["r"],
                        anchor_left,
                        anchor_right,
                        horizontal_tolerance,
                    )
                    or horizontally_overlaps(
                        other_bbox["l"],
                        other_bbox["r"],
                        caption_bbox["l"],
                        caption_bbox["r"],
                        horizontal_tolerance,
                    )
                ):
                    continue
                boundary = max(anchor_top, other_bbox["b"] - 2)
                barrier_top = boundary if barrier_top is None else min(barrier_top, boundary)
            extra_chart_texts = select_chart_texts_above_picture_anchor(
                above_texts=above_texts,
                caption_bbox=caption_bbox,
                page_size=page_size,
                anchor_left=anchor_left,
                anchor_right=anchor_right,
                anchor_top=anchor_top,
                anchored_indexes=anchored_indexes,
                barrier_top=barrier_top,
            )
            candidate_texts = anchored_texts + extra_chart_texts
        else:
            candidate_texts = select_nearest_chart_text_cluster(
                above_texts=above_texts,
                caption_bbox=caption_bbox,
                page_size=page_size,
            )

        if not candidate_texts:
            continue
        short_text_count = sum(1 for _idx, text, _bbox in candidate_texts if len(text) <= 40)
        short_ratio = short_text_count / len(candidate_texts)
        if len(candidate_texts) < 18 or short_text_count < 12 or short_ratio < 0.45:
            continue

        boxes = [bbox for _idx, _text, bbox in candidate_texts]
        boxes.extend(picture_boxes)

        page_width, page_height = page_size
        left = max(0.0, min(box["l"] for box in boxes) - 4)
        right = min(page_width, max(box["r"] for box in boxes) + 4)
        top = min(page_height, max(box["t"] for box in boxes) + 4)
        bottom = max(caption_bbox["t"] + 2, min(box["b"] for box in boxes) - 4)
        if right <= left or top <= bottom:
            continue

        figure_match = re.match(r"^\s*(?:Figure|Fig\.)\s+([A-Za-z0-9]+)", caption_text, re.IGNORECASE)
        figure_id = figure_match.group(1).lower() if figure_match else f"caption{caption_index}"
        dedupe_key = (page_no, figure_id)
        if dedupe_key in used_pages:
            continue
        used_pages.add(dedupe_key)
        asset_name = f"figure_fallback_page_{page_no:04d}_{figure_id}_scale_{image_scale:g}.png"
        noise_texts = {text for _idx, text, _bbox in candidate_texts}
        fallbacks.append(
            FigureFallback(
                page_no=page_no,
                caption_text=caption_text,
                asset_name=asset_name,
                crop_bbox={"l": left, "t": top, "r": right, "b": bottom},
                noise_texts=noise_texts,
                removed_image_refs=[],
            )
        )
    return fallbacks


def render_pdf_crop(
    pdf_path: Path,
    page_no: int,
    crop_bbox: dict[str, float],
    output_path: Path,
    image_scale: float,
) -> None:
    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        raise RuntimeError("pypdfium2 is required for figure fallback rendering") from exc

    pdf = pdfium.PdfDocument(str(pdf_path))
    page = None
    try:
        page = pdf[page_no - 1]
        _width, height = page.get_size()
        bitmap = page.render(scale=image_scale)
        image = bitmap.to_pil()
        left = max(0, math.floor(crop_bbox["l"] * image_scale))
        upper = max(0, math.floor((height - crop_bbox["t"]) * image_scale))
        right = min(image.width, math.ceil(crop_bbox["r"] * image_scale))
        lower = min(image.height, math.ceil((height - crop_bbox["b"]) * image_scale))
        if right <= left or lower <= upper:
            raise ValueError(f"Invalid figure fallback crop on page {page_no}: {crop_bbox}")
        image.crop((left, upper, right, lower)).save(output_path)
    finally:
        if page is not None:
            close_page = getattr(page, "close", None)
            if callable(close_page):
                close_page()
        close_pdf = getattr(pdf, "close", None)
        if callable(close_pdf):
            close_pdf()


def find_caption_line(lines: list[str], caption_text: str) -> int | None:
    caption_norm = normalize_inline_text(caption_text)
    caption_escaped_norm = normalize_inline_text(escape(caption_text, quote=False))
    for index, line in enumerate(lines):
        line_norm = normalize_inline_text(line)
        if line_norm in {caption_norm, caption_escaped_norm}:
            return index
    caption_prefix = caption_norm[:100]
    for index, line in enumerate(lines):
        if normalize_inline_text(line).startswith(caption_prefix):
            return index
    return None


def cleanup_markdown_for_figure_fallbacks(
    markdown: str, fallbacks: list[FigureFallback]
) -> str:
    if not fallbacks:
        return markdown

    lines = markdown.splitlines()
    removed: set[int] = set()
    insert_before: dict[int, list[str]] = {}

    for fallback in fallbacks:
        caption_index = find_caption_line(lines, fallback.caption_text)
        if caption_index is None:
            continue
        noise_texts = {normalize_inline_text(text) for text in fallback.noise_texts if text}
        fallback.noise_texts = noise_texts
        before_removed = 0
        removed_image_refs: list[str] = []

        cursor = caption_index - 1
        while cursor >= 0:
            line_norm = normalize_inline_text(lines[cursor])
            if not line_norm:
                if before_removed:
                    removed.add(cursor)
                cursor -= 1
                continue
            match = MARKDOWN_IMAGE_RE.search(lines[cursor])
            if match:
                removed.add(cursor)
                removed_image_refs.append(match.group(1))
                before_removed += 1
                cursor -= 1
                continue
            if line_norm in noise_texts:
                removed.add(cursor)
                before_removed += 1
                cursor -= 1
                continue
            break

        next_caption = len(lines)
        for index in range(caption_index + 1, len(lines)):
            if FIGURE_CAPTION_RE.match(normalize_inline_text(lines[index])):
                next_caption = index
                break

        for index in range(caption_index + 1, next_caption):
            line_norm = normalize_inline_text(lines[index])
            if line_norm and line_norm in noise_texts:
                removed.add(index)

        cursor = caption_index + 1
        while cursor < len(lines) and not normalize_inline_text(lines[cursor]):
            cursor += 1
        if cursor < len(lines):
            match = MARKDOWN_IMAGE_RE.search(lines[cursor])
            if match:
                removed.add(cursor)
                removed_image_refs.append(match.group(1))

        fallback.removed_image_refs = removed_image_refs
        insert_before[caption_index] = [
            "",
            f"![Figure fallback page {fallback.page_no}](assets/{fallback.asset_name})",
            "",
        ]

    output: list[str] = []
    removed_count_by_caption = {id(fallback): 0 for fallback in fallbacks}
    for index, line in enumerate(lines):
        if index in insert_before:
            output.extend(insert_before[index])
        if index in removed:
            for fallback in fallbacks:
                if normalize_inline_text(line) in fallback.noise_texts:
                    removed_count_by_caption[id(fallback)] += 1
            continue
        output.append(line)

    compacted: list[str] = []
    blank_run = 0
    for line in output:
        if line.strip():
            blank_run = 0
            compacted.append(line)
        else:
            blank_run += 1
            if blank_run <= 2:
                compacted.append(line)

    for fallback in fallbacks:
        fallback.removed_markdown_lines = removed_count_by_caption.get(id(fallback), 0)
    return "\n".join(compacted).strip() + "\n"


def is_markdown_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def find_previous_nonblank(lines: list[str], start: int) -> int | None:
    cursor = start
    while cursor >= 0:
        if normalize_inline_text(lines[cursor]):
            return cursor
        cursor -= 1
    return None


def normalize_markdown_media_caption_order(markdown: str) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        line_norm = normalize_inline_text(line)
        if MEDIA_CAPTION_RE.match(line_norm):
            cursor = index + 1
            while cursor < len(lines) and not normalize_inline_text(lines[cursor]):
                cursor += 1

            previous_index = find_previous_nonblank(output, len(output) - 1)
            previous_is_image = (
                previous_index is not None and MARKDOWN_IMAGE_RE.search(output[previous_index])
            )

            if cursor < len(lines) and not previous_is_image:
                image_match = MARKDOWN_IMAGE_RE.search(lines[cursor])
                if image_match:
                    if output and normalize_inline_text(output[-1]):
                        output.append("")
                    output.append(lines[cursor])
                    output.append("")
                    output.append(line)
                    index = cursor + 1
                    continue

                if TABLE_CAPTION_RE.match(line_norm) and is_markdown_table_line(lines[cursor]):
                    table_end = cursor
                    while table_end < len(lines) and is_markdown_table_line(lines[table_end]):
                        table_end += 1
                    if output and normalize_inline_text(output[-1]):
                        output.append("")
                    output.extend(lines[cursor:table_end])
                    output.append("")
                    output.append(line)
                    index = table_end
                    continue

        output.append(line)
        index += 1

    compacted: list[str] = []
    blank_run = 0
    for line in output:
        if line.strip():
            blank_run = 0
            compacted.append(line)
        else:
            blank_run += 1
            if blank_run <= 2:
                compacted.append(line)

    return "\n".join(compacted).strip() + "\n"


def prune_unreferenced_assets(assets_dir: Path, markdown: str) -> int:
    referenced = {
        unquote(match.group(1)).replace("\\", "/")
        for match in MARKDOWN_IMAGE_RE.finditer(markdown)
    }
    removed = 0
    for item in assets_dir.iterdir():
        if not item.is_file():
            continue
        if item.name not in referenced:
            item.unlink()
            removed += 1
    return removed


def apply_figure_fallbacks(
    markdown: str,
    paper_json: dict[str, Any],
    pdf_path: Path,
    assets_dir: Path,
    image_scale: float,
    warnings: list[str],
) -> tuple[str, list[dict[str, Any]], int]:
    fallbacks = detect_figure_fallbacks(paper_json, image_scale)
    rendered: list[FigureFallback] = []
    for fallback in fallbacks:
        try:
            render_pdf_crop(
                pdf_path=pdf_path,
                page_no=fallback.page_no,
                crop_bbox=fallback.crop_bbox,
                output_path=assets_dir / fallback.asset_name,
                image_scale=image_scale,
            )
            rendered.append(fallback)
        except Exception as exc:
            warnings.append(
                f"figure_fallback_render_failed_page_{fallback.page_no}:{type(exc).__name__}"
            )

    if not rendered:
        return markdown, [], 0

    markdown = cleanup_markdown_for_figure_fallbacks(markdown, rendered)
    pruned_assets = prune_unreferenced_assets(assets_dir, markdown)
    metadata = []
    for fallback in rendered:
        warnings.append(
            f"figure_text_soup_fallback_page_{fallback.page_no}:{fallback.asset_name}"
        )
        metadata.append(
            {
                "page_no": fallback.page_no,
                "caption": fallback.caption_text,
                "asset": f"assets/{fallback.asset_name}",
                "crop_bbox": fallback.crop_bbox,
                "noise_text_count": len(fallback.noise_texts),
                "removed_markdown_lines": fallback.removed_markdown_lines,
                "removed_image_refs": fallback.removed_image_refs or [],
            }
        )
    return markdown, metadata, pruned_assets


def process_one(
    source_value: str,
    out_root: Path,
    slug: str | None,
    session: requests.Session,
    image_scale: float,
) -> dict[str, Any]:
    source = normalize_source(source_value)
    arxiv_info = fetch_arxiv_info(source.arxiv_id, session)
    title_for_slug = arxiv_info.title or source.default_slug
    out_dir = out_root / (safe_slug(slug) if slug else safe_slug(title_for_slug, source.default_slug))
    assets_dir = out_dir / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_directory(assets_dir)

    source_pdf = out_dir / "source.pdf"
    download_info = download_or_copy_pdf(source, source_pdf, session)
    pdf_hash = sha256_file(source_pdf)
    page_count = get_page_count(source_pdf)
    warnings: list[str] = []
    warnings.extend(arxiv_info.warnings)
    content_type = (download_info.get("content_type") or "").lower()
    if "pdf" not in content_type:
        warnings.append(f"download_content_type_not_pdf:{download_info.get('content_type')}")
    if page_count is None:
        warnings.append("pypdf_page_count_unavailable")

    markdown_body_path = out_dir / "_docling_body.md"
    markdown, paper_json, docling_version, docling_warnings = extract_docling(
        source_pdf, markdown_body_path, image_scale
    )
    warnings.extend(docling_warnings)
    markdown, fallback_warning = append_pypdf_tail_fallback(
        markdown, source_pdf, docling_error_fallback_page_count(docling_warnings, page_count)
    )
    if fallback_warning:
        warnings.append(fallback_warning)
    markdown = normalize_markdown_asset_paths(markdown)
    markdown, figure_fallbacks, pruned_assets = apply_figure_fallbacks(
        markdown=markdown,
        paper_json=paper_json,
        pdf_path=source_pdf,
        assets_dir=assets_dir,
        image_scale=image_scale,
        warnings=warnings,
    )
    markdown = normalize_markdown_media_caption_order(markdown)
    title = arxiv_info.title or first_markdown_title(markdown) or source.default_slug
    authors = arxiv_info.authors
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    asset_count = len([item for item in assets_dir.iterdir() if item.is_file()])
    picture_count = count_picture_items(paper_json)
    markdown_image_refs = count_markdown_image_refs(markdown)
    image_effective_dpi = int(PDF_POINTS_PER_INCH * image_scale)
    if picture_count > 0 and asset_count == 0:
        warnings.append("docling_picture_items_found_but_no_assets_exported")
    if asset_count > 0 and markdown_image_refs == 0:
        warnings.append("docling_picture_assets_exported_but_markdown_has_no_refs")

    frontmatter_fields = {
        "source_type": "paper_pdf",
        "source_url": source.source_url or source.input_value,
        "pdf_url": source.pdf_url,
        "title": title,
        "authors": authors,
        "arxiv_id": source.arxiv_id,
        "version": source.arxiv_version,
        "published": arxiv_info.published,
        "captured_at": captured_at,
        "parser": "docling",
        "page_count": page_count,
        "content_chars": len(markdown),
        "asset_count": asset_count,
        "picture_count": picture_count,
        "markdown_image_refs": markdown_image_refs,
        "image_scale": image_scale,
        "image_effective_dpi": image_effective_dpi,
        "figure_fallback_count": len(figure_fallbacks),
        "canonical_source": "paper.md",
        "status": "raw",
    }
    paper_md = out_dir / "paper.md"
    paper_md.write_text(frontmatter(frontmatter_fields) + markdown.strip() + "\n", encoding="utf-8")

    paper_json_path = out_dir / "paper.json"
    paper_json_path.write_text(
        json.dumps(paper_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    metadata = {
        "schema_version": 1,
        "source_type": "paper_pdf",
        "input": source.input_value,
        "source_url": source.source_url or source.input_value,
        "pdf_url": source.pdf_url,
        "download": download_info,
        "title": title,
        "authors": authors,
        "arxiv_id": source.arxiv_id,
        "version": source.arxiv_version,
        "published": arxiv_info.published,
        "arxiv_summary": arxiv_info.summary,
        "captured_at": captured_at,
        "parser": "docling",
        "parser_version": docling_version,
        "page_count": page_count,
        "content_chars": len(markdown),
        "content_sha256": content_hash,
        "source_pdf_sha256": pdf_hash,
        "asset_count": asset_count,
        "picture_count": picture_count,
        "markdown_image_refs": markdown_image_refs,
        "image_asset_mode": "referenced_assets",
        "image_scale": image_scale,
        "image_effective_dpi": image_effective_dpi,
        "image_generation": {
            "source": "docling_picture_items_and_pdf_crop_fallbacks",
            "generate_page_images": False,
            "generate_picture_images": True,
            "generate_table_images": False,
            "generate_figure_fallback_images": True,
        },
        "figure_fallback_count": len(figure_fallbacks),
        "figure_fallbacks": figure_fallbacks,
        "pruned_unreferenced_assets": pruned_assets,
        "ocr_mode": "disabled_by_default",
        "warnings": warnings,
        "canonical_source": "paper.md",
        "files": ["metadata.json", "paper.md", "paper.json", "source.pdf", "assets/"],
        "notes": [
            "paper.md is the main reading source for downstream agents.",
            "paper.json preserves Docling structured output for evidence lookup.",
            "source.pdf is retained as the original evidence file.",
        ],
    }
    metadata_path = out_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    old_readme = out_dir / "README.md"
    if old_readme.exists():
        old_readme.unlink()

    return {
        "OutDir": str(out_dir),
        "Title": title,
        "ArxivId": source.arxiv_id,
        "PdfUrl": source.pdf_url,
        "PageCount": page_count,
        "ContentChars": len(markdown),
        "AssetCount": asset_count,
        "PictureCount": picture_count,
        "MarkdownImageRefs": markdown_image_refs,
        "ImageScale": image_scale,
        "ImageEffectiveDpi": image_effective_dpi,
        "FigureFallbackCount": len(figure_fallbacks),
        "PrunedUnreferencedAssets": pruned_assets,
        "Warnings": warnings,
        "Files": ["metadata.json", "paper.md", "paper.json", "source.pdf", "assets/"],
    }


def parse_image_scale(value: str) -> float:
    try:
        scale = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--image-scale must be a number.") from exc
    if scale <= 0:
        raise argparse.ArgumentTypeError("--image-scale must be greater than 0.")
    return scale


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive paper PDFs for downstream agents.")
    parser.add_argument("sources", nargs="+", help="arXiv URLs, PDF URLs, Hugging Face PDF URLs, or local PDFs.")
    parser.add_argument(
        "--out-root",
        default="outputs",
        help="Output root directory. Default: outputs",
    )
    parser.add_argument(
        "--slug",
        help="Optional output directory name. Only valid with one source.",
    )
    parser.add_argument(
        "--image-scale",
        type=parse_image_scale,
        default=DEFAULT_IMAGE_SCALE,
        help=(
            f"Docling image scale for exported PDF picture assets. Default: {DEFAULT_IMAGE_SCALE} "
            f"(effective {int(PDF_POINTS_PER_INCH * DEFAULT_IMAGE_SCALE)} DPI). "
            "Use 2 for smaller/faster assets or 4+ when chart labels need to stay readable."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.slug and len(args.sources) != 1:
        print("--slug can only be used with a single source.", file=sys.stderr)
        return 2

    out_root = Path(args.out_root)
    session = make_session()
    results: list[dict[str, Any]] = []
    failed = 0
    for source in args.sources:
        try:
            results.append(
                process_one(source, out_root, args.slug, session, args.image_scale)
            )
            time.sleep(0.5)
        except Exception as exc:
            failed += 1
            print(f"[error] {source}: {exc}", file=sys.stderr)

    if results:
        payload: Any = results[0] if len(results) == 1 else results
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
