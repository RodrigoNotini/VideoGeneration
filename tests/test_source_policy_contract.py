"""Source access policy contract tests."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from agents import article_extractor
from core.common.utils import SCRAPE_POLICY_FULL, SCRAPE_POLICY_METADATA_ONLY
from core.config.config_loader import ConfigError, _validate_rss_config
from core.state import PipelineState, make_initial_state


class SourcePolicyConfigValidationTests(unittest.TestCase):
    def test_rss_config_fails_when_scrape_policy_is_missing(self) -> None:
        with self.assertRaises(ConfigError):
            _validate_rss_config(
                {
                    "feeds": [
                        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
                    ]
                }
            )

    def test_rss_config_fails_when_scrape_policy_is_invalid(self) -> None:
        with self.assertRaises(ConfigError):
            _validate_rss_config(
                {
                    "feeds": [
                        {
                            "name": "TechCrunch",
                            "url": "https://techcrunch.com/feed/",
                            "scrape_policy": "allow_all",
                        },
                    ]
                }
            )

    def test_rss_config_passes_with_valid_scrape_policy_enum(self) -> None:
        _validate_rss_config(
            {
                "feeds": [
                    {
                        "name": "Wired",
                        "url": "https://www.wired.com/feed/rss",
                        "scrape_policy": SCRAPE_POLICY_METADATA_ONLY,
                    },
                    {
                        "name": "TechCrunch",
                        "url": "https://techcrunch.com/feed/",
                        "scrape_policy": SCRAPE_POLICY_FULL,
                    },
                ]
            }
        )


class SourcePolicyPhase4GateTests(unittest.TestCase):
    def _make_state(self) -> PipelineState:
        return make_initial_state(
            topic="AI & Tech Daily Briefing",
            target_platform="youtube_shorts",
            target_duration_sec=45,
            version_info={
                "prompt_version": "phase4-article-extractor",
                "schema_version": "phase4-article-extractor",
                "template_version": "v1-placeholder",
                "model_version": "phase4-no-model",
            },
        )

    def test_metadata_only_policy_hard_blocks_extraction(self) -> None:
        state = self._make_state()
        selected_url = "https://example.com/blocked"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Blocked Story",
                "source": "Wired",
                "scrape_policy": SCRAPE_POLICY_METADATA_ONLY,
            }
        ]

        final_state = article_extractor.run(state)
        self.assertTrue(final_state["article"]["metadata_only"])
        self.assertEqual("policy_blocked", final_state["article"]["extraction_status"])
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, final_state["article"]["scrape_policy"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_policy_blocked_metadata_only"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_html_fetch_attempted"])
        self.assertEqual(1, final_state["metrics"]["counters"]["phase4_policy_blocked_count"])

    def test_full_scrape_allowed_policy_uses_normal_path(self) -> None:
        state = self._make_state()
        selected_url = "https://example.com/allowed"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Allowed Story",
                "source": "TechCrunch",
                "scrape_policy": SCRAPE_POLICY_FULL,
            }
        ]

        final_state = article_extractor.run(state)
        self.assertFalse(final_state["article"]["metadata_only"])
        self.assertEqual(
            "full_scrape_allowed_placeholder",
            final_state["article"]["extraction_status"],
        )
        self.assertEqual(SCRAPE_POLICY_FULL, final_state["article"]["scrape_policy"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_policy_blocked_metadata_only"])
        self.assertEqual(0, final_state["metrics"]["counters"]["phase4_policy_blocked_count"])

    def test_phase4_uses_fallback_lookup_when_policy_not_in_state(self) -> None:
        state = self._make_state()
        selected_url = "https://example.com/fallback"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Fallback Story",
                "source": "Unknown",
            }
        ]

        with patch.object(
            article_extractor,
            "_resolve_policy_from_db",
            return_value=SCRAPE_POLICY_METADATA_ONLY,
        ) as db_lookup:
            final_state = article_extractor.run(state)

        db_lookup.assert_called_once_with(selected_url)
        self.assertTrue(final_state["article"]["metadata_only"])
        self.assertEqual("policy_blocked", final_state["article"]["extraction_status"])

    def test_phase4_unresolved_policy_fails_closed(self) -> None:
        state = self._make_state()
        selected_url = "https://example.com/unresolved-policy"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Unresolved Policy Story",
                "source": "Unknown",
            }
        ]

        with patch.object(article_extractor, "_resolve_policy_from_db", return_value=None) as db_lookup:
            final_state = article_extractor.run(state)

        db_lookup.assert_called_once_with(selected_url)
        self.assertTrue(final_state["article"]["metadata_only"])
        self.assertEqual("policy_resolution_failed", final_state["article"]["extraction_status"])
        self.assertTrue(final_state["article"]["policy_resolution_failed"])
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, final_state["article"]["scrape_policy"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_policy_resolution_failed"])
        self.assertEqual(1, final_state["metrics"]["counters"]["phase4_policy_resolution_failed_count"])
        self.assertEqual(1, final_state["metrics"]["counters"]["phase4_policy_blocked_count"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_html_fetch_attempted"])

    def test_phase4_db_policy_lookup_fails_safe_when_db_init_fails(self) -> None:
        selected_url = "https://example.com/db-init-failure"
        with (
            patch.object(
                article_extractor,
                "load_all_configs",
                return_value={"pipeline": {"database_path": "data/db/app.sqlite"}},
            ),
            patch.object(article_extractor, "initialize_database", side_effect=RuntimeError("db down")),
        ):
            policy = article_extractor._resolve_policy_from_db(selected_url)
        self.assertIsNone(policy)

    def test_phase4_db_policy_lookup_fails_safe_when_query_fails(self) -> None:
        selected_url = "https://example.com/db-query-failure"
        fake_connection = Mock()
        with (
            patch.object(
                article_extractor,
                "load_all_configs",
                return_value={"pipeline": {"database_path": "data/db/app.sqlite"}},
            ),
            patch.object(article_extractor, "initialize_database", return_value=fake_connection),
            patch.object(
                article_extractor,
                "fetch_rss_item_scrape_policy_by_url",
                side_effect=RuntimeError("query failed"),
            ),
        ):
            policy = article_extractor._resolve_policy_from_db(selected_url)
        self.assertIsNone(policy)
        fake_connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
