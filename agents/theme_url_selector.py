"""Phase 2 theme URL selector."""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.model_retry import score_with_retry_and_fallback
from core.common.utils import is_runtime_verbose_logging_enabled, resolve_scrape_policy, write_json
from core.config.config_loader import load_all_configs
from core.state import PipelineState, copy_state


logger = logging.getLogger(__name__)

ALLOWED_THEMES = {"AI", "Tech"}
THEME_NORMALIZATION = {"ai": "AI", "tech": "Tech"}
THEME_OVERRIDE_ENV = "VG_THEME"
DEFAULT_TARGET_COUNT = 30
DEFAULT_LOWER_BOUND = 25
DEFAULT_UPPER_BOUND = 35
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOP_P = 1.0
DEFAULT_PROMPT_VERSION = "phase2-theme-selector-v1"
OPENAI_TIMEOUT_SECONDS = 30.0
TIE_BREAK_POLICY = "published_at_desc_then_canonical_url_asc"
SUPPORTED_TIE_BREAK_POLICIES = {TIE_BREAK_POLICY}

THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI": (
        "ai",
        "artificial intelligence",
        "llm",
        "model",
        "openai",
        "anthropic",
        "gemini",
        "copilot",
        "machine learning",
        "inference",
        "agent",
        "robotics",
        "semiconductor",
        "chip",
    ),
    "Tech": (
        "technology",
        "software",
        "hardware",
        "startup",
        "cloud",
        "cybersecurity",
        "platform",
        "device",
        "mobile",
        "internet",
        "economy",
        "enterprise",
        "developer",
        "app",
    ),
}


class ThemeURLSelectorError(RuntimeError):
    """Structured Phase 2 selector error."""


class ModelResponseError(ThemeURLSelectorError):
    """Raised when model response cannot be parsed/validated."""


class SelectorDependencyError(ThemeURLSelectorError):
    """Raised when required selector dependencies are unavailable."""


