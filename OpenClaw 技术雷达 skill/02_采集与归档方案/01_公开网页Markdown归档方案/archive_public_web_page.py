#!/usr/bin/env python
"""Archive public web pages as stable Markdown sources for agents."""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_markdown


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class ExtractedPage:
    source_url: str
    final_url: str
    canonical_url: str
    title: str
    markdown: str
    method: str
    author: str = ""
    published: str = ""
    site: str = ""
    description: str = ""
    image: str = ""
    language: str = ""
    word_count: int = 0
    warnings: list[str] = field(default_factory=list)


MOJIBAKE_MARKERS = ("\u00e2", "\u00c2", "\u00c3")


def repair_mojibake(value: str) -> str:
    if not value or not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value
    try:
        repaired = value.encode("latin1").decode("utf-8")
    except Exception:
        return value
    old_score = sum(value.count(marker) for marker in MOJIBAKE_MARKERS)
    new_score = sum(repaired.count(marker) for marker in MOJIBAKE_MARKERS)
    return repaired if new_score < old_score else value


def clean_text(value: Any) -> str:
    text = html_lib.unescape(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return repair_mojibake(text)


def response_encoding(response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "")
    header_encoding = requests.utils.get_encoding_from_headers(response.headers)
    has_charset = "charset=" in content_type.lower()
    if header_encoding and (has_charset or header_encoding.lower() != "iso-8859-1"):
        return header_encoding
    return response.apparent_encoding or header_encoding or "utf-8"


def fetch_html(url: str, timeout: int, user_agent: str) -> tuple[str, str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise RuntimeError(f"URL did not return HTML content: {content_type}")
    response.encoding = response_encoding(response)
    return response.url, repair_mojibake(response.text), content_type


def unique_values(values: Sequence[str | None]) -> list[str | None]:
    result: list[str | None] = []
    seen: set[str | None] = set()
    for value in values:
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def fetch_rendered_html(
    url: str,
    timeout: int,
    user_agent: str,
    browser_channel: str,
) -> tuple[str, str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "--render-js requires the optional playwright package. "
            "Install requirements.txt again before using browser rendering."
        ) from exc

    timeout_ms = timeout * 1000
    target_url = url
    try:
        target_url = fetch_html(url, timeout=min(timeout, 15), user_agent=user_agent)[0]
    except Exception:
        pass
    channels = unique_values([browser_channel or None, "msedge", "chrome", None])
    last_error: Exception | None = None
    with sync_playwright() as playwright:
        for channel in channels:
            try:
                launch_args: dict[str, Any] = {"headless": True}
                if channel:
                    launch_args["channel"] = channel
                browser = playwright.chromium.launch(**launch_args)
                break
            except Exception as exc:
                last_error = exc
        else:
            raise RuntimeError(f"Could not launch Chromium browser: {last_error}")

        try:
            context = browser.new_context(user_agent=user_agent, locale="zh-CN")
            page = context.new_page()
            navigation_error: Exception | None = None
            for attempt in range(2):
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    navigation_error = None
                    break
                except Exception as exc:
                    navigation_error = exc
                    try:
                        html = page.content()
                        if page.url != "about:blank" and len(html) > 1000:
                            return page.url, repair_mojibake(html), "text/html; rendered=1"
                    except Exception:
                        pass
                    if attempt == 0:
                        page.wait_for_timeout(1_000)
            if navigation_error:
                raise navigation_error
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15_000))
            except Exception:
                pass
            page.wait_for_timeout(1_000)
            return page.url, repair_mojibake(page.content()), "text/html; rendered=1"
        finally:
            browser.close()


def first_meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    return ""


def joined_meta(soup: BeautifulSoup, *names: str) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for name in names:
        tags = soup.find_all("meta", attrs={"property": name}) + soup.find_all(
            "meta", attrs={"name": name}
        )
        for tag in tags:
            value = clean_text(tag.get("content"))
            key = comparable_text(value)
            if value and key not in seen:
                values.append(value)
                seen.add(key)
    return "; ".join(values)


