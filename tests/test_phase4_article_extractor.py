"""Phase 4 article extraction exit criteria tests."""

from __future__ import annotations

import json
import re
import shutil
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from agents import article_extractor
from core.common.utils import SCRAPE_POLICY_FULL, SCRAPE_POLICY_METADATA_ONLY
from core.persistence.db import initialize_database
from core.state import PipelineState, make_initial_state


class Phase4ArticleExtractorTests(unittest.TestCase):
    HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

    def _assert_payload_matches_schema(self, payload: dict[str, Any], schema: dict[str, Any]) -> None:
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        self.assertTrue(set(required).issubset(set(payload.keys())))
        self.assertTrue(set(payload.keys()).issubset(set(properties.keys())))

        type_map: dict[str, type[Any]] = {
            "string": str,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for key, property_schema in properties.items():
            if key not in payload:
                continue
            expected_type_name = property_schema.get("type")
            if expected_type_name in type_map:
                self.assertIsInstance(payload[key], type_map[expected_type_name])

            allowed_values = property_schema.get("enum")
            if isinstance(allowed_values, list):
                self.assertIn(payload[key], allowed_values)

            if expected_type_name == "array" and isinstance(payload[key], list):
                item_type_name = property_schema.get("items", {}).get("type")
                if item_type_name in type_map:
                    for item in payload[key]:
                        self.assertIsInstance(item, type_map[item_type_name])

    def _make_temp_root(self) -> Path:
        base = Path(__file__).resolve().parent / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"phase4-extractor-{time.time_ns()}"
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

    def _run_extractor(
        self,
        *,
        root: Path,
        state: PipelineState,
        html: str | None = None,
        fetch_side_effect: Exception | None = None,
    ) -> PipelineState:
        with (
            patch.object(article_extractor, "_project_root", return_value=root),
            patch.object(
                article_extractor,
                "_load_runtime_pipeline_config",
                return_value={"output_dir": "outputs", "database_path": "data/db/app.sqlite"},
            ),
        ):
            if fetch_side_effect is not None:
                with patch.object(article_extractor, "_fetch_html", side_effect=fetch_side_effect):
                    return article_extractor.run(state)
            if html is not None:
                with patch.object(article_extractor, "_fetch_html", return_value=html):
                    return article_extractor.run(state)
            return article_extractor.run(state)

    def test_full_scrape_path_writes_article_artifacts(self) -> None:
        root = self._make_temp_root()
        state = self._make_state()
        selected_url = "https://example.com/full-scrape"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Selected Story Title",
                "source": "TechCrunch",
                "published_at": "2026-01-02T00:00:00Z",
                "summary": "Short metadata summary.",
                "scrape_policy": SCRAPE_POLICY_FULL,
            }
        ]
        html = (
            "<html><head>"
            "<title>HTML Title</title>"
            '<meta property="og:title" content="Normalized OG Title"/>'
            '<meta name="author" content="Jane Doe"/>'
            '<meta property="article:published_time" content="2026-01-02T12:34:56Z"/>'
            "</head><body>"
            "<nav>Subscribe to newsletter</nav>"
            "<article>"
            "<p>This is the first retained paragraph with enough content for deterministic extraction.</p>"
            "<p>This is the second retained paragraph with enough content and no ad content included.</p>"
            '<div class="ad banner"><p>Advertisement should be removed.</p></div>'
            "</article>"
            "<script>var tracking = true;</script>"
            "</body></html>"
        )

        final_state = self._run_extractor(root=root, state=state, html=html)

        article_raw_path = root / "outputs" / "article_raw.html"
        article_json_path = root / "outputs" / "article.json"
        self.assertTrue(article_raw_path.exists())
        self.assertTrue(article_json_path.exists())

        article = final_state["article"]
        persisted = json.loads(article_json_path.read_text(encoding="utf-8"))
        self.assertEqual(article, persisted)
        self.assertEqual("extracted", article["extraction_status"])
        self.assertFalse(article["metadata_only"])
        self.assertEqual(SCRAPE_POLICY_FULL, article["scrape_policy"])
        self.assertEqual("Normalized OG Title", article["title"])
        self.assertEqual("Jane Doe", article["author"])
        self.assertGreaterEqual(len(article["paragraphs"]), 2)
        self.assertTrue(final_state["metrics"]["flags"]["phase4_html_fetch_attempted"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_html_fetch_succeeded"])

    def test_metadata_only_policy_no_longer_blocks_fetch(self) -> None:
        root = self._make_temp_root()
        state = self._make_state()
        selected_url = "https://example.com/metadata-only"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "<strong>Blocked Story</strong> &amp; More",
                "source": "Wired",
                "published_at": "<time>2026-01-03T00:00:00Z</time>",
                "scrape_policy": SCRAPE_POLICY_METADATA_ONLY,
            }
        ]
        html = (
            "<html><head><title>Fetched Story</title></head><body>"
            "<article><p>This paragraph is long enough to keep extraction in full scrape mode.</p></article>"
            "</body></html>"
        )

        with patch.object(article_extractor, "_fetch_html", return_value=html) as fetch_html:
            final_state = self._run_extractor(root=root, state=state)

        self.assertFalse(final_state["article"]["metadata_only"])
        self.assertEqual("extracted", final_state["article"]["extraction_status"])
        self.assertEqual(SCRAPE_POLICY_FULL, final_state["article"]["scrape_policy"])
        self.assertEqual(SCRAPE_POLICY_FULL, final_state["metrics"]["flags"]["phase4_selected_scrape_policy"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_policy_blocked_metadata_only"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_html_fetch_attempted"])
        self.assertTrue((root / "outputs" / "article_raw.html").exists())
        self.assertTrue((root / "outputs" / "article.json").exists())
        fetch_html.assert_called_once_with(selected_url)

    def test_phase4_does_not_use_db_policy_lookup_gate(self) -> None:
        root = self._make_temp_root()
        selected_url = "https://example.com/policy-not-used"
        state = self._make_state()
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Policy Not Used Story",
                "source": "Example",
                "published_at": "2026-01-03T00:00:00Z",
                "summary": "summary",
            }
        ]
        html = (
            "<html><head><title>Policy Ignored</title></head><body>"
            "<article><p>This paragraph is long enough for extraction when policy gate is disabled.</p></article>"
            "</body></html>"
        )

        with (
            patch.object(article_extractor, "_resolve_policy_from_db", return_value=SCRAPE_POLICY_METADATA_ONLY) as db_lookup,
            patch.object(article_extractor, "_fetch_html", return_value=html) as fetch_html,
        ):
            final_state = self._run_extractor(root=root, state=state)

        self.assertEqual("extracted", final_state["article"]["extraction_status"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_policy_resolution_failed"])
        fetch_html.assert_called_once_with(selected_url)
        db_lookup.assert_not_called()

    def test_ranked_fallback_uses_second_candidate_after_fetch_failure(self) -> None:
        root = self._make_temp_root()
        first_url = "https://example.com/first"
        second_url = "https://example.com/second"
        state = self._make_state()
        state["selected_url"] = first_url
        state["ranked_items"] = [
            {"url": first_url, "title": "First", "source": "Example", "scrape_policy": SCRAPE_POLICY_FULL},
            {"url": second_url, "title": "Second", "source": "Example", "scrape_policy": SCRAPE_POLICY_FULL},
        ]
        html = (
            "<html><head><title>Second Winner</title></head><body>"
            "<article><p>This paragraph is long enough to satisfy extraction success on fallback candidate.</p></article>"
            "</body></html>"
        )

        with (
            patch.object(article_extractor, "_is_public_fetchable_url", return_value=True),
            patch.object(article_extractor, "_fetch_html", side_effect=[RuntimeError("timeout"), html]) as fetch_html,
        ):
            final_state = self._run_extractor(root=root, state=state)

        self.assertEqual(second_url, final_state["selected_url"])
        self.assertEqual("extracted", final_state["article"]["extraction_status"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_fallback_used"])
        self.assertEqual(1, final_state["metrics"]["flags"]["phase4_selected_rank_index"])
        self.assertEqual([first_url, second_url], final_state["metrics"]["flags"]["phase4_attempted_urls"])
        self.assertEqual(["fetch_failed"], final_state["metrics"]["flags"]["phase4_attempt_failure_reasons"])
        self.assertEqual(2, final_state["metrics"]["counters"]["phase4_candidate_attempt_count"])
        self.assertEqual(1, final_state["metrics"]["counters"]["phase4_candidate_failure_count"])
        fetch_html.assert_any_call(first_url)
        fetch_html.assert_any_call(second_url)

    def test_ranked_fallback_uses_second_candidate_after_empty_paragraphs(self) -> None:
        root = self._make_temp_root()
        first_url = "https://example.com/empty-first"
        second_url = "https://example.com/non-empty-second"
        state = self._make_state()
        state["selected_url"] = first_url
        state["ranked_items"] = [
            {"url": first_url, "title": "First", "source": "Example", "scrape_policy": SCRAPE_POLICY_FULL},
            {"url": second_url, "title": "Second", "source": "Example", "scrape_policy": SCRAPE_POLICY_FULL},
        ]
        empty_html = "<html><head><title>Empty First</title></head><body><article></article></body></html>"
        good_html = (
            "<html><head><title>Winner</title></head><body>"
            "<article><p>This fallback paragraph is valid and should become the selected extraction output.</p></article>"
            "</body></html>"
        )
        with (
            patch.object(article_extractor, "_is_public_fetchable_url", return_value=True),
            patch.object(article_extractor, "_fetch_html", side_effect=[empty_html, good_html]),
        ):
            final_state = self._run_extractor(root=root, state=state)

        self.assertEqual(second_url, final_state["selected_url"])
        self.assertEqual("extracted", final_state["article"]["extraction_status"])
        self.assertEqual(["empty_paragraphs"], final_state["metrics"]["flags"]["phase4_attempt_failure_reasons"])
        self.assertEqual(2, final_state["metrics"]["counters"]["phase4_candidate_attempt_count"])
        self.assertEqual(1, final_state["metrics"]["counters"]["phase4_candidate_failure_count"])

    def test_all_candidates_failed_raises_runtime_error_and_reports_exhaustion_metrics(self) -> None:
        root = self._make_temp_root()
        state = self._make_state()
        first_url = "https://example.com/first-fail"
        second_url = "https://example.com/second-fail"
        state["selected_url"] = first_url
        state["ranked_items"] = [
            {
                "url": first_url,
                "title": "<em>Failure Story 1</em> &amp; More",
                "source": "TechCrunch",
                "published_at": "<span>2026-01-03T00:00:00Z</span>",
                "summary": "Fallback summary content for extraction failure path.",
                "scrape_policy": SCRAPE_POLICY_FULL,
            },
            {
                "url": second_url,
                "title": "Failure Story 2",
                "source": "TechCrunch",
                "published_at": "2026-01-03T00:00:00Z",
                "summary": "Another failure.",
                "scrape_policy": SCRAPE_POLICY_FULL,
            },
        ]

        with (
            patch.object(article_extractor, "_project_root", return_value=root),
            patch.object(
                article_extractor,
                "_load_runtime_pipeline_config",
                return_value={"output_dir": "outputs", "database_path": "data/db/app.sqlite"},
            ),
            patch.object(article_extractor, "copy_state", side_effect=lambda value: value),
            patch.object(article_extractor, "_is_public_fetchable_url", return_value=True),
            patch.object(article_extractor, "_fetch_html", side_effect=RuntimeError("network down")),
        ):
            with self.assertRaises(RuntimeError) as raised:
                article_extractor.run(state)

        self.assertIn("all_ranked_candidates_failed", str(raised.exception))
        self.assertEqual("all_candidates_failed", state["article"]["extraction_status"])
        self.assertTrue(state["metrics"]["flags"]["phase4_extraction_failed"])
        self.assertFalse(state["metrics"]["flags"]["phase4_html_fetch_succeeded"])
        self.assertEqual(
            [first_url, second_url],
            state["metrics"]["flags"]["phase4_attempted_urls"],
        )
        self.assertEqual(
            ["fetch_failed", "fetch_failed"],
            state["metrics"]["flags"]["phase4_attempt_failure_reasons"],
        )
        self.assertEqual(2, state["metrics"]["counters"]["phase4_candidate_attempt_count"])
        self.assertEqual(2, state["metrics"]["counters"]["phase4_candidate_failure_count"])
        self.assertEqual(1, state["metrics"]["counters"]["phase4_candidate_exhausted_count"])
        artifact = json.loads((root / "outputs" / "article.json").read_text(encoding="utf-8"))
        self.assertEqual("all_candidates_failed", artifact["extraction_status"])
        self.assertEqual(SCRAPE_POLICY_FULL, artifact["scrape_policy"])
        self.assertFalse((root / "outputs" / "article_raw.html").exists())
        self.assertTrue((root / "outputs" / "article.json").exists())

    def test_article_payload_matches_schema_definition(self) -> None:
        root = self._make_temp_root()
        state = self._make_state()
        selected_url = "https://example.com/schema"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Schema Story",
                "source": "Example",
                "published_at": "2026-01-04T00:00:00Z",
                "scrape_policy": SCRAPE_POLICY_FULL,
            }
        ]
        html = (
            "<html><head><title>Schema Title</title></head><body>"
            "<article><p>This paragraph is long enough to satisfy schema and extraction checks for this test.</p></article>"
            "</body></html>"
        )
        final_state = self._run_extractor(root=root, state=state, html=html)
        article = final_state["article"]

        schema_path = Path(__file__).resolve().parent.parent / "schemas" / "article_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self._assert_payload_matches_schema(article, schema)

    def test_schema_guard_catches_invalid_scrape_policy_enum(self) -> None:
        schema_path = Path(__file__).resolve().parent.parent / "schemas" / "article_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        invalid_payload: dict[str, Any] = {
            "title": "Bad",
            "author": "",
            "published_at": "",
            "source_url": "https://example.com",
            "paragraphs": [],
            "scrape_policy": "invalid_policy",
            "metadata_only": False,
            "extraction_status": "extracted",
            "policy_resolution_failed": False,
        }

        with self.assertRaises(AssertionError):
            self._assert_payload_matches_schema(invalid_payload, schema)

    def test_no_raw_html_is_exposed_in_article_payload(self) -> None:
        root = self._make_temp_root()
        state = self._make_state()
        selected_url = "https://example.com/no-html-leak"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "HTML Leak Guard",
                "source": "Example",
                "published_at": "2026-01-05T00:00:00Z",
                "scrape_policy": SCRAPE_POLICY_FULL,
            }
        ]
        html = (
            "<html><head><title>Guard Title</title></head><body>"
            "<article><p>Paragraph with <strong>inline tags</strong> that must be normalized to plain text.</p></article>"
            "</body></html>"
        )
        final_state = self._run_extractor(root=root, state=state, html=html)
        article = final_state["article"]

        self.assertNotIn("raw_html", article)
        self.assertNotIn("html", article)
        self.assertFalse(self.HTML_TAG_PATTERN.search(article["title"]))
        self.assertFalse(self.HTML_TAG_PATTERN.search(article["author"]))
        for paragraph in article["paragraphs"]:
            self.assertFalse(self.HTML_TAG_PATTERN.search(paragraph))

    def test_blocks_non_public_url_before_fetch(self) -> None:
        root = self._make_temp_root()
        state = self._make_state()
        selected_url = "http://127.0.0.1/internal"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Loopback Story",
                "source": "Example",
                "published_at": "2026-01-05T00:00:00Z",
                "scrape_policy": SCRAPE_POLICY_FULL,
            }
        ]
        with patch.object(article_extractor, "_fetch_html") as fetch_html:
            with self.assertRaises(RuntimeError):
                self._run_extractor(root=root, state=state)

        artifact = json.loads((root / "outputs" / "article.json").read_text(encoding="utf-8"))
        self.assertEqual("all_candidates_failed", artifact["extraction_status"])
        self.assertFalse((root / "outputs" / "article_raw.html").exists())
        fetch_html.assert_not_called()

    def test_fetch_html_blocks_redirect_to_private_destination(self) -> None:
        first_response = Mock()
        first_response.__enter__ = Mock(return_value=first_response)
        first_response.__exit__ = Mock(return_value=False)
        first_response.status_code = 302
        first_response.headers = {"Location": "http://127.0.0.1/private"}
        first_response.iter_content = Mock(return_value=iter(()))
        first_response.encoding = "utf-8"
        first_response.apparent_encoding = "utf-8"

        with patch.object(article_extractor.requests, "get", return_value=first_response):
            with self.assertRaises(ValueError):
                article_extractor._fetch_html("https://example.com/public")

    def test_fetch_html_blocks_private_effective_destination_ip(self) -> None:
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status_code = 200
        response.headers = {}
        response.iter_content = Mock(return_value=iter((b"<html></html>",)))
        response.encoding = "utf-8"
        response.apparent_encoding = "utf-8"
        response.raise_for_status = Mock()

        with (
            patch.object(article_extractor.requests, "get", return_value=response),
            patch.object(article_extractor, "_extract_response_peer_ip", return_value="127.0.0.1"),
        ):
            with self.assertRaises(ValueError):
                article_extractor._fetch_html("https://example.com/public")

    def test_fetch_html_blocks_when_effective_destination_ip_is_unavailable(self) -> None:
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status_code = 200
        response.headers = {}
        response.iter_content = Mock(return_value=iter((b"<html></html>",)))
        response.encoding = "utf-8"
        response.apparent_encoding = "utf-8"
        response.raise_for_status = Mock()

        with (
            patch.object(article_extractor.requests, "get", return_value=response),
            patch.object(article_extractor, "_extract_response_peer_ip", return_value=None),
        ):
            with self.assertRaises(ValueError):
                article_extractor._fetch_html("https://example.com/public")

    def test_output_dir_rejects_absolute_configured_path(self) -> None:
        root = self._make_temp_root()
        with (
            patch.object(article_extractor, "_project_root", return_value=root),
            patch.object(
                article_extractor,
                "_load_runtime_pipeline_config",
                return_value={"output_dir": "C:/escape"},
            ),
        ):
            with self.assertRaises(ValueError):
                article_extractor._output_dir()

    def test_output_dir_rejects_parent_traversal_configured_path(self) -> None:
        root = self._make_temp_root()
        with (
            patch.object(article_extractor, "_project_root", return_value=root),
            patch.object(
                article_extractor,
                "_load_runtime_pipeline_config",
                return_value={"output_dir": "../outside"},
            ),
        ):
            with self.assertRaises(ValueError):
                article_extractor._output_dir()

    def test_db_policy_lookup_rejects_absolute_database_path_without_db_init(self) -> None:
        with (
            patch.object(
                article_extractor,
                "_load_runtime_pipeline_config",
                return_value={"database_path": "C:/outside/app.sqlite"},
            ),
            patch.object(article_extractor, "initialize_database") as db_init,
        ):
            policy = article_extractor._resolve_policy_from_db("https://example.com/story")

        self.assertIsNone(policy)
        db_init.assert_not_called()

    def test_db_policy_lookup_rejects_parent_traversal_database_path_without_db_init(self) -> None:
        with (
            patch.object(
                article_extractor,
                "_load_runtime_pipeline_config",
                return_value={"database_path": "../outside/app.sqlite"},
            ),
            patch.object(article_extractor, "initialize_database") as db_init,
        ):
            policy = article_extractor._resolve_policy_from_db("https://example.com/story")

        self.assertIsNone(policy)
        db_init.assert_not_called()


if __name__ == "__main__":
    unittest.main()