@dataclass(frozen=True)
class Candidate:
    item_id: int
    url: str
    canonical_url: str
    title: str
    source: str
    scrape_policy: str
    published_at: str
    summary: str


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float
    reason: str


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _phase2_error(*, code: str, message: str, details: dict[str, Any] | None = None) -> str:
    payload = {
        "phase": 2,
        "agent": "theme_url_selector",
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


def _resolve_theme(pipeline_config: dict[str, Any]) -> str:
    override_theme = os.getenv(THEME_OVERRIDE_ENV)
    if override_theme is not None and override_theme.strip():
        raw_theme = override_theme.strip()
    else:
        raw_theme = str(pipeline_config.get("theme", "")).strip()
    normalized = THEME_NORMALIZATION.get(raw_theme.casefold())
    if normalized in ALLOWED_THEMES:
        return normalized

    raise ThemeURLSelectorError(
        _phase2_error(
            code="invalid_theme",
            message="Theme must be AI or Tech.",
            details={
                "received_theme": raw_theme,
                "allowed_themes": sorted(ALLOWED_THEMES),
                "theme_override_env_var": THEME_OVERRIDE_ENV,
            },
        )
    )


def _selector_settings(pipeline_config: dict[str, Any], openai_config: dict[str, Any]) -> dict[str, Any]:
    selector_cfg = pipeline_config.get("phase2_selector", {})
    if not isinstance(selector_cfg, dict):
        selector_cfg = {}

    deterministic_cfg = selector_cfg.get("deterministic", {})
    if not isinstance(deterministic_cfg, dict):
        deterministic_cfg = {}

    openai_models = openai_config.get("models", {})
    if not isinstance(openai_models, dict):
        openai_models = {}

    model_name = str(
        selector_cfg.get("model")
        or openai_models.get("theme_selector")
        or DEFAULT_MODEL
    ).strip()
    prompt_version = str(
        selector_cfg.get("prompt_version")
        or pipeline_config.get("versions", {}).get("prompt_version")
        or DEFAULT_PROMPT_VERSION
    ).strip()
    try:
        target_count = int(selector_cfg.get("target_count", DEFAULT_TARGET_COUNT))
        lower_bound = int(selector_cfg.get("lower_bound", DEFAULT_LOWER_BOUND))
        upper_bound = int(selector_cfg.get("upper_bound", DEFAULT_UPPER_BOUND))
        temperature = float(deterministic_cfg.get("temperature", DEFAULT_TEMPERATURE))
        top_p = float(deterministic_cfg.get("top_p", DEFAULT_TOP_P))
    except (TypeError, ValueError) as error:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="invalid_selector_config",
                message="Selector config has invalid numeric values.",
                details={"phase2_selector": selector_cfg},
            )
        ) from error
    tie_break_policy = str(selector_cfg.get("tie_break_policy") or TIE_BREAK_POLICY).strip()

    if not model_name:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="invalid_selector_config",
                message="Selector model name cannot be empty.",
            )
        )
    if not prompt_version:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="invalid_selector_config",
                message="Selector prompt_version cannot be empty.",
            )
        )
    if lower_bound < 1:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="invalid_selector_config",
                message="Selector lower_bound must be >= 1.",
                details={"lower_bound": lower_bound},
            )
        )
    if upper_bound < lower_bound:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="invalid_selector_config",
                message="Selector upper_bound must be >= lower_bound.",
                details={"lower_bound": lower_bound, "upper_bound": upper_bound},
            )
        )
    if target_count < lower_bound or target_count > upper_bound:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="invalid_selector_config",
                message="Selector target_count must be within [lower_bound, upper_bound].",
                details={
                    "target_count": target_count,
                    "lower_bound": lower_bound,
                    "upper_bound": upper_bound,
                },
            )
        )
    if not math.isfinite(temperature) or temperature < 0.0:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="invalid_selector_config",
                message="Selector temperature must be a finite value >= 0.",
                details={"temperature": temperature},
            )
        )
    if not math.isfinite(top_p) or top_p <= 0.0 or top_p > 1.0:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="invalid_selector_config",
                message="Selector top_p must be a finite value in (0, 1].",
                details={"top_p": top_p},
            )
        )
    if tie_break_policy not in SUPPORTED_TIE_BREAK_POLICIES:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="invalid_tie_break_policy",
                message="Unsupported Phase 2 tie-break policy.",
                details={
                    "received_policy": tie_break_policy,
                    "supported_policies": sorted(SUPPORTED_TIE_BREAK_POLICIES),
                },
            )
        )

    return {
        "model_name": model_name,
        "prompt_version": prompt_version,
        "target_count": target_count,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "temperature": temperature,
        "top_p": top_p,
        "tie_break_policy": tie_break_policy,
    }


def _normalize_candidates(items: list[dict[str, Any]]) -> tuple[list[Candidate], int]:
    candidates: list[Candidate] = []
    invalid_item_count = 0

    for item in items:
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not url or not title:
            invalid_item_count += 1
            continue

        source = str(item.get("source", "")).strip()
        scrape_policy = resolve_scrape_policy(item.get("scrape_policy"), fallback_to_full=True)
        published_at = str(item.get("published_at", "")).strip()
        summary = str(item.get("summary", "")).strip()
        candidates.append(
            Candidate(
                item_id=len(candidates) + 1,
                url=url,
                canonical_url=url,
                title=title,
                source=source,
                scrape_policy=scrape_policy,
                published_at=published_at,
                summary=summary,
            )
        )

    return candidates, invalid_item_count


def _selector_prompt(theme: str, prompt_version: str) -> tuple[str, str]:
    system_prompt = (
        "You are a deterministic URL theme selector.\n"
        "Score each candidate for thematic relevance.\n"
        "Return only valid JSON matching the schema.\n"
        "Do not omit any candidate id."
    )
    user_instruction = (
        f"prompt_version={prompt_version}\n"
        f"theme={theme}\n"
        "Each score must be a number from 0.0 to 1.0.\n"
        "Each reason must be short (max 140 chars).\n"
        "Use only provided metadata.\n"
    )
    return system_prompt, user_instruction


def _selector_response_schema(item_count: int) -> dict[str, Any]:
    return {
        "name": "phase2_theme_selector_scores",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": item_count,
                    "maxItems": item_count,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "integer", "minimum": 1},
                            "score": {"type": "number", "minimum": 0, "maximum": 1},
                            "reason": {"type": "string", "maxLength": 140},
                        },
                        "required": ["id", "score", "reason"],
                    },
                }
            },
            "required": ["items"],
        },
    }


