"""Phase 4 article extraction with source access policy enforcement."""

from __future__ import annotations

import json
import logging
import socket
import re
from ipaddress import ip_address
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from core.common.utils import (
    SCRAPE_POLICY_FULL,
    SCRAPE_POLICY_METADATA_ONLY,
    resolve_scrape_policy,
    write_json,
)
from core.config.config_loader import ConfigError, load_all_configs
from core.persistence.db import fetch_rss_item_scrape_policy_by_url, initialize_database
from core.state import PipelineState, copy_state

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 20
HTTP_USER_AGENT = "VideoGenerationPhase4Extractor/1.0 (+https://example.com)"
DEFAULT_OUTPUT_DIR = "outputs"
MAX_HTML_BYTES = 2_000_000
MAX_REDIRECT_HOPS = 4
MAX_PARAGRAPHS = 12
MAX_PARAGRAPH_CHARS = 420
MAX_TOTAL_PARAGRAPH_CHARS = 3600
MIN_PARAGRAPH_CHARS = 35

NOISE_LINE_PATTERN = re.compile(
    r"\b("
    r"advertisement|sponsored|newsletter|subscribe|sign up|cookie|privacy policy|"
    r"all rights reserved|copyright|share this|follow us|read more"
    r")\b",
    re.IGNORECASE,
)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
MULTISPACE_PATTERN = re.compile(r"\s+")

META_TITLE_KEYS = (
    "og:title",
    "twitter:title",
    "title",
)
META_AUTHOR_KEYS = (
    "author",
    "article:author",
    "parsely-author",
    "twitter:creator",
)
META_PUBLISHED_AT_KEYS = (
    "article:published_time",
    "og:pubdate",
    "pubdate",
    "publish-date",
    "date",
)
META_DESCRIPTION_KEYS = (
    "description",
    "og:description",
    "twitter:description",
)


