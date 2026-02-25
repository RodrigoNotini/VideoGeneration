"""Phase 0 stub script validator."""

from __future__ import annotations

from core.state import PipelineState, copy_state


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    next_state["metrics"]["flags"]["script_validation_stub_passed"] = True
    next_state["metrics"]["counters"]["image_prompt_count"] = 3
    return next_state
