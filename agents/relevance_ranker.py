"""Phase 3 criteria-based interestingness ranker."""

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
from core.common.utils import resolve_scrape_policy, write_json
from core.config.config_loader import load_all_configs
from core.state import PipelineState, copy_state


logger = logging.getLogger(__name__)

ALLOWED_THEMES = {"AI", "Tech"}
THEME_NORMALIZATION = {"ai": "AI", "tech": "Tech"}
THEME_OVERRIDE_ENV = "VG_THEME"
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_PROMPT_VERSION = "phase3-interestingness-ranker-v1"
DEFAULT_CRITERIA_POLICY_VERSION = "phase3-interestingness-policy-v1"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOP_P = 1.0
DEFAULT_TARGET_SELECTION_COUNT = 1
DEFAULT_MIN_OVERLAP_RATIO = 0.9
DEFAULT_TIMEOUT_SECONDS = 90.0
NO_SELECTION_URL = "https://example.com/phase3/no_selection"
TIE_BREAK_POLICY = "score_desc_then_published_at_desc_then_url_asc"
SUPPORTED_TIE_BREAK_POLICIES = {TIE_BREAK_POLICY}

THEME_CRITERIA: dict[str, tuple[dict[str, str], ...]] = {
    "AI": (
        {
            "label": "Human stakes",
            "description": "How directly the story affects people, jobs, safety, or society.",
        },
        {
            "label": "Novelty / first-ever capability",
            "description": "Whether this introduces a genuinely new capability or first-of-its-kind milestone.",
        },
        {
            "label": "Controversy or tension",
            "description": "Presence of meaningful disagreement, risk, conflict, or competing narratives.",
        },
        {
            "label": "Visual or demonstrable proof",
            "description": "Evidence with demos, concrete outputs, measurable results, or verifiable examples.",
        },
        {
            "label": "Speculation about the future",
            "description": "Signals about credible forward-looking implications for products, policy, or behavior.",
        },
    ),
    "Tech": (
        {
            "label": "Immediate real-world impact",
            "description": "Near-term effect on users, operators, customers, or businesses.",
        },
        {
            "label": "Credibility of the source",
            "description": "Reliability of evidence and source trustworthiness for the core claims.",
        },
        {
            "label": "Simplicity of the core idea",
            "description": "How easily the central concept can be explained and understood.",
        },
        {
            "label": "Timeliness / news hook",
            "description": "Strength of the immediate news angle and relevance to current events.",
        },
        {
            "label": "Contrarianism",
            "description": "Whether the story offers a surprising or non-consensus perspective with evidence.",
        },
    ),
}

THEME_CRITERIA_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "AI": {
        "Human stakes": (
            "jobs",
            "workers",
            "patients",
            "students",
            "privacy",
            "safety",
            "harm",
            "rights",
        ),
        "Novelty / first-ever capability": (
            "first",
            "breakthrough",
            "new capability",
            "state-of-the-art",
            "sota",
            "novel",
            "milestone",
            "unprecedented",
        ),
        "Controversy or tension": (
            "lawsuit",
            "criticism",
            "backlash",
            "risk",
            "ban",
            "debate",
            "controversy",
            "conflict",
        ),
        "Visual or demonstrable proof": (
            "demo",
            "video",
            "screenshot",
            "benchmark",
            "results",
            "evidence",
            "measured",
            "replication",
        ),
        "Speculation about the future": (
            "future",
            "forecast",
            "prediction",
            "roadmap",
            "next year",
            "long term",
            "transform",
            "trajectory",
        ),
    },
    "Tech": {
        "Immediate real-world impact": (
            "customer",
            "users",
            "enterprise",
            "deployment",
            "rollout",
            "cost",
            "uptime",
            "operations",
        ),
        "Credibility of the source": (
            "official",
            "filing",
            "regulator",
            "reuters",
            "bloomberg",
            "press release",
            "independent",
            "verified",
        ),
        "Simplicity of the core idea": (
            "simple",
            "easy",
            "clear",
            "one-click",
            "single",
            "straightforward",
            "plain",
            "concise",
        ),
        "Timeliness / news hook": (
            "today",
            "breaking",
            "this week",
            "launch",
            "announcement",
            "new",
            "latest",
            "now",
        ),
        "Contrarianism": (
            "contrarian",
            "against",
            "despite",
            "unexpected",
            "surprising",
            "counter",
            "opposes",
            "unpopular",
        ),
    },
}