def _phase4_error(*, code: str, message: str, details: dict[str, Any] | None = None) -> str:
    payload = {
        "phase": 4,
        "agent": "article_extractor",
        "code": code,
        "message": message,
        "details": details or {},
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


class _ArticleHTMLParser(HTMLParser):
    BLOCKED_TAGS = {
        "script",
        "style",
        "noscript",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "svg",
        "canvas",
        "iframe",
        "button",
    }
    PARAGRAPH_TAGS = {"p", "li"}
    NOISE_CONTAINER_TOKENS = {
        "ad",
        "ads",
        "advert",
        "sponsored",
        "promo",
        "newsletter",
        "subscribe",
        "cookie",
        "social",
        "share",
        "related",
        "breadcrumb",
        "footer",
        "header",
        "navbar",
        "menu",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_stack: list[str] = []
        self._captured_tag: str | None = None
        self._captured_chunks: list[str] = []
        self._inside_title = False
        self._title_chunks: list[str] = []
        self.meta: dict[str, str] = {}
        self.paragraphs: list[str] = []

    def _in_skipped_container(self) -> bool:
        return bool(self._skip_stack)

    def _attributes_as_token_blob(self, attrs: list[tuple[str, str | None]]) -> str:
        values: list[str] = []
        for key, value in attrs:
            key_norm = (key or "").strip().lower()
            if key_norm in {"class", "id", "role", "aria-label"}:
                values.append((value or "").strip().lower())
        return " ".join(values)

    def _is_noise_container(self, attrs: list[tuple[str, str | None]]) -> bool:
        token_blob = self._attributes_as_token_blob(attrs)
        if not token_blob:
            return False
        normalized = re.sub(r"[^a-z0-9]+", " ", token_blob)
        tokens = {piece for piece in normalized.split(" ") if piece}
        return bool(tokens & self.NOISE_CONTAINER_TOKENS)

    def _push_skip_tag(self, tag: str) -> None:
        self._skip_stack.append(tag)

    def _pop_skip_tag(self, tag: str) -> None:
        if not self._skip_stack:
            return
        if self._skip_stack[-1] == tag:
            self._skip_stack.pop()
            return
        for index in range(len(self._skip_stack) - 1, -1, -1):
            if self._skip_stack[index] == tag:
                self._skip_stack.pop(index)
                return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered_tag = tag.lower()
        if lowered_tag == "meta":
            attrs_map = {str(key).lower(): str(value or "") for key, value in attrs}
            meta_key = (
                attrs_map.get("property")
                or attrs_map.get("name")
                or attrs_map.get("itemprop")
                or ""
            ).strip().lower()
            content = attrs_map.get("content", "").strip()
            if meta_key and content and meta_key not in self.meta:
                self.meta[meta_key] = content
            return

        if lowered_tag in self.BLOCKED_TAGS or self._is_noise_container(attrs):
            self._push_skip_tag(lowered_tag)
            return

        if self._in_skipped_container():
            return

        if lowered_tag == "title":
            self._inside_title = True
            return

        if lowered_tag in self.PARAGRAPH_TAGS and self._captured_tag is None:
            self._captured_tag = lowered_tag
            self._captured_chunks = []
            return

        if lowered_tag == "br" and self._captured_tag is not None:
            self._captured_chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.lower()
        if self._inside_title and lowered_tag == "title":
            self._inside_title = False

        if self._captured_tag is not None and lowered_tag == self._captured_tag:
            paragraph = _normalize_text("".join(self._captured_chunks))
            if paragraph:
                self.paragraphs.append(paragraph)
            self._captured_tag = None
            self._captured_chunks = []

        self._pop_skip_tag(lowered_tag)

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self._title_chunks.append(data)
        if self._in_skipped_container():
            return
        if self._captured_tag is not None:
            self._captured_chunks.append(data)

    def title_text(self) -> str:
        return _normalize_text("".join(self._title_chunks))


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_runtime_pipeline_config() -> dict[str, Any]:
    configs = load_all_configs(_project_root())
    return dict(configs.get("pipeline", {}))


def _resolve_project_confined_path(configured_path_text: str, *, field_name: str) -> Path:
    configured_path = Path(configured_path_text)
    if configured_path.is_absolute() or configured_path.anchor:
        raise ValueError(f"{field_name} must be a relative path within project root")
    if ".." in configured_path.parts:
        raise ValueError(f"{field_name} cannot contain parent traversal segments")

    project_root = _project_root().resolve()
    confined_path = (project_root / configured_path).resolve()
    try:
        confined_path.relative_to(project_root)
    except ValueError as error:
        raise ValueError(f"{field_name} resolves outside project root") from error
    return confined_path


def _output_dir() -> Path:
    try:
        pipeline_cfg = _load_runtime_pipeline_config()
        configured_dir = str(pipeline_cfg.get("output_dir", "")).strip()
    except ConfigError:
        configured_dir = ""
    if not configured_dir:
        configured_dir = DEFAULT_OUTPUT_DIR
    return _resolve_project_confined_path(configured_dir, field_name="pipeline.output_dir")


def _normalize_text(value: str) -> str:
    if not value:
        return ""
    unescaped = unescape(value)
    no_markup = HTML_TAG_PATTERN.sub(" ", unescaped)
    return MULTISPACE_PATTERN.sub(" ", no_markup).strip()


def _resolve_policy_from_items(items: list[dict[str, Any]], selected_url: str) -> str | None:
    for item in items:
        if str(item.get("url", "")).strip() == selected_url:
            raw_policy = item.get("scrape_policy")
            if raw_policy is None or not str(raw_policy).strip():
                return None
            try:
                return resolve_scrape_policy(raw_policy, fallback_to_full=False)
            except ValueError:
                return None
    return None


def _resolve_policy_from_db(selected_url: str) -> str | None:
    if not selected_url:
        return None
    try:
        pipeline_cfg = _load_runtime_pipeline_config()
    except ConfigError:
        return None

    db_rel_path = str(pipeline_cfg.get("database_path", "")).strip()
    if not db_rel_path:
        return None

    try:
        db_path = _resolve_project_confined_path(db_rel_path, field_name="pipeline.database_path")
    except ValueError:
        logger.exception("PHASE4_POLICY_DB_PATH_INVALID")
        return None

    try:
        connection = initialize_database(db_path)
    except Exception:
        logger.exception("PHASE4_POLICY_DB_INIT_FAILED")
        return None

    try:
        return fetch_rss_item_scrape_policy_by_url(connection, selected_url)
    except Exception:
        logger.exception("PHASE4_POLICY_DB_LOOKUP_FAILED")
        return None
    finally:
        connection.close()


def _resolve_selected_scrape_policy(state: PipelineState, selected_url: str) -> tuple[str, bool]:
    ranked_policy = _resolve_policy_from_items(
        state["ranked_items"] if isinstance(state["ranked_items"], list) else [],
        selected_url,
    )
    if ranked_policy:
        return ranked_policy, False

    rss_policy = _resolve_policy_from_items(
        state["rss_items"] if isinstance(state["rss_items"], list) else [],
        selected_url,
    )
    if rss_policy:
        return rss_policy, False

    db_policy = _resolve_policy_from_db(selected_url)
    if db_policy:
        logger.info("PHASE4_POLICY_RESOLVED source=db")
        return db_policy, False

    logger.warning("PHASE4_POLICY_RESOLUTION_FAILED applying=metadata_only")
    return SCRAPE_POLICY_METADATA_ONLY, True


def _selected_item_metadata(state: PipelineState, selected_url: str) -> dict[str, Any]:
    containers: list[Any] = [state.get("ranked_items"), state.get("rss_items")]
    for container in containers:
        if not isinstance(container, list):
            continue
        for item in container:
            if not isinstance(item, dict):
                continue
            if str(item.get("url", "")).strip() == selected_url:
                return dict(item)
    return {}


def _metadata_only_article(
    selected_url: str,
    selected_item: dict[str, Any],
    *,
    extraction_status: str = "policy_blocked",
    policy_resolution_failed: bool = False,
) -> dict[str, Any]:
    normalized_title = _normalize_text(str(selected_item.get("title", "")))
    normalized_published_at = _normalize_text(str(selected_item.get("published_at", "")))
    return {
        "title": normalized_title or "Metadata-only article blocked by source policy",
        "author": "",
        "published_at": normalized_published_at,
        "source_url": selected_url,
        "paragraphs": [],
        "scrape_policy": SCRAPE_POLICY_METADATA_ONLY,
        "metadata_only": True,
        "extraction_status": extraction_status,
        "policy_resolution_failed": policy_resolution_failed,
    }


def _failed_full_scrape_article(
    selected_url: str,
    selected_item: dict[str, Any],
    *,
    status: str,
    policy_resolution_failed: bool,
) -> dict[str, Any]:
    fallback_title = _normalize_text(str(selected_item.get("title", ""))) or "Article extraction failed"
    fallback_summary = _normalize_text(str(selected_item.get("summary", "")).strip())
    normalized_published_at = _normalize_text(str(selected_item.get("published_at", "")))
    paragraphs = [fallback_summary[:MAX_PARAGRAPH_CHARS]] if fallback_summary else []
    return {
        "title": fallback_title,
        "author": "",
        "published_at": normalized_published_at,
        "source_url": selected_url,
        "paragraphs": paragraphs,
        "scrape_policy": SCRAPE_POLICY_FULL,
        "metadata_only": False,
        "extraction_status": status,
        "policy_resolution_failed": policy_resolution_failed,
    }


def _all_candidates_failed_article(
    selected_url: str,
    selected_item: dict[str, Any],
) -> dict[str, Any]:
    fallback_title = _normalize_text(str(selected_item.get("title", ""))) or "Article extraction failed"
    normalized_published_at = _normalize_text(str(selected_item.get("published_at", "")))
    return {
        "title": fallback_title,
        "author": "",
        "published_at": normalized_published_at,
        "source_url": selected_url,
        "paragraphs": [],
        "scrape_policy": SCRAPE_POLICY_FULL,
        "metadata_only": False,
        "extraction_status": "all_candidates_failed",
        "policy_resolution_failed": False,
    }


def _phase4_candidates(
    state: PipelineState,
    selected_url: str,
) -> list[tuple[int, str, dict[str, Any]]]:
    ranked_items = state.get("ranked_items")
    if isinstance(ranked_items, list) and ranked_items:
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        seen_urls: set[str] = set()
        for index, raw_item in enumerate(ranked_items):
            if not isinstance(raw_item, dict):
                continue
            url = str(raw_item.get("url", "")).strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            candidates.append((index, url, dict(raw_item)))
        return candidates

    fallback_item = _selected_item_metadata(state, selected_url)
    return [(0, selected_url, fallback_item)]


def _read_meta_value(meta: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _normalize_text(meta.get(key, ""))
        if value:
            return value
    return ""


def _is_noise_line(value: str) -> bool:
    if not value:
        return True
    if NOISE_LINE_PATTERN.search(value):
        return True
    return False


def _clean_paragraphs(raw_paragraphs: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    total_chars = 0

    for raw in raw_paragraphs:
        paragraph = _normalize_text(raw)
        if len(paragraph) < MIN_PARAGRAPH_CHARS:
            continue
        if _is_noise_line(paragraph):
            continue
        dedupe_key = paragraph.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        clipped = paragraph[:MAX_PARAGRAPH_CHARS].strip()
        if not clipped:
            continue
        projected_total = total_chars + len(clipped)
        if projected_total > MAX_TOTAL_PARAGRAPH_CHARS:
            break
        cleaned.append(clipped)
        total_chars = projected_total
        if len(cleaned) >= MAX_PARAGRAPHS:
            break
    return cleaned


def _build_full_scrape_article(
    *,
    selected_url: str,
    selected_item: dict[str, Any],
    parser: _ArticleHTMLParser,
    policy_resolution_failed: bool,
) -> dict[str, Any]:
    title = (
        _read_meta_value(parser.meta, META_TITLE_KEYS)
        or parser.title_text()
        or _normalize_text(str(selected_item.get("title", "")))
        or selected_url
    )
    author = _read_meta_value(parser.meta, META_AUTHOR_KEYS)
    published_at = (
        _read_meta_value(parser.meta, META_PUBLISHED_AT_KEYS)
        or _normalize_text(str(selected_item.get("published_at", "")))
    )

    paragraphs = _clean_paragraphs(parser.paragraphs)
    if not paragraphs:
        fallback_description = _read_meta_value(parser.meta, META_DESCRIPTION_KEYS)
        if fallback_description:
            paragraphs = [fallback_description[:MAX_PARAGRAPH_CHARS]]

    return {
        "title": title,
        "author": author,
        "published_at": published_at,
        "source_url": selected_url,
        "paragraphs": paragraphs,
        "scrape_policy": SCRAPE_POLICY_FULL,
        "metadata_only": False,
        "extraction_status": "extracted",
        "policy_resolution_failed": policy_resolution_failed,
    }


def _fetch_html(selected_url: str) -> str:
    current_url = selected_url
    for _ in range(MAX_REDIRECT_HOPS + 1):
        if not _is_public_fetchable_url(current_url):
            raise ValueError("blocked target url")

        with requests.get(
            current_url,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": HTTP_USER_AGENT},
            stream=True,
            allow_redirects=False,
        ) as response:
            _assert_response_peer_is_public(response)
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location", "").strip()
                if not location:
                    raise ValueError("redirect without location")
                next_url = urljoin(current_url, location)
                current_url = next_url
                continue

            response.raise_for_status()
            content_length_header = response.headers.get("Content-Length", "").strip()
            if content_length_header:
                try:
                    advertised_length = int(content_length_header)
                except ValueError:
                    advertised_length = 0
                if advertised_length > MAX_HTML_BYTES:
                    raise ValueError("response too large")

            chunks: list[bytes] = []
            total_bytes = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if total_bytes > MAX_HTML_BYTES:
                    raise ValueError("response too large")
                chunks.append(chunk)

            raw_bytes = b"".join(chunks)
            encoding = response.encoding or response.apparent_encoding or "utf-8"
            return raw_bytes.decode(encoding, errors="replace")
    raise ValueError("too many redirects")


def _is_public_ip(ip_value: str) -> bool:
    try:
        parsed_ip = ip_address(ip_value)
    except ValueError:
        return False
    return not (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_multicast
        or parsed_ip.is_reserved
        or parsed_ip.is_unspecified
    )


def _extract_response_peer_ip(response: requests.Response) -> str | None:
    try:
        raw_obj = getattr(response, "raw", None)
        connection = getattr(raw_obj, "_connection", None)
        sock = getattr(connection, "sock", None)
        if sock is None:
            return None
        peername = sock.getpeername()
        if not isinstance(peername, tuple) or not peername:
            return None
        peer_ip = str(peername[0]).strip()
        return peer_ip or None
    except Exception:
        return None


def _assert_response_peer_is_public(response: requests.Response) -> None:
    peer_ip = _extract_response_peer_ip(response)
    if not peer_ip:
        raise ValueError("blocked response destination")
    if not _is_public_ip(peer_ip):
        raise ValueError("blocked response destination")


def _is_public_fetchable_url(selected_url: str) -> bool:
    parsed = urlparse(selected_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        return False

    lowered_host = hostname.casefold()
    if lowered_host in {"localhost", "localhost.localdomain"} or lowered_host.endswith(".local"):
        return False

    if _is_public_ip(hostname):
        return True
    try:
        ip_address(hostname)
        return False
    except ValueError:
        try:
            addr_info = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False
        for _, _, _, _, sockaddr in addr_info:
            if not sockaddr:
                return False
            try:
                resolved_ip = str(sockaddr[0])
            except IndexError:
                return False
            if not _is_public_ip(resolved_ip):
                return False
        return True


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _write_article_artifacts(
    *,
    output_dir: Path,
    article_payload: dict[str, Any],
    raw_html: str | None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if raw_html is not None:
        _write_text(output_dir / "article_raw.html", raw_html)
    write_json(output_dir / "article.json", article_payload)


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    selected_url = next_state["selected_url"] or "https://example.com/phase0/no_selection"
    output_dir = _output_dir()

    counters = next_state["metrics"]["counters"]
    flags = next_state["metrics"]["flags"]
    flags["phase4_selected_scrape_policy"] = SCRAPE_POLICY_FULL
    flags["phase4_policy_resolution_failed"] = False
    flags["phase4_policy_blocked_metadata_only"] = False
    flags["phase4_fallback_used"] = False
    flags["phase4_selected_rank_index"] = -1
    flags["phase4_attempted_urls"] = []
    flags["phase4_attempt_failure_reasons"] = []
    counters["phase4_policy_resolution_failed_count"] = 0
    counters["phase4_policy_blocked_count"] = 0
    candidates = _phase4_candidates(next_state, selected_url)
    attempted_urls: list[str] = []
    failure_reasons: list[str] = []
    fetch_attempted_any = False

    for rank_index, candidate_url, candidate_item in candidates:
        attempted_urls.append(candidate_url)
        if not _is_public_fetchable_url(candidate_url):
            logger.warning("PHASE4_URL_BLOCKED url=%s rank_index=%s", candidate_url, rank_index)
            failure_reasons.append("url_blocked")
            continue

        fetch_attempted_any = True
        try:
            raw_html = _fetch_html(candidate_url)
        except Exception:
            logger.exception("PHASE4_HTML_FETCH_FAILED url=%s rank_index=%s", candidate_url, rank_index)
            failure_reasons.append("fetch_failed")
            continue

        try:
            parser = _ArticleHTMLParser()
            parser.feed(raw_html)
            parser.close()
            article_payload = _build_full_scrape_article(
                selected_url=candidate_url,
                selected_item=candidate_item,
                parser=parser,
                policy_resolution_failed=False,
            )
        except Exception:
            logger.exception("PHASE4_PARSE_FAILED url=%s rank_index=%s", candidate_url, rank_index)
            failure_reasons.append("parse_failed")
            continue

        if not article_payload.get("paragraphs"):
            logger.warning("PHASE4_EMPTY_PARAGRAPHS url=%s rank_index=%s", candidate_url, rank_index)
            failure_reasons.append("empty_paragraphs")
            continue

        next_state["selected_url"] = candidate_url
        next_state["article"] = article_payload

        flags["phase4_html_fetch_attempted"] = fetch_attempted_any
        flags["phase4_html_fetch_succeeded"] = True
        flags["phase4_extraction_failed"] = False
        flags["phase4_fallback_used"] = rank_index > 0
        flags["phase4_selected_rank_index"] = rank_index
        flags["phase4_attempted_urls"] = list(attempted_urls)
        flags["phase4_attempt_failure_reasons"] = list(failure_reasons)
        counters["phase4_candidate_attempt_count"] = len(attempted_urls)
        counters["phase4_candidate_failure_count"] = len(failure_reasons)
        counters["phase4_candidate_exhausted_count"] = 0
        counters["phase4_extracted_paragraph_count"] = len(next_state["article"]["paragraphs"])

        _write_article_artifacts(
            output_dir=output_dir,
            article_payload=next_state["article"],
            raw_html=raw_html,
        )
        logger.info(
            "PHASE4_EXTRACTION_SUMMARY policy=%s metadata_only=%s html_fetch_attempted=%s html_fetch_succeeded=%s paragraphs=%s",
            flags["phase4_selected_scrape_policy"],
            next_state["article"]["metadata_only"],
            flags["phase4_html_fetch_attempted"],
            flags["phase4_html_fetch_succeeded"],
            counters["phase4_extracted_paragraph_count"],
        )
        return next_state

    failed_url = attempted_urls[-1] if attempted_urls else selected_url
    failed_item = _selected_item_metadata(next_state, failed_url)
    next_state["selected_url"] = failed_url
    next_state["article"] = _all_candidates_failed_article(
        failed_url,
        failed_item,
    )

    flags["phase4_html_fetch_attempted"] = fetch_attempted_any
    flags["phase4_html_fetch_succeeded"] = False
    flags["phase4_extraction_failed"] = True
    flags["phase4_fallback_used"] = False
    flags["phase4_selected_rank_index"] = -1
    flags["phase4_attempted_urls"] = list(attempted_urls)
    flags["phase4_attempt_failure_reasons"] = list(failure_reasons)
    counters["phase4_candidate_attempt_count"] = len(attempted_urls)
    counters["phase4_candidate_failure_count"] = len(failure_reasons)
    counters["phase4_candidate_exhausted_count"] = 1
    counters["phase4_extracted_paragraph_count"] = 0

    _write_article_artifacts(
        output_dir=output_dir,
        article_payload=next_state["article"],
        raw_html=None,
    )
    logger.info(
        "PHASE4_EXTRACTION_SUMMARY policy=%s metadata_only=%s html_fetch_attempted=%s html_fetch_succeeded=%s paragraphs=%s",
        flags["phase4_selected_scrape_policy"],
        next_state["article"]["metadata_only"],
        flags["phase4_html_fetch_attempted"],
        flags["phase4_html_fetch_succeeded"],
        counters["phase4_extracted_paragraph_count"],
    )
    raise RuntimeError(
        _phase4_error(
            code="all_ranked_candidates_failed",
            message="Phase 4 failed to extract content with paragraphs from all ranked candidates.",
            details={
                "attempted_count": len(attempted_urls),
                "attempted_urls": attempted_urls,
                "failure_reasons": failure_reasons,
            },
        )
    )