def _call_selector_model(
    *,
    theme: str,
    candidates: list[Candidate],
    model_name: str,
    temperature: float,
    top_p: float,
    prompt_version: str,
    openai_api_key_env_var: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as error:  # pragma: no cover - exercised in integration fallback paths
        raise SelectorDependencyError("Missing dependency: openai. Install requirements/phase2.txt") from error

    api_key = str(os.getenv(openai_api_key_env_var, "")).strip()
    client = (
        OpenAI(api_key=api_key, timeout=OPENAI_TIMEOUT_SECONDS)
        if api_key
        else OpenAI(timeout=OPENAI_TIMEOUT_SECONDS)
    )

    system_prompt, user_instruction = _selector_prompt(theme, prompt_version)
    payload_items = [
        {
            "id": candidate.item_id,
            "title": candidate.title,
            "source": candidate.source,
            "published_at": candidate.published_at,
            "url": candidate.url,
            "summary": candidate.summary,
        }
        for candidate in candidates
    ]
    user_payload = json.dumps(
        {
            "instruction": user_instruction,
            "candidates": payload_items,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )

    response = client.chat.completions.create(
        model=model_name,
        temperature=temperature,
        top_p=top_p,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": _selector_response_schema(len(candidates)),
        },
    )
    message = response.choices[0].message.content if response.choices else ""
    if not message:
        raise ModelResponseError("Selector model returned empty content.")

    try:
        parsed = json.loads(message)
    except json.JSONDecodeError as error:
        raise ModelResponseError("Selector model returned malformed JSON.") from error

    usage = getattr(response, "usage", None)
    usage_payload = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    return parsed, usage_payload


def _parse_scores(model_payload: dict[str, Any], candidate_count: int) -> dict[int, tuple[float, str]]:
    raw_items = model_payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) != candidate_count:
        raise ModelResponseError("Selector model returned invalid number of items.")

    scores_by_id: dict[int, tuple[float, str]] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ModelResponseError("Selector model returned invalid score item.")
        raw_id = raw_item.get("id")
        raw_score = raw_item.get("score")
        raw_reason = raw_item.get("reason")
        if not isinstance(raw_id, int):
            raise ModelResponseError("Selector model item id must be integer.")
        if raw_id in scores_by_id:
            raise ModelResponseError("Selector model returned duplicate item ids.")
        if not isinstance(raw_reason, str):
            raise ModelResponseError("Selector model reason must be string.")
        try:
            score = float(raw_score)
        except (TypeError, ValueError) as error:
            raise ModelResponseError("Selector model score must be numeric.") from error
        if score < 0 or score > 1:
            raise ModelResponseError("Selector model score must be in [0, 1].")
        scores_by_id[raw_id] = (round(score, 6), raw_reason.strip()[:140])

    expected_ids = set(range(1, candidate_count + 1))
    if set(scores_by_id.keys()) != expected_ids:
        raise ModelResponseError("Selector model ids do not match candidate ids.")
    return scores_by_id


def _heuristic_scores(candidates: list[Candidate], theme: str) -> dict[int, tuple[float, str]]:
    keywords = THEME_KEYWORDS[theme]
    scores: dict[int, tuple[float, str]] = {}
    for candidate in candidates:
        haystack = " ".join(
            (
                candidate.title.lower(),
                candidate.summary.lower(),
                candidate.source.lower(),
                candidate.url.lower(),
            )
        )
        match_count = 0
        for keyword in keywords:
            if keyword in haystack:
                match_count += 1
        normalized = round(min(match_count / max(len(keywords), 1), 1.0), 6)
        reason = (
            "Keyword heuristic fallback."
            if match_count == 0
            else f"Matched {match_count} theme keyword(s)."
        )
        scores[candidate.item_id] = (normalized, reason)
    return scores


