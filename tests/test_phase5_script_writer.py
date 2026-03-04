"""Phase 5 script writer exit-criteria tests."""

from __future__ import annotations

import json
import shutil
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents import script_writer
from core.state import PipelineState, make_initial_state


class Phase5ScriptWriterTests(unittest.TestCase):
    def _make_temp_root(self) -> Path:
        base = Path(__file__).resolve().parent / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"phase5-script-writer-{time.time_ns()}"
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
                "prompt_version": "phase5-script-writer-v1",
                "schema_version": "phase5-script-schema-v1",
                "template_version": "v1-placeholder",
                "model_version": "gpt-4.1-mini",
            },
        )
        state["selected_url"] = "https://example.com/selected"
        state["ranked_items"] = [
            {
                "url": "https://example.com/selected",
                "source": "Example Source",
            }
        ]
        state["article"] = {
            "title": "Phase 5 Story",
            "author": "Reporter",
            "published_at": "2026-01-01T00:00:00Z",
            "source_url": "https://example.com/selected",
            "paragraphs": ["Paragraph one.", "Paragraph two."],
            "metadata_only": False,
            "extraction_status": "extracted",
        }
        return state

    def _load_schema(self) -> dict[str, Any]:
        schema_path = Path(__file__).resolve().parent.parent / "schemas" / "script_schema.json"
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def _valid_payload(self) -> dict[str, Any]:
        return {
            "video_title": "Daily AI Briefing",
            "source_line": "Source: https://example.com/selected",
            "hook": "Here is what changed in AI today.",
            "scenes": [
                {"id": 1, "narration": "Scene one narration.", "image_prompt": "AI newsroom"},
                {"id": 2, "narration": "Scene two narration.", "image_prompt": None},
            ],
            "cta": "Follow for more updates.",
        }

    def test_output_strictly_matches_schema_rejects_unexpected_keys(self) -> None:
        schema = self._load_schema()
        payload = self._valid_payload()
        payload["unexpected"] = "not allowed"

        with self.assertRaises(script_writer.ModelResponseError):
            script_writer._parse_script_payload(payload, schema)

    def test_no_free_form_output_is_accepted(self) -> None:
        schema = self._load_schema()

        with self.assertRaises(script_writer.ModelResponseError):
            script_writer._parse_script_payload("this is free-form text", schema)

    def test_run_logs_deterministic_params_and_writes_script_json(self) -> None:
        root = self._make_temp_root()
        state = self._make_state()
        payload = self._valid_payload()
        generation_metadata = {
            "retry_count": 0,
            "fallback_used": False,
            "last_error": None,
            "model_latency_ms": 7,
            "token_usage": {
                "prompt_tokens": 11,
                "completion_tokens": 22,
                "total_tokens": 33,
            },
        }

        pipeline_config = {
            "output_dir": "outputs",
            "phase5_script_writer": {
                "prompt_version": "phase5-script-writer-v1",
                "deterministic": {"temperature": 0.0, "top_p": 1.0},
                "timeout_seconds": 45,
                "schema_path": "schemas/custom_script_schema.json",
            },
            "versions": {"schema_version": "phase5-script-schema-v1"},
        }
        openai_config = {
            "api_key_env_var": "OPENAI_API_KEY",
            "models": {"script_writer": "gpt-4.1-mini"},
        }

        with (
            patch.object(script_writer, "_project_root", return_value=root),
            patch.object(script_writer, "_load_project_env", return_value=None),
            patch.object(script_writer, "_load_runtime_configs", return_value=(pipeline_config, openai_config)),
            patch.object(script_writer, "_load_text_file", return_value="system prompt"),
            patch.object(script_writer, "_load_script_schema", return_value=self._load_schema()) as mocked_load_schema,
            patch.object(script_writer, "_generate_script", return_value=(payload, generation_metadata)),
        ):
            final_state = script_writer.run(state)

        mocked_load_schema.assert_called_once_with(root / "schemas" / "custom_script_schema.json")
        self.assertEqual(payload, final_state["script_json"])

        flags = final_state["metrics"]["flags"]
        self.assertEqual("gpt-4.1-mini", flags["phase5_script_writer_model"])
        self.assertEqual("phase5-script-writer-v1", flags["phase5_script_writer_prompt_version"])
        self.assertEqual(45.0, flags["phase5_script_writer_timeout_seconds"])
        self.assertEqual("schemas/custom_script_schema.json", flags["phase5_script_writer_schema_path"])
        self.assertEqual("phase5-script-schema-v1", flags["phase5_script_writer_schema_version"])

        counters = final_state["metrics"]["counters"]
        self.assertEqual(7, counters["phase5_script_writer_model_latency_ms"])
        self.assertEqual(0, counters["phase5_script_writer_retry_count"])
        self.assertEqual(11, counters["phase5_script_writer_prompt_tokens"])
        self.assertEqual(22, counters["phase5_script_writer_completion_tokens"])
        self.assertEqual(33, counters["phase5_script_writer_total_tokens"])

        output_path = root / "outputs" / "script.json"
        self.assertTrue(output_path.exists())
        persisted = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload, persisted)

    def test_script_writer_does_not_enforce_phase6_validation_constraints(self) -> None:
        schema = self._load_schema()
        payload = {
            "video_title": "Short Script",
            "source_line": "Source: https://example.com/selected",
            "hook": "This hook intentionally has more than fifteen words to assert phase six limits are not applied here.",
            "scenes": [
                {"id": 1, "narration": "S1", "image_prompt": "prompt 1"},
                {"id": 2, "narration": "S2", "image_prompt": "prompt 2"},
                {"id": 3, "narration": "S3", "image_prompt": "prompt 3"},
                {"id": 4, "narration": "S4", "image_prompt": "prompt 4"},
            ],
            "cta": "Follow.",
        }

        parsed = script_writer._parse_script_payload(payload, schema)
        self.assertEqual(payload, parsed)

    def test_settings_use_phase5_model_when_openai_model_is_empty(self) -> None:
        pipeline_config = {
            "phase5_script_writer": {
                "model": "gpt-4.1",
            }
        }
        openai_config = {
            "api_key_env_var": "OPENAI_API_KEY",
            "models": {"script_writer": "  "},
        }

        settings = script_writer._script_writer_settings(pipeline_config, openai_config)
        self.assertEqual("gpt-4.1", settings["model_name"])
        self.assertEqual("schemas/script_schema.json", settings["schema_path"])


if __name__ == "__main__":
    unittest.main()
