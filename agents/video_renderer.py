"""Phase 0 stub video renderer."""

from __future__ import annotations

from core.state import PipelineState, copy_state


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    next_state["render_output_path"] = "outputs/videos/phase0_render.mp4"
    next_state["metrics"]["flags"]["render_stub_completed"] = True
    return next_state
