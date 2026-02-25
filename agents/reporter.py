"""Reporter skeleton for Phase 0 observability."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from core.state import PipelineState, copy_state
from core.common.utils import canonical_json, sha256_text


def _parse_iso_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Reporter:
    """Tracks deterministic run metadata and structured metrics."""

    def __init__(
        self,
        *,
        phase_name: str,
        version_info: dict[str, str],
        deterministic_seed: str,
        deterministic_started_at: str,
    ) -> None:
        self.phase_name = phase_name
        self.version_info = deepcopy(version_info)
        self.deterministic_seed = deterministic_seed
        self._base_dt = _parse_iso_utc(deterministic_started_at)
        self._tick = 0
        self.started_at = _to_iso_utc(self._base_dt)
        self.finished_at = self.started_at
        self.status = "in_progress"

        run_identity = canonical_json(
            {
                "phase_name": self.phase_name,
                "deterministic_seed": self.deterministic_seed,
                "started_at": self.started_at,
                "version_info": self.version_info,
            }
        )
        self.run_id = "run_" + sha256_text(run_identity)[:12]

        self.metrics: dict[str, Any] = {
            "stages": {},
            "counters": {"completed_stages": 0},
            "flags": {"deterministic_clock": True, "phase0_stub_mode": True},
        }

    def _next_timestamp(self) -> str:
        instant = self._base_dt + timedelta(seconds=self._tick)
        self._tick += 1
        return _to_iso_utc(instant)

    def stage_started(self, stage_name: str) -> None:
        started_at = self._next_timestamp()
        stage = self.metrics["stages"].setdefault(stage_name, {})
        stage["started_at"] = started_at
        stage["status"] = "in_progress"

    def stage_finished(self, stage_name: str, *, note: str = "stub-stage") -> None:
        finished_at = self._next_timestamp()
        stage = self.metrics["stages"].setdefault(stage_name, {})
        stage["finished_at"] = finished_at
        stage["status"] = "done"
        stage["note"] = note
        self.metrics["counters"]["completed_stages"] += 1

    def sync_state_metrics(self, state: PipelineState) -> PipelineState:
        """Mirror reporter metrics into pipeline state."""
        next_state = copy_state(state)
        next_state["metrics"]["stages"] = deepcopy(self.metrics["stages"])
        next_state["metrics"]["counters"] = deepcopy(self.metrics["counters"])
        next_state["metrics"]["flags"] = deepcopy(self.metrics["flags"])
        return next_state

    def finalize(
        self,
        *,
        final_state: PipelineState,
        status: str,
        artifacts: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.status = status
        self.finished_at = self._next_timestamp()
        return {
            "run_id": self.run_id,
            "phase_name": self.phase_name,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "version_info": deepcopy(self.version_info),
            "metrics": deepcopy(self.metrics),
            "state_checksum": sha256_text(canonical_json(final_state)),
            "artifacts": artifacts,
        }


def run(state: PipelineState) -> PipelineState:
    """Reporter agent node placeholder."""
    next_state = copy_state(state)
    next_state["metrics"]["flags"]["reporter_node_executed"] = True
    return next_state
