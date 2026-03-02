"""Phase 2 exit criteria verification tests."""

from __future__ import annotations

import json
import shutil
import sys
import time
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents import relevance_ranker, theme_url_selector
from agents.reporter import Reporter, run as reporter_agent_run
from core.common.utils import SCRAPE_POLICY_FULL, SCRAPE_POLICY_METADATA_ONLY
from core.state import PipelineState, make_initial_state
from graphs import news_to_video_graph


class Phase2ThemeSelectorTests(unittest.TestCase):
    def _make_temp_root(self) -> Path:
        base = Path(__file__).resolve().parent / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"phase2-selector-{time.time_ns()}"
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
                "prompt_version": "phase2-theme-selector-v1",
                "schema_version": "phase2-theme-selector-v1",
                "template_version": "v1-placeholder",
                "model_version": "phase2-gpt-4.1-mini",
            },
        )

    def _make_pipeline_config(
        self,
        theme: str,
        *,
        selector_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = {
            "theme": theme,
            "output_dir": "outputs",
            "phase2_selector": {
                "model": "gpt-4.1-mini",
                "prompt_version": "phase2-theme-selector-v1",
                "target_count": 30,
                "lower_bound": 25,
                "upper_bound": 35,
                "tie_break_policy": "published_at_desc_then_canonical_url_asc",
                "deterministic": {"temperature": 0.0, "top_p": 1.0},
            },
            "versions": {
                "prompt_version": "phase2-theme-selector-v1",
                "schema_version": "phase2-theme-selector-v1",
                "template_version": "v1-placeholder",
                "model_version": "phase2-gpt-4.1-mini",
            },
        }
        if selector_overrides:
            config["phase2_selector"].update(selector_overrides)
        return config

    def _make_openai_config(self) -> dict[str, Any]:
        return {
            "api_key_env_var": "OPENAI_API_KEY",
            "models": {
                "theme_selector": "gpt-4.1-mini",
                "embeddings": "placeholder",
                "script_writer": "placeholder",
                "image_generator": "placeholder",
                "tts": "placeholder",
            },
        }

    def _make_rss_items(self, count: int) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for index in range(count):
            day = (index % 28) + 1
            items.append(
                {
                    "url": f"https://example.com/story-{index:03d}",
                    "title": f"Story {index:03d}",
                    "source": "ExampleFeed",
                    "scrape_policy": SCRAPE_POLICY_FULL,
                    "published_at": f"2026-01-{day:02d}T00:00:00Z",
                }
            )
        return items

    def _default_score_candidates(
        self,
        *,
        theme: str,
        candidates: list[theme_url_selector.Candidate],
        model_name: str,
        temperature: float,
        top_p: float,
        prompt_version: str,
        openai_api_key_env_var: str,
    ) -> tuple[dict[int, tuple[float, str]], dict[str, Any]]:
        del theme, model_name, temperature, top_p, prompt_version, openai_api_key_env_var
        scores = {
            candidate.item_id: (round(1.0 - (candidate.item_id / 1000), 6), "Mocked model score.")
            for candidate in candidates
        }
        return scores, {
            "retry_count": 0,
            "fallback_used": False,
            "model_latency_ms": 5,
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

    def _run_selector(
        self,
        *,
        root: Path,
        theme: str,
        rss_items: list[dict[str, Any]],
        score_side_effect: Any | None = None,
        pipeline_config: dict[str, Any] | None = None,
    ) -> PipelineState:
        state = self._make_initial_state()
        state["rss_items"] = rss_items
        score_impl = score_side_effect or self._default_score_candidates
        selector_config = pipeline_config or self._make_pipeline_config(theme)

        with (
            patch.object(theme_url_selector, "_project_root", return_value=root),
            patch.object(
                theme_url_selector,
                "_load_runtime_configs",
                return_value=(selector_config, self._make_openai_config()),
            ),
            patch.object(theme_url_selector, "_score_candidates", side_effect=score_impl),
        ):
            return theme_url_selector.run(state)

    def test_selector_accepts_ai_and_tech_only(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(3)

        ai_state = self._run_selector(root=root, theme="AI", rss_items=rss_items)
        tech_state = self._run_selector(root=root, theme="Tech", rss_items=rss_items)

        self.assertEqual(3, len(ai_state["ranked_items"]))
        self.assertEqual(3, len(tech_state["ranked_items"]))

        with self.assertRaises(theme_url_selector.ThemeURLSelectorError) as context:
            self._run_selector(root=root, theme="Finance", rss_items=rss_items)
        error_payload = json.loads(str(context.exception))
        self.assertEqual(2, error_payload["phase"])
        self.assertEqual("invalid_theme", error_payload["code"])

    def test_selector_rejects_unsupported_tie_break_policy(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(8)
        bad_config = self._make_pipeline_config(
            "AI",
            selector_overrides={"tie_break_policy": "unsupported-policy"},
        )

        with self.assertRaises(theme_url_selector.ThemeURLSelectorError) as context:
            self._run_selector(
                root=root,
                theme="AI",
                rss_items=rss_items,
                pipeline_config=bad_config,
            )
        error_payload = json.loads(str(context.exception))
        self.assertEqual("invalid_tie_break_policy", error_payload["code"])

    def test_selector_rejects_invalid_target_bounds(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(8)
        bad_config = self._make_pipeline_config(
            "AI",
            selector_overrides={"target_count": 40, "lower_bound": 25, "upper_bound": 35},
        )

        with self.assertRaises(theme_url_selector.ThemeURLSelectorError) as context:
            self._run_selector(
                root=root,
                theme="AI",
                rss_items=rss_items,
                pipeline_config=bad_config,
            )
        error_payload = json.loads(str(context.exception))
        self.assertEqual("invalid_selector_config", error_payload["code"])

    def test_deterministic_ordering_with_identical_scores(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(8)

        def _same_scores(**kwargs: Any) -> tuple[dict[int, tuple[float, str]], dict[str, Any]]:
            candidates = kwargs["candidates"]
            scores = {candidate.item_id: (0.8, "Equal score") for candidate in candidates}
            metadata = {
                "retry_count": 0,
                "fallback_used": False,
                "model_latency_ms": 5,
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return scores, metadata

        run_a = self._run_selector(root=root, theme="AI", rss_items=rss_items, score_side_effect=_same_scores)
        run_b = self._run_selector(root=root, theme="AI", rss_items=rss_items, score_side_effect=_same_scores)

        self.assertEqual(run_a["ranked_items"], run_b["ranked_items"])

    def test_stability_overlap_under_controlled_score_variance(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(50)

        def _stable_scores_run_a(**kwargs: Any) -> tuple[dict[int, tuple[float, str]], dict[str, Any]]:
            candidates = kwargs["candidates"]
            scores = {
                candidate.item_id: (round(1.0 - (candidate.item_id / 1000), 6), "Run A score")
                for candidate in candidates
            }
            metadata = {
                "retry_count": 0,
                "fallback_used": False,
                "model_latency_ms": 5,
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return scores, metadata

        def _stable_scores_run_b(**kwargs: Any) -> tuple[dict[int, tuple[float, str]], dict[str, Any]]:
            candidates = kwargs["candidates"]
            scores = {
                candidate.item_id: (round(1.0 - (candidate.item_id / 1000), 6), "Run B score")
                for candidate in candidates
            }
            # Controlled variance around the target-count boundary to mimic live-model jitter.
            scores[30] = (scores[31][0] - 0.0001, "Boundary jitter")
            scores[31] = (scores[31][0] + 0.0001, "Boundary jitter")
            metadata = {
                "retry_count": 0,
                "fallback_used": False,
                "model_latency_ms": 5,
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return scores, metadata

        run_a = self._run_selector(root=root, theme="AI", rss_items=rss_items, score_side_effect=_stable_scores_run_a)
        run_b = self._run_selector(root=root, theme="AI", rss_items=rss_items, score_side_effect=_stable_scores_run_b)

        urls_a = {item["url"] for item in run_a["ranked_items"]}
        urls_b = {item["url"] for item in run_b["ranked_items"]}
        overlap_ratio = len(urls_a & urls_b) / max(len(urls_a), 1)

        self.assertNotEqual(urls_a, urls_b)
        self.assertGreaterEqual(overlap_ratio, 0.8)

    def test_tie_break_orders_by_published_desc_then_url(self) -> None:
        root = self._make_temp_root()
        rss_items = [
            {
                "url": "https://example.com/b-url",
                "title": "B URL",
                "source": "ExampleFeed",
                "published_at": "2026-01-02T00:00:00Z",
            },
            {
                "url": "https://example.com/a-url",
                "title": "A URL",
                "source": "ExampleFeed",
                "published_at": "2026-01-02T00:00:00Z",
            },
            {
                "url": "https://example.com/newer",
                "title": "Newer",
                "source": "ExampleFeed",
                "published_at": "2026-01-03T00:00:00Z",
            },
        ]

        def _same_scores(**kwargs: Any) -> tuple[dict[int, tuple[float, str]], dict[str, Any]]:
            candidates = kwargs["candidates"]
            scores = {candidate.item_id: (0.9, "Equal score") for candidate in candidates}
            metadata = {
                "retry_count": 0,
                "fallback_used": False,
                "model_latency_ms": 5,
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return scores, metadata

        state = self._run_selector(root=root, theme="AI", rss_items=rss_items, score_side_effect=_same_scores)
        ordered_urls = [item["url"] for item in state["ranked_items"]]
        self.assertEqual(
            [
                "https://example.com/newer",
                "https://example.com/a-url",
                "https://example.com/b-url",
            ],
            ordered_urls,
        )

    def test_cardinality_policy_rules(self) -> None:
        root = self._make_temp_root()

        state_12 = self._run_selector(root=root, theme="AI", rss_items=self._make_rss_items(12))
        self.assertEqual(12, len(state_12["ranked_items"]))
        self.assertTrue(state_12["metrics"]["flags"]["phase2_selector_policy_warning_low_input"])

        state_27 = self._run_selector(root=root, theme="AI", rss_items=self._make_rss_items(27))
        self.assertEqual(27, len(state_27["ranked_items"]))
        self.assertFalse(state_27["metrics"]["flags"]["phase2_selector_policy_warning_low_input"])

        state_50 = self._run_selector(root=root, theme="AI", rss_items=self._make_rss_items(50))
        self.assertEqual(30, len(state_50["ranked_items"]))
        self.assertFalse(state_50["metrics"]["flags"]["phase2_selector_policy_warning_low_input"])

    def test_malformed_model_response_retries_then_falls_back(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(6)

        with (
            patch.object(theme_url_selector, "_project_root", return_value=root),
            patch.object(
                theme_url_selector,
                "_load_runtime_configs",
                return_value=(self._make_pipeline_config("AI"), self._make_openai_config()),
            ),
            patch.object(
                theme_url_selector,
                "_call_selector_model",
                side_effect=[
                    theme_url_selector.ModelResponseError("bad response"),
                    theme_url_selector.ModelResponseError("still bad"),
                ],
            ) as call_mock,
        ):
            state = self._make_initial_state()
            state["rss_items"] = rss_items
            final_state = theme_url_selector.run(state)

        self.assertEqual(2, call_mock.call_count)
        self.assertTrue(final_state["metrics"]["flags"]["phase2_selector_fallback_used"])
        self.assertEqual(1, final_state["metrics"]["counters"]["phase2_selector_retry_count"])
        self.assertEqual(6, len(final_state["ranked_items"]))

    def test_unexpected_model_error_is_re_raised_with_context(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(6)

        with (
            patch.object(theme_url_selector, "_project_root", return_value=root),
            patch.object(
                theme_url_selector,
                "_load_runtime_configs",
                return_value=(self._make_pipeline_config("AI"), self._make_openai_config()),
            ),
            patch.object(
                theme_url_selector,
                "_call_selector_model",
                side_effect=ValueError("unexpected scoring bug"),
            ) as call_mock,
        ):
            state = self._make_initial_state()
            state["rss_items"] = rss_items
            with self.assertRaises(theme_url_selector.ThemeURLSelectorError) as context:
                theme_url_selector.run(state)

        self.assertEqual(1, call_mock.call_count)
        error_payload = json.loads(str(context.exception))
        self.assertEqual("unexpected_selector_scoring_error", error_payload["code"])
        self.assertEqual("ValueError", error_payload["details"]["error_type"])

    def test_selector_openai_call_includes_explicit_timeout(self) -> None:
        candidate = theme_url_selector.Candidate(
            item_id=1,
            url="https://example.com/story-001",
            canonical_url="https://example.com/story-001",
            title="Story 001",
            source="ExampleFeed",
            scrape_policy=SCRAPE_POLICY_FULL,
            published_at="2026-01-01T00:00:00Z",
            summary="Summary",
        )
        captured_kwargs: dict[str, Any] = {}

        class _FakeCompletions:
            def create(self, **kwargs: Any) -> Any:
                captured_kwargs.update(kwargs)
                usage = types.SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5)
                message = types.SimpleNamespace(
                    content='{"items":[{"id":1,"score":0.7,"reason":"Good thematic match"}]}'
                )
                choice = types.SimpleNamespace(message=message)
                return types.SimpleNamespace(choices=[choice], usage=usage)

        class _FakeOpenAI:
            def __init__(self, api_key: str | None = None) -> None:
                del api_key
                self.chat = types.SimpleNamespace(completions=_FakeCompletions())

        fake_openai_module = types.SimpleNamespace(OpenAI=_FakeOpenAI)
        with patch.dict(sys.modules, {"openai": fake_openai_module}):
            payload, usage = theme_url_selector._call_selector_model(
                theme="AI",
                candidates=[candidate],
                model_name="gpt-4.1-mini",
                temperature=0.0,
                top_p=1.0,
                prompt_version="phase2-theme-selector-v1",
                openai_api_key_env_var="OPENAI_API_KEY",
            )

        self.assertIn("timeout", captured_kwargs)
        self.assertEqual(theme_url_selector.OPENAI_TIMEOUT_SECONDS, captured_kwargs["timeout"])
        self.assertEqual(5, usage["total_tokens"])
        self.assertIn("items", payload)

    def test_missing_openai_dependency_surfaces_structured_error(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(6)

        with (
            patch.object(theme_url_selector, "_project_root", return_value=root),
            patch.object(
                theme_url_selector,
                "_load_runtime_configs",
                return_value=(self._make_pipeline_config("AI"), self._make_openai_config()),
            ),
            patch.object(
                theme_url_selector,
                "_call_selector_model",
                side_effect=theme_url_selector.SelectorDependencyError(
                    "Missing dependency: openai. Install requirements/phase2.txt"
                ),
            ) as call_mock,
        ):
            state = self._make_initial_state()
            state["rss_items"] = rss_items
            with self.assertRaises(theme_url_selector.ThemeURLSelectorError) as context:
                theme_url_selector.run(state)

        self.assertEqual(1, call_mock.call_count)
        error_payload = json.loads(str(context.exception))
        self.assertEqual("missing_phase2_dependency", error_payload["code"])

    def test_graph_handoff_uses_phase2_subset_and_writes_artifact(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(50)

        def _fake_rss_collector(state: PipelineState) -> PipelineState:
            next_state = dict(state)
            next_state["rss_items"] = rss_items
            return next_state  # type: ignore[return-value]

        with (
            patch.object(theme_url_selector, "_project_root", return_value=root),
            patch.object(
                theme_url_selector,
                "_load_runtime_configs",
                return_value=(self._make_pipeline_config("AI"), self._make_openai_config()),
            ),
            patch.object(theme_url_selector, "_score_candidates", side_effect=self._default_score_candidates),
            patch.object(
                news_to_video_graph,
                "NODE_FLOW",
                (
                    ("rss_collector", _fake_rss_collector),
                    ("theme_url_selector", theme_url_selector.run),
                    ("relevance_ranker", relevance_ranker.run),
                    ("reporter", reporter_agent_run),
                ),
            ),
        ):
            reporter = Reporter(
                phase_name="Theme URL Selection",
                version_info=self._make_initial_state()["version_info"],
                deterministic_seed="phase2-seed-v1",
                deterministic_started_at="2026-01-01T00:00:00Z",
            )
            final_state = news_to_video_graph.run_pipeline(self._make_initial_state(), reporter)

        self.assertEqual(50, len(final_state["rss_items"]))
        self.assertEqual(30, len(final_state["ranked_items"]))

        artifact_path = root / "outputs" / "theme_selected_urls.json"
        self.assertTrue(artifact_path.exists())
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual("AI", artifact["theme"])
        self.assertEqual(50, artifact["input_count"])
        self.assertEqual(30, artifact["output_count"])
        self.assertEqual("gpt-4.1-mini", artifact["selector_model"]["name"])
        self.assertEqual(0.0, artifact["selector_model"]["temperature"])
        self.assertEqual(1.0, artifact["selector_model"]["top_p"])
        self.assertEqual("phase2-theme-selector-v1", artifact["selector_model"]["prompt_version"])
        self.assertEqual("published_at_desc_then_canonical_url_asc", artifact["tie_break_policy"])
        self.assertIn("run_metadata", artifact)

    def test_scrape_policy_is_propagated_to_phase2_outputs(self) -> None:
        root = self._make_temp_root()
        rss_items = self._make_rss_items(5)
        rss_items[0]["scrape_policy"] = SCRAPE_POLICY_METADATA_ONLY

        final_state = self._run_selector(root=root, theme="AI", rss_items=rss_items)

        self.assertIn("scrape_policy", final_state["ranked_items"][0])
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, final_state["ranked_items"][0]["scrape_policy"])

        artifact = json.loads((root / "outputs" / "theme_selected_urls.json").read_text(encoding="utf-8"))
        self.assertIn("scrape_policy", artifact["selected_items"][0])
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, artifact["selected_items"][0]["scrape_policy"])


if __name__ == "__main__":
    unittest.main()
