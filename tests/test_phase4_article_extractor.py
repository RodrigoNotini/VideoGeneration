"""Phase 4 article extraction exit criteria tests."""

from __future__ import annotations

import json
import re
import shutil
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents import article_extractor
from core.common.utils import SCRAPE_POLICY_FULL, SCRAPE_POLICY_METADATA_ONLY
from core.state import PipelineState, make_initial_state


class Phase4ArticleExtractorTests(unittest.TestCase):
    HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

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

    def test_metadata_only_policy_skips_fetch_and_raw_html_artifact(self) -> None:
        root = self._make_temp_root()
        state = self._make_state()
        selected_url = "https://example.com/metadata-only"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Blocked Story",
                "source": "Wired",
                "published_at": "2026-01-03T00:00:00Z",
                "scrape_policy": SCRAPE_POLICY_METADATA_ONLY,
            }
        ]

        with patch.object(article_extractor, "_fetch_html") as fetch_html:
            final_state = self._run_extractor(root=root, state=state)

        self.assertTrue(final_state["article"]["metadata_only"])
        self.assertEqual("policy_blocked", final_state["article"]["extraction_status"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_policy_blocked_metadata_only"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_html_fetch_attempted"])
        self.assertFalse((root / "outputs" / "article_raw.html").exists())
        self.assertTrue((root / "outputs" / "article.json").exists())
        fetch_html.assert_not_called()

    def test_fetch_failure_returns_structured_article_json(self) -> None:
        root = self._make_temp_root()
        state = self._make_state()
        selected_url = "https://example.com/fetch-failure"
        state["selected_url"] = selected_url
        state["ranked_items"] = [
            {
                "url": selected_url,
                "title": "Failure Story",
                "source": "TechCrunch",
                "published_at": "2026-01-03T00:00:00Z",
                "summary": "Fallback summary content for extraction failure path.",
                "scrape_policy": SCRAPE_POLICY_FULL,
            }
        ]

        final_state = self._run_extractor(
            root=root,
            state=state,
            fetch_side_effect=RuntimeError("network down"),
        )

        article = final_state["article"]
        self.assertFalse(article["metadata_only"])
        self.assertEqual("fetch_failed", article["extraction_status"])
        self.assertEqual(SCRAPE_POLICY_FULL, article["scrape_policy"])
        self.assertFalse(final_state["metrics"]["flags"]["phase4_html_fetch_succeeded"])
        self.assertTrue(final_state["metrics"]["flags"]["phase4_extraction_failed"])
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
        required = set(schema.get("required", []))
        allowed = set(schema.get("properties", {}).keys())

        self.assertTrue(required.issubset(set(article.keys())))
        self.assertTrue(set(article.keys()).issubset(allowed))
        self.assertIsInstance(article["paragraphs"], list)

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


if __name__ == "__main__":
    unittest.main()
