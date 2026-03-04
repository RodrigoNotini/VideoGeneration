"""Strict config loader for Phase 0."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

from core.common.utils import ALLOWED_SCRAPE_POLICIES, resolve_scrape_policy


class ConfigError(ValueError):
    """Raised when config files are missing or invalid."""


CONFIG_FILE_MAP: dict[str, str] = {
    "rss_feeds": "configs/rss_feeds.yaml",
    "openai": "configs/openai.yaml",
    "pipeline": "configs/pipeline.yaml",
}


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Missing config file: {path.as_posix()}")

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if not isinstance(loaded, dict):
        raise ConfigError(f"Config must be a mapping: {path.as_posix()}")
    return loaded


def _require_keys(mapping: dict[str, Any], keys: tuple[str, ...], file_label: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ConfigError(f"{file_label} missing required keys: {missing}")


def _validate_rss_config(config: dict[str, Any]) -> None:
    _require_keys(config, ("feeds",), "configs/rss_feeds.yaml")
    feeds = config["feeds"]
    if not isinstance(feeds, list) or not feeds:
        raise ConfigError("configs/rss_feeds.yaml 'feeds' must be a non-empty list")
    for index, feed in enumerate(feeds):
        if not isinstance(feed, dict):
            raise ConfigError(f"Feed entry at index {index} must be a mapping")
        _require_keys(feed, ("name", "url", "scrape_policy"), f"configs/rss_feeds.yaml feed[{index}]")
        for key in ("name", "url", "scrape_policy"):
            if not isinstance(feed[key], str) or not feed[key].strip():
                raise ConfigError(f"configs/rss_feeds.yaml feed[{index}].{key} must be a non-empty string")
        try:
            resolve_scrape_policy(feed["scrape_policy"], fallback_to_full=False)
        except ValueError as error:
            raise ConfigError(
                "configs/rss_feeds.yaml "
                f"feed[{index}].scrape_policy must be one of: {', '.join(sorted(ALLOWED_SCRAPE_POLICIES))}"
            ) from error


def _validate_openai_config(config: dict[str, Any]) -> None:
    _require_keys(config, ("api_key_env_var", "models"), "configs/openai.yaml")
    if not isinstance(config["api_key_env_var"], str) or not config["api_key_env_var"].strip():
        raise ConfigError("configs/openai.yaml 'api_key_env_var' must be a non-empty string")
    models = config["models"]
    if not isinstance(models, dict):
        raise ConfigError("configs/openai.yaml 'models' must be a mapping")
    _require_keys(
        models,
        (
            "theme_selector",
            "interestingness_ranker",
            "embeddings",
            "script_writer",
            "image_generator",
            "tts",
        ),
        "configs/openai.yaml models",
    )
    for key in (
        "theme_selector",
        "interestingness_ranker",
        "embeddings",
        "script_writer",
        "image_generator",
        "tts",
    ):
        if not isinstance(models[key], str) or not models[key].strip():
            raise ConfigError(f"configs/openai.yaml models.{key} must be a non-empty string")


def _validate_pipeline_config(config: dict[str, Any]) -> None:
    _require_keys(
        config,
        (
            "name",
            "phase",
            "phase_name",
            "topic",
            "theme",
            "target_platform",
            "target_duration_sec",
            "max_articles_per_run",
            "rss_skip_fetch_threshold",
            "rss_retention_days",
            "rss_feed_rotation_basis",
            "phase2_selector",
            "phase3_ranker",
            "phase5_script_writer",
            "output_dir",
            "database_path",
            "deterministic_seed",
            "deterministic_started_at",
            "versions",
        ),
        "configs/pipeline.yaml",
    )
    if not isinstance(config["phase"], int):
        raise ConfigError("configs/pipeline.yaml 'phase' must be an integer")
    if not isinstance(config["target_duration_sec"], int):
        raise ConfigError("configs/pipeline.yaml 'target_duration_sec' must be an integer")
    if not isinstance(config["max_articles_per_run"], int) or config["max_articles_per_run"] < 1:
        raise ConfigError("configs/pipeline.yaml 'max_articles_per_run' must be an integer >= 1")
    if (
        not isinstance(config["rss_skip_fetch_threshold"], int)
        or config["rss_skip_fetch_threshold"] < 1
    ):
        raise ConfigError("configs/pipeline.yaml 'rss_skip_fetch_threshold' must be an integer >= 1")
    if not isinstance(config["rss_retention_days"], int) or config["rss_retention_days"] < 1:
        raise ConfigError("configs/pipeline.yaml 'rss_retention_days' must be an integer >= 1")
    if (
        not isinstance(config["rss_feed_rotation_basis"], str)
        or not config["rss_feed_rotation_basis"].strip()
    ):
        raise ConfigError("configs/pipeline.yaml 'rss_feed_rotation_basis' must be a non-empty string")
    allowed_rotation_bases = {"utc_date"}
    if config["rss_feed_rotation_basis"] not in allowed_rotation_bases:
        raise ConfigError(
            "configs/pipeline.yaml 'rss_feed_rotation_basis' must be one of: "
            + ", ".join(sorted(allowed_rotation_bases))
        )
    string_keys = (
        "name",
        "phase_name",
        "topic",
        "theme",
        "target_platform",
        "output_dir",
        "database_path",
        "deterministic_seed",
        "deterministic_started_at",
    )
    for key in string_keys:
        if not isinstance(config[key], str) or not config[key].strip():
            raise ConfigError(f"configs/pipeline.yaml '{key}' must be a non-empty string")
    if config["theme"] not in {"AI", "Tech"}:
        raise ConfigError("configs/pipeline.yaml 'theme' must be one of: AI, Tech")

    selector = config["phase2_selector"]
    if not isinstance(selector, dict):
        raise ConfigError("configs/pipeline.yaml 'phase2_selector' must be a mapping")
    _require_keys(
        selector,
        (
            "model",
            "prompt_version",
            "target_count",
            "lower_bound",
            "upper_bound",
            "tie_break_policy",
            "replacement_enabled",
            "replacement_worst_count",
            "replacement_score_tol",
            "replacement_freshness_days",
            "replacement_history_semantics",
            "deterministic",
        ),
        "configs/pipeline.yaml phase2_selector",
    )
    for key in ("model", "prompt_version", "tie_break_policy"):
        if not isinstance(selector[key], str) or not selector[key].strip():
            raise ConfigError(f"configs/pipeline.yaml phase2_selector.{key} must be a non-empty string")
    for key in ("target_count", "lower_bound", "upper_bound"):
        if not isinstance(selector[key], int) or selector[key] < 1:
            raise ConfigError(f"configs/pipeline.yaml phase2_selector.{key} must be an integer >= 1")
    if not isinstance(selector["replacement_enabled"], bool):
        raise ConfigError("configs/pipeline.yaml phase2_selector.replacement_enabled must be a boolean")
    if not isinstance(selector["replacement_worst_count"], int) or selector["replacement_worst_count"] < 1:
        raise ConfigError(
            "configs/pipeline.yaml phase2_selector.replacement_worst_count must be an integer >= 1"
        )
    replacement_score_tol = selector["replacement_score_tol"]
    if not isinstance(replacement_score_tol, (int, float)):
        raise ConfigError("configs/pipeline.yaml phase2_selector.replacement_score_tol must be numeric")
    if not math.isfinite(float(replacement_score_tol)):
        raise ConfigError("configs/pipeline.yaml phase2_selector.replacement_score_tol must be finite")
    if float(replacement_score_tol) < 0 or float(replacement_score_tol) > 1:
        raise ConfigError("configs/pipeline.yaml phase2_selector.replacement_score_tol must be in [0, 1]")
    if (
        not isinstance(selector["replacement_freshness_days"], int)
        or selector["replacement_freshness_days"] < 1
    ):
        raise ConfigError(
            "configs/pipeline.yaml phase2_selector.replacement_freshness_days must be an integer >= 1"
        )
    if (
        not isinstance(selector["replacement_history_semantics"], str)
        or not selector["replacement_history_semantics"].strip()
    ):
        raise ConfigError(
            "configs/pipeline.yaml phase2_selector.replacement_history_semantics must be a non-empty string"
        )
    allowed_replacement_semantics = {"max_per_url_theme"}
    if selector["replacement_history_semantics"] not in allowed_replacement_semantics:
        raise ConfigError(
            "configs/pipeline.yaml phase2_selector.replacement_history_semantics must be one of: "
            + ", ".join(sorted(allowed_replacement_semantics))
        )
    if selector["lower_bound"] > selector["upper_bound"]:
        raise ConfigError("configs/pipeline.yaml phase2_selector.lower_bound must be <= upper_bound")
    if selector["target_count"] < selector["lower_bound"] or selector["target_count"] > selector["upper_bound"]:
        raise ConfigError(
            "configs/pipeline.yaml phase2_selector.target_count must be within [lower_bound, upper_bound]"
        )
    deterministic = selector["deterministic"]
    if not isinstance(deterministic, dict):
        raise ConfigError("configs/pipeline.yaml phase2_selector.deterministic must be a mapping")
    _require_keys(
        deterministic,
        ("temperature", "top_p"),
        "configs/pipeline.yaml phase2_selector.deterministic",
    )
    for key in ("temperature", "top_p"):
        value = deterministic[key]
        if not isinstance(value, (int, float)):
            raise ConfigError(
                f"configs/pipeline.yaml phase2_selector.deterministic.{key} must be numeric"
            )
    if float(deterministic["temperature"]) < 0:
        raise ConfigError("configs/pipeline.yaml phase2_selector.deterministic.temperature must be >= 0")
    if float(deterministic["top_p"]) <= 0 or float(deterministic["top_p"]) > 1:
        raise ConfigError("configs/pipeline.yaml phase2_selector.deterministic.top_p must be in (0, 1]")

    ranker = config["phase3_ranker"]
    if not isinstance(ranker, dict):
        raise ConfigError("configs/pipeline.yaml 'phase3_ranker' must be a mapping")
    _require_keys(
        ranker,
        (
            "model",
            "prompt_version",
            "criteria_policy_version",
            "target_selection_count",
            "tie_break_policy",
            "timeout_seconds",
            "deterministic",
            "stability",
        ),
        "configs/pipeline.yaml phase3_ranker",
    )
    for key in ("model", "prompt_version", "criteria_policy_version", "tie_break_policy"):
        if not isinstance(ranker[key], str) or not ranker[key].strip():
            raise ConfigError(f"configs/pipeline.yaml phase3_ranker.{key} must be a non-empty string")
    if not isinstance(ranker["target_selection_count"], int):
        raise ConfigError("configs/pipeline.yaml phase3_ranker.target_selection_count must be an integer")
    if ranker["target_selection_count"] != 1:
        raise ConfigError("configs/pipeline.yaml phase3_ranker.target_selection_count must be 1")

    ranker_deterministic = ranker["deterministic"]
    if not isinstance(ranker_deterministic, dict):
        raise ConfigError("configs/pipeline.yaml phase3_ranker.deterministic must be a mapping")
    _require_keys(
        ranker_deterministic,
        ("temperature", "top_p"),
        "configs/pipeline.yaml phase3_ranker.deterministic",
    )
    for key in ("temperature", "top_p"):
        value = ranker_deterministic[key]
        if not isinstance(value, (int, float)):
            raise ConfigError(
                f"configs/pipeline.yaml phase3_ranker.deterministic.{key} must be numeric"
            )
        if not math.isfinite(float(value)):
            raise ConfigError(
                f"configs/pipeline.yaml phase3_ranker.deterministic.{key} must be finite"
            )
    if float(ranker_deterministic["temperature"]) < 0:
        raise ConfigError("configs/pipeline.yaml phase3_ranker.deterministic.temperature must be >= 0")
    if float(ranker_deterministic["top_p"]) <= 0 or float(ranker_deterministic["top_p"]) > 1:
        raise ConfigError("configs/pipeline.yaml phase3_ranker.deterministic.top_p must be in (0, 1]")

    ranker_timeout_seconds = ranker["timeout_seconds"]
    if not isinstance(ranker_timeout_seconds, (int, float)):
        raise ConfigError("configs/pipeline.yaml phase3_ranker.timeout_seconds must be numeric")
    if not math.isfinite(float(ranker_timeout_seconds)):
        raise ConfigError("configs/pipeline.yaml phase3_ranker.timeout_seconds must be finite")
    if float(ranker_timeout_seconds) <= 0:
        raise ConfigError("configs/pipeline.yaml phase3_ranker.timeout_seconds must be > 0")

    stability = ranker["stability"]
    if not isinstance(stability, dict):
        raise ConfigError("configs/pipeline.yaml phase3_ranker.stability must be a mapping")
    _require_keys(
        stability,
        ("min_overlap_ratio",),
        "configs/pipeline.yaml phase3_ranker.stability",
    )
    min_overlap_ratio = stability["min_overlap_ratio"]
    if not isinstance(min_overlap_ratio, (int, float)):
        raise ConfigError("configs/pipeline.yaml phase3_ranker.stability.min_overlap_ratio must be numeric")
    if not math.isfinite(float(min_overlap_ratio)):
        raise ConfigError("configs/pipeline.yaml phase3_ranker.stability.min_overlap_ratio must be finite")
    if float(min_overlap_ratio) <= 0 or float(min_overlap_ratio) > 1:
        raise ConfigError("configs/pipeline.yaml phase3_ranker.stability.min_overlap_ratio must be in (0, 1]")

    script_writer = config["phase5_script_writer"]
    if not isinstance(script_writer, dict):
        raise ConfigError("configs/pipeline.yaml 'phase5_script_writer' must be a mapping")
    _require_keys(
        script_writer,
        ("model", "prompt_version", "schema_path", "deterministic", "timeout_seconds"),
        "configs/pipeline.yaml phase5_script_writer",
    )
    for key in ("model", "prompt_version", "schema_path"):
        if not isinstance(script_writer[key], str) or not script_writer[key].strip():
            raise ConfigError(
                f"configs/pipeline.yaml phase5_script_writer.{key} must be a non-empty string"
            )
    schema_path = Path(script_writer["schema_path"])
    if schema_path.is_absolute() or schema_path.drive:
        raise ConfigError(
            "configs/pipeline.yaml phase5_script_writer.schema_path must be a project-relative path"
        )
    if any(part == ".." for part in schema_path.parts):
        raise ConfigError(
            "configs/pipeline.yaml phase5_script_writer.schema_path must not traverse parent directories"
        )

    script_writer_deterministic = script_writer["deterministic"]
    if not isinstance(script_writer_deterministic, dict):
        raise ConfigError("configs/pipeline.yaml phase5_script_writer.deterministic must be a mapping")
    _require_keys(
        script_writer_deterministic,
        ("temperature", "top_p"),
        "configs/pipeline.yaml phase5_script_writer.deterministic",
    )
    for key in ("temperature", "top_p"):
        value = script_writer_deterministic[key]
        if not isinstance(value, (int, float)):
            raise ConfigError(
                f"configs/pipeline.yaml phase5_script_writer.deterministic.{key} must be numeric"
            )
        if not math.isfinite(float(value)):
            raise ConfigError(
                f"configs/pipeline.yaml phase5_script_writer.deterministic.{key} must be finite"
            )
    if float(script_writer_deterministic["temperature"]) < 0:
        raise ConfigError(
            "configs/pipeline.yaml phase5_script_writer.deterministic.temperature must be >= 0"
        )
    if (
        float(script_writer_deterministic["top_p"]) <= 0
        or float(script_writer_deterministic["top_p"]) > 1
    ):
        raise ConfigError("configs/pipeline.yaml phase5_script_writer.deterministic.top_p must be in (0, 1]")

    timeout_seconds = script_writer["timeout_seconds"]
    if not isinstance(timeout_seconds, (int, float)):
        raise ConfigError("configs/pipeline.yaml phase5_script_writer.timeout_seconds must be numeric")
    if not math.isfinite(float(timeout_seconds)):
        raise ConfigError("configs/pipeline.yaml phase5_script_writer.timeout_seconds must be finite")
    if float(timeout_seconds) <= 0:
        raise ConfigError("configs/pipeline.yaml phase5_script_writer.timeout_seconds must be > 0")

    versions = config["versions"]
    if not isinstance(versions, dict):
        raise ConfigError("configs/pipeline.yaml 'versions' must be a mapping")
    _require_keys(
        versions,
        ("prompt_version", "schema_version", "template_version", "model_version"),
        "configs/pipeline.yaml versions",
    )


def load_all_configs(project_root: Path) -> dict[str, dict[str, Any]]:
    """Load and strictly validate all required config files."""
    configs: dict[str, dict[str, Any]] = {}
    for key, rel_path in CONFIG_FILE_MAP.items():
        configs[key] = _load_yaml_file(project_root / rel_path)

    _validate_rss_config(configs["rss_feeds"])
    _validate_openai_config(configs["openai"])
    _validate_pipeline_config(configs["pipeline"])
    return configs
