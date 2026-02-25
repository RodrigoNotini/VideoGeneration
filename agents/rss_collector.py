"""Phase 0 stub RSS collector."""

from __future__ import annotations

from core.state import PipelineState, copy_state


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    next_state["rss_items"] = [
        {
            "id": "rss_stub_001",
            "source": "phase0",
            "title": "Deterministic AI infrastructure placeholder headline",
            "url": "https://example.com/phase0/rss_stub_001",
            "published_at": "2026-01-01T00:00:00Z",
        }
    ]
    next_state["metrics"]["counters"]["rss_items_count"] = len(next_state["rss_items"])
    return next_state