class RelevanceRankerError(RuntimeError):
    """Structured Phase 3 ranker error."""


class ModelResponseError(RelevanceRankerError):
    """Raised when model response cannot be parsed/validated."""


class RankerDependencyError(RelevanceRankerError):
    """Raised when required ranker dependencies are unavailable."""


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
    phase2_reason: str
    phase2_theme_match_score: float


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float
    reason: str
    criteria_scores: dict[str, float]


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _phase3_error(*, code: str, message: str, details: dict[str, Any] | None = None) -> str:
    payload = {
        "phase": 3,
        "agent": "relevance_ranker",
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

    raise RelevanceRankerError(
        _phase3_error(
            code="invalid_theme",
            message="Theme must be AI or Tech.",
            details={
                "received_theme": raw_theme,
                "allowed_themes": sorted(ALLOWED_THEMES),
                "theme_override_env_var": THEME_OVERRIDE_ENV,
            },
        )
    )


def _ranker_settings(pipeline_config: dict[str, Any], openai_config: dict[str, Any]) -> dict[str, Any]:
    ranker_cfg = pipeline_config.get("phase3_ranker", {})
    if not isinstance(ranker_cfg, dict):
        ranker_cfg = {}
    deterministic_cfg = ranker_cfg.get("deterministic", {})
    if not isinstance(deterministic_cfg, dict):
        deterministic_cfg = {}
    stability_cfg = ranker_cfg.get("stability", {})
    if not isinstance(stability_cfg, dict):
        stability_cfg = {}

    openai_models = openai_config.get("models", {})
    if not isinstance(openai_models, dict):
        openai_models = {}

    model_name = str(
        ranker_cfg.get("model")
        or openai_models.get("interestingness_ranker")
        or DEFAULT_MODEL
    ).strip()
    prompt_version = str(ranker_cfg.get("prompt_version") or DEFAULT_PROMPT_VERSION).strip()
    criteria_policy_version = str(
        ranker_cfg.get("criteria_policy_version") or DEFAULT_CRITERIA_POLICY_VERSION
    ).strip()
    tie_break_policy = str(ranker_cfg.get("tie_break_policy") or TIE_BREAK_POLICY).strip()

    try:
        target_selection_count = int(
            ranker_cfg.get("target_selection_count", DEFAULT_TARGET_SELECTION_COUNT)
        )
        temperature = float(deterministic_cfg.get("temperature", DEFAULT_TEMPERATURE))
        top_p = float(deterministic_cfg.get("top_p", DEFAULT_TOP_P))
        timeout_seconds = float(ranker_cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        min_overlap_ratio = float(
            stability_cfg.get("min_overlap_ratio", DEFAULT_MIN_OVERLAP_RATIO)
        )
    except (TypeError, ValueError) as error:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_ranker_config",
                message="Phase 3 ranker config has invalid numeric values.",
                details={"phase3_ranker": ranker_cfg},
            )
        ) from error

    if not model_name:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_ranker_config",
                message="Phase 3 model name cannot be empty.",
            )
        )
    if not prompt_version:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_ranker_config",
                message="Phase 3 prompt_version cannot be empty.",
            )
        )
    if not criteria_policy_version:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_ranker_config",
                message="Phase 3 criteria_policy_version cannot be empty.",
            )
        )
    if target_selection_count != 1:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_ranker_config",
                message="Phase 3 target_selection_count must be 1.",
                details={"target_selection_count": target_selection_count},
            )
        )
    if tie_break_policy not in SUPPORTED_TIE_BREAK_POLICIES:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_tie_break_policy",
                message="Unsupported Phase 3 tie-break policy.",
                details={
                    "received_policy": tie_break_policy,
                    "supported_policies": sorted(SUPPORTED_TIE_BREAK_POLICIES),
                },
            )
        )
    if not math.isfinite(temperature) or temperature < 0.0:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_ranker_config",
                message="Phase 3 temperature must be finite and >= 0.",
                details={"temperature": temperature},
            )
        )
    if not math.isfinite(top_p) or top_p <= 0.0 or top_p > 1.0:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_ranker_config",
                message="Phase 3 top_p must be finite and in (0, 1].",
                details={"top_p": top_p},
            )
        )
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0.0:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_ranker_config",
                message="Phase 3 timeout_seconds must be finite and > 0.",
                details={"timeout_seconds": timeout_seconds},
            )
        )
    if not math.isfinite(min_overlap_ratio) or min_overlap_ratio <= 0.0 or min_overlap_ratio > 1.0:
        raise RelevanceRankerError(
            _phase3_error(
                code="invalid_ranker_config",
                message="Phase 3 stability.min_overlap_ratio must be finite and in (0, 1].",
                details={"min_overlap_ratio": min_overlap_ratio},
            )
        )

    return {
        "model_name": model_name,
        "prompt_version": prompt_version,
        "criteria_policy_version": criteria_policy_version,
        "target_selection_count": target_selection_count,
        "temperature": temperature,
        "top_p": top_p,
        "timeout_seconds": round(timeout_seconds, 3),
        "tie_break_policy": tie_break_policy,
        "min_overlap_ratio": round(min_overlap_ratio, 6),
    }