def _score_candidates(
    *,
    theme: str,
    candidates: list[Candidate],
    model_name: str,
    temperature: float,
    top_p: float,
    prompt_version: str,
    openai_api_key_env_var: str,
) -> tuple[dict[int, tuple[float, str]], dict[str, Any]]:
    def _model_call() -> tuple[dict[str, Any], dict[str, int]]:
        return _call_selector_model(
            theme=theme,
            candidates=candidates,
            model_name=model_name,
            temperature=temperature,
            top_p=top_p,
            prompt_version=prompt_version,
            openai_api_key_env_var=openai_api_key_env_var,
        )

    def _parse(payload: dict[str, Any]) -> dict[int, tuple[float, str]]:
        return _parse_scores(payload, len(candidates))

    def _unexpected_error(error: Exception) -> ThemeURLSelectorError:
        return ThemeURLSelectorError(
            _phase2_error(
                code="unexpected_selector_scoring_error",
                message="Unexpected Phase 2 selector scoring failure.",
                details={
                    "error_type": error.__class__.__name__,
                    "error": str(error),
                },
            )
        )

    return score_with_retry_and_fallback(
        model_call=_model_call,
        parse_scores=_parse,
        fallback_scores=lambda: _heuristic_scores(candidates, theme),
        dependency_error_type=SelectorDependencyError,
        model_response_error_type=ModelResponseError,
        unexpected_error_factory=_unexpected_error,
        logger=logger,
        fallback_log_template="Phase 2 selector model failed after retry; using deterministic fallback. error=%s",
        operation_name="phase2_theme_selector_scoring",
    )


def _published_sort_parts(published_at: str) -> tuple[int, float]:
    value = published_at.strip()
    if not value:
        return (1, 0.0)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return (1, 0.0)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    timestamp = parsed.astimezone(timezone.utc).timestamp()
    return (0, -timestamp)


def _sort_scored_candidates(
    scored: list[ScoredCandidate], tie_break_policy: str, *, verbose_runtime_logs: bool
) -> tuple[list[ScoredCandidate], int]:
    ties_by_score: dict[float, list[ScoredCandidate]] = {}
    for item in scored:
        ties_by_score.setdefault(item.score, []).append(item)

    tie_break_events = 0
    for score, tie_group in sorted(ties_by_score.items(), key=lambda entry: entry[0], reverse=True):
        if len(tie_group) < 2:
            continue
        tie_break_events += 1
        logger.info(
            "PHASE2_TIE_BREAK event=%s score=%s group_size=%s policy=%s",
            tie_break_events,
            score,
            len(tie_group),
            tie_break_policy,
        )
        if verbose_runtime_logs:
            ordered_urls = [
                _item.candidate.canonical_url
                for _item in sorted(
                    tie_group,
                    key=lambda value: (
                        _published_sort_parts(value.candidate.published_at),
                        value.candidate.canonical_url,
                    ),
                )
            ]
            logger.debug(
                "PHASE2_TIE_BREAK_DETAIL event=%s urls=%s",
                tie_break_events,
                ordered_urls,
            )

    ordered = sorted(
        scored,
        key=lambda item: (
            -item.score,
            _published_sort_parts(item.candidate.published_at),
            item.candidate.canonical_url,
        ),
    )
    return ordered, tie_break_events


def _resolve_output_count(total_candidates: int, *, target: int, lower_bound: int, upper_bound: int) -> tuple[int, bool]:
    if total_candidates < lower_bound:
        return total_candidates, True
    if total_candidates <= upper_bound:
        return total_candidates, False
    return min(target, total_candidates), False


