"""SQLite bootstrap for Phase 0."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
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
    """
    CREATE TABLE IF NOT EXISTS rss_item_theme_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        theme TEXT NOT NULL,
        score REAL NOT NULL,
        reason TEXT NOT NULL,
        source TEXT NOT NULL,
        published_at TEXT,
        discovered_at TEXT NOT NULL,
        run_id TEXT NOT NULL,
        scored_at TEXT NOT NULL,
        model_name TEXT NOT NULL,
        prompt_version TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rss_item_theme_scores_theme_score "
    "ON rss_item_theme_scores(theme, score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_rss_item_theme_scores_theme_url "
    "ON rss_item_theme_scores(theme, url)",
    "CREATE INDEX IF NOT EXISTS idx_rss_item_theme_scores_scored_at "
    "ON rss_item_theme_scores(scored_at DESC)",
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
    raw_policy = row[0]
    if raw_policy is None or not str(raw_policy).strip():
        return None
    try:
        return resolve_scrape_policy(raw_policy, fallback_to_full=False)
    except ValueError:
        return None


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _freshness_cutoff_iso(*, freshness_days: int, now_iso: str | None = None) -> str:
    now_value = now_iso or _now_utc_iso()
    normalized = now_value.replace("Z", "+00:00")
    now_dt = datetime.fromisoformat(normalized)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    return (now_dt.astimezone(timezone.utc) - timedelta(days=freshness_days)).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def insert_theme_scores(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Insert Phase 2 score history rows and return inserted row count."""
    if not rows:
        return 0

    values: list[tuple[Any, ...]] = []
    for row in rows:
        url = str(row.get("url", "")).strip()
        theme = str(row.get("theme", "")).strip()
        reason = str(row.get("reason", "")).strip()
        source = str(row.get("source", "")).strip()
        published_at = str(row.get("published_at", "")).strip()
        discovered_at = str(row.get("discovered_at", "")).strip()
        run_id = str(row.get("run_id", "")).strip()
        scored_at = str(row.get("scored_at", "")).strip()
        model_name = str(row.get("model_name", "")).strip()
        prompt_version = str(row.get("prompt_version", "")).strip()
        try:
            score = float(row.get("score", 0.0))
        except (TypeError, ValueError) as error:
            raise ValueError("Theme score row contains non-numeric score.") from error
        if score < 0.0 or score > 1.0:
            raise ValueError("Theme score row score must be in [0, 1].")
        if not all((url, theme, reason, source, discovered_at, run_id, scored_at, model_name, prompt_version)):
            raise ValueError("Theme score row is missing required fields.")
        values.append(
            (
                url,
                theme,
                round(score, 6),
                reason,
                source,
                published_at,
                discovered_at,
                run_id,
                scored_at,
                model_name,
                prompt_version,
            )
        )

    before_changes = connection.total_changes
    with connection:
        connection.executemany(
            """
            INSERT INTO rss_item_theme_scores (
                url,
                theme,
                score,
                reason,
                source,
                published_at,
                discovered_at,
                run_id,
                scored_at,
                model_name,
                prompt_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    return connection.total_changes - before_changes


def fetch_replacement_candidates(
    connection: sqlite3.Connection,
    *,
    theme: str,
    min_score: float,
    freshness_days: int,
    excluded_urls: list[str],
    limit: int,
    now_iso: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch replacement pool using same-theme, freshness, and score constraints.

    Semantics are max score per (theme, url), with deterministic ordering:
    score DESC, published_at DESC, url ASC.
    """
    if limit < 1:
        return []

    cutoff_discovered_at = _freshness_cutoff_iso(freshness_days=freshness_days, now_iso=now_iso)
    unique_excluded = sorted(
        {
            str(url).strip()
            for url in excluded_urls
            if str(url).strip()
        }
    )

    params: list[Any] = [theme, float(min_score), cutoff_discovered_at]
    exclusion_sql = ""
    if unique_excluded:
        placeholders = ", ".join("?" for _ in unique_excluded)
        exclusion_sql = f"AND scores.url NOT IN ({placeholders})"
        params.extend(unique_excluded)
    params.append(limit)

    rows = connection.execute(
        f"""
        WITH eligible AS (
            SELECT
                scores.url AS url,
                scores.theme AS theme,
                scores.score AS score,
                TRIM(COALESCE(scores.reason, '')) AS reason,
                COALESCE(NULLIF(metadata.source, ''), scores.source, '') AS source,
                metadata.title AS title,
                metadata.scrape_policy AS scrape_policy,
                COALESCE(NULLIF(metadata.published_at, ''), COALESCE(scores.published_at, '')) AS published_at,
                scores.discovered_at AS discovered_at,
                scores.scored_at AS scored_at
            FROM rss_item_theme_scores AS scores
            INNER JOIN rss_items AS metadata
                ON metadata.url = scores.url
            WHERE
                scores.theme = ?
                AND scores.score >= ?
                AND scores.discovered_at >= ?
                {exclusion_sql}
        ),
        ranked AS (
            SELECT
                url,
                theme,
                score,
                reason,
                source,
                title,
                scrape_policy,
                published_at,
                discovered_at,
                scored_at,
                ROW_NUMBER() OVER (
                    PARTITION BY theme, url
                    ORDER BY
                        score DESC,
                        CASE WHEN COALESCE(published_at, '') = '' THEN 1 ELSE 0 END ASC,
                        published_at DESC,
                        url ASC,
                        scored_at DESC
                ) AS row_number_per_url
            FROM eligible
        )
        SELECT
            url,
            theme,
            score,
            reason,
            source,
            title,
            scrape_policy,
            COALESCE(published_at, '') AS published_at,
            discovered_at,
            scored_at
        FROM ranked
        WHERE row_number_per_url = 1
        ORDER BY
            score DESC,
            CASE WHEN COALESCE(published_at, '') = '' THEN 1 ELSE 0 END ASC,
            published_at DESC,
            url ASC
        LIMIT ?
        """,
        params,
    ).fetchall()

    return [
        {
            "url": str(row[0]),
            "theme": str(row[1]),
            "score": float(row[2]),
            "reason": str(row[3]),
            "source": str(row[4]),
            "title": str(row[5]),
            "scrape_policy": resolve_scrape_policy(row[6], fallback_to_full=True),
            "published_at": str(row[7]),
            "discovered_at": str(row[8]),
            "scored_at": str(row[9]),
        }
        for row in rows
    ]
