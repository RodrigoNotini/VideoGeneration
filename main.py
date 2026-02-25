"""Phase 0 bootstrap entrypoint."""

from __future__ import annotations

from pathlib import Path

from agents.reporter import Reporter
from core.config.config_loader import ConfigError, load_all_configs
from core.config.env_validation import validate_environment
from core.persistence.db import initialize_database, save_artifact, save_run
from core.state import make_initial_state
from core.common.utils import sha256_text, write_json
from graphs.news_to_video_graph import run_pipeline


def _artifact_checksum(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def main() -> int:
    project_root = Path(__file__).resolve().parent

    try:
        configs = load_all_configs(project_root)
    except ConfigError as error:
        print(f"Configuration error: {error}")
        return 1

    pipeline_cfg = configs["pipeline"]
    openai_cfg = configs["openai"]

    missing_env = validate_environment(
        phase=pipeline_cfg["phase"],
        openai_api_key_var=openai_cfg["api_key_env_var"],
    )
    if missing_env:
        print(
            "Environment error: missing required variables for "
            f"phase {pipeline_cfg['phase']}: {', '.join(missing_env)}"
        )
        return 1

    db_path = project_root / pipeline_cfg["database_path"]
    connection = initialize_database(db_path)

    reporter = Reporter(
        phase_name=pipeline_cfg["phase_name"],
        version_info=pipeline_cfg["versions"],
        deterministic_seed=pipeline_cfg["deterministic_seed"],
        deterministic_started_at=pipeline_cfg["deterministic_started_at"],
    )

    initial_state = make_initial_state(
        topic=pipeline_cfg["topic"],
        target_platform=pipeline_cfg["target_platform"],
        target_duration_sec=pipeline_cfg["target_duration_sec"],
        version_info=pipeline_cfg["versions"],
    )
    final_state = run_pipeline(initial_state, reporter)

    output_dir = project_root / pipeline_cfg["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    metadata_path = output_dir / "metadata.json"

    write_json(state_path, final_state)
    state_checksum = _artifact_checksum(state_path)

    artifacts = [
        {"type": "state", "path": state_path.relative_to(project_root).as_posix()},
        {"type": "metadata", "path": metadata_path.relative_to(project_root).as_posix()},
    ]
    metadata = reporter.finalize(
        final_state=final_state,
        status="success",
        artifacts=artifacts,
        state_checksum=state_checksum,
    )
    write_json(metadata_path, metadata)

    save_run(connection, metadata)
    save_artifact(
        connection,
        run_id=metadata["run_id"],
        artifact_type="state",
        artifact_path=artifacts[0]["path"],
        created_at=metadata["finished_at"],
        checksum=state_checksum,
    )
    save_artifact(
        connection,
        run_id=metadata["run_id"],
        artifact_type="metadata",
        artifact_path=artifacts[1]["path"],
        created_at=metadata["finished_at"],
        checksum=_artifact_checksum(metadata_path),
    )
    connection.close()

    print(f"Phase 0 run completed. run_id={metadata['run_id']}")
    print(f"State written to {state_path.as_posix()}")
    print(f"Metadata written to {metadata_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
