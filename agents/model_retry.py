"""Shared model scoring retry/fallback orchestration for Phase 2 and Phase 3."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_TRANSIENT_ERROR_CLASS_NAMES = {
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
}
_RETRYABLE_OPENAI_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


def _load_openai_transient_error_types() -> tuple[tuple[type[Exception], ...], tuple[type[Exception], ...]]:
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    except (ModuleNotFoundError, ImportError):
        return (), ()
    return (
        APITimeoutError,
        APIConnectionError,
        RateLimitError,
        InternalServerError,
    ), (APIStatusError,)


_OPENAI_TRANSIENT_ERROR_TYPES, _OPENAI_STATUS_ERROR_TYPES = _load_openai_transient_error_types()


def _is_retryable_openai_status_error(error: Exception) -> bool:
    if not _OPENAI_STATUS_ERROR_TYPES or not isinstance(error, _OPENAI_STATUS_ERROR_TYPES):
        return False
    status_code = getattr(error, "status_code", None)
    return isinstance(status_code, int) and status_code in _RETRYABLE_OPENAI_STATUS_CODES


def is_retryable_model_error(error: Exception, *, model_response_error_type: type[Exception]) -> bool:
    if isinstance(error, model_response_error_type):
        return True
    if _OPENAI_TRANSIENT_ERROR_TYPES and isinstance(error, _OPENAI_TRANSIENT_ERROR_TYPES):
        return True
    if _is_retryable_openai_status_error(error):
        return True
    if isinstance(error, TimeoutError):
        return True
    return error.__class__.__name__ in _TRANSIENT_ERROR_CLASS_NAMES


def score_with_retry_and_fallback(
    *,
    model_call: Callable[[], tuple[dict[str, Any], dict[str, int]]],
    parse_scores: Callable[[dict[str, Any]], T],
    fallback_scores: Callable[[], T],
    dependency_error_type: type[Exception],
    model_response_error_type: type[Exception],
    unexpected_error_factory: Callable[[Exception], Exception],
    logger: logging.Logger,
    fallback_log_template: str,
    include_last_error: bool = False,
) -> tuple[T, dict[str, Any]]:
    retry_count = 0
    fallback_used = False
    model_latency_ms = 0
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    last_error: Exception | None = None

    for attempt in range(2):
        started = time.perf_counter()
        try:
            payload, usage = model_call()
            model_latency_ms += int((time.perf_counter() - started) * 1000)
            parsed_scores = parse_scores(payload)
            for key in token_usage:
                token_usage[key] += int(usage.get(key, 0) or 0)
            metadata: dict[str, Any] = {
                "retry_count": retry_count,
                "fallback_used": fallback_used,
                "model_latency_ms": model_latency_ms,
                "token_usage": token_usage,
            }
            if include_last_error:
                metadata["last_error"] = str(last_error) if last_error is not None else None
            return parsed_scores, metadata
        except dependency_error_type:
            raise
        except Exception as error:
            model_latency_ms += int((time.perf_counter() - started) * 1000)
            if not is_retryable_model_error(error, model_response_error_type=model_response_error_type):
                raise unexpected_error_factory(error) from error
            last_error = error
            if attempt == 0:
                retry_count = 1
                continue
            break

    fallback_used = True
    if last_error is not None:
        logger.warning(fallback_log_template, str(last_error))

    metadata = {
        "retry_count": retry_count,
        "fallback_used": fallback_used,
        "model_latency_ms": model_latency_ms,
        "token_usage": token_usage,
    }
    if include_last_error:
        metadata["last_error"] = str(last_error) if last_error is not None else None
    return fallback_scores(), metadata
