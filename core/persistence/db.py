"""SQLite bootstrap for Phase 0."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from core.common.utils import (
    SCRAPE_POLICY_FULL,
    canonical_json,
    resolve_scrape_policy,
    sha256_text,
)


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS rss_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        title_hash TEXT NOT NULL,
        source TEXT NOT NULL,
        scrape_policy TEXT NOT NULL DEFAULT 'full_scrape_allowed',
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
    "CREATE INDEX IF NOT EXISTS idx_rss_items_discovered_at ON rss_items(discovered_at)",
    "CREATE INDEX IF NOT EXISTS idx_runs_phase ON runs(phase_name)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts(run_id)",
)


def _ensure_rss_items_scrape_policy_column(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA table_info(rss_items)").fetchall()
    columns = {str(row[1]) for row in rows}
    if "scrape_policy" not in columns:
        connection.execute(
            "ALTER TABLE rss_items "
            "ADD COLUMN scrape_policy TEXT NOT NULL DEFAULT 'full_scrape_allowed'"
        )
    connection.execute(
        """
        UPDATE rss_items
        SET scrape_policy = ?
        WHERE scrape_policy IS NULL OR TRIM(scrape_policy) = ''
        """,
        (SCRAPE_POLICY_FULL,),
    )


def initialize_database(db_path: Path) -> sqlite3.Connection:
    """Create DB and all required Phase 0 tables idempotently."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path.as_posix())
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        _ensure_rss_items_scrape_policy_column(connection)
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
            resolve_scrape_policy(item.get("scrape_policy"), fallback_to_full=True),
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
                url, title, title_hash, source, scrape_policy, published_at, discovered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    return connection.total_changes - before_changes


def delete_rss_items_older_than(connection: sqlite3.Connection, cutoff_discovered_at: str) -> int:
    """Delete RSS rows older than the provided discovered_at cutoff (UTC ISO-8601)."""
    before_changes = connection.total_changes
    with connection:
        connection.execute(
            "DELETE FROM rss_items WHERE discovered_at < ?",
            (cutoff_discovered_at,),
        )
    return connection.total_changes - before_changes


def count_rss_items(connection: sqlite3.Connection) -> int:
    """Return current RSS inventory size."""
    row = connection.execute("SELECT COUNT(*) FROM rss_items").fetchone()
    if not row:
        return 0
    return int(row[0])


def fetch_rss_items_for_ranking(connection: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    """Load deterministic RSS candidates from DB for downstream ranking."""
    if limit < 1:
        return []

    rows = connection.execute(
        """
        SELECT
            url,
            title,
            title_hash,
            source,
            scrape_policy,
            COALESCE(published_at, '') AS published_at,
            discovered_at
        FROM rss_items
        ORDER BY
            CASE WHEN COALESCE(published_at, '') = '' THEN 1 ELSE 0 END ASC,
            published_at DESC,
            source ASC,
            title ASC,
            url ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        {
            "id": sha256_text(str(row[0])),
            "url": str(row[0]),
            "title": str(row[1]),
            "title_hash": str(row[2]),
            "source": str(row[3]),
            "scrape_policy": resolve_scrape_policy(row[4], fallback_to_full=True),
            "published_at": str(row[5]),
            "discovered_at": str(row[6]),
        }
        for row in rows
    ]


def sync_rss_item_policies_by_source(
    connection: sqlite3.Connection,
    source_policy_map: dict[str, str],
) -> int:
    """Synchronize rss_items.scrape_policy to current feed config by source name."""
    if not source_policy_map:
        return 0

    before_changes = connection.total_changes
    with connection:
        for source, policy in sorted(source_policy_map.items(), key=lambda item: item[0]):
            source_name = str(source).strip()
            if not source_name:
                continue
            resolved_policy = resolve_scrape_policy(policy, fallback_to_full=True)
            connection.execute(
                """
                UPDATE rss_items
                SET scrape_policy = ?
                WHERE source = ? AND COALESCE(scrape_policy, ?) != ?
                """,
                (resolved_policy, source_name, SCRAPE_POLICY_FULL, resolved_policy),
            )
    return connection.total_changes - before_changes


def fetch_rss_item_scrape_policy_by_url(connection: sqlite3.Connection, url: str) -> str | None:
    row = connection.execute(
        """
        SELECT scrape_policy
        FROM rss_items
        WHERE url = ?
        LIMIT 1
        """,
        (url,),
    ).fetchone()
    if not row:
        return None
    return resolve_scrape_policy(row[0], fallback_to_full=True)
