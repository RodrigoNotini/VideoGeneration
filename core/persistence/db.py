"""SQLite bootstrap for Phase 0."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from core.common.utils import canonical_json


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS rss_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        title_hash TEXT NOT NULL,
        source TEXT NOT NULL,
        published_at TEXT,
        discovered_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        phase_name TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        artifact_path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        checksum TEXT,
        FOREIGN KEY(run_id) REFERENCES runs(run_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rss_items_url ON rss_items(url)",
    "CREATE INDEX IF NOT EXISTS idx_runs_phase ON runs(phase_name)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts(run_id)",
)


def initialize_database(db_path: Path) -> sqlite3.Connection:
    """Create DB and all required Phase 0 tables idempotently."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path.as_posix())
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
    return connection


def save_run(connection: sqlite3.Connection, metadata: dict[str, Any]) -> None:
    with connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, phase_name, status, started_at, finished_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                metadata["run_id"],
                metadata["phase_name"],
                metadata["status"],
                metadata["started_at"],
                metadata["finished_at"],
                canonical_json(metadata),
            ),
        )


def save_artifact(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    artifact_type: str,
    artifact_path: str,
    created_at: str,
    checksum: str | None = None,
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO artifacts (run_id, artifact_type, artifact_path, created_at, checksum)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, artifact_type, artifact_path, created_at, checksum),
        )


def fetch_existing_rss_keys(connection: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """Return existing RSS URL and title_hash keys for duplicate detection."""
    cursor = connection.execute("SELECT url, title_hash FROM rss_items")
    existing_urls: set[str] = set()
    existing_title_hashes: set[str] = set()
    for row in cursor.fetchall():
        existing_urls.add(str(row[0]))
        existing_title_hashes.add(str(row[1]))
    return existing_urls, existing_title_hashes


def insert_rss_items(connection: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """Insert RSS rows with duplicate-safe semantics, returning inserted row count."""
    if not items:
        return 0

    values = [
        (
            item["url"],
            item["title"],
            item["title_hash"],
            item["source"],
            item.get("published_at", ""),
            item["discovered_at"],
        )
        for item in items
    ]

    before_changes = connection.total_changes
    with connection:
        connection.executemany(
            """
            INSERT OR IGNORE INTO rss_items (
                url, title, title_hash, source, published_at, discovered_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    return connection.total_changes - before_changes