def _criteria_for_theme(theme: str) -> list[dict[str, str]]:
    return [dict(item) for item in THEME_CRITERIA[theme]]


def _criteria_labels(theme: str) -> list[str]:
    return [item["label"] for item in THEME_CRITERIA[theme]]


def _normalize_candidates(items: list[dict[str, Any]]) -> tuple[list[Candidate], int]:
    candidates: list[Candidate] = []
    invalid_item_count = 0
    for item in items:
        url = str(item.get("url", "")).strip()
        if not url:
            invalid_item_count += 1
            continue
        title = str(item.get("title", "")).strip() or url
        source = str(item.get("source", "")).strip()
        scrape_policy = resolve_scrape_policy(item.get("scrape_policy"), fallback_to_full=True)
        published_at = str(item.get("published_at", "")).strip()
        summary = str(item.get("summary", "")).strip()
        phase2_reason = str(item.get("selection_reason", "")).strip()
        raw_phase2_score = item.get("theme_match_score", 0.0)
        try:
            phase2_theme_match_score = float(raw_phase2_score)
        except (TypeError, ValueError):
            phase2_theme_match_score = 0.0
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
                phase2_reason=phase2_reason,
                phase2_theme_match_score=round(phase2_theme_match_score, 6),
            )
        )
    return candidates, invalid_item_count


def _ranker_prompt(
    *,
    theme: str,
    prompt_version: str,
    criteria: list[dict[str, str]],
) -> tuple[str, str]:
    system_prompt = (
        "You are a deterministic interestingness ranker.\n"
        "Score each candidate only using provided metadata and theme criteria.\n"
        "Return only valid JSON matching the schema."
    )
    instruction = {
        "prompt_version": prompt_version,
        "theme": theme,
        "scoring_scale": "0.0_to_1.0",
        "criteria": criteria,
        "requirements": [
            "score every candidate id exactly once",
            "include per-criterion scores",
            "keep reason concise and factual",
        ],
    }
    return system_prompt, json.dumps(instruction, ensure_ascii=True, separators=(",", ":"))


