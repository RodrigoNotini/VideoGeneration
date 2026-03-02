"""Phase 4 extraction gate contract with source access policy enforcement."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.common.utils import (
    SCRAPE_POLICY_FULL,
    SCRAPE_POLICY_METADATA_ONLY,
    resolve_scrape_policy,
)
from core.config.config_loader import ConfigError, load_all_configs
from core.persistence.db import fetch_rss_item_scrape_policy_by_url, initialize_database
from core.state import PipelineState, copy_state

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


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
        configs = load_all_configs(_project_root())
    except ConfigError:
        return None

    pipeline_cfg = configs.get("pipeline", {})
    db_rel_path = str(pipeline_cfg.get("database_path", "")).strip()
    if not db_rel_path:
        return None

    try:
        connection = initialize_database(_project_root() / db_rel_path)
    except Exception:
        logger.exception("Phase 4 failed to initialize DB while resolving policy for selected_url=%s", selected_url)
        return None

    try:
        return fetch_rss_item_scrape_policy_by_url(connection, selected_url)
    except Exception:
        logger.exception("Phase 4 failed DB policy lookup for selected_url=%s", selected_url)
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
        logger.info("Phase 4 resolved scrape_policy from DB for selected_url=%s", selected_url)
        return db_policy, False

    logger.warning("Phase 4 policy resolution failed for selected_url=%s; applying fail-closed metadata_only", selected_url)
    return SCRAPE_POLICY_METADATA_ONLY, True


def _metadata_only_article(
    selected_url: str,
    *,
    extraction_status: str = "policy_blocked",
    policy_resolution_failed: bool = False,
) -> dict[str, Any]:
    return {
        "title": "Metadata-only article blocked by source policy",
        "author": "",
        "published_at": "",
        "source_url": selected_url,
        "paragraphs": [],
        "scrape_policy": SCRAPE_POLICY_METADATA_ONLY,
        "metadata_only": True,
        "extraction_status": extraction_status,
        "policy_resolution_failed": policy_resolution_failed,
    }


def _full_scrape_placeholder_article(selected_url: str) -> dict[str, Any]:
    return {
        "title": "Phase 0 article placeholder",
        "author": "Phase 0 System",
        "published_at": "2026-01-01T00:00:00Z",
        "source_url": selected_url,
        "paragraphs": [
            "This is a deterministic placeholder article paragraph.",
            "No scraping is performed in Phase 0.",
        ],
        "scrape_policy": SCRAPE_POLICY_FULL,
        "metadata_only": False,
        "extraction_status": "full_scrape_allowed_placeholder",
    }


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    selected_url = next_state["selected_url"] or "https://example.com/phase0/no_selection"
    selected_policy, policy_resolution_failed = _resolve_selected_scrape_policy(next_state, selected_url)

    counters = next_state["metrics"]["counters"]
    flags = next_state["metrics"]["flags"]
    flags["phase4_selected_scrape_policy"] = selected_policy
    flags["phase4_policy_resolution_failed"] = policy_resolution_failed
    counters["phase4_policy_resolution_failed_count"] = 1 if policy_resolution_failed else 0

    if selected_policy == SCRAPE_POLICY_METADATA_ONLY:
        next_state["article"] = _metadata_only_article(
            selected_url,
            extraction_status="policy_resolution_failed" if policy_resolution_failed else "policy_blocked",
            policy_resolution_failed=policy_resolution_failed,
        )
        counters["phase4_policy_blocked_count"] = 1
        flags["phase4_policy_blocked_metadata_only"] = True
        flags["phase4_html_fetch_attempted"] = False
        flags["article_stub_created"] = False
        return next_state

    next_state["article"] = _full_scrape_placeholder_article(selected_url)
    counters["phase4_policy_blocked_count"] = 0
    flags["phase4_policy_blocked_metadata_only"] = False
    flags["phase4_html_fetch_attempted"] = False
    flags["article_stub_created"] = True
    return next_state
