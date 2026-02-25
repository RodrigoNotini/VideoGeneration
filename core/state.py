"""Phase 0 deterministic pipeline state contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, TypedDict


class PipelineState(TypedDict):
    """Frozen field contract for the LangGraph shared state."""

    topic: str
    target_platform: str
    target_duration_sec: int
    rss_items: list[dict[str, Any]]
    ranked_items: list[dict[str, Any]]
    selected_url: str
    article: dict[str, Any]
    script_json: dict[str, Any]
    generated_images: list[str]
    narration_audio_path: str
    render_output_path: str
    metrics: dict[str, Any]
    version_info: dict[str, str]


INITIAL_STATE: PipelineState = {
    "topic": "AI & Tech Daily Briefing",
    "target_platform": "youtube_shorts",
    "target_duration_sec": 45,
    "rss_items": [],
    "ranked_items": [],
    "selected_url": "",
    "article": {
        "title": "Phase 0 placeholder article",
        "source": "placeholder://source",
        "paragraphs": [],
    },
    "script_json": {
        "video_title": "Phase 0 placeholder script",
        "source_line": "Source: placeholder://source",
        "hook": "Phase 0 deterministic hook.",
        "scenes": [],
        "cta": "Subscribe for daily AI updates.",
    },
    "generated_images": [],
    "narration_audio_path": "outputs/audio/placeholder_narration.wav",
    "render_output_path": "outputs/videos/placeholder_render.mp4",
    "metrics": {
        "stages": {},
        "counters": {},
        "flags": {},
    },
    "version_info": {
        "prompt_version": "phase0-placeholder",
        "schema_version": "phase0-placeholder",
        "template_version": "v1-placeholder",
        "model_version": "phase0-placeholder",
    },
}

REQUIRED_STATE_KEYS: tuple[str, ...] = tuple(INITIAL_STATE.keys())


def make_initial_state(
    topic: str,
    target_platform: str,
    target_duration_sec: int,
    version_info: dict[str, str],
) -> PipelineState:
    """Build deterministic initial state with explicit required inputs."""
    state = deepcopy(INITIAL_STATE)
    state["topic"] = topic
    state["target_platform"] = target_platform
    state["target_duration_sec"] = target_duration_sec
    state["version_info"] = deepcopy(version_info)
    assert_state_contract(state)
    return state


def copy_state(state: PipelineState) -> PipelineState:
    """Deep copy helper to avoid accidental in-place mutation."""
    return deepcopy(state)


def assert_state_contract(state: dict[str, Any]) -> None:
    """Validate exact state contract: no missing and no extra fields."""
    missing = [key for key in REQUIRED_STATE_KEYS if key not in state]
    extra = [key for key in state.keys() if key not in REQUIRED_STATE_KEYS]

    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing keys={missing}")
        if extra:
            parts.append(f"extra keys={extra}")
        raise ValueError("Invalid state contract: " + ", ".join(parts))
