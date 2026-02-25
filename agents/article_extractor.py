"""Phase 0 stub article extractor."""

from __future__ import annotations

from core.state import PipelineState, copy_state


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    selected_url = next_state["selected_url"] or "https://example.com/phase0/no_selection"
    next_state["article"] = {
        "title": "Phase 0 article placeholder",
        "author": "Phase 0 System",
        "published_at": "2026-01-01T00:00:00Z",
        "source_url": selected_url,
        "paragraphs": [
            "This is a deterministic placeholder article paragraph.",
            "No scraping is performed in Phase 0.",
        ],
    }
    next_state["metrics"]["flags"]["article_stub_created"] = True
    return next_state
