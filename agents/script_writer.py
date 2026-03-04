"""Phase 5 script writer."""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.model_retry import score_with_retry_and_fallback
from core.common.utils import write_json
from core.config.config_loader import load_all_configs
from core.state import PipelineState, copy_state


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_PROMPT_VERSION = "phase5-script-writer-v1"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOP_P = 1.0
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_OUTPUT_DIR = "outputs"
NO_SELECTION_URL = "https://example.com/phase5/no_selection"
PROMPT_PATH = Path("prompts/script_writer/system.txt")
SCHEMA_PATH = Path("schemas/script_schema.json")


class ScriptWriterError(RuntimeError):
    """Structured Phase 5 script writer error."""


class ModelResponseError(ScriptWriterError):
    """Raised when model response cannot be parsed/validated."""


class ScriptWriterDependencyError(ScriptWriterError):
    """Raised when required script writer dependencies are unavailable."""


@dataclass(frozen=True)
class ScriptWriterInput:
    title: str
    author: str
    published_at: str
    source_url: str
    paragraphs: list[str]
    metadata_only: bool
    extraction_status: str
    selected_url: str
    selected_source: str


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _phase5_error(*, code: str, message: str, details: dict[str, Any] | None = None) -> str:
    payload = {
        "phase": 5,
        "agent": "script_writer",
        "code": code,
        "message": message,
        "details": details or {},
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _load_runtime_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    configs = load_all_configs(_project_root())
    return dict(configs["pipeline"]), dict(configs["openai"])


def _load_project_env() -> None:
    load_dotenv(dotenv_path=_project_root() / ".env", override=False)


def _script_writer_settings(pipeline_config: dict[str, Any], openai_config: dict[str, Any]) -> dict[str, Any]:
    phase5_cfg = pipeline_config.get("phase5_script_writer", {})
    if not isinstance(phase5_cfg, dict):
        phase5_cfg = {}

    deterministic_cfg = phase5_cfg.get("deterministic", {})
    if not isinstance(deterministic_cfg, dict):
        deterministic_cfg = {}

    openai_models = openai_config.get("models", {})
    if not isinstance(openai_models, dict):
        openai_models = {}

    openai_model_name = str(openai_models.get("script_writer") or "").strip()
    phase5_model_name = str(phase5_cfg.get("model") or "").strip()
    model_name = openai_model_name or phase5_model_name or DEFAULT_MODEL
    schema_path = str(phase5_cfg.get("schema_path") or "").strip()
    if schema_path:
        schema_path = Path(schema_path).as_posix()
    else:
        schema_path = SCHEMA_PATH.as_posix()
    openai_api_key_env_var = str(openai_config.get("api_key_env_var", "")).strip()
    prompt_version = str(phase5_cfg.get("prompt_version") or DEFAULT_PROMPT_VERSION).strip()
    try:
        temperature = float(deterministic_cfg.get("temperature", DEFAULT_TEMPERATURE))
        top_p = float(deterministic_cfg.get("top_p", DEFAULT_TOP_P))
        timeout_seconds = float(phase5_cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    except (TypeError, ValueError) as error:
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_writer_config",
                message="Phase 5 script writer config has invalid numeric values.",
                details={"phase5_script_writer": phase5_cfg},
            )
        ) from error

    if not model_name:
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_writer_config",
                message="Script writer model name cannot be empty.",
            )
        )
    if not openai_api_key_env_var:
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_writer_config",
                message="openai.api_key_env_var cannot be empty for script writer.",
            )
        )
    if not prompt_version:
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_writer_config",
                message="Phase 5 prompt_version cannot be empty.",
            )
        )
    if not math.isfinite(temperature) or temperature < 0.0:
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_writer_config",
                message="Phase 5 temperature must be finite and >= 0.",
                details={"temperature": temperature},
            )
        )
    if not math.isfinite(top_p) or top_p <= 0.0 or top_p > 1.0:
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_writer_config",
                message="Phase 5 top_p must be finite and in (0, 1].",
                details={"top_p": top_p},
            )
        )
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0.0:
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_writer_config",
                message="Phase 5 timeout_seconds must be finite and > 0.",
                details={"timeout_seconds": timeout_seconds},
            )
        )

    return {
        "model_name": model_name,
        "openai_api_key_env_var": openai_api_key_env_var,
        "prompt_version": prompt_version,
        "schema_path": schema_path,
        "temperature": temperature,
        "top_p": top_p,
        "timeout_seconds": round(timeout_seconds, 3),
    }


