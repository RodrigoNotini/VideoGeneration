"""Unit tests for shared model retry classification."""

from __future__ import annotations

import types
import unittest
from unittest.mock import patch

from agents import model_retry


class _ModelResponseError(Exception):
    pass


class ModelRetryTests(unittest.TestCase):
    def test_model_response_error_is_retryable(self) -> None:
        error = _ModelResponseError("bad response")
        self.assertTrue(
            model_retry.is_retryable_model_error(
                error,
                model_response_error_type=_ModelResponseError,
            )
        )

    def test_openai_concrete_type_matching_is_used(self) -> None:
        class _SdkTransientError(Exception):
            pass

        with (
            patch.object(model_retry, "_OPENAI_TRANSIENT_ERROR_TYPES", (_SdkTransientError,)),
            patch.object(model_retry, "_TRANSIENT_ERROR_CLASS_NAMES", set()),
        ):
            self.assertTrue(
                model_retry.is_retryable_model_error(
                    _SdkTransientError("temporary"),
                    model_response_error_type=_ModelResponseError,
                )
            )

    def test_openai_loader_falls_back_when_symbol_import_raises_import_error(self) -> None:
        fake_openai = types.ModuleType("openai")
        fake_openai.APIStatusError = type("APIStatusError", (Exception,), {})
        fake_openai.APITimeoutError = type("APITimeoutError", (Exception,), {})
        fake_openai.InternalServerError = type("InternalServerError", (Exception,), {})
        fake_openai.RateLimitError = type("RateLimitError", (Exception,), {})

        with patch.dict("sys.modules", {"openai": fake_openai}):
            self.assertEqual(model_retry._load_openai_transient_error_types(), ((), ()))

    def test_openai_status_error_retries_for_transient_codes(self) -> None:
        class _FakeAPIStatusError(Exception):
            def __init__(self, status_code: int) -> None:
                super().__init__(f"status={status_code}")
                self.status_code = status_code

        with (
            patch.object(model_retry, "_OPENAI_STATUS_ERROR_TYPES", (_FakeAPIStatusError,)),
            patch.object(model_retry, "_TRANSIENT_ERROR_CLASS_NAMES", set()),
        ):
            self.assertTrue(
                model_retry.is_retryable_model_error(
                    _FakeAPIStatusError(503),
                    model_response_error_type=_ModelResponseError,
                )
            )
            self.assertFalse(
                model_retry.is_retryable_model_error(
                    _FakeAPIStatusError(400),
                    model_response_error_type=_ModelResponseError,
                )
            )

    def test_class_name_fallback_is_kept_for_compatibility(self) -> None:
        ServiceUnavailableError = type("ServiceUnavailableError", (Exception,), {})
        with patch.object(model_retry, "_OPENAI_TRANSIENT_ERROR_TYPES", ()):
            self.assertTrue(
                model_retry.is_retryable_model_error(
                    ServiceUnavailableError("compat path"),
                    model_response_error_type=_ModelResponseError,
                )
            )

    def test_unexpected_error_is_not_retryable(self) -> None:
        self.assertFalse(
            model_retry.is_retryable_model_error(
                ValueError("unexpected"),
                model_response_error_type=_ModelResponseError,
            )
        )


if __name__ == "__main__":
    unittest.main()
