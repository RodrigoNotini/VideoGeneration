"""Phase 1 RSS discovery collector."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from core.common.utils import sha256_text
from core.config.config_loader import load_all_configs
from core.persistence.db import fetch_existing_rss_keys, initialize_database, insert_rss_items
from core.state import PipelineState, copy_state


USER_AGENT = "VideoGenerationPhase1RSSCollector/1.0"
FEED_TIMEOUT_SECONDS = 10
TRACKING_QUERY_PREFIXES: tuple[str, ...] = ("utm_",)
TRACKING_QUERY_KEYS: set[str] = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
}


class RSSCollectorError(RuntimeError):
    """Raised when RSS collection cannot return any valid items."""


class RSSCollectorDependencyError(RSSCollectorError):
    """Raised when required collector dependencies are missing."""


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_runtime_configs() -> tuple[list[dict[str, str]], dict[str, Any]]:
    configs = load_all_configs(_project_root())
    rss_config = configs["rss_feeds"]
    pipeline_config = configs["pipeline"]
    return list(rss_config["feeds"]), pipeline_config


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_title(raw_title: Any) -> str:
    title = unescape(str(raw_title or ""))
    return " ".join(title.split())


def _is_tracking_query_param(key: str) -> bool:
    normalized = key.lower().strip()
    return normalized.startswith(TRACKING_QUERY_PREFIXES) or normalized in TRACKING_QUERY_KEYS


def _canonicalize_url(raw_url: Any) -> str:
    url_text = str(raw_url or "").strip()
    if not url_text:
        return ""

    parsed = urlsplit(url_text)
    if not parsed.scheme:
        parsed = urlsplit(f"https://{url_text}")

    host = (parsed.hostname or "").lower().strip()
    if not host:
        return ""

    scheme = (parsed.scheme or "https").lower()
    try:
        port = parsed.port
    except ValueError:
        return ""
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)

    if port and not default_port:
        netloc = f"{host}:{port}"
    else:
        netloc = host

    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if not _is_tracking_query_param(key)
    ]
    query_items.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(query_items, doseq=True)

    return urlunsplit((scheme, netloc, path, query, ""))


def _entry_published_at(entry: dict[str, Any]) -> str:
    def _parsed_tuple_to_iso(parsed: Any) -> str | None:
        try:
            if len(parsed) < 6:
                return None
            year, month, day, hour, minute, second = (int(parsed[index]) for index in range(6))
            value = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
        except (TypeError, ValueError, OverflowError, IndexError):
            return None
        return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            parsed_iso = _parsed_tuple_to_iso(parsed)
            if parsed_iso:
                return parsed_iso

    for key in ("published", "updated"):
        raw_value = str(entry.get(key, "")).strip()
        if not raw_value:
            continue
        try:
            value = parsedate_to_datetime(raw_value)
        except (TypeError, ValueError, OverflowError):
            continue

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc).replace(microsecond=0)
        return value.isoformat().replace("+00:00", "Z")

    return ""


def _build_normalized_item(
    *,
    source: str,
    entry: dict[str, Any],
    discovered_at: str,
) -> dict[str, str] | None:
    canonical_url = _canonicalize_url(entry.get("link", ""))
    title = _normalize_title(entry.get("title", ""))
    if not canonical_url or not title:
        return None

    title_hash = sha256_text(title.lower())
    return {
        "id": sha256_text(canonical_url),
        "source": source.strip(),
        "title": title,
        "url": canonical_url,
        "published_at": _entry_published_at(entry),
        "title_hash": title_hash,
        "discovered_at": discovered_at,
    }


def _sort_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    def _sort_key(item: dict[str, str]) -> tuple[int, float, str, str, str]:
        published_at = item.get("published_at", "")
        if not published_at:
            return (1, 0.0, item["source"], item["title"], item["url"])
        try:
            parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return (1, 0.0, item["source"], item["title"], item["url"])
        return (0, -parsed.timestamp(), item["source"], item["title"], item["url"])

    return sorted(items, key=_sort_key)


def _fetch_feed_entries(feed_url: str) -> list[dict[str, Any]]:
    try:
        import feedparser
    except ModuleNotFoundError as error:
        raise RSSCollectorDependencyError(
            "Missing dependency: feedparser. Install requirements/phase1.txt"
        ) from error

    response = requests.get(
        feed_url,
        headers={"User-Agent": USER_AGENT},
        timeout=FEED_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    parsed = feedparser.parse(response.content)
    if getattr(parsed, "bozo", 0):
        raise ValueError(f"Feed parser error for {feed_url}")

    return [dict(entry) for entry in parsed.entries]


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    feeds, pipeline_config = _load_runtime_configs()
    max_articles = int(pipeline_config["max_articles_per_run"])

    connection = initialize_database(_project_root() / pipeline_config["database_path"])
    discovered_at = _now_utc_iso()

    collected: list[dict[str, str]] = []
    ordered_items: list[dict[str, str]] = []
    duplicates_dropped = 0
    feeds_succeeded = 0
    feeds_failed = 0
    attempted_feeds = 0

    try:
        existing_urls, existing_title_hashes = fetch_existing_rss_keys(connection)
        seen_urls = set(existing_urls)
        seen_title_hashes = set(existing_title_hashes)

        for feed in feeds:
            if len(collected) >= max_articles:
                break

            attempted_feeds += 1
            source = str(feed["name"]).strip()
            feed_url = str(feed["url"]).strip()
            try:
                entries = _fetch_feed_entries(feed_url)
                feeds_succeeded += 1
            except RSSCollectorDependencyError:
                raise
            except Exception:
                feeds_failed += 1
                continue

            for entry in entries:
                if len(collected) >= max_articles:
                    break

                normalized = _build_normalized_item(
                    source=source,
                    entry=entry,
                    discovered_at=discovered_at,
                )
                if normalized is None:
                    continue

                if normalized["url"] in seen_urls:
                    duplicates_dropped += 1
                    continue
                if normalized["title_hash"] in seen_title_hashes:
                    duplicates_dropped += 1
                    continue

                seen_urls.add(normalized["url"])
                seen_title_hashes.add(normalized["title_hash"])
                collected.append(normalized)

        ordered_items = _sort_items(collected)
        insert_payload = [
            {
                "url": item["url"],
                "title": item["title"],
                "title_hash": item["title_hash"],
                "source": item["source"],
                "published_at": item["published_at"],
                "discovered_at": item["discovered_at"],
            }
            for item in ordered_items
        ]
        insert_rss_items(connection, insert_payload)
    finally:
        connection.close()

    state_items = [
        {
            "id": item["id"],
            "source": item["source"],
            "title": item["title"],
            "url": item["url"],
            "published_at": item["published_at"],
            "title_hash": item["title_hash"],
        }
        for item in ordered_items
    ]
    next_state["rss_items"] = state_items

    counters = next_state["metrics"]["counters"]
    counters["rss_items_count"] = len(state_items)
    counters["rss_items_target_count"] = max_articles
    counters["rss_feeds_total"] = len(feeds)
    counters["rss_feeds_succeeded"] = feeds_succeeded
    counters["rss_feeds_failed"] = feeds_failed
    counters["rss_duplicates_dropped"] = duplicates_dropped

    flags = next_state["metrics"]["flags"]
    flags["rss_partial_success"] = feeds_failed > 0 and len(state_items) > 0
    flags["rss_collection_failed"] = len(state_items) == 0
    flags["rss_target_reached"] = len(state_items) >= max_articles
    flags["rss_feeds_exhausted_before_target"] = (
        not flags["rss_target_reached"] and attempted_feeds == len(feeds)
    )

    if not state_items:
        raise RSSCollectorError("RSS collection failed: no valid items collected")

    return next_state
