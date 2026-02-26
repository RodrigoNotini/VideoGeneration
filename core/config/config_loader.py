"""Strict config loader for Phase 0."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


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
        _require_keys(feed, ("name", "url"), f"configs/rss_feeds.yaml feed[{index}]")
        for key in ("name", "url"):
            if not isinstance(feed[key], str) or not feed[key].strip():
                raise ConfigError(f"configs/rss_feeds.yaml feed[{index}].{key} must be a non-empty string")


def _validate_openai_config(config: dict[str, Any]) -> None:
    _require_keys(config, ("api_key_env_var", "models"), "configs/openai.yaml")
    if not isinstance(config["api_key_env_var"], str) or not config["api_key_env_var"].strip():
        raise ConfigError("configs/openai.yaml 'api_key_env_var' must be a non-empty string")
    models = config["models"]
    if not isinstance(models, dict):
        raise ConfigError("configs/openai.yaml 'models' must be a mapping")
    _require_keys(
        models,
        ("embeddings", "script_writer", "image_generator", "tts"),
        "configs/openai.yaml models",
    )
    for key in ("embeddings", "script_writer", "image_generator", "tts"):
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
            "target_platform",
            "target_duration_sec",
            "max_articles_per_run",
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
    string_keys = (
        "name",
        "phase_name",
        "topic",
        "target_platform",
        "output_dir",
        "database_path",
        "deterministic_seed",
        "deterministic_started_at",
    )
    for key in string_keys:
        if not isinstance(config[key], str) or not config[key].strip():
            raise ConfigError(f"configs/pipeline.yaml '{key}' must be a non-empty string")
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
