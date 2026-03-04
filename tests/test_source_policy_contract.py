"""Source access policy contract tests."""

from __future__ import annotations

import json
import shutil
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from agents import article_extractor, theme_url_selector
from core.common.utils import SCRAPE_POLICY_FULL, SCRAPE_POLICY_METADATA_ONLY, sha256_text
from core.config.config_loader import ConfigError, _validate_rss_config
from core.persistence.db import initialize_database, insert_theme_scores
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
    def _make_temp_root(self) -> Path:
        base = Path(__file__).resolve().parent / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"phase4-policy-contract-{time.time_ns()}"
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

    def test_metadata_only_policy_no_longer_blocks_extraction(self) -> None:
        root = self._make_temp_root()
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

        with (
            patch.object(article_extractor, "_project_root", return_value=root),
            patch.object(
                article_extractor,
                "_fetch_html",
                return_value=(
                    "<html><head><title>Allowed</title></head><body>"
                    "<article><p>This is a clean paragraph for extraction testing with enough content.</p></article>"
                    "</body></html>"
                ),
            ) as fetch_html,
        ):
            final_state = article_extractor.run(state)
        self.assertFalse(final_state["article"]["metadata_only"])
        self.assertEqual("extracted", final_state["article"]["extraction_status"])
        self.assertEqual(SCRAPE_POLICY_FULL, final_state["article"]["scrape_policy"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_policy_blocked_metadata_only"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_html_fetch_attempted"])
        self.assertEqual(0, final_state["metrics"]["counters"]["phase4_policy_blocked_count"])
        fetch_html.assert_called_once_with(selected_url)

    def test_full_scrape_allowed_policy_uses_normal_path(self) -> None:
        root = self._make_temp_root()
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

        html = (
            "<html><head><title>Allowed Story</title></head><body>"
            "<article><p>This is a clean paragraph for extraction testing with enough content.</p></article>"
            "</body></html>"
        )
        with (
            patch.object(article_extractor, "_project_root", return_value=root),
            patch.object(article_extractor, "_fetch_html", return_value=html) as fetch_html,
        ):
            final_state = article_extractor.run(state)
        self.assertFalse(final_state["article"]["metadata_only"])
        self.assertEqual("extracted", final_state["article"]["extraction_status"])
        self.assertEqual(SCRAPE_POLICY_FULL, final_state["article"]["scrape_policy"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_policy_blocked_metadata_only"])
        self.assertEqual(0, final_state["metrics"]["counters"]["phase4_policy_blocked_count"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_html_fetch_attempted"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_html_fetch_succeeded"])
        fetch_html.assert_called_once_with(selected_url)

    def test_phase4_does_not_use_policy_lookup_when_policy_not_in_state(self) -> None:
        root = self._make_temp_root()
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
            with (
                patch.object(article_extractor, "_project_root", return_value=root),
                patch.object(
                    article_extractor,
                    "_fetch_html",
                    return_value=(
                        "<html><head><title>Allowed</title></head><body>"
                        "<article><p>This is a clean paragraph for extraction testing with enough content.</p></article>"
                        "</body></html>"
                    ),
                ),
            ):
                final_state = article_extractor.run(state)

        db_lookup.assert_not_called()
        self.assertEqual("extracted", final_state["article"]["extraction_status"])

    def test_phase4_unresolved_policy_lookup_is_ignored(self) -> None:
        root = self._make_temp_root()
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
            with (
                patch.object(article_extractor, "_project_root", return_value=root),
                patch.object(
                    article_extractor,
                    "_fetch_html",
                    return_value=(
                        "<html><head><title>Allowed</title></head><body>"
                        "<article><p>This is a clean paragraph for extraction testing with enough content.</p></article>"
                        "</body></html>"
                    ),
                ),
            ):
                final_state = article_extractor.run(state)

        db_lookup.assert_not_called()
        self.assertEqual("extracted", final_state["article"]["extraction_status"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_policy_resolution_failed"])
        self.assertEqual(0, final_state["metrics"]["counters"]["phase4_policy_resolution_failed_count"])
        self.assertEqual(0, final_state["metrics"]["counters"]["phase4_policy_blocked_count"])

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


class SourcePolicyPhase2ReplacementContractTests(unittest.TestCase):
    def _make_temp_root(self) -> Path:
        base = Path(__file__).resolve().parent / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"phase2-policy-contract-{time.time_ns()}"
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

    def _make_state(self) -> PipelineState:
        state = make_initial_state(
            topic="AI & Tech Daily Briefing",
            target_platform="youtube_shorts",
            target_duration_sec=45,
            version_info={
                "prompt_version": "phase2-theme-selector-v1",
                "schema_version": "phase2-theme-selector-v1",
                "template_version": "v1-placeholder",
                "model_version": "phase2-gpt-4.1-mini",
            },
        )
        state["rss_items"] = [
            {
                "url": "https://example.com/a",
                "title": "Story A",
                "source": "Feed",
                "scrape_policy": SCRAPE_POLICY_FULL,
                "published_at": "2026-01-01T00:00:00Z",
                "discovered_at": "2026-01-01T00:00:00Z",
            },
            {
                "url": "https://example.com/b",
                "title": "Story B",
                "source": "Feed",
                "scrape_policy": SCRAPE_POLICY_FULL,
                "published_at": "2026-01-01T00:00:00Z",
                "discovered_at": "2026-01-01T00:00:00Z",
            },
        ]
        return state

    def _pipeline_config(self) -> dict[str, object]:
        return {
            "theme": "AI",
            "output_dir": "outputs",
            "database_path": "data/db/app.sqlite",
            "phase2_selector": {
                "model": "gpt-4.1-mini",
                "prompt_version": "phase2-theme-selector-v1",
                "target_count": 2,
                "lower_bound": 1,
                "upper_bound": 2,
                "tie_break_policy": "published_at_desc_then_canonical_url_asc",
                "replacement_enabled": True,
                "replacement_worst_count": 1,
                "replacement_score_tol": 0.55,
                "replacement_freshness_days": 7,
                "replacement_history_semantics": "max_per_url_theme",
                "deterministic": {"temperature": 0.0, "top_p": 1.0},
            },
            "versions": {
                "prompt_version": "phase2-theme-selector-v1",
                "schema_version": "phase2-theme-selector-v1",
                "template_version": "v1-placeholder",
                "model_version": "phase2-gpt-4.1-mini",
            },
        }

    def _openai_config(self) -> dict[str, object]:
        return {
            "api_key_env_var": "OPENAI_API_KEY",
            "models": {
                "theme_selector": "gpt-4.1-mini",
                "embeddings": "unused",
                "script_writer": "unused",
                "image_generator": "unused",
                "tts": "unused",
            },
        }

    def _seed_metadata_and_history(self, root: Path) -> None:
        db_path = root / "data" / "db" / "app.sqlite"
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
                            "https://example.com/a",
                            "Story A",
                            sha256_text("story a"),
                            "Feed",
                            SCRAPE_POLICY_FULL,
                            "2026-01-01T00:00:00Z",
                            "2026-01-01T00:00:00Z",
                        ),
                        (
                            "https://example.com/b",
                            "Story B",
                            sha256_text("story b"),
                            "Feed",
                            SCRAPE_POLICY_FULL,
                            "2026-01-01T00:00:00Z",
                            "2026-01-01T00:00:00Z",
                        ),
                        (
                            "https://history.example.com/policy-replacement",
                            "Policy Replacement",
                            sha256_text("policy replacement"),
                            "History",
                            SCRAPE_POLICY_METADATA_ONLY,
                            "2026-01-20T00:00:00Z",
                            now_iso,
                        ),
                    ],
                )
            insert_theme_scores(
                connection,
                [
                    {
                        "url": "https://history.example.com/policy-replacement",
                        "theme": "AI",
                        "score": 0.99,
                        "reason": "metadata-only replacement",
                        "source": "History",
                        "published_at": "2026-01-20T00:00:00Z",
                        "discovered_at": now_iso,
                        "run_id": "seed-history",
                        "scored_at": now_iso,
                        "model_name": "seed-model",
                        "prompt_version": "seed-prompt",
                    }
                ],
            )
        finally:
            connection.close()

    def test_phase2_replacement_preserves_scrape_policy_from_db_metadata(self) -> None:
        root = self._make_temp_root()
        self._seed_metadata_and_history(root)

        def _scores(**kwargs: object) -> tuple[dict[int, tuple[float, str]], dict[str, object]]:
            candidates = kwargs["candidates"]
            assert isinstance(candidates, list)
            score_by_url = {
                "https://example.com/a": 0.95,
                "https://example.com/b": 0.60,
            }
            return (
                {
                    candidate.item_id: (score_by_url[candidate.url], "mock score")
                    for candidate in candidates
                },
                {
                    "retry_count": 0,
                    "fallback_used": False,
                    "model_latency_ms": 5,
                    "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        with (
            patch.object(theme_url_selector, "_project_root", return_value=root),
            patch.object(
                theme_url_selector,
                "_load_runtime_configs",
                return_value=(self._pipeline_config(), self._openai_config()),
            ),
            patch.object(theme_url_selector, "_score_candidates", side_effect=_scores),
        ):
            final_state = theme_url_selector.run(self._make_state())

        selected_item = next(
            item
            for item in final_state["ranked_items"]
            if item["url"] == "https://history.example.com/policy-replacement"
        )
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, selected_item["scrape_policy"])

        artifact = json.loads((root / "outputs" / "theme_selected_urls.json").read_text(encoding="utf-8"))
        artifact_item = next(
            item
            for item in artifact["selected_items"]
            if item["url"] == "https://history.example.com/policy-replacement"
        )
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, artifact_item["scrape_policy"])


if __name__ == "__main__":
    unittest.main()
