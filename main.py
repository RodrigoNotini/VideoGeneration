"""Pipeline bootstrap entrypoint."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from agents.reporter import Reporter
from core.config.config_loader import ConfigError, load_all_configs
from core.config.env_validation import validate_environment
from core.persistence.db import initialize_database, save_artifact, save_run
from core.state import copy_state, make_initial_state
from core.common.utils import sha256_text, write_json
from graphs.news_to_video_graph import run_pipeline


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VideoGeneration pipeline")
    parser.add_argument(
        "--max-articles-per-run",
        type=int,
        default=None,
        help="Override max RSS articles fetched in this run (must be >= 1).",
    )
    return parser.parse_args()


def _artifact_checksum(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def _artifact_path_for_metadata(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    args = _parse_args()
    if args.max_articles_per_run is not None:
        if args.max_articles_per_run < 1:
            print("Argument error: --max-articles-per-run must be >= 1")
            return 1
        os.environ["VG_MAX_ARTICLES_PER_RUN"] = str(args.max_articles_per_run)

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

    output_dir = project_root / pipeline_cfg["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    rss_items_path = output_dir / "rss_items.json"
    metadata_path = output_dir / "metadata.json"

    connection = None
    final_state = None
    try:
        connection = initialize_database(db_path)
        final_state = run_pipeline(initial_state, reporter)

        write_json(state_path, final_state)
        write_json(rss_items_path, final_state["rss_items"])
        state_checksum = _artifact_checksum(state_path)
        rss_items_checksum = _artifact_checksum(rss_items_path)

        artifacts = [
            {"type": "state", "path": _artifact_path_for_metadata(state_path, project_root)},
            {"type": "rss_items", "path": _artifact_path_for_metadata(rss_items_path, project_root)},
            {"type": "metadata", "path": _artifact_path_for_metadata(metadata_path, project_root)},
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
            artifact_type="rss_items",
            artifact_path=artifacts[1]["path"],
            created_at=metadata["finished_at"],
            checksum=rss_items_checksum,
        )
        save_artifact(
            connection,
            run_id=metadata["run_id"],
            artifact_type="metadata",
            artifact_path=artifacts[2]["path"],
            created_at=metadata["finished_at"],
            checksum=_artifact_checksum(metadata_path),
        )

        print(f"Pipeline run completed. run_id={metadata['run_id']}")
        print(f"State written to {state_path.as_posix()}")
        print(f"RSS items written to {rss_items_path.as_posix()}")
        print(f"Metadata written to {metadata_path.as_posix()}")
        return 0
    except Exception as error:
        failure_state = copy_state(final_state) if final_state is not None else copy_state(initial_state)
        failure_flags = failure_state["metrics"]["flags"]
        failure_flags["pipeline_failed"] = True
        failure_flags["failure_stage"] = "run_pipeline"

        metadata_artifacts = [
            {"type": "metadata", "path": _artifact_path_for_metadata(metadata_path, project_root)},
        ]
        metadata = reporter.finalize(
            final_state=failure_state,
            status="failed",
            artifacts=metadata_artifacts,
        )
        write_json(metadata_path, metadata)

        if connection is not None:
            save_run(connection, metadata)
            save_artifact(
                connection,
                run_id=metadata["run_id"],
                artifact_type="metadata",
                artifact_path=metadata_artifacts[0]["path"],
                created_at=metadata["finished_at"],
                checksum=_artifact_checksum(metadata_path),
            )

        print(f"Pipeline run failed: {error}")
        print(f"Metadata written to {metadata_path.as_posix()}")
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
