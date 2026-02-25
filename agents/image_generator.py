"""Phase 0 stub image generator."""

from __future__ import annotations

from core.state import PipelineState, copy_state


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    next_state["generated_images"] = [
        "outputs/images/placeholder_01.png",
        "outputs/images/placeholder_02.png",
        "outputs/images/placeholder_03.png",
    ]
    next_state["metrics"]["counters"]["generated_images_count"] = len(next_state["generated_images"])
    return next_state