def _load_text_file(path: Path, *, missing_code: str, invalid_code: str) -> str:
    if not path.exists():
        raise ScriptWriterError(
            _phase5_error(
                code=missing_code,
                message=f"Missing required file: {path.as_posix()}",
                details={"path": path.as_posix()},
            )
        )
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ScriptWriterError(
            _phase5_error(
                code=invalid_code,
                message=f"File is empty: {path.as_posix()}",
                details={"path": path.as_posix()},
            )
        )
    return content


def _load_script_schema(path: Path) -> dict[str, Any]:
    raw_content = _load_text_file(
        path,
        missing_code="missing_script_schema",
        invalid_code="invalid_script_schema",
    )
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as error:
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_schema",
                message="Script schema file must contain valid JSON.",
                details={"path": path.as_posix(), "error": str(error)},
            )
        ) from error
    if not isinstance(parsed, dict):
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_schema",
                message="Script schema JSON must be an object.",
                details={"path": path.as_posix()},
            )
        )
    return parsed


def _selected_source_name(state: PipelineState, selected_url: str) -> str:
    for key in ("ranked_items", "rss_items"):
        raw_items = state.get(key)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if str(item.get("url", "")).strip() != selected_url:
                continue
            source = str(item.get("source", "")).strip()
            if source:
                return source
    return ""


def _normalize_script_input(state: PipelineState) -> ScriptWriterInput:
    raw_article = state.get("article")
    if not isinstance(raw_article, dict):
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_article_payload",
                message="state.article must be an object.",
                details={"received_type": type(raw_article).__name__},
            )
        )

    raw_paragraphs = raw_article.get("paragraphs", [])
    if raw_paragraphs is None:
        raw_paragraphs = []
    if not isinstance(raw_paragraphs, list):
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_article_payload",
                message="state.article.paragraphs must be an array.",
                details={"received_type": type(raw_paragraphs).__name__},
            )
        )

    paragraphs: list[str] = []
    for item in raw_paragraphs:
        normalized = str(item).strip()
        if normalized:
            paragraphs.append(normalized[:500])

    title = str(raw_article.get("title", "")).strip()
    author = str(raw_article.get("author", "")).strip()
    published_at = str(raw_article.get("published_at", "")).strip()
    source_url = str(raw_article.get("source_url", "")).strip() or str(
        raw_article.get("source", "")
    ).strip()

    selected_url = str(state.get("selected_url", "")).strip() or source_url or NO_SELECTION_URL
    selected_source = _selected_source_name(state, selected_url) or str(
        raw_article.get("source", "")
    ).strip()

    if not title:
        title = "AI & Tech Daily Briefing"

    return ScriptWriterInput(
        title=title,
        author=author,
        published_at=published_at,
        source_url=source_url,
        paragraphs=paragraphs[:12],
        metadata_only=bool(raw_article.get("metadata_only", False)),
        extraction_status=str(raw_article.get("extraction_status", "")).strip(),
        selected_url=selected_url,
        selected_source=selected_source,
    )


