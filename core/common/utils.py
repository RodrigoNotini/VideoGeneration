"""Shared deterministic utilities for Phase 0."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


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
