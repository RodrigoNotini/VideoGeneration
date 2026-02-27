"""Phase 0 stub relevance ranker."""

from __future__ import annotations

from core.state import PipelineState, copy_state


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    candidate_items = next_state["ranked_items"] if next_state["ranked_items"] else next_state["rss_items"]
    ranked = []
    for index, item in enumerate(candidate_items, start=1):
        ranked.append(
            {
                "rank": index,
                "score": 1.0 / index,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
            }
        )
    next_state["ranked_items"] = ranked
    next_state["selected_url"] = ranked[0]["url"] if ranked else "https://example.com/phase0/no_selection"
    next_state["metrics"]["counters"]["ranked_items_count"] = len(ranked)
    return next_state
