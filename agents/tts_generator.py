"""Phase 0 stub TTS generator."""

from __future__ import annotations

from core.state import PipelineState, copy_state


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    next_state["narration_audio_path"] = "outputs/audio/phase0_narration.wav"
    next_state["metrics"]["counters"]["audio_duration_sec"] = next_state["target_duration_sec"]
    return next_state
