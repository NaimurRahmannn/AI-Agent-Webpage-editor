from __future__ import annotations

import pytest

from web.reliability import (
    CrewFailureKind,
    classify_crew_exception,
)


class AuthenticationError(Exception):
    pass


class RateLimitError(Exception):
    pass


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


@pytest.mark.parametrize(
    ("exception", "expected_kind", "retryable"),
    [
        (
            AuthenticationError(
                "Invalid API key: secret-value"
            ),
            CrewFailureKind.AUTHENTICATION,
            False,
        ),
        (
            RateLimitError("Too many requests"),
            CrewFailureKind.RATE_LIMIT,
            True,
        ),
        (
            APITimeoutError("Request timed out"),
            CrewFailureKind.TIMEOUT,
            True,
        ),
        (
            APIConnectionError("Connection refused"),
            CrewFailureKind.CONNECTION,
            True,
        ),
        (
            RuntimeError("Model not found"),
            CrewFailureKind.MODEL,
            False,
        ),
        (
            RuntimeError("Context length exceeded"),
            CrewFailureKind.REQUEST,
            False,
        ),
        (
            RuntimeError("Unknown provider failure"),
            CrewFailureKind.PROVIDER,
            True,
        ),
    ],
)
def test_classifies_provider_failures(
    exception,
    expected_kind,
    retryable,
) -> None:
    result = classify_crew_exception(exception)

    assert result.kind is expected_kind
    assert result.retryable is retryable
    assert result.message


def test_safe_message_does_not_expose_original_secret() -> None:
    exception = AuthenticationError(
        "Invalid API key: super-secret-key"
    )

    result = classify_crew_exception(exception)

    assert "super-secret-key" not in result.message


def test_classifies_nested_exception_cause() -> None:
    provider_error = RateLimitError(
        "Status code 429"
    )

    try:
        raise RuntimeError(
            "crew failed"
        ) from provider_error
    except RuntimeError as outer_error:
        result = classify_crew_exception(
            outer_error
        )

    assert result.kind is CrewFailureKind.RATE_LIMIT