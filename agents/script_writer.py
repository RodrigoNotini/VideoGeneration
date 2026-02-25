"""Phase 0 stub script writer."""

from __future__ import annotations

from core.state import PipelineState, copy_state


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    source_url = next_state["selected_url"] or "https://example.com/phase0/no_selection"
    next_state["script_json"] = {
        "video_title": "AI & Tech Daily Placeholder",
        "source_line": f"Source: {source_url}",
        "hook": "AI headlines, fast and factual.",
        "scenes": [
            {"id": 1, "narration": "Welcome to today's AI and tech brief.", "image_prompt": "placeholder scene 1"},
            {"id": 2, "narration": "This pipeline is currently in bootstrap mode.", "image_prompt": "placeholder scene 2"},
            {"id": 3, "narration": "No live RSS fetching is enabled in this phase.", "image_prompt": "placeholder scene 3"},
            {"id": 4, "narration": "No embeddings, scraping, or model calls are active.", "image_prompt": None},
            {"id": 5, "narration": "State and observability contracts are now in place.", "image_prompt": None},
            {"id": 6, "narration": "Next phases will activate real generation logic.", "image_prompt": None},
        ],
        "cta": "Follow for daily AI updates.",
    }
    next_state["metrics"]["counters"]["scene_count"] = len(next_state["script_json"]["scenes"])
    return next_state
