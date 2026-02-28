"""Shared deterministic utilities for Phase 0."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCRAPE_POLICY_FULL = "full_scrape_allowed"
SCRAPE_POLICY_METADATA_ONLY = "metadata_only"
ALLOWED_SCRAPE_POLICIES: frozenset[str] = frozenset(
    {SCRAPE_POLICY_FULL, SCRAPE_POLICY_METADATA_ONLY}
)


def canonical_json(data: Any) -> str:
    """Stable JSON string representation for deterministic hashing/output."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")


def resolve_scrape_policy(value: Any, *, fallback_to_full: bool = True) -> str:
    normalized = str(value or "").strip()
    if normalized in ALLOWED_SCRAPE_POLICIES:
        return normalized
    if fallback_to_full:
        return SCRAPE_POLICY_FULL
    raise ValueError(
        "Invalid scrape_policy. Expected one of: "
        + ", ".join(sorted(ALLOWED_SCRAPE_POLICIES))
    )