def first_time_value(soup: BeautifulSoup) -> str:
    tag = soup.find("time", attrs={"datetime": True}) or soup.find("time")
    if not tag:
        return ""
    return clean_text(tag.get("datetime") or tag.get_text(" ", strip=True))


def parse_json_ld_blocks(soup: BeautifulSoup) -> list[Any]:
    payloads: list[Any] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text()
        if not text:
            continue
        try:
            payloads.append(json.loads(text.strip()))
        except Exception:
            continue
    return payloads


def iter_json_objects(payload: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    stack = payload if isinstance(payload, list) else [payload]
    while stack:
        item = stack.pop(0)
        if isinstance(item, list):
            stack.extend(item)
            continue
        if not isinstance(item, dict):
            continue
        objects.append(item)
        if isinstance(item.get("@graph"), list):
            stack.extend(item["@graph"])
        stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
    return objects


def coerce_schema_value(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, (int, float, bool)):
        return clean_text(value)
    if isinstance(value, list):
        for item in value:
            coerced = coerce_schema_value(item)
            if coerced:
                return coerced
        return ""
    if isinstance(value, dict):
        for key in ("name", "headline", "title", "url", "@id"):
            coerced = coerce_schema_value(value.get(key))
            if coerced:
                return coerced
    return ""


def first_json_ld_value(soup: BeautifulSoup, *keys: str) -> str:
    for payload in parse_json_ld_blocks(soup):
        for item in iter_json_objects(payload):
            for key in keys:
                if key in item:
                    value = coerce_schema_value(item.get(key))
                    if value:
                        return value
    return ""


def canonical_url(soup: BeautifulSoup, final_url: str) -> str:
    tag = soup.find("link", attrs={"rel": lambda rel: rel and "canonical" in rel})
    href = clean_text(tag.get("href")) if tag else ""
    return urljoin(final_url, href) if href else final_url


def document_language(soup: BeautifulSoup) -> str:
    html_tag = soup.find("html")
    language = clean_text(html_tag.get("lang")) if html_tag else ""
    return language or first_meta(soup, "language", "content-language")


def absolute_url(value: str, base_url: str) -> str:
    value = clean_text(value)
    return urljoin(base_url, value) if value else ""


def extract_dom_metadata(soup: BeautifulSoup, final_url: str) -> dict[str, str]:
    title = (
        first_meta(soup, "og:title", "twitter:title")
        or first_json_ld_value(soup, "headline", "name", "title")
        or (clean_text(soup.title.string) if soup.title and soup.title.string else "")
        or (clean_text(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else "")
        or urlparse(final_url).netloc
    )
    author = joined_meta(soup, "author", "article:author") or first_json_ld_value(
        soup, "author", "creator"
    )
    published = (
        first_meta(
            soup,
            "article:published_time",
            "date",
            "pubdate",
            "datePublished",
            "publish_date",
        )
        or first_json_ld_value(soup, "datePublished", "dateCreated", "uploadDate")
        or first_time_value(soup)
    )
    site = (
        first_meta(soup, "og:site_name", "application-name")
        or first_json_ld_value(soup, "publisher")
        or urlparse(final_url).netloc
    )
    description = first_meta(soup, "description", "og:description", "twitter:description")
    image = absolute_url(
        first_meta(soup, "og:image", "twitter:image")
        or first_json_ld_value(soup, "image", "thumbnailUrl"),
        final_url,
    )
    return {
        "canonical_url": canonical_url(soup, final_url),
        "title": title,
        "author": author,
        "published": published,
        "site": site,
        "description": description,
        "image": image,
        "language": document_language(soup),
    }


def count_words(markdown: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", markdown))


def clean_markdown(markdown: str) -> str:
    text = repair_mojibake(markdown.replace("\r\n", "\n").replace("\r", "\n"))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def comparable_text(value: str) -> str:
    value = re.sub(r"^#+\s*", "", value).lower()
    value = value.split("|", 1)[0]
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value)
    return value


def label_from_image_url(image_url: str) -> str:
    stem = Path(urlparse(image_url).path).stem
    stem = re.sub(r"^[0-9a-f]{12,}_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[-_]+", " ", stem)
    stem = re.sub(r"\b[0-9a-f]{12,}\b", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem or comparable_text(stem) in {"placeholder", "image"}:
        return "article image"
    return stem


TRAILING_BOILERPLATE_KEYS = {
    "allrightsreserved",
    "cookiepreferences",
    "cookiesettings",
    "getstarted",
    "getstartedtoday",
    "newsletter",
    "relatedarticles",
    "sharethisarticle",
    "signup",
    "subscribenow",
}

SECTION_STOP_KEYS = {
    "ebook",
    "furtherreading",
    "getthedevelopernewsletter",
    "latestposts",
    "morefromtheblog",
    "recommended",
    "related",
    "relatedposts",
}


def is_short_boilerplate(block: str, keys: set[str]) -> bool:
    key = comparable_text(block)
    return len(block) < 260 and key in keys


def markdown_line_key(line: str) -> str:
    line = re.sub(r"\]\([^)]+\)", "]", line)
    line = re.sub(r"^[>*\-\s]+", "", line)
    return comparable_text(line)


def is_boilerplate_line(line: str) -> bool:
    return markdown_line_key(line) in {"share", "copylink", "sharecopylink"}


def normalize_article_body(markdown: str, title: str) -> str:
    text = clean_markdown(markdown)
    text = re.sub(r"^- Date([A-Z][^\n]+)$", r"- Date: \1", text, flags=re.MULTILINE)
    text = re.sub(
        r"^- Reading time([^\n]+)$",
        r"- Reading time: \1",
        text,
        flags=re.MULTILINE,
    )
    blocks = [block.strip() for block in re.split(r"\n{2,}", text) if block.strip()]

    title_key = comparable_text(title)
    while blocks and blocks[0].startswith("#"):
        first_key = comparable_text(blocks[0])
        if first_key and title_key and (first_key in title_key or title_key in first_key):
            blocks.pop(0)
        else:
            break

    deduped: list[str] = []
    previous_key = ""
    for block in blocks:
        block = "\n".join(
            line for line in block.split("\n") if not is_boilerplate_line(line)
        ).strip()
        if not block:
            continue
        key = comparable_text(block)
        if key and key == previous_key:
            continue
        deduped.append(block)
        previous_key = key

    for index, block in enumerate(deduped):
        if index > 2 and is_short_boilerplate(block, SECTION_STOP_KEYS):
            deduped = deduped[:index]
            break
    while deduped and is_short_boilerplate(deduped[-1], TRAILING_BOILERPLATE_KEYS):
        deduped.pop()
    return clean_markdown("\n\n".join(deduped))


def extract_with_trafilatura(
    html: str,
    source_url: str,
    final_url: str,
    soup: BeautifulSoup,
) -> ExtractedPage | None:
    metadata = trafilatura.extract_metadata(html, default_url=final_url)
    markdown = trafilatura.extract(
        html,
        url=final_url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        include_images=True,
        include_links=True,
        favor_recall=True,
        deduplicate=True,
    )
    if not markdown or len(clean_markdown(markdown)) < 80:
        return None

    markdown = clean_markdown(markdown)
    dom_meta = extract_dom_metadata(soup, final_url)

    title = clean_text(getattr(metadata, "title", "") if metadata else "") or dom_meta["title"]
    author = clean_text(getattr(metadata, "author", "") if metadata else "") or dom_meta["author"]
    published = clean_text(getattr(metadata, "date", "") if metadata else "") or dom_meta["published"]
    site = clean_text(getattr(metadata, "sitename", "") if metadata else "") or dom_meta["site"]
    description = (
        clean_text(getattr(metadata, "description", "") if metadata else "")
        or dom_meta["description"]
    )

    return ExtractedPage(
        source_url=source_url,
        final_url=final_url,
        canonical_url=dom_meta["canonical_url"],
        title=title,
        author=author,
        published=published,
        site=site,
        description=description,
        image=dom_meta["image"],
        language=dom_meta["language"],
        word_count=count_words(markdown),
        markdown=markdown,
        method="trafilatura",
    )


NOISE_SELECTORS = [
    "script",
    "style",
    "noscript",
    "svg",
    "form",
    "nav",
    "footer",
    "aside",
    "iframe",
    "[aria-hidden='true']",
    "[hidden]",
    ".ad",
    ".ads",
    ".advertisement",
    ".banner",
    ".breadcrumbs",
    ".cookie",
    ".comments",
    ".footer",
    ".header",
    ".menu",
    ".modal",
    ".newsletter",
    ".pagination",
    ".related",
    ".share",
    ".sidebar",
    ".social",
    ".subscribe",
    "#comments",
    "#footer",
    "#header",
    "#nav",
    "#sidebar",
]

CONTENT_SELECTORS = [
    "article",
    "main",
    "[role='main']",
    ".markdown-preview-view",
    ".markdown-rendered",
    ".publish-renderer",
    ".article",
    ".article-content",
    ".content",
    ".content-body",
    ".docs-content",
    ".document",
    ".entry-content",
    ".markdown-body",
    ".md-content",
    ".post",
    ".post-content",
    ".rst-content",
    "#article",
    "#content",
    "#main",
]


def remove_noise(soup: BeautifulSoup) -> None:
    for selector in NOISE_SELECTORS:
        for tag in soup.select(selector):
            tag.decompose()


def link_density(tag: Any) -> float:
    text = tag.get_text(" ", strip=True)
    if not text:
        return 1.0
    link_text = " ".join(link.get_text(" ", strip=True) for link in tag.find_all("a"))
    return min(1.0, len(link_text) / max(len(text), 1))


def score_content_node(tag: Any) -> float:
    text = tag.get_text(" ", strip=True)
    text_len = len(text)
    if text_len < 80:
        return 0.0
    paragraph_count = len(tag.find_all("p"))
    heading_count = len(tag.find_all(re.compile(r"^h[1-6]$")))
    code_count = len(tag.find_all(["pre", "code"]))
    list_count = len(tag.find_all(["li"]))
    density_penalty = link_density(tag) * text_len * 0.8
    return text_len + paragraph_count * 120 + heading_count * 60 + code_count * 80 + list_count * 12 - density_penalty


def best_content_node(soup: BeautifulSoup, prefer_selector_order: bool = False) -> Any:
    if prefer_selector_order:
        for selector in CONTENT_SELECTORS:
            matches = soup.select(selector)
            if not matches:
                continue
            best_match = max(matches, key=score_content_node)
            if score_content_node(best_match) >= 500:
                return best_match
    candidates: list[Any] = []
    for selector in CONTENT_SELECTORS:
        candidates.extend(soup.select(selector))
    if soup.body:
        candidates.append(soup.body)
    if not candidates:
        return soup
    return max(candidates, key=score_content_node)


def extract_with_dom_fallback(
    html: str,
    source_url: str,
    final_url: str,
    prefer_selector_order: bool = False,
) -> ExtractedPage:
    soup = BeautifulSoup(html, "html.parser")
    dom_meta = extract_dom_metadata(soup, final_url)
    remove_noise(soup)

    main = best_content_node(soup, prefer_selector_order=prefer_selector_order)
    markdown = html_to_markdown(
        str(main),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "noscript"],
    )
    markdown = clean_markdown(markdown)
    if len(markdown) < 80:
        raise RuntimeError(
            "Could not extract readable Markdown from page. "
            "The page may require JavaScript rendering or site-specific selectors."
        )

    return ExtractedPage(
        source_url=source_url,
        final_url=final_url,
        canonical_url=dom_meta["canonical_url"],
        title=dom_meta["title"],
        author=dom_meta["author"],
        published=dom_meta["published"],
        site=dom_meta["site"],
        description=dom_meta["description"],
        image=dom_meta["image"],
        language=dom_meta["language"],
        word_count=count_words(markdown),
        markdown=markdown,
        method="dom-fallback",
        warnings=["trafilatura extraction was empty or too short; used DOM fallback"],
    )


def should_expand_with_dom(primary: ExtractedPage, candidate: ExtractedPage) -> bool:
    if primary.word_count <= 0:
        return True
    if candidate.word_count < 1200:
        return False
    return candidate.word_count >= primary.word_count * 4


def expand_with_dom_if_partial(
    primary: ExtractedPage,
    html: str,
    source_url: str,
    final_url: str,
    prefer_clean_dom: bool = False,
) -> ExtractedPage:
    if primary.word_count >= 2500 and not prefer_clean_dom:
        return primary
    try:
        candidate = extract_with_dom_fallback(
            html,
            source_url,
            final_url,
            prefer_selector_order=prefer_clean_dom,
        )
    except Exception:
        return primary
    if prefer_clean_dom and candidate.word_count >= max(200, int(primary.word_count * 0.6)):
        candidate.method = "dom-rendered"
        candidate.warnings = ["used rendered browser DOM content candidate"]
        return candidate
    if not should_expand_with_dom(primary, candidate):
        return primary

    candidate.method = "dom-expanded"
    candidate.warnings = [
        (
            "trafilatura result looked partial "
            f"({primary.word_count} words); used larger DOM candidate "
            f"({candidate.word_count} words)"
        )
    ]
    for attr in ("canonical_url", "site", "description", "image", "language"):
        if not getattr(candidate, attr):
            setattr(candidate, attr, getattr(primary, attr))
    if not candidate.author and primary.author:
        candidate.author = primary.author
    return candidate


def extract_page(
    url: str,
    timeout: int,
    user_agent: str,
    render_js: bool,
    browser_channel: str,
) -> ExtractedPage:
    if render_js:
        final_url, html, _content_type = fetch_rendered_html(
            url,
            timeout=timeout,
            user_agent=user_agent,
            browser_channel=browser_channel,
        )
    else:
        final_url, html, _content_type = fetch_html(url, timeout=timeout, user_agent=user_agent)
    soup = BeautifulSoup(html, "html.parser")
    extracted = extract_with_trafilatura(html, source_url=url, final_url=final_url, soup=soup)
    if extracted is None:
        extracted = extract_with_dom_fallback(
            html,
            source_url=url,
            final_url=final_url,
            prefer_selector_order=render_js,
        )
        if render_js and "rendered browser DOM" not in extracted.warnings:
            extracted.warnings.append("used rendered browser DOM")
    else:
        extracted = expand_with_dom_if_partial(
            extracted,
            html,
            source_url=url,
            final_url=final_url,
            prefer_clean_dom=render_js,
        )
        if render_js:
            extracted.warnings.append("used rendered browser DOM")
    if not extracted.title:
        extracted.title = urlparse(final_url).netloc
    if not extracted.canonical_url:
        extracted.canonical_url = final_url
    return extracted


def safe_name(value: str, max_length: int = 80) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "", value)
    cleaned = re.sub(r"\s+", "-", cleaned).strip("-._ ")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    if not cleaned:
        cleaned = "web-page"
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip("-._ ")
    return cleaned or "web-page"


def yaml_scalar(value: str | int | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value or ""), ensure_ascii=False)


