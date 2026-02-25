"""Phase 0 stub relevance ranker."""

from __future__ import annotations

from core.state import PipelineState, copy_state


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    ranked = []
    for index, item in enumerate(next_state["rss_items"], start=1):
        ranked.append(
            {
                "rank": index,
                "score": 1.0 / index,
                "title": item["title"],
                "url": item["url"],
            }
        )
    next_state["ranked_items"] = ranked
    next_state["selected_url"] = ranked[0]["url"] if ranked else "https://example.com/phase0/no_selection"
    next_state["metrics"]["counters"]["ranked_items_count"] = len(ranked)
    return next_state