def _ranker_response_schema(item_count: int, criteria_labels: list[str]) -> dict[str, Any]:
    criteria_properties = {
        label: {"type": "number", "minimum": 0, "maximum": 1}
        for label in criteria_labels
    }
    return {
        "name": "phase3_interestingness_scores",
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
                            "reason": {"type": "string", "maxLength": 200},
                            "criteria_scores": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": criteria_properties,
                                "required": criteria_labels,
                            },
                        },
                        "required": ["id", "score", "reason", "criteria_scores"],
                    },
                }
            },
            "required": ["items"],
        },
    }


def _call_ranker_model(
    *,
    theme: str,
    criteria: list[dict[str, str]],
    candidates: list[Candidate],
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
        raise RankerDependencyError("Missing dependency: openai. Install requirements/phase3.txt") from error

    api_key = str(os.getenv(openai_api_key_env_var, "")).strip()
    client = OpenAI(api_key=api_key, max_retries=0) if api_key else OpenAI(max_retries=0)

    system_prompt, user_instruction = _ranker_prompt(
        theme=theme,
        prompt_version=prompt_version,
        criteria=criteria,
    )
    user_payload = json.dumps(
        {
            "instruction": user_instruction,
            "candidates": [
                {
                    "id": candidate.item_id,
                    "url": candidate.url,
                    "title": candidate.title,
                    "source": candidate.source,
                    "published_at": candidate.published_at,
                    "summary": candidate.summary,
                    "phase2_theme_match_score": candidate.phase2_theme_match_score,
                    "phase2_selection_reason": candidate.phase2_reason,
                }
                for candidate in candidates
            ],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )

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
            "json_schema": _ranker_response_schema(len(candidates), _criteria_labels(theme)),
        },
    )
    message = response.choices[0].message.content if response.choices else ""
    if not message:
        raise ModelResponseError("Ranker model returned empty content.")
    try:
        parsed = json.loads(message)
    except json.JSONDecodeError as error:
        raise ModelResponseError("Ranker model returned malformed JSON.") from error

    usage = getattr(response, "usage", None)
    usage_payload = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    return parsed, usage_payload


def _parse_scores(
    model_payload: dict[str, Any],
    candidate_count: int,
    criteria_labels: list[str],
) -> dict[int, tuple[float, str, dict[str, float]]]:
    raw_items = model_payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) != candidate_count:
        raise ModelResponseError("Ranker model returned invalid number of items.")

    scores_by_id: dict[int, tuple[float, str, dict[str, float]]] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ModelResponseError("Ranker model returned invalid score item.")
        raw_id = raw_item.get("id")
        raw_score = raw_item.get("score")
        raw_reason = raw_item.get("reason", "")
        raw_criteria_scores = raw_item.get("criteria_scores")

        if not isinstance(raw_id, int):
            raise ModelResponseError("Ranker model item id must be integer.")
        if raw_id in scores_by_id:
            raise ModelResponseError("Ranker model returned duplicate item ids.")
        try:
            score = float(raw_score)
        except (TypeError, ValueError) as error:
            raise ModelResponseError("Ranker model score must be numeric.") from error
        if score < 0 or score > 1:
            raise ModelResponseError("Ranker model score must be in [0, 1].")
        if not isinstance(raw_reason, str):
            raise ModelResponseError("Ranker model reason must be a string when present.")
        if not isinstance(raw_criteria_scores, dict):
            raise ModelResponseError("Ranker model criteria_scores must be an object.")

        criteria_scores: dict[str, float] = {}
        for label in criteria_labels:
            if label not in raw_criteria_scores:
                raise ModelResponseError("Ranker model criteria_scores missing required labels.")
            raw_value = raw_criteria_scores[label]
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError) as error:
                raise ModelResponseError("Ranker model criterion score must be numeric.") from error
            if numeric_value < 0 or numeric_value > 1:
                raise ModelResponseError("Ranker model criterion score must be in [0, 1].")
            criteria_scores[label] = round(numeric_value, 6)

        scores_by_id[raw_id] = (round(score, 6), raw_reason.strip()[:200], criteria_scores)

    expected_ids = set(range(1, candidate_count + 1))
    if set(scores_by_id.keys()) != expected_ids:
        raise ModelResponseError("Ranker model ids do not match candidate ids.")
    return scores_by_id