def _build_selected_items(scored_items: list[ScoredCandidate], output_count: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in scored_items[:output_count]:
        selected.append(
            {
                "url": item.candidate.url,
                "title": item.candidate.title,
                "source": item.candidate.source,
                "scrape_policy": item.candidate.scrape_policy,
                "published_at": item.candidate.published_at,
                "theme_match_score": item.score,
                "selection_reason": item.reason,
            }
        )
    return selected


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    verbose_runtime_logs = is_runtime_verbose_logging_enabled()
    _load_project_env()
    pipeline_config, openai_config = _load_runtime_configs()
    theme = _resolve_theme(pipeline_config)

    candidates, invalid_item_count = _normalize_candidates(next_state["rss_items"][:50])
    if not candidates:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="empty_candidates",
                message="No valid rss_items available for phase 2 selection.",
                details={"invalid_item_count": invalid_item_count},
            )
        )

    settings = _selector_settings(pipeline_config, openai_config)
    try:
        scores_by_id, scoring_metadata = _score_candidates(
            theme=theme,
            candidates=candidates,
            model_name=settings["model_name"],
            temperature=settings["temperature"],
            top_p=settings["top_p"],
            prompt_version=settings["prompt_version"],
            openai_api_key_env_var=str(openai_config["api_key_env_var"]),
        )
    except SelectorDependencyError as error:
        raise ThemeURLSelectorError(
            _phase2_error(
                code="missing_phase2_dependency",
                message="Phase 2 selector dependency is missing.",
                details={"error": str(error)},
            )
        ) from error
    scored_candidates = [
        ScoredCandidate(
            candidate=candidate,
            score=scores_by_id[candidate.item_id][0],
            reason=scores_by_id[candidate.item_id][1],
        )
        for candidate in candidates
    ]

    ordered_scored, tie_break_events = _sort_scored_candidates(
        scored_candidates,
        settings["tie_break_policy"],
        verbose_runtime_logs=verbose_runtime_logs,
    )
    output_count, policy_warning = _resolve_output_count(
        len(ordered_scored),
        target=settings["target_count"],
        lower_bound=settings["lower_bound"],
        upper_bound=settings["upper_bound"],
    )
    selected_items = _build_selected_items(ordered_scored, output_count)

    next_state["ranked_items"] = selected_items

    counters = next_state["metrics"]["counters"]
    counters["phase2_selector_input_count"] = len(candidates)
    counters["phase2_selector_output_count"] = len(selected_items)
    counters["phase2_selector_invalid_item_count"] = invalid_item_count
    counters["phase2_selector_model_latency_ms"] = int(scoring_metadata["model_latency_ms"])
    counters["phase2_selector_retry_count"] = int(scoring_metadata["retry_count"])
    counters["phase2_selector_tie_break_events"] = tie_break_events
    counters["phase2_selector_prompt_tokens"] = int(scoring_metadata["token_usage"]["prompt_tokens"])
    counters["phase2_selector_completion_tokens"] = int(
        scoring_metadata["token_usage"]["completion_tokens"]
    )
    counters["phase2_selector_total_tokens"] = int(scoring_metadata["token_usage"]["total_tokens"])

    flags = next_state["metrics"]["flags"]
    flags["phase2_selector_theme"] = theme
    flags["phase2_selector_policy_warning_low_input"] = policy_warning
    flags["phase2_selector_fallback_used"] = bool(scoring_metadata["fallback_used"])
    flags["phase2_selector_tie_break_policy"] = settings["tie_break_policy"]
    logger.info(
        "PHASE2_SELECTOR_SUMMARY theme=%s input=%s output=%s retries=%s fallback=%s tie_break_events=%s latency_ms=%s",
        theme,
        counters["phase2_selector_input_count"],
        counters["phase2_selector_output_count"],
        counters["phase2_selector_retry_count"],
        flags["phase2_selector_fallback_used"],
        counters["phase2_selector_tie_break_events"],
        counters["phase2_selector_model_latency_ms"],
    )

    output_dir = _project_root() / str(pipeline_config["output_dir"])
    artifact_path = output_dir / "theme_selected_urls.json"
    write_json(
        artifact_path,
        {
            "theme": theme,
            "input_count": len(candidates),
            "output_count": len(selected_items),
            "selected_items": selected_items,
            "selector_model": {
                "name": settings["model_name"],
                "temperature": settings["temperature"],
                "top_p": settings["top_p"],
                "prompt_version": settings["prompt_version"],
            },
            "tie_break_policy": settings["tie_break_policy"],
            "run_metadata": {
                "phase": 2,
                "retry_count": int(scoring_metadata["retry_count"]),
                "fallback_used": bool(scoring_metadata["fallback_used"]),
                "invalid_item_count": invalid_item_count,
                "model_latency_ms": int(scoring_metadata["model_latency_ms"]),
                "token_usage": scoring_metadata["token_usage"],
            },
        },
    )
    return next_state
