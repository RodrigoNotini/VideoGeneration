"""Phase 3 exit criteria verification tests."""

from __future__ import annotations

import inspect
import json
import shutil
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents import relevance_ranker
from agents.reporter import Reporter, run as reporter_agent_run
from core.common.utils import SCRAPE_POLICY_FULL, SCRAPE_POLICY_METADATA_ONLY
from core.state import PipelineState, make_initial_state
from graphs import news_to_video_graph


class Phase3RelevanceRankerTests(unittest.TestCase):
    AI_CRITERIA = [
        "Human stakes",
        "Novelty / first-ever capability",
        "Controversy or tension",
        "Visual or demonstrable proof",
        "Speculation about the future",
    ]
    TECH_CRITERIA = [
        "Immediate real-world impact",
        "Credibility of the source",
        "Simplicity of the core idea",
        "Timeliness / news hook",
        "Contrarianism",
    ]

    def _make_temp_root(self) -> Path:
        base = Path(__file__).resolve().parent / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"phase3-ranker-{time.time_ns()}"
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
                "prompt_version": "phase3-interestingness-ranker-v1",
                "schema_version": "phase3-interestingness-ranker-v1",
                "template_version": "v1-placeholder",
                "model_version": "phase3-gpt-4.1-mini",
            },
        )

    def _make_pipeline_config(
        self,
        theme: str,
        *,
        ranker_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = {
            "theme": theme,
            "output_dir": "outputs",
            "phase3_ranker": {
                "model": "gpt-4.1-mini",
                "prompt_version": "phase3-interestingness-ranker-v1",
                "criteria_policy_version": "phase3-interestingness-policy-v1",
                "target_selection_count": 1,
                "tie_break_policy": "score_desc_then_published_at_desc_then_url_asc",
                "deterministic": {"temperature": 0.0, "top_p": 1.0},
                "stability": {"min_overlap_ratio": 0.9},
            },
        }
        if ranker_overrides:
            config["phase3_ranker"].update(ranker_overrides)
        return config

    def _make_openai_config(self) -> dict[str, Any]:
        return {
            "api_key_env_var": "OPENAI_API_KEY",
            "models": {
                "interestingness_ranker": "gpt-4.1-mini",
            },
        }

    def _make_phase2_subset(self, count: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for index in range(count):
            day = (index % 28) + 1
            items.append(
                {
                    "url": f"https://example.com/story-{index:03d}",
                    "title": f"Story {index:03d}",
                    "source": "ExampleFeed",
                    "scrape_policy": SCRAPE_POLICY_FULL,
                    "published_at": f"2026-01-{day:02d}T00:00:00Z",
                    "summary": f"Summary for story {index:03d}",
                    "selection_reason": "Selected by theme selector",
                    "theme_match_score": round(1.0 - (index / 1000), 6),
                }
            )
        return items

    def _default_score_candidates(
        self,
        *,
        theme: str,
        criteria: list[dict[str, str]],
        candidates: list[relevance_ranker.Candidate],
        model_name: str,
        temperature: float,
        top_p: float,
        prompt_version: str,
        openai_api_key_env_var: str,
    ) -> tuple[dict[int, tuple[float, str, dict[str, float]]], dict[str, Any]]:
        del criteria, model_name, temperature, top_p, prompt_version, openai_api_key_env_var
        labels = self.AI_CRITERIA if theme == "AI" else self.TECH_CRITERIA
        scores: dict[int, tuple[float, str, dict[str, float]]] = {}
        for candidate in candidates:
            criteria_scores = {
                label: round(max(0.0, 1.0 - (candidate.item_id / 100.0)), 6)
                for label in labels
            }
            score = round(sum(criteria_scores.values()) / len(criteria_scores), 6)
            scores[candidate.item_id] = (score, "Mocked deterministic score.", criteria_scores)
        return scores, {
            "retry_count": 0,
            "fallback_used": False,
            "model_latency_ms": 5,
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "last_error": None,
        }

    def _run_ranker(
        self,
        *,
        root: Path,
        theme: str,
        phase2_subset: list[dict[str, Any]],
        score_side_effect: Any | None = None,
        pipeline_config: dict[str, Any] | None = None,
    ) -> PipelineState:
        state = self._make_initial_state()
        state["ranked_items"] = phase2_subset
        score_impl = score_side_effect or self._default_score_candidates
        ranker_config = pipeline_config or self._make_pipeline_config(theme)

        with (
            patch.object(relevance_ranker, "_project_root", return_value=root),
            patch.object(
                relevance_ranker,
                "_load_runtime_configs",
                return_value=(ranker_config, self._make_openai_config()),
            ),
            patch.object(relevance_ranker, "_score_candidates", side_effect=score_impl),
        ):
            return relevance_ranker.run(state)

    def test_theme_enforcement_accepts_ai_and_tech_and_rejects_invalid(self) -> None:
        root = self._make_temp_root()
        phase2_subset = self._make_phase2_subset(5)

        ai_state = self._run_ranker(root=root, theme="AI", phase2_subset=phase2_subset)
        tech_state = self._run_ranker(root=root, theme="Tech", phase2_subset=phase2_subset)

        self.assertEqual(5, len(ai_state["ranked_items"]))
        self.assertEqual(5, len(tech_state["ranked_items"]))

        with self.assertRaises(relevance_ranker.RelevanceRankerError) as context:
            self._run_ranker(root=root, theme="Finance", phase2_subset=phase2_subset)
        error_payload = json.loads(str(context.exception))
        self.assertEqual(3, error_payload["phase"])
        self.assertEqual("invalid_theme", error_payload["code"])

    def test_criteria_policy_application_matches_context_contract(self) -> None:
        root = self._make_temp_root()
        phase2_subset = self._make_phase2_subset(5)

        self._run_ranker(root=root, theme="AI", phase2_subset=phase2_subset)
        ai_report = json.loads(
            (root / "outputs" / "ranking_criteria_report.json").read_text(encoding="utf-8")
        )
        ai_labels = [item["label"] for item in ai_report["criteria"]]
        self.assertEqual(self.AI_CRITERIA, ai_labels)

        self._run_ranker(root=root, theme="Tech", phase2_subset=phase2_subset)
        tech_report = json.loads(
            (root / "outputs" / "ranking_criteria_report.json").read_text(encoding="utf-8")
        )
        tech_labels = [item["label"] for item in tech_report["criteria"]]
        self.assertEqual(self.TECH_CRITERIA, tech_labels)

    def test_deterministic_tie_break_orders_by_published_then_url(self) -> None:
        root = self._make_temp_root()
        phase2_subset = [
            {
                "url": "https://example.com/b-url",
                "title": "B URL",
                "source": "ExampleFeed",
                "published_at": "2026-01-02T00:00:00Z",
                "summary": "Summary",
                "selection_reason": "Phase 2 reason",
                "theme_match_score": 0.6,
            },
            {
                "url": "https://example.com/a-url",
                "title": "A URL",
                "source": "ExampleFeed",
                "published_at": "2026-01-02T00:00:00Z",
                "summary": "Summary",
                "selection_reason": "Phase 2 reason",
                "theme_match_score": 0.6,
            },
            {
                "url": "https://example.com/newer",
                "title": "Newer",
                "source": "ExampleFeed",
                "published_at": "2026-01-03T00:00:00Z",
                "summary": "Summary",
                "selection_reason": "Phase 2 reason",
                "theme_match_score": 0.6,
            },
        ]

        def _same_scores(**kwargs: Any) -> tuple[dict[int, tuple[float, str, dict[str, float]]], dict[str, Any]]:
            candidates = kwargs["candidates"]
            labels = self.AI_CRITERIA
            scores = {
                candidate.item_id: (
                    0.8,
                    "Equal score",
                    {label: 0.8 for label in labels},
                )
                for candidate in candidates
            }
            return scores, {
                "retry_count": 0,
                "fallback_used": False,
                "model_latency_ms": 5,
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "last_error": None,
            }

        state = self._run_ranker(
            root=root,
            theme="AI",
            phase2_subset=phase2_subset,
            score_side_effect=_same_scores,
        )
        ordered_urls = [item["url"] for item in state["ranked_items"]]
        self.assertEqual(
            [
                "https://example.com/newer",
                "https://example.com/a-url",
                "https://example.com/b-url",
            ],
            ordered_urls,
        )

    def test_exactly_one_selection_from_phase2_subset(self) -> None:
        root = self._make_temp_root()
        phase2_subset = self._make_phase2_subset(12)
        state = self._run_ranker(root=root, theme="AI", phase2_subset=phase2_subset)

        selected_url = state["selected_url"]
        subset_urls = {item["url"] for item in phase2_subset}
        self.assertIn(selected_url, subset_urls)
        self.assertEqual(selected_url, state["ranked_items"][0]["url"])

        selection = json.loads((root / "outputs" / "selection.json").read_text(encoding="utf-8"))
        self.assertEqual(1, selection["selection_count"])
        self.assertEqual(12, selection["source_subset_count"])

    def test_no_embedding_guard(self) -> None:
        root = self._make_temp_root()
        phase2_subset = self._make_phase2_subset(6)
        state = self._run_ranker(root=root, theme="AI", phase2_subset=phase2_subset)

        self.assertEqual(6, len(state["ranked_items"]))
        source = inspect.getsource(relevance_ranker)
        self.assertNotIn(".embeddings", source)
        self.assertNotIn("embeddings.create", source)

    def test_ranker_response_schema_requires_reason_field(self) -> None:
        schema = relevance_ranker._ranker_response_schema(2, self.AI_CRITERIA)
        required_fields = schema["schema"]["properties"]["items"]["items"]["required"]
        self.assertIn("reason", required_fields)

    def test_malformed_model_response_retries_once_then_falls_back(self) -> None:
        root = self._make_temp_root()
        phase2_subset = self._make_phase2_subset(6)
        state = self._make_initial_state()
        state["ranked_items"] = phase2_subset

        with (
            patch.object(relevance_ranker, "_project_root", return_value=root),
            patch.object(
                relevance_ranker,
                "_load_runtime_configs",
                return_value=(self._make_pipeline_config("AI"), self._make_openai_config()),
            ),
            patch.object(
                relevance_ranker,
                "_call_ranker_model",
                side_effect=[
                    relevance_ranker.ModelResponseError("bad response"),
                    relevance_ranker.ModelResponseError("still bad"),
                ],
            ) as call_mock,
        ):
            final_state = relevance_ranker.run(state)

        self.assertEqual(2, call_mock.call_count)
        self.assertEqual(1, final_state["metrics"]["counters"]["phase3_ranker_retry_count"])
        self.assertTrue(final_state["metrics"]["flags"]["phase3_ranker_fallback_used"])
        self.assertEqual(6, len(final_state["ranked_items"]))

    def test_artifact_contract_creates_three_phase3_outputs(self) -> None:
        root = self._make_temp_root()
        phase2_subset = self._make_phase2_subset(10)
        self._run_ranker(root=root, theme="AI", phase2_subset=phase2_subset)

        ranked_path = root / "outputs" / "ranked_items.json"
        selection_path = root / "outputs" / "selection.json"
        report_path = root / "outputs" / "ranking_criteria_report.json"
        self.assertTrue(ranked_path.exists())
        self.assertTrue(selection_path.exists())
        self.assertTrue(report_path.exists())

        ranked = json.loads(ranked_path.read_text(encoding="utf-8"))
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertIn("ranked_items", ranked)
        self.assertIn("ranking_model", ranked)
        self.assertIn("criteria_policy_version", ranked)
        self.assertIn("selected_url", selection)
        self.assertIn("selection_count", selection)
        self.assertIn("from_phase2_subset", selection)
        self.assertIn("criteria", report)
        self.assertIn("model_params", report)
        self.assertIn("retry_fallback", report)

    def test_model_parameter_logging_in_artifacts_and_metrics(self) -> None:
        root = self._make_temp_root()
        phase2_subset = self._make_phase2_subset(8)
        state = self._run_ranker(root=root, theme="Tech", phase2_subset=phase2_subset)

        ranked = json.loads((root / "outputs" / "ranked_items.json").read_text(encoding="utf-8"))
        report = json.loads(
            (root / "outputs" / "ranking_criteria_report.json").read_text(encoding="utf-8")
        )

        self.assertEqual("gpt-4.1-mini", ranked["ranking_model"]["name"])
        self.assertEqual(0.0, ranked["ranking_model"]["temperature"])
        self.assertEqual(1.0, ranked["ranking_model"]["top_p"])
        self.assertEqual(
            "phase3-interestingness-ranker-v1",
            ranked["ranking_model"]["prompt_version"],
        )
        self.assertEqual(
            "phase3-interestingness-policy-v1",
            ranked["criteria_policy_version"],
        )
        self.assertEqual(
            "score_desc_then_published_at_desc_then_url_asc",
            ranked["tie_break_policy"],
        )

        self.assertEqual("phase3-interestingness-policy-v1", report["criteria_policy_version"])
        self.assertEqual("gpt-4.1-mini", report["model_params"]["name"])
        self.assertEqual(0.0, report["model_params"]["temperature"])
        self.assertEqual(1.0, report["model_params"]["top_p"])
        self.assertEqual(
            "phase3-interestingness-ranker-v1",
            report["model_params"]["prompt_version"],
        )

        flags = state["metrics"]["flags"]
        self.assertEqual("Tech", flags["phase3_ranker_theme"])
        self.assertEqual(
            "score_desc_then_published_at_desc_then_url_asc",
            flags["phase3_ranker_tie_break_policy"],
        )
        self.assertEqual("phase3-interestingness-policy-v1", flags["phase3_ranker_criteria_policy_version"])

    def test_stability_overlap_meets_threshold_under_controlled_variance(self) -> None:
        root = self._make_temp_root()
        phase2_subset = self._make_phase2_subset(20)
        threshold = self._make_pipeline_config("AI")["phase3_ranker"]["stability"]["min_overlap_ratio"]

        def _scores_run_a(**kwargs: Any) -> tuple[dict[int, tuple[float, str, dict[str, float]]], dict[str, Any]]:
            candidates = kwargs["candidates"]
            scores = {}
            for candidate in candidates:
                base_score = round(1.0 - (candidate.item_id / 100.0), 6)
                scores[candidate.item_id] = (
                    base_score,
                    "Run A score",
                    {label: base_score for label in self.AI_CRITERIA},
                )
            return scores, {
                "retry_count": 0,
                "fallback_used": False,
                "model_latency_ms": 5,
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "last_error": None,
            }

        def _scores_run_b(**kwargs: Any) -> tuple[dict[int, tuple[float, str, dict[str, float]]], dict[str, Any]]:
            candidates = kwargs["candidates"]
            scores = {}
            for candidate in candidates:
                base_score = round(1.0 - (candidate.item_id / 100.0), 6)
                if candidate.item_id == 10:
                    base_score = 0.8899
                elif candidate.item_id == 11:
                    base_score = 0.9001
                scores[candidate.item_id] = (
                    base_score,
                    "Run B score",
                    {label: base_score for label in self.AI_CRITERIA},
                )
            return scores, {
                "retry_count": 0,
                "fallback_used": False,
                "model_latency_ms": 5,
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "last_error": None,
            }

        state_a = self._run_ranker(
            root=root,
            theme="AI",
            phase2_subset=phase2_subset,
            score_side_effect=_scores_run_a,
        )
        state_b = self._run_ranker(
            root=root,
            theme="AI",
            phase2_subset=phase2_subset,
            score_side_effect=_scores_run_b,
        )

        ranked_urls_a = [item["url"] for item in state_a["ranked_items"]]
        ranked_urls_b = [item["url"] for item in state_b["ranked_items"]]
        top_k = min(10, len(ranked_urls_a), len(ranked_urls_b))
        overlap_ratio = len(set(ranked_urls_a[:top_k]) & set(ranked_urls_b[:top_k])) / max(top_k, 1)

        self.assertNotEqual(ranked_urls_a[:top_k], ranked_urls_b[:top_k])
        self.assertGreaterEqual(overlap_ratio, threshold)

    def test_graph_handoff_consumes_phase2_subset_and_emits_selection(self) -> None:
        root = self._make_temp_root()
        subset = self._make_phase2_subset(30)

        def _fake_rss_collector(state: PipelineState) -> PipelineState:
            next_state = dict(state)
            next_state["rss_items"] = self._make_phase2_subset(50)
            return next_state  # type: ignore[return-value]

        def _fake_theme_selector(state: PipelineState) -> PipelineState:
            next_state = dict(state)
            next_state["ranked_items"] = subset
            return next_state  # type: ignore[return-value]

        with (
            patch.object(relevance_ranker, "_project_root", return_value=root),
            patch.object(
                relevance_ranker,
                "_load_runtime_configs",
                return_value=(self._make_pipeline_config("AI"), self._make_openai_config()),
            ),
            patch.object(relevance_ranker, "_score_candidates", side_effect=self._default_score_candidates),
            patch.object(
                news_to_video_graph,
                "NODE_FLOW",
                (
                    ("rss_collector", _fake_rss_collector),
                    ("theme_url_selector", _fake_theme_selector),
                    ("relevance_ranker", relevance_ranker.run),
                    ("reporter", reporter_agent_run),
                ),
            ),
        ):
            reporter = Reporter(
                phase_name="Interestingness Ranking",
                version_info=self._make_initial_state()["version_info"],
                deterministic_seed="phase3-seed-v1",
                deterministic_started_at="2026-01-01T00:00:00Z",
            )
            final_state = news_to_video_graph.run_pipeline(self._make_initial_state(), reporter)

        self.assertEqual(30, len(final_state["ranked_items"]))
        subset_urls = {item["url"] for item in subset}
        self.assertIn(final_state["selected_url"], subset_urls)

        selection = json.loads((root / "outputs" / "selection.json").read_text(encoding="utf-8"))
        self.assertTrue(selection["from_phase2_subset"])
        self.assertEqual(30, selection["source_subset_count"])
        self.assertIn(selection["selected_url"], subset_urls)

    def test_scrape_policy_is_propagated_to_phase3_outputs(self) -> None:
        root = self._make_temp_root()
        phase2_subset = self._make_phase2_subset(6)
        phase2_subset[0]["scrape_policy"] = SCRAPE_POLICY_METADATA_ONLY

        final_state = self._run_ranker(root=root, theme="AI", phase2_subset=phase2_subset)
        self.assertIn("scrape_policy", final_state["ranked_items"][0])
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, final_state["ranked_items"][0]["scrape_policy"])

        ranked = json.loads((root / "outputs" / "ranked_items.json").read_text(encoding="utf-8"))
        self.assertIn("scrape_policy", ranked["ranked_items"][0])
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, ranked["ranked_items"][0]["scrape_policy"])

        selection = json.loads((root / "outputs" / "selection.json").read_text(encoding="utf-8"))
        self.assertIn("scrape_policy", selection["selected_item"])
        self.assertEqual(SCRAPE_POLICY_METADATA_ONLY, selection["selected_item"]["scrape_policy"])


if __name__ == "__main__":
    unittest.main()
