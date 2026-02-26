"""Phase 1 exit criteria verification tests."""

from __future__ import annotations

import sqlite3
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents import rss_collector
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
        entries_by_feed_url: dict[str, list[dict[str, Any]]],
        max_articles_per_run: int = 20,
    ) -> PipelineState:
        pipeline_config: dict[str, Any] = {
            "max_articles_per_run": max_articles_per_run,
            "database_path": "data/db/app.sqlite",
        }

        def _fake_fetch(feed_url: str) -> list[dict[str, Any]]:
            return entries_by_feed_url[feed_url]

        with (
            patch.object(rss_collector, "_project_root", return_value=root),
            patch.object(rss_collector, "_load_runtime_configs", return_value=(feeds, pipeline_config)),
            patch.object(rss_collector, "_fetch_feed_entries", side_effect=_fake_fetch),
            patch.object(rss_collector, "_now_utc_iso", return_value="2026-01-01T00:00:00Z"),
        ):
            return rss_collector.run(self._make_initial_state())

    def _rss_row_count(self, root: Path) -> int:
        db_path = root / "data" / "db" / "app.sqlite"
        with sqlite3.connect(db_path.as_posix()) as connection:
            row = connection.execute("SELECT COUNT(*) FROM rss_items").fetchone()
        return int(row[0]) if row else 0

    def test_exit_criteria_rss_fetching_verified(self) -> None:
        root = self._make_temp_root()
        feed_url = "https://feed.example.com/rss"
        final_state = self._run_collector(
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

        self.assertEqual(1, len(final_state["rss_items"]))
        self.assertEqual("https://example.com/alpha", final_state["rss_items"][0]["url"])

        counters = final_state["metrics"]["counters"]
        self.assertEqual(1, counters["rss_items_count"])
        self.assertEqual(1, counters["rss_feeds_succeeded"])
        self.assertEqual(0, counters["rss_feeds_failed"])
        self.assertEqual(0, counters["rss_duplicates_dropped"])
        self.assertEqual(1, self._rss_row_count(root))

        flags = final_state["metrics"]["flags"]
        self.assertFalse(flags["rss_collection_failed"])
        self.assertFalse(flags["rss_partial_success"])

    def test_exit_criteria_deduplication_verified(self) -> None:
        root = self._make_temp_root()
        feed_url = "https://feed.example.com/rss"
        final_state = self._run_collector(
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

        result_1 = self._run_collector(
            root=self._make_temp_root(),
            feeds=feeds,
            entries_by_feed_url=entries_by_feed_url,
        )
        result_2 = self._run_collector(
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


if __name__ == "__main__":
    unittest.main()