def _schema_for_openai_response(schema: dict[str, Any]) -> dict[str, Any]:
    def _sanitize(value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, child in value.items():
                if key == "$schema":
                    continue
                sanitized[key] = _sanitize(child)
            return sanitized
        if isinstance(value, list):
            return [_sanitize(item) for item in value]
        return value

    return {
        "name": "phase5_script_writer_output",
        "strict": True,
        "schema": _sanitize(schema),
    }


def _script_writer_prompt_payload(script_input: ScriptWriterInput, prompt_version: str) -> str:
    payload = {
        "prompt_version": prompt_version,
        "selected_url": script_input.selected_url,
        "selected_source": script_input.selected_source,
        "article": {
            "title": script_input.title,
            "author": script_input.author,
            "published_at": script_input.published_at,
            "source_url": script_input.source_url,
            "metadata_only": script_input.metadata_only,
            "extraction_status": script_input.extraction_status,
            "paragraphs": script_input.paragraphs,
        },
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _call_script_writer_model(
    *,
    script_input: ScriptWriterInput,
    system_prompt: str,
    script_schema: dict[str, Any],
    model_name: str,
    temperature: float,
    top_p: float,
    timeout_seconds: float,
    prompt_version: str,
    openai_api_key_env_var: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as error:  # pragma: no cover - integration dependency path
        raise ScriptWriterDependencyError(
            "Missing dependency: openai. Install requirements/phase5.txt"
        ) from error

    api_key = str(os.getenv(openai_api_key_env_var, "")).strip()
    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    user_payload = _script_writer_prompt_payload(script_input, prompt_version)

    response = client.chat.completions.create(
        model=model_name,
        temperature=temperature,
        top_p=top_p,
        timeout=timeout_seconds,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": _schema_for_openai_response(script_schema),
        },
    )

    content = response.choices[0].message.content if response.choices else ""
    if not content:
        raise ModelResponseError("Script writer model returned empty content.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise ModelResponseError("Script writer model returned malformed JSON.") from error

    usage = getattr(response, "usage", None)
    usage_payload = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    return parsed, usage_payload


def _is_type_match(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return False


def _schema_types(schema: dict[str, Any]) -> list[str]:
    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        return [raw_type]
    if isinstance(raw_type, list):
        types: list[str] = []
        for item in raw_type:
            if isinstance(item, str):
                types.append(item)
        return types
    return []


def _validate_scalar_constraints(value: Any, schema: dict[str, Any], path: str) -> None:
    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise ModelResponseError(f"{path} length must be >= {min_length}.")
        if isinstance(max_length, int) and len(value) > max_length:
            raise ModelResponseError(f"{path} length must be <= {max_length}.")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric_value = float(value)
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and numeric_value < float(minimum):
            raise ModelResponseError(f"{path} must be >= {minimum}.")
        if isinstance(maximum, (int, float)) and numeric_value > float(maximum):
            raise ModelResponseError(f"{path} must be <= {maximum}.")


def _validate_against_schema(value: Any, schema: dict[str, Any], path: str) -> None:
    expected_types = _schema_types(schema)
    if expected_types and not any(_is_type_match(value, item) for item in expected_types):
        raise ModelResponseError(
            f"{path} type mismatch. expected={expected_types} received={type(value).__name__}"
        )

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required", [])
        if isinstance(required, list):
            for field in required:
                if isinstance(field, str) and field not in value:
                    raise ModelResponseError(f"{path}.{field} is required.")

        additional_properties = schema.get("additionalProperties", True)
        if additional_properties is False:
            unexpected = sorted(set(value.keys()) - set(properties.keys()))
            if unexpected:
                raise ModelResponseError(f"{path} contains unexpected keys: {unexpected}")

        for key, child_schema in properties.items():
            if key not in value:
                continue
            if not isinstance(child_schema, dict):
                raise ModelResponseError(f"{path}.{key} has invalid schema definition.")
            _validate_against_schema(value[key], child_schema, f"{path}.{key}")
        return

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            raise ModelResponseError(f"{path} must contain at least {min_items} items.")
        if isinstance(max_items, int) and len(value) > max_items:
            raise ModelResponseError(f"{path} must contain at most {max_items} items.")

        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_against_schema(item, item_schema, f"{path}[{index}]")
        return

    _validate_scalar_constraints(value, schema, path)


def _parse_script_payload(model_payload: dict[str, Any], script_schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(model_payload, dict):
        raise ModelResponseError("Script writer payload must be a JSON object.")
    _validate_against_schema(model_payload, script_schema, "$")
    return model_payload


def _first_sentence(text: str) -> str:
    for separator in (". ", "? ", "! "):
        if separator in text:
            return text.split(separator, 1)[0].strip()
    return text.strip()


def _fallback_script(script_input: ScriptWriterInput) -> dict[str, Any]:
    fallback_lines = [
        "Here is the key update from today's selected story.",
        "The report outlines the central claim and why it matters now.",
        "It provides concrete context so viewers can understand the impact quickly.",
        "The article also highlights risks, tradeoffs, and open questions.",
        "This gives a balanced snapshot of what to watch next.",
        "Check the source for full details and follow for tomorrow's briefing.",
    ]
    scenes: list[dict[str, Any]] = []
    for index in range(6):
        narration = (
            script_input.paragraphs[index]
            if index < len(script_input.paragraphs)
            else fallback_lines[index]
        ).strip()
        if not narration:
            narration = fallback_lines[index]
        image_prompt = f"{script_input.title}, news visual {index + 1}" if index < 3 else None
        scenes.append(
            {
                "id": index + 1,
                "narration": narration[:320],
                "image_prompt": image_prompt,
            }
        )

    hook_seed = script_input.paragraphs[0] if script_input.paragraphs else script_input.title
    hook = _first_sentence(hook_seed)[:140] or "Fast AI and tech briefing."
    source_url = script_input.selected_url or script_input.source_url or NO_SELECTION_URL
    return {
        "video_title": script_input.title[:120] or "AI & Tech Daily Briefing",
        "source_line": f"Source: {source_url}",
        "hook": hook,
        "scenes": scenes,
        "cta": "Follow for daily AI and tech updates.",
    }


def _generate_script(
    *,
    script_input: ScriptWriterInput,
    system_prompt: str,
    script_schema: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    def _model_call() -> tuple[dict[str, Any], dict[str, int]]:
        return _call_script_writer_model(
            script_input=script_input,
            system_prompt=system_prompt,
            script_schema=script_schema,
            model_name=settings["model_name"],
            temperature=settings["temperature"],
            top_p=settings["top_p"],
            timeout_seconds=settings["timeout_seconds"],
            prompt_version=settings["prompt_version"],
            openai_api_key_env_var=settings["openai_api_key_env_var"],
        )

    def _parse(payload: dict[str, Any]) -> dict[str, Any]:
        return _parse_script_payload(payload, script_schema)

    def _unexpected_error(error: Exception) -> ScriptWriterError:
        return ScriptWriterError(
            _phase5_error(
                code="unexpected_script_generation_error",
                message="Unexpected Phase 5 script generation failure.",
                details={
                    "error_type": error.__class__.__name__,
                    "error": str(error),
                },
            )
        )

    return score_with_retry_and_fallback(
        model_call=_model_call,
        parse_scores=_parse,
        fallback_scores=lambda: _fallback_script(script_input),
        dependency_error_type=ScriptWriterDependencyError,
        model_response_error_type=ModelResponseError,
        unexpected_error_factory=_unexpected_error,
        logger=logger,
        fallback_log_template="Phase 5 script writer model failed after retry; using deterministic fallback. error=%s",
        include_last_error=True,
        operation_name="phase5_script_writer_generation",
    )


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    _load_project_env()
    pipeline_config, openai_config = _load_runtime_configs()
    settings = _script_writer_settings(pipeline_config, openai_config)

    project_root = _project_root()
    prompt_path = project_root / PROMPT_PATH
    configured_schema_path = Path(settings["schema_path"])
    schema_path = (
        configured_schema_path
        if configured_schema_path.is_absolute()
        else project_root / configured_schema_path
    )
    system_prompt = _load_text_file(
        prompt_path,
        missing_code="missing_script_prompt",
        invalid_code="invalid_script_prompt",
    )
    script_schema = _load_script_schema(schema_path)
    script_input = _normalize_script_input(next_state)

    try:
        script_payload, generation_metadata = _generate_script(
            script_input=script_input,
            system_prompt=system_prompt,
            script_schema=script_schema,
            settings=settings,
        )
    except ScriptWriterDependencyError as error:
        raise ScriptWriterError(
            _phase5_error(
                code="missing_phase5_dependency",
                message="Phase 5 script writer dependency is missing.",
                details={"error": str(error)},
            )
        ) from error

    try:
        validated_script = _parse_script_payload(script_payload, script_schema)
    except ModelResponseError as error:
        raise ScriptWriterError(
            _phase5_error(
                code="invalid_script_payload",
                message="Phase 5 generated script failed schema validation.",
                details={"error": str(error)},
            )
        ) from error

    next_state["script_json"] = validated_script

    counters = next_state["metrics"]["counters"]
    counters["phase5_script_writer_model_latency_ms"] = int(generation_metadata["model_latency_ms"])
    counters["phase5_script_writer_retry_count"] = int(generation_metadata["retry_count"])
    counters["phase5_script_writer_prompt_tokens"] = int(
        generation_metadata["token_usage"]["prompt_tokens"]
    )
    counters["phase5_script_writer_completion_tokens"] = int(
        generation_metadata["token_usage"]["completion_tokens"]
    )
    counters["phase5_script_writer_total_tokens"] = int(
        generation_metadata["token_usage"]["total_tokens"]
    )
    counters["phase5_script_writer_scene_count"] = len(validated_script.get("scenes", []))
    counters["scene_count"] = len(validated_script.get("scenes", []))

    flags = next_state["metrics"]["flags"]
    flags["phase5_script_writer_model"] = settings["model_name"]
    flags["phase5_script_writer_prompt_version"] = settings["prompt_version"]
    flags["phase5_script_writer_prompt_path"] = PROMPT_PATH.as_posix()
    flags["phase5_script_writer_schema_path"] = settings["schema_path"]
    flags["phase5_script_writer_schema_version"] = str(
        pipeline_config.get("versions", {}).get("schema_version", "")
    ).strip()
    flags["phase5_script_writer_timeout_seconds"] = settings["timeout_seconds"]
    flags["phase5_script_writer_fallback_used"] = bool(generation_metadata["fallback_used"])
    flags["phase5_script_writer_last_error"] = generation_metadata.get("last_error")
    flags["phase5_script_writer_input_metadata_only"] = script_input.metadata_only
    logger.info(
        "PHASE5_SCRIPT_WRITER_SUMMARY model=%s retries=%s fallback=%s latency_ms=%s scenes=%s",
        settings["model_name"],
        counters["phase5_script_writer_retry_count"],
        flags["phase5_script_writer_fallback_used"],
        counters["phase5_script_writer_model_latency_ms"],
        counters["phase5_script_writer_scene_count"],
    )

    output_dir = project_root / str(pipeline_config.get("output_dir", DEFAULT_OUTPUT_DIR))
    write_json(output_dir / "script.json", validated_script)
    return next_state