def _heuristic_scores(
    candidates: list[Candidate], theme: str
) -> dict[int, tuple[float, str, dict[str, float]]]:
    criteria_keywords = THEME_CRITERIA_KEYWORDS[theme]
    scores: dict[int, tuple[float, str, dict[str, float]]] = {}
    for candidate in candidates:
        haystack = " ".join(
            (
                candidate.title.lower(),
                candidate.summary.lower(),
                candidate.source.lower(),
                candidate.url.lower(),
                candidate.phase2_reason.lower(),
            )
        )
        criteria_scores: dict[str, float] = {}
        for label, keywords in criteria_keywords.items():
            match_count = sum(1 for keyword in keywords if keyword in haystack)
            normalized = round(min(match_count / max(min(len(keywords), 4), 1), 1.0), 6)
            criteria_scores[label] = normalized

        score = round(sum(criteria_scores.values()) / max(len(criteria_scores), 1), 6)
        matched_criteria = sum(1 for value in criteria_scores.values() if value > 0)
        reason = (
            "Keyword heuristic fallback."
            if matched_criteria == 0
            else f"Matched {matched_criteria} thematic criterion bucket(s)."
        )
        scores[candidate.item_id] = (score, reason, criteria_scores)
    return scores


def _score_candidates(
    *,
    theme: str,
    criteria: list[dict[str, str]],
    candidates: list[Candidate],
    model_name: str,
    temperature: float,
    top_p: float,
    timeout_seconds: float,
    prompt_version: str,
    openai_api_key_env_var: str,
) -> tuple[dict[int, tuple[float, str, dict[str, float]]], dict[str, Any]]:
    labels = [item["label"] for item in criteria]

    def _model_call() -> tuple[dict[str, Any], dict[str, int]]:
        return _call_ranker_model(
            theme=theme,
            criteria=criteria,
            candidates=candidates,
            model_name=model_name,
            temperature=temperature,
            top_p=top_p,
            timeout_seconds=timeout_seconds,
            prompt_version=prompt_version,
            openai_api_key_env_var=openai_api_key_env_var,
        )

    def _parse(payload: dict[str, Any]) -> dict[int, tuple[float, str, dict[str, float]]]:
        return _parse_scores(payload, len(candidates), labels)

    def _unexpected_error(error: Exception) -> RelevanceRankerError:
        return RelevanceRankerError(
            _phase3_error(
                code="unexpected_ranker_scoring_error",
                message="Unexpected Phase 3 ranker scoring failure.",
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
        dependency_error_type=RankerDependencyError,
        model_response_error_type=ModelResponseError,
        unexpected_error_factory=_unexpected_error,
        logger=logger,
        fallback_log_template="Phase 3 ranker model failed after retry; using deterministic fallback. error=%s",
        include_last_error=True,
        operation_name="phase3_interestingness_ranker_scoring",
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
    scored: list[ScoredCandidate], tie_break_policy: str
) -> tuple[list[ScoredCandidate], int]:
    ties_by_score: dict[float, list[ScoredCandidate]] = {}
    for item in scored:
        ties_by_score.setdefault(item.score, []).append(item)

    tie_break_events = 0
    for _, tie_group in ties_by_score.items():
        if len(tie_group) > 1:
            tie_break_events += 1

    ordered = sorted(
        scored,
        key=lambda item: (
            -item.score,
            _published_sort_parts(item.candidate.published_at),
            item.candidate.canonical_url,
        ),
    )
    if tie_break_events > 0:
        logger.info(
            "PHASE3_TIE_BREAK events=%s policy=%s",
            tie_break_events,
            tie_break_policy,
        )
    return ordered, tie_break_events


def _build_ranked_items(ordered: list[ScoredCandidate]) -> list[dict[str, Any]]:
    ranked_items: list[dict[str, Any]] = []
    for index, item in enumerate(ordered, start=1):
        ranked_items.append(
            {
                "rank": index,
                "url": item.candidate.url,
                "title": item.candidate.title,
                "source": item.candidate.source,
                "scrape_policy": item.candidate.scrape_policy,
                "published_at": item.candidate.published_at,
                "interestingness_score": item.score,
                "selection_reason": item.reason,
                "criteria_scores": item.criteria_scores,
                "phase2_theme_match_score": item.candidate.phase2_theme_match_score,
            }
        )
    return ranked_items


def _write_artifacts(
    *,
    output_dir: Path,
    theme: str,
    criteria: list[dict[str, str]],
    settings: dict[str, Any],
    candidates: list[Candidate],
    ranked_items: list[dict[str, Any]],
    selected_url: str,
    scoring_metadata: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        output_dir / "ranked_items.json",
        {
            "phase": 3,
            "theme": theme,
            "input_count": len(candidates),
            "output_count": len(ranked_items),
            "ranked_items": ranked_items,
            "ranking_model": {
                "name": settings["model_name"],
                "temperature": settings["temperature"],
                "top_p": settings["top_p"],
                "timeout_seconds": settings["timeout_seconds"],
                "prompt_version": settings["prompt_version"],
            },
            "criteria_policy_version": settings["criteria_policy_version"],
            "tie_break_policy": settings["tie_break_policy"],
            "run_metadata": {
                "retry_count": int(scoring_metadata["retry_count"]),
                "fallback_used": bool(scoring_metadata["fallback_used"]),
                "model_latency_ms": int(scoring_metadata["model_latency_ms"]),
                "token_usage": dict(scoring_metadata["token_usage"]),
            },
        },
    )

    selected_item = ranked_items[0] if ranked_items else None
    write_json(
        output_dir / "selection.json",
        {
            "phase": 3,
            "selected_url": selected_url,
            "selected_item": selected_item,
            "selection_count": 1 if selected_item else 0,
            "target_selection_count": settings["target_selection_count"],
            "source_subset_count": len(candidates),
            "from_phase2_subset": True,
            "selection_policy": {
                "tie_break_policy": settings["tie_break_policy"],
                "stability_min_overlap_ratio": settings["min_overlap_ratio"],
            },
        },
    )

    write_json(
        output_dir / "ranking_criteria_report.json",
        {
            "phase": 3,
            "theme": theme,
            "criteria": criteria,
            "criteria_policy_version": settings["criteria_policy_version"],
            "model_params": {
                "name": settings["model_name"],
                "temperature": settings["temperature"],
                "top_p": settings["top_p"],
                "timeout_seconds": settings["timeout_seconds"],
                "prompt_version": settings["prompt_version"],
            },
            "selection_policy": {
                "target_selection_count": settings["target_selection_count"],
                "tie_break_policy": settings["tie_break_policy"],
                "stability_min_overlap_ratio": settings["min_overlap_ratio"],
            },
            "retry_fallback": {
                "retry_count": int(scoring_metadata["retry_count"]),
                "fallback_used": bool(scoring_metadata["fallback_used"]),
                "last_error": scoring_metadata.get("last_error"),
            },
            "metrics": {
                "input_count": len(candidates),
                "output_count": len(ranked_items),
                "model_latency_ms": int(scoring_metadata["model_latency_ms"]),
                "token_usage": dict(scoring_metadata["token_usage"]),
            },
        },
    )


def run(state: PipelineState) -> PipelineState:
    next_state = copy_state(state)
    _load_project_env()
    pipeline_config, openai_config = _load_runtime_configs()
    theme = _resolve_theme(pipeline_config)
    settings = _ranker_settings(pipeline_config, openai_config)
    criteria = _criteria_for_theme(theme)

    phase2_subset = next_state["ranked_items"] if isinstance(next_state["ranked_items"], list) else []
    candidates, invalid_item_count = _normalize_candidates(phase2_subset)

    scoring_metadata: dict[str, Any] = {
        "retry_count": 0,
        "fallback_used": False,
        "model_latency_ms": 0,
        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "last_error": None,
    }
    ranked_items: list[dict[str, Any]] = []
    tie_break_events = 0

    if candidates:
        try:
            scores_by_id, scoring_metadata = _score_candidates(
                theme=theme,
                criteria=criteria,
                candidates=candidates,
                model_name=settings["model_name"],
                temperature=settings["temperature"],
                top_p=settings["top_p"],
                timeout_seconds=settings["timeout_seconds"],
                prompt_version=settings["prompt_version"],
                openai_api_key_env_var=str(openai_config["api_key_env_var"]),
            )
        except RankerDependencyError as error:
            raise RelevanceRankerError(
                _phase3_error(
                    code="missing_phase3_dependency",
                    message="Phase 3 ranker dependency is missing.",
                    details={"error": str(error)},
                )
            ) from error

        scored_candidates = [
            ScoredCandidate(
                candidate=candidate,
                score=scores_by_id[candidate.item_id][0],
                reason=scores_by_id[candidate.item_id][1],
                criteria_scores=scores_by_id[candidate.item_id][2],
            )
            for candidate in candidates
        ]
        ordered_scored, tie_break_events = _sort_scored_candidates(
            scored_candidates,
            settings["tie_break_policy"],
        )
        ranked_items = _build_ranked_items(ordered_scored)

    next_state["ranked_items"] = ranked_items
    selected_url = ranked_items[0]["url"] if ranked_items else NO_SELECTION_URL
    next_state["selected_url"] = selected_url

    counters = next_state["metrics"]["counters"]
    counters["phase3_ranker_input_count"] = len(candidates)
    counters["phase3_ranker_output_count"] = len(ranked_items)
    counters["phase3_ranker_invalid_item_count"] = invalid_item_count
    counters["phase3_ranker_retry_count"] = int(scoring_metadata["retry_count"])
    counters["phase3_ranker_tie_break_events"] = tie_break_events
    counters["phase3_ranker_model_latency_ms"] = int(scoring_metadata["model_latency_ms"])
    counters["phase3_ranker_prompt_tokens"] = int(scoring_metadata["token_usage"]["prompt_tokens"])
    counters["phase3_ranker_completion_tokens"] = int(
        scoring_metadata["token_usage"]["completion_tokens"]
    )
    counters["phase3_ranker_total_tokens"] = int(scoring_metadata["token_usage"]["total_tokens"])

    flags = next_state["metrics"]["flags"]
    flags["phase3_ranker_theme"] = theme
    flags["phase3_ranker_tie_break_policy"] = settings["tie_break_policy"]
    flags["phase3_ranker_fallback_used"] = bool(scoring_metadata["fallback_used"])
    flags["phase3_ranker_stability_min_overlap_ratio"] = settings["min_overlap_ratio"]
    flags["phase3_ranker_criteria_policy_version"] = settings["criteria_policy_version"]
    flags["phase3_ranker_timeout_seconds"] = settings["timeout_seconds"]
    logger.info(
        "PHASE3_RANKER_SUMMARY theme=%s input=%s output=%s retries=%s fallback=%s tie_break_events=%s latency_ms=%s",
        theme,
        counters["phase3_ranker_input_count"],
        counters["phase3_ranker_output_count"],
        counters["phase3_ranker_retry_count"],
        flags["phase3_ranker_fallback_used"],
        counters["phase3_ranker_tie_break_events"],
        counters["phase3_ranker_model_latency_ms"],
    )

    output_dir = _project_root() / str(pipeline_config["output_dir"])
    _write_artifacts(
        output_dir=output_dir,
        theme=theme,
        criteria=criteria,
        settings=settings,
        candidates=candidates,
        ranked_items=ranked_items,
        selected_url=selected_url,
        scoring_metadata=scoring_metadata,
    )
    return next_state
