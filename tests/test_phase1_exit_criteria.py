"""Phase 1 exit criteria verification tests."""

from __future__ import annotations

import os
import sqlite3
import shutil
import sys
import time
import types
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from agents import rss_collector
from core.common.utils import SCRAPE_POLICY_FULL, SCRAPE_POLICY_METADATA_ONLY, sha256_text
from core.persistence.db import initialize_database, sync_rss_item_policies_by_source
from core.state import PipelineState, make_initial_state


class Phase1ExitCriteriaTests(unittest.TestCase):
    def _make_temp_root(self) -> Path:
        base = Path(__file__).resolve().parent / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"phase1-exit-criteria-{time.time_ns()}"
        root.mkdir(parents=True, exist_ok=False)

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
        feeds: list[dict[str, Any]],
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
                        url, title, title_hash, source, scrape_policy, published_at, discovered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            row["url"],
                            row["title"],
                            row["title_hash"],
                            row["source"],
                            row.get("scrape_policy", SCRAPE_POLICY_FULL),
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
        scrape_policy: str = SCRAPE_POLICY_FULL,
    ) -> dict[str, str]:
        return {
            "url": url,
            "title": title,
            "title_hash": sha256_text(title.lower()),
            "source": source,
            "scrape_policy": scrape_policy,
            "published_at": published_at,
            "discovered_at": discovered_at,
        }

    def _db_contains_url(self, root: Path, url: str) -> bool:
        db_path = root / "data" / "db" / "app.sqlite"
        with sqlite3.connect(db_path.as_posix()) as connection:
            row = connection.execute("SELECT COUNT(*) FROM rss_items WHERE url = ?", (url,)).fetchone()
        return bool(row and int(row[0]) > 0)

    def _db_scrape_policy(self, root: Path, url: str) -> str:
        db_path = root / "data" / "db" / "app.sqlite"
        with sqlite3.connect(db_path.as_posix()) as connection:
            row = connection.execute(
                "SELECT scrape_policy FROM rss_items WHERE url = ?",
                (url,),
            ).fetchone()
        return str(row[0]) if row else ""

    def test_fetch_feed_entries_accepts_bozo_feed_when_entries_exist(self) -> None:
        feed_url = "https://feed.example.com/bozo-with-entries"

        class _FakeResponse:
            content = b"<rss/>"

            def raise_for_status(self) -> None:
                return None

        parsed = types.SimpleNamespace(
            bozo=1,
            bozo_exception=ValueError("not well-formed xml"),
            entries=[{"title": "Recovered", "link": "https://example.com/recovered"}],
        )
        fake_feedparser = types.SimpleNamespace(parse=lambda _content: parsed)

        with (
            patch.object(rss_collector.requests, "get", return_value=_FakeResponse()),
            patch.object(
                rss_collector.socket,
                "getaddrinfo",
                return_value=[(0, 0, 0, "", ("93.184.216.34", 0))],
            ),
            patch.dict(sys.modules, {"feedparser": fake_feedparser}),
        ):
            entries = rss_collector._fetch_feed_entries(feed_url)

        self.assertEqual(1, len(entries))
        self.assertEqual("https://example.com/recovered", entries[0]["link"])

    def test_fetch_feed_entries_rejects_unusable_bozo_feed_without_entries(self) -> None:
        feed_url = "https://feed.example.com/bozo-empty"

        class _FakeResponse:
            content = b"<rss/>"

            def raise_for_status(self) -> None:
                return None

        parsed = types.SimpleNamespace(
            bozo=1,
            bozo_exception=ValueError("not well-formed xml"),
            entries=[],
        )
        fake_feedparser = types.SimpleNamespace(parse=lambda _content: parsed)

        with (
            patch.object(rss_collector.requests, "get", return_value=_FakeResponse()),
            patch.object(
                rss_collector.socket,
                "getaddrinfo",
                return_value=[(0, 0, 0, "", ("93.184.216.34", 0))],
            ),
            patch.dict(sys.modules, {"feedparser": fake_feedparser}),
        ):
            with self.assertRaises(ValueError):
                rss_collector._fetch_feed_entries(feed_url)

    def test_fetch_feed_entries_blocks_non_http_scheme(self) -> None:
        fake_feedparser = types.SimpleNamespace(parse=lambda _content: None)
        with (
            patch.object(rss_collector.requests, "get") as requests_get,
            patch.dict(sys.modules, {"feedparser": fake_feedparser}),
        ):
            with self.assertRaises(ValueError):
                rss_collector._fetch_feed_entries("file:///tmp/feed.xml")
        requests_get.assert_not_called()

    def test_fetch_feed_entries_blocks_localhost_destination(self) -> None:
        fake_feedparser = types.SimpleNamespace(parse=lambda _content: None)
        with (
            patch.object(rss_collector.requests, "get") as requests_get,
            patch.dict(sys.modules, {"feedparser": fake_feedparser}),
        ):
            with self.assertRaises(ValueError):
                rss_collector._fetch_feed_entries("http://localhost/rss")
        requests_get.assert_not_called()

    def test_fetch_feed_entries_blocks_dns_resolution_with_private_ip(self) -> None:
        fake_feedparser = types.SimpleNamespace(parse=lambda _content: None)
        with (
            patch.object(rss_collector.requests, "get") as requests_get,
            patch.object(
                rss_collector.socket,
                "getaddrinfo",
                return_value=[(0, 0, 0, "", ("10.0.0.7", 0))],
            ),
            patch.dict(sys.modules, {"feedparser": fake_feedparser}),
        ):
            with self.assertRaises(ValueError):
                rss_collector._fetch_feed_entries("https://feed.example.com/rss")
        requests_get.assert_not_called()

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

    def test_feed_start_index_can_be_overridden_via_env(self) -> None:
        root = self._make_temp_root()
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

        with patch.dict(os.environ, {"VG_RSS_FEED_START_INDEX": "2"}, clear=False):
            final_state, fetch_calls = self._run_collector(
                root=root,
                feeds=feeds,
                entries_by_feed_url=entries_by_feed_url,
                max_articles_per_run=1,
                rss_skip_fetch_threshold=999,
                now_iso="2026-01-01T00:00:00Z",
            )

        self.assertEqual(2, final_state["metrics"]["counters"]["rss_feed_start_index"])
        self.assertEqual(["https://feed.example.com/c"], fetch_calls)
        self.assertEqual("https://feed.example.com/c", final_state["metrics"]["flags"]["rss_feed_rotation_first_feed_url"])

    def test_scrape_policy_fetch_path_persists_to_state_and_db(self) -> None:
        root = self._make_temp_root()
        feed_url = "https://feed.example.com/policy-fetch"
        final_state, _ = self._run_collector(
            root=root,
            feeds=[{"name": "Wired", "url": feed_url, "scrape_policy": SCRAPE_POLICY_METADATA_ONLY}],
            entries_by_feed_url={
                feed_url: [
                    {
                        "title": "Policy Story",
                        "link": "https://example.com/policy-fetch-story",
                    }
                ]
            },
            rss_skip_fetch_threshold=999,
        )

        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, final_state["rss_items"][0]["scrape_policy"])
        self.assertEqual(
            SCRAPE_POLICY_METADATA_ONLY,
            self._db_scrape_policy(root, "https://example.com/policy-fetch-story"),
        )

    def test_skip_fetch_path_preserves_scrape_policy_from_db(self) -> None:
        root = self._make_temp_root()
        self._seed_rss_items(
            root,
            [
                self._seed_row(
                    url=f"https://seed.example.com/policy-{index:03d}",
                    title=f"Policy Seed Story {index:03d}",
                    source="Wired",
                    scrape_policy=SCRAPE_POLICY_METADATA_ONLY,
                    published_at=f"2026-01-{(index % 28) + 1:02d}T00:00:00Z",
                    discovered_at="2026-01-05T00:00:00Z",
                )
                for index in range(205)
            ],
        )

        final_state, fetch_calls = self._run_collector(
            root=root,
            feeds=[{"name": "Wired", "url": "https://feed.example.com/wired", "scrape_policy": SCRAPE_POLICY_METADATA_ONLY}],
            entries_by_feed_url={},
            now_iso="2026-01-08T00:00:00Z",
        )

        self.assertEqual([], fetch_calls)
        self.assertTrue(final_state["metrics"]["flags"]["rss_fetch_skipped_threshold_hit"])
        self.assertEqual(
            50,
            sum(1 for item in final_state["rss_items"] if item["scrape_policy"] == SCRAPE_POLICY_METADATA_ONLY),
        )

    def test_db_policy_migration_and_sync_is_idempotent(self) -> None:
        root = self._make_temp_root()
        db_path = root / "data" / "db" / "app.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(db_path.as_posix()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE rss_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL UNIQUE,
                        title TEXT NOT NULL,
                        title_hash TEXT NOT NULL,
                        source TEXT NOT NULL,
                        published_at TEXT,
                        discovered_at TEXT NOT NULL
                    )
                    """
                )
                rows = [
                    (
                        "https://example.com/wired",
                        "Wired Story",
                        sha256_text("wired story"),
                        "Wired",
                        "2026-01-01T00:00:00Z",
                        "2026-01-01T00:00:00Z",
                    ),
                    (
                        "https://example.com/bloomberg",
                        "Bloomberg Story",
                        sha256_text("bloomberg story"),
                        "Bloomberg - Technology",
                        "2026-01-01T00:00:00Z",
                        "2026-01-01T00:00:00Z",
                    ),
                    (
                        "https://example.com/techcrunch",
                        "TechCrunch Story",
                        sha256_text("techcrunch story"),
                        "TechCrunch",
                        "2026-01-01T00:00:00Z",
                        "2026-01-01T00:00:00Z",
                    ),
                ]
                connection.executemany(
                    """
                    INSERT INTO rss_items (url, title, title_hash, source, published_at, discovered_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

        connection = initialize_database(db_path)
        try:
            # Run twice to verify idempotency.
            initialize_database(db_path).close()
            updated_count = sync_rss_item_policies_by_source(
                connection,
                {
                    "Wired": SCRAPE_POLICY_METADATA_ONLY,
                    "Bloomberg - Technology": SCRAPE_POLICY_METADATA_ONLY,
                    "TechCrunch": SCRAPE_POLICY_FULL,
                },
            )
            sync_rss_item_policies_by_source(
                connection,
                {
                    "Wired": SCRAPE_POLICY_METADATA_ONLY,
                    "Bloomberg - Technology": SCRAPE_POLICY_METADATA_ONLY,
                    "TechCrunch": SCRAPE_POLICY_FULL,
                },
            )
            rows = connection.execute(
                "SELECT source, scrape_policy FROM rss_items ORDER BY source ASC"
            ).fetchall()
        finally:
            connection.close()

        by_source = {str(row[0]): str(row[1]) for row in rows}
        self.assertGreaterEqual(updated_count, 2)
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, by_source["Wired"])
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, by_source["Bloomberg - Technology"])
        self.assertEqual(SCRAPE_POLICY_FULL, by_source["TechCrunch"])


if __name__ == "__main__":
    unittest.main()
