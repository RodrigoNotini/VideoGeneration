"""Regression tests for reporter checksum and metrics behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.reporter import Reporter
from core.common.utils import write_json
from core.state import make_initial_state
from main import _artifact_checksum


VERSION_INFO = {
    "prompt_version": "phase0-placeholder",
    "schema_version": "phase0-placeholder",
    "template_version": "v1-placeholder",
    "model_version": "phase0-placeholder",
}


def _make_reporter() -> Reporter:
    return Reporter(
        phase_name="phase0",
        version_info=VERSION_INFO,
        deterministic_seed="seed",
        deterministic_started_at="2026-01-01T00:00:00Z",
    )


def _make_state():
    return make_initial_state(
        topic="AI & Tech Daily Briefing",
        target_platform="youtube_shorts",
        target_duration_sec=45,
        version_info=VERSION_INFO,
    )


class ReporterRegressionTests(unittest.TestCase):
    def test_sync_state_metrics_preserves_agent_metrics(self) -> None:
        reporter = _make_reporter()
        state = _make_state()
        state["metrics"]["counters"]["rss_items_count"] = 4
        state["metrics"]["flags"]["article_stub_created"] = True

        reporter.stage_started("rss_collector")
        reporter.stage_finished("rss_collector", note="phase0_stub")

        synced = reporter.sync_state_metrics(state)

        self.assertEqual(synced["metrics"]["counters"]["rss_items_count"], 4)
        self.assertEqual(synced["metrics"]["counters"]["completed_stages"], 1)
        self.assertTrue(synced["metrics"]["flags"]["article_stub_created"])
        self.assertTrue(synced["metrics"]["flags"]["deterministic_clock"])
        self.assertIn("rss_collector", synced["metrics"]["stages"])

        metadata = reporter.finalize(
            final_state=synced,
            status="success",
            artifacts=[],
            state_checksum="state-checksum",
        )
        self.assertEqual(metadata["state_checksum"], "state-checksum")
        self.assertEqual(metadata["metrics"]["counters"]["rss_items_count"], 4)
        self.assertTrue(metadata["metrics"]["flags"]["article_stub_created"])
        self.assertIn("rss_collector", metadata["metrics"]["stages"])

    def test_finalize_can_use_state_artifact_checksum(self) -> None:
        reporter = _make_reporter()
        state = _make_state()

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            write_json(state_path, state)
            artifact_checksum = _artifact_checksum(state_path)

        metadata = reporter.finalize(
            final_state=state,
            status="success",
            artifacts=[],
            state_checksum=artifact_checksum,
        )

        self.assertEqual(metadata["state_checksum"], artifact_checksum)


if __name__ == "__main__":
    unittest.main()