def frontmatter(
    page: ExtractedPage,
    asset_count: int,
    body_word_count: int,
    content_chars: int,
    content_hash: str,
) -> str:
    captured_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    fields: list[tuple[str, str | int | bool]] = [
        ("source_type", "public_web_page"),
        ("source_url", page.source_url),
        ("final_url", page.final_url),
        ("canonical_url", page.canonical_url),
        ("title", page.title),
        ("site", page.site),
        ("author", page.author),
        ("published", page.published),
        ("description", page.description),
        ("image", page.image),
        ("language", page.language),
        ("captured_at", captured_at),
        ("extraction_method", page.method),
        ("word_count", body_word_count),
        ("content_chars", content_chars),
        ("content_sha256", content_hash),
        ("asset_count", asset_count),
        ("status", "raw"),
    ]
    lines = ["---"]
    for key, value in fields:
        lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def image_extension(url: str, content_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower().strip(".")
    if suffix in {"jpg", "jpeg", "png", "gif", "webp", "avif"}:
        return "jpg" if suffix == "jpeg" else suffix
    if "png" in content_type:
        return "png"
    if "gif" in content_type:
        return "gif"
    if "webp" in content_type:
        return "webp"
    if "avif" in content_type:
        return "avif"
    return "jpg"


def localize_images(
    markdown: str,
    base_url: str,
    assets_dir: Path,
    timeout: int,
    user_agent: str,
    max_image_bytes: int,
) -> tuple[str, int, list[str]]:
    assets_dir.mkdir(parents=True, exist_ok=True)
    replacements: dict[str, str] = {}
    warnings: list[str] = []
    image_urls: list[str] = []

    for match in IMAGE_RE.finditer(markdown):
        raw_url = match.group(2).strip("<>")
        absolute = urljoin(base_url, raw_url)
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute not in image_urls:
            image_urls.append(absolute)

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Referer": base_url})
    local_labels: dict[str, str] = {}
    for index, image_url in enumerate(image_urls, start=1):
        try:
            response = session.get(image_url, timeout=timeout, stream=True)
            response.raise_for_status()
            content = response.content
            if len(content) > max_image_bytes:
                warnings.append(f"skipped oversized image: {image_url}")
                continue
            ext = image_extension(image_url, response.headers.get("content-type", ""))
            filename = f"image-{index:02d}.{ext}"
            out_path = assets_dir / filename
            out_path.write_bytes(content)
            local_path = f"assets/{filename}"
            replacements[image_url] = local_path
            local_labels[local_path] = label_from_image_url(image_url)
        except Exception as exc:
            warnings.append(f"failed to download image: {image_url} ({exc})")

    localized = markdown
    for remote, local in replacements.items():
        localized = localized.replace(remote, local)
    localized = re.sub(
        r"!\[\]\((assets/[^)]+)\)",
        lambda match: f"![{local_labels.get(match.group(1), 'article image')}]({match.group(1)})",
        localized,
    )
    return localized, len(replacements), warnings


def write_archive(
    page: ExtractedPage,
    out_root: Path,
    slug: str,
    download_images: bool,
    timeout: int,
    user_agent: str,
    max_image_bytes: int,
) -> dict:
    out_dir = out_root / slug
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    body = normalize_article_body(page.markdown, page.title)
    asset_count = 0
    image_warnings: list[str] = []
    if download_images:
        body, asset_count, image_warnings = localize_images(
            body,
            base_url=page.final_url,
            assets_dir=assets_dir,
            timeout=timeout,
            user_agent=user_agent,
            max_image_bytes=max_image_bytes,
        )

    body = body.strip()
    body_word_count = count_words(body)
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    article = (
        f"{frontmatter(page, asset_count, body_word_count, len(body), content_hash)}"
        f"\n\n# {page.title}\n\n{body}\n"
    )
    (out_dir / "article.md").write_text(article, encoding="utf-8")

    readme_lines = [
        f"# {page.title}",
        "",
        f"source: {page.source_url}",
        f"final_url: {page.final_url}",
        f"canonical_url: {page.canonical_url}",
        f"site: {page.site}",
        f"author: {page.author}",
        f"published: {page.published}",
        f"language: {page.language}",
        f"extraction_method: {page.method}",
        f"word_count: {body_word_count}",
        f"content_chars: {len(body)}",
        f"content_sha256: {content_hash}",
        f"download_images: {str(download_images).lower()}",
        f"asset_count: {asset_count}",
        "",
        "canonical_source: article.md",
        "",
        "Agent reading order:",
        "1. README.md for metadata and warnings.",
        "2. article.md for the readable article body.",
        "3. assets/ only when local images are needed.",
        "",
        "Directory contract:",
        "- README.md",
        "- article.md",
        "- assets/",
        "",
        "Notes:",
        "- This archive is for public ordinary web pages.",
        "- It does not bypass login, paywalls, captcha, or access controls.",
        "- Images are downloaded only when --download-images is used.",
    ]
    warnings = page.warnings + image_warnings
    if warnings:
        readme_lines.extend(["", "Warnings:"])
        readme_lines.extend(f"- {warning}" for warning in warnings)
    (out_dir / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

    return {
        "OutDir": str(out_dir),
        "Title": page.title,
        "SourceUrl": page.source_url,
        "FinalUrl": page.final_url,
        "Site": page.site,
        "Author": page.author,
        "Published": page.published,
        "CanonicalUrl": page.canonical_url,
        "Language": page.language,
        "ExtractionMethod": page.method,
        "ContentChars": len(body),
        "WordCount": body_word_count,
        "ContentSha256": content_hash,
        "AssetCount": asset_count,
        "Warnings": warnings,
        "Files": ["README.md", "article.md", "assets/"],
    }


def default_out_root() -> Path:
    return Path(__file__).resolve().parents[2] / "03_归档样例" / "web"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive public web pages into agent-friendly Markdown."
    )
    parser.add_argument("urls", nargs="+", help="Public web page URL(s).")
    parser.add_argument(
        "--out-root",
        default=str(default_out_root()),
        help="Output root directory. Default: ../../03_归档样例/web",
    )
    parser.add_argument(
        "--slug",
        default="",
        help="Output directory name. Only valid when archiving one URL.",
    )
    parser.add_argument(
        "--download-images",
        action="store_true",
        help="Download Markdown image URLs into assets/ and rewrite links.",
    )
    parser.add_argument(
        "--render-js",
        action="store_true",
        help="Render the page in a local Chromium browser before extracting HTML.",
    )
    parser.add_argument(
        "--browser-channel",
        default="msedge",
        help="Chromium channel for --render-js. Default: msedge; fallback tries chrome and bundled Chromium.",
    )
    parser.add_argument("--timeout", type=int, default=45, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--max-image-bytes",
        type=int,
        default=10_000_000,
        help="Skip image downloads larger than this many bytes.",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="User-Agent header for public page and image requests.",
    )
    return parser.parse_args(argv)


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def main(argv: Sequence[str]) -> int:
    configure_stdio()
    args = parse_args(argv)
    if args.slug and len(args.urls) != 1:
        print("--slug can only be used with one URL.", file=sys.stderr)
        return 2

    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    failures = 0
    for url in args.urls:
        try:
            page = extract_page(
                url,
                timeout=args.timeout,
                user_agent=args.user_agent,
                render_js=args.render_js,
                browser_channel=args.browser_channel,
            )
            date_part = datetime.now().strftime("%Y-%m-%d")
            slug = args.slug or f"{date_part}-{safe_name(page.title)}"
            results.append(
                write_archive(
                    page,
                    out_root=out_root,
                    slug=slug,
                    download_images=args.download_images,
                    timeout=args.timeout,
                    user_agent=args.user_agent,
                    max_image_bytes=args.max_image_bytes,
                )
            )
        except Exception as exc:
            failures += 1
            results.append({"SourceUrl": url, "Error": str(exc)})

    output = results[0] if len(results) == 1 else results
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
