"""Phase 1 exit criteria verification tests."""

from __future__ import annotations

import os
import sqlite3
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from agents import rss_collector
from core.common.utils import sha256_text
from core.persistence.db import initialize_database
from core.state import PipelineState, make_initial_state


class Phase1ExitCriteriaTests(unittest.TestCase):
    def _make_temp_root(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="phase1-exit-criteria-"))

        def _cleanup() -> None:
            for _ in range(5):
                try:
                    shutil.rmtree(root)
                    return
                except PermissionError:
                    time.sleep(0.05)
            shutil.rmtree(root, ignore_errors=True)

        self.addCleanup(_cleanup)
        return root

    def _make_initial_state(self) -> PipelineState:
        return make_initial_state(
            topic="AI & Tech Daily Briefing",
            target_platform="youtube_shorts",
            target_duration_sec=45,
            version_info={
                "prompt_version": "phase1-rss-discovery",
                "schema_version": "phase1-rss-discovery",
                "template_version": "v1-placeholder",
                "model_version": "phase1-no-model",
            },
        )

    def _run_collector(
        self,
        *,
        root: Path,
        feeds: list[dict[str, str]],
        entries_by_feed_url: dict[str, list[dict[str, Any]]] | None = None,
        max_articles_per_run: int = 50,
        rss_skip_fetch_threshold: int = 200,
        rss_retention_days: int = 7,
        rss_feed_rotation_basis: str = "utc_date",
        now_iso: str = "2026-01-01T00:00:00Z",
        fetch_side_effect: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> tuple[PipelineState, list[str]]:
        pipeline_config: dict[str, Any] = {
            "max_articles_per_run": max_articles_per_run,
            "rss_skip_fetch_threshold": rss_skip_fetch_threshold,
            "rss_retention_days": rss_retention_days,
            "rss_feed_rotation_basis": rss_feed_rotation_basis,
            "database_path": "data/db/app.sqlite",
        }
        feed_entries = entries_by_feed_url or {}
        fetch_calls: list[str] = []

        def _fake_fetch(feed_url: str) -> list[dict[str, Any]]:
            fetch_calls.append(feed_url)
            if fetch_side_effect is not None:
                return fetch_side_effect(feed_url)
            return feed_entries.get(feed_url, [])

        with (
            patch.object(rss_collector, "_project_root", return_value=root),
            patch.object(rss_collector, "_load_runtime_configs", return_value=(feeds, pipeline_config)),
            patch.object(rss_collector, "_fetch_feed_entries", side_effect=_fake_fetch),
            patch.object(rss_collector, "_now_utc_iso", return_value=now_iso),
        ):
            final_state = rss_collector.run(self._make_initial_state())
        return final_state, fetch_calls

    def _rss_row_count(self, root: Path) -> int:
        db_path = root / "data" / "db" / "app.sqlite"
        with sqlite3.connect(db_path.as_posix()) as connection:
            row = connection.execute("SELECT COUNT(*) FROM rss_items").fetchone()
        return int(row[0]) if row else 0

    def _seed_rss_items(self, root: Path, rows: list[dict[str, str]]) -> None:
        db_path = root / "data" / "db" / "app.sqlite"
        connection = initialize_database(db_path)
        try:
            with connection:
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO rss_items (
                        url, title, title_hash, source, published_at, discovered_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            row["url"],
                            row["title"],
                            row["title_hash"],
                            row["source"],
                            row["published_at"],
                            row["discovered_at"],
                        )
                        for row in rows
                    ],
                )
        finally:
            connection.close()

    def _seed_row(
        self,
        *,
        url: str,
        title: str,
        source: str,
        published_at: str,
        discovered_at: str,
    ) -> dict[str, str]:
        return {
            "url": url,
            "title": title,
            "title_hash": sha256_text(title.lower()),
            "source": source,
            "published_at": published_at,
            "discovered_at": discovered_at,
        }

    def _db_contains_url(self, root: Path, url: str) -> bool:
        db_path = root / "data" / "db" / "app.sqlite"
        with sqlite3.connect(db_path.as_posix()) as connection:
            row = connection.execute("SELECT COUNT(*) FROM rss_items WHERE url = ?", (url,)).fetchone()
        return bool(row and int(row[0]) > 0)

    def test_exit_criteria_rss_fetching_verified(self) -> None:
        root = self._make_temp_root()
        feed_url = "https://feed.example.com/rss"
        final_state, fetch_calls = self._run_collector(
            root=root,
            feeds=[{"name": "Example Feed", "url": feed_url}],
            entries_by_feed_url={
                feed_url: [
                    {
                        "title": "Alpha AI News",
                        "link": "https://example.com/alpha",
                        "published": "Tue, 02 Jan 2024 00:00:00 GMT",
                    }
                ]
            },
        )

        self.assertEqual([feed_url], fetch_calls)
        self.assertEqual(1, len(final_state["rss_items"]))
        self.assertEqual("https://example.com/alpha", final_state["rss_items"][0]["url"])

        counters = final_state["metrics"]["counters"]
        self.assertEqual(1, counters["rss_items_count"])
        self.assertEqual(50, counters["rss_items_target_count"])
        self.assertEqual(1, counters["rss_feeds_succeeded"])
        self.assertEqual(0, counters["rss_feeds_failed"])
        self.assertEqual(0, counters["rss_duplicates_dropped"])
        self.assertEqual(0, counters["rss_retention_deleted_count"])
        self.assertEqual(0, counters["rss_inventory_count_after_cleanup"])
        self.assertEqual(1, self._rss_row_count(root))

        flags = final_state["metrics"]["flags"]
        self.assertFalse(flags["rss_collection_failed"])
        self.assertFalse(flags["rss_partial_success"])
        self.assertFalse(flags["rss_fetch_skipped_threshold_hit"])

    def test_exit_criteria_deduplication_verified(self) -> None:
        root = self._make_temp_root()
        feed_url = "https://feed.example.com/rss"
        final_state, _ = self._run_collector(
            root=root,
            feeds=[{"name": "Example Feed", "url": feed_url}],
            entries_by_feed_url={
                feed_url: [
                    {
                        "title": "Same URL Original",
                        "link": "https://example.com/story?utm_source=rss",
                    },
                    {
                        "title": "Different Title Same URL",
                        "link": "https://example.com/story",
                    },
                    {
                        "title": "Same Title",
                        "link": "https://example.com/unique-1",
                    },
                    {
                        "title": "  same   title  ",
                        "link": "https://example.net/unique-2",
                    },
                    {
                        "title": "Distinct Entry",
                        "link": "https://example.org/distinct?fbclid=tracking",
                    },
                ]
            },
        )

        self.assertEqual(3, len(final_state["rss_items"]))
        collected_urls = {item["url"] for item in final_state["rss_items"]}
        self.assertSetEqual(
            {
                "https://example.com/story",
                "https://example.com/unique-1",
                "https://example.org/distinct",
            },
            collected_urls,
        )

        counters = final_state["metrics"]["counters"]
        self.assertEqual(2, counters["rss_duplicates_dropped"])
        self.assertEqual(3, counters["rss_items_count"])
        self.assertEqual(3, self._rss_row_count(root))

    def test_exit_criteria_deterministic_ordering_verified(self) -> None:
        feeds = [
            {"name": "Zeta Feed", "url": "https://feed.example.com/zeta"},
            {"name": "Alpha Feed", "url": "https://feed.example.com/alpha"},
        ]
        entries_by_feed_url = {
            "https://feed.example.com/zeta": [
                {
                    "title": "Z Story",
                    "link": "https://example.com/z-story",
                    "published": "Mon, 01 Jan 2024 00:00:00 GMT",
                },
                {
                    "title": "No Date Story",
                    "link": "https://example.com/no-date",
                },
            ],
            "https://feed.example.com/alpha": [
                {
                    "title": "A Story B",
                    "link": "https://example.com/a-story-b",
                    "published": "Tue, 02 Jan 2024 00:00:00 GMT",
                },
                {
                    "title": "A Story A",
                    "link": "https://example.com/a-story-a",
                    "published": "Tue, 02 Jan 2024 00:00:00 GMT",
                },
            ],
        }

        result_1, _ = self._run_collector(
            root=self._make_temp_root(),
            feeds=feeds,
            entries_by_feed_url=entries_by_feed_url,
        )
        result_2, _ = self._run_collector(
            root=self._make_temp_root(),
            feeds=feeds,
            entries_by_feed_url=entries_by_feed_url,
        )

        expected_order = [
            "https://example.com/a-story-a",
            "https://example.com/a-story-b",
            "https://example.com/z-story",
            "https://example.com/no-date",
        ]
        ordered_urls_1 = [item["url"] for item in result_1["rss_items"]]
        ordered_urls_2 = [item["url"] for item in result_2["rss_items"]]

        self.assertEqual(expected_order, ordered_urls_1)
        self.assertEqual(expected_order, ordered_urls_2)
        self.assertEqual(result_1["rss_items"], result_2["rss_items"])

    def test_retention_cleanup_runs_before_threshold_check(self) -> None:
        root = self._make_temp_root()
        old_rows = [
            self._seed_row(
                url=f"https://seed.example.com/old-{index}",
                title=f"Old Story {index}",
                source="Seed",
                published_at="2025-12-01T00:00:00Z",
                discovered_at="2025-12-01T00:00:00Z",
            )
            for index in range(201)
        ]
        self._seed_rss_items(root, old_rows)

        feed_url = "https://feed.example.com/retention"
        final_state, fetch_calls = self._run_collector(
            root=root,
            feeds=[{"name": "Retention Feed", "url": feed_url}],
            entries_by_feed_url={
                feed_url: [
                    {
                        "title": "Fresh Story",
                        "link": "https://example.com/fresh-after-cleanup",
                        "published": "Wed, 08 Jan 2026 00:00:00 GMT",
                    }
                ]
            },
            now_iso="2026-01-08T00:00:00Z",
        )

        self.assertEqual([feed_url], fetch_calls)
        self.assertFalse(final_state["metrics"]["flags"]["rss_fetch_skipped_threshold_hit"])
        self.assertEqual(201, final_state["metrics"]["counters"]["rss_retention_deleted_count"])
        self.assertEqual(0, final_state["metrics"]["counters"]["rss_inventory_count_after_cleanup"])

    def test_retention_cleanup_keeps_cutoff_boundary(self) -> None:
        root = self._make_temp_root()
        kept_url = "https://seed.example.com/kept-boundary"
        removed_url = "https://seed.example.com/removed-old"
        self._seed_rss_items(
            root,
            [
                self._seed_row(
                    url=kept_url,
                    title="Boundary Story",
                    source="Seed",
                    published_at="2026-01-01T00:00:00Z",
                    discovered_at="2026-01-01T00:00:00Z",
                ),
                self._seed_row(
                    url=removed_url,
                    title="Old Story",
                    source="Seed",
                    published_at="2025-12-31T00:00:00Z",
                    discovered_at="2025-12-31T23:59:59Z",
                ),
            ],
        )

        feed_url = "https://feed.example.com/boundary"
        final_state, _ = self._run_collector(
            root=root,
            feeds=[{"name": "Boundary Feed", "url": feed_url}],
            entries_by_feed_url={
                feed_url: [
                    {
                        "title": "Fresh Story",
                        "link": "https://example.com/new-boundary-story",
                    }
                ]
            },
            rss_skip_fetch_threshold=999,
            now_iso="2026-01-08T00:00:00Z",
        )

        counters = final_state["metrics"]["counters"]
        self.assertEqual(1, counters["rss_retention_deleted_count"])
        self.assertEqual(1, counters["rss_inventory_count_after_cleanup"])
        self.assertTrue(self._db_contains_url(root, kept_url))
        self.assertFalse(self._db_contains_url(root, removed_url))

    def test_skip_fetch_when_inventory_above_threshold(self) -> None:
        root = self._make_temp_root()
        seeded_rows = [
            self._seed_row(
                url=f"https://seed.example.com/story-{index:03d}",
                title=f"Seed Story {index:03d}",
                source="Seed",
                published_at=f"2026-01-{(index % 28) + 1:02d}T00:00:00Z",
                discovered_at="2026-01-05T00:00:00Z",
            )
            for index in range(205)
        ]
        self._seed_rss_items(root, seeded_rows)

        feeds = [
            {"name": "A", "url": "https://feed.example.com/a"},
            {"name": "B", "url": "https://feed.example.com/b"},
        ]
        final_state, fetch_calls = self._run_collector(
            root=root,
            feeds=feeds,
            entries_by_feed_url={},
            now_iso="2026-01-08T00:00:00Z",
        )

        self.assertEqual([], fetch_calls)
        self.assertTrue(final_state["metrics"]["flags"]["rss_fetch_skipped_threshold_hit"])
        self.assertEqual(50, len(final_state["rss_items"]))
        self.assertEqual(205, final_state["metrics"]["counters"]["rss_inventory_count_after_cleanup"])

    def test_threshold_is_strictly_greater_than(self) -> None:
        root = self._make_temp_root()
        seeded_rows = [
            self._seed_row(
                url=f"https://seed.example.com/story-{index:03d}",
                title=f"Seed Story {index:03d}",
                source="Seed",
                published_at="2026-01-05T00:00:00Z",
                discovered_at="2026-01-05T00:00:00Z",
            )
            for index in range(200)
        ]
        self._seed_rss_items(root, seeded_rows)

        feed_url = "https://feed.example.com/threshold"
        final_state, fetch_calls = self._run_collector(
            root=root,
            feeds=[{"name": "Threshold Feed", "url": feed_url}],
            entries_by_feed_url={
                feed_url: [
                    {
                        "title": "Threshold Fresh",
                        "link": "https://example.com/threshold-fresh",
                    }
                ]
            },
            now_iso="2026-01-08T00:00:00Z",
        )

        self.assertEqual([feed_url], fetch_calls)
        self.assertFalse(final_state["metrics"]["flags"]["rss_fetch_skipped_threshold_hit"])

    def test_max_articles_cap_applies_to_fetch_and_skip_paths(self) -> None:
        fetch_root = self._make_temp_root()
        feed_url = "https://feed.example.com/cap-fetch"
        entries = [
            {
                "title": f"Story {index}",
                "link": f"https://example.com/cap-fetch-{index}",
                "published": "Tue, 02 Jan 2024 00:00:00 GMT",
            }
            for index in range(80)
        ]
        fetch_state, _ = self._run_collector(
            root=fetch_root,
            feeds=[{"name": "Cap Feed", "url": feed_url}],
            entries_by_feed_url={feed_url: entries},
            rss_skip_fetch_threshold=999,
        )
        self.assertEqual(50, len(fetch_state["rss_items"]))

        skip_root = self._make_temp_root()
        self._seed_rss_items(
            skip_root,
            [
                self._seed_row(
                    url=f"https://seed.example.com/cap-skip-{index:03d}",
                    title=f"Cap Skip Story {index:03d}",
                    source="Seed",
                    published_at="2026-01-05T00:00:00Z",
                    discovered_at="2026-01-05T00:00:00Z",
                )
                for index in range(260)
            ],
        )
        skip_state, _ = self._run_collector(
            root=skip_root,
            feeds=[{"name": "Unused Feed", "url": "https://feed.example.com/unused"}],
            entries_by_feed_url={},
        )
        self.assertEqual(50, len(skip_state["rss_items"]))

    def test_feed_rotation_is_deterministic_and_rotates_by_day(self) -> None:
        feeds = [
            {"name": "Feed A", "url": "https://feed.example.com/a"},
            {"name": "Feed B", "url": "https://feed.example.com/b"},
            {"name": "Feed C", "url": "https://feed.example.com/c"},
        ]
        entries_by_feed_url = {
            "https://feed.example.com/a": [{"title": "A1", "link": "https://example.com/a1"}],
            "https://feed.example.com/b": [{"title": "B1", "link": "https://example.com/b1"}],
            "https://feed.example.com/c": [{"title": "C1", "link": "https://example.com/c1"}],
        }

        first_state, first_calls = self._run_collector(
            root=self._make_temp_root(),
            feeds=feeds,
            entries_by_feed_url=entries_by_feed_url,
            max_articles_per_run=1,
            rss_skip_fetch_threshold=999,
            now_iso="2026-01-04T00:00:00Z",
        )
        second_state, second_calls = self._run_collector(
            root=self._make_temp_root(),
            feeds=feeds,
            entries_by_feed_url=entries_by_feed_url,
            max_articles_per_run=1,
            rss_skip_fetch_threshold=999,
            now_iso="2026-01-04T00:00:00Z",
        )
        third_state, third_calls = self._run_collector(
            root=self._make_temp_root(),
            feeds=feeds,
            entries_by_feed_url=entries_by_feed_url,
            max_articles_per_run=1,
            rss_skip_fetch_threshold=999,
            now_iso="2026-01-01T00:00:00Z",
        )

        self.assertEqual(
            first_state["metrics"]["counters"]["rss_feed_start_index"],
            second_state["metrics"]["counters"]["rss_feed_start_index"],
        )
        self.assertEqual(first_calls, second_calls)
        self.assertEqual(1, first_state["metrics"]["counters"]["rss_feed_start_index"])
        self.assertEqual(["https://feed.example.com/b"], first_calls)
        self.assertEqual("https://feed.example.com/b", first_state["metrics"]["flags"]["rss_feed_rotation_first_feed_url"])

        self.assertEqual(0, third_state["metrics"]["counters"]["rss_feed_start_index"])
        self.assertEqual(["https://feed.example.com/a"], third_calls)

    def test_max_articles_can_be_overridden_via_env(self) -> None:
        root = self._make_temp_root()
        feed_url = "https://feed.example.com/override"
        entries = [
            {"title": f"Story {index}", "link": f"https://example.com/override-{index}"}
            for index in range(5)
        ]

        with patch.dict(os.environ, {"VG_MAX_ARTICLES_PER_RUN": "1"}, clear=False):
            final_state, _ = self._run_collector(
                root=root,
                feeds=[{"name": "Override Feed", "url": feed_url}],
                entries_by_feed_url={feed_url: entries},
                max_articles_per_run=50,
                rss_skip_fetch_threshold=999,
            )

        self.assertEqual(1, len(final_state["rss_items"]))
        self.assertEqual(1, final_state["metrics"]["counters"]["rss_items_target_count"])


if __name__ == "__main__":
    unittest.main()
