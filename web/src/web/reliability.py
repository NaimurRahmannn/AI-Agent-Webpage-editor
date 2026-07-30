from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator


class CrewFailureKind(str, Enum):
    """Safe categories for external LLM failures."""

    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    MODEL = "model"
    REQUEST = "request"
    PROVIDER = "provider"


@dataclass(frozen=True, slots=True)
class CrewFailureDetails:
    """Safe information that may be displayed in the terminal."""

    kind: CrewFailureKind
    message: str
    retryable: bool


def _exception_chain(
    exception: BaseException,
) -> Iterator[BaseException]:
    """Yield an exception and its causes without looping forever."""

    current: BaseException | None = exception
    seen: set[int] = set()

    while current is not None:
        identity = id(current)

        if identity in seen:
            return

        seen.add(identity)
        yield current

        current = (
            current.__cause__
            if current.__cause__ is not None
            else current.__context__
        )


def _searchable_exception_text(
    exception: BaseException,
) -> str:
    """
    Build internal classification text.

    This text is never returned to the user because provider messages
    could contain request details or sensitive configuration data.
    """

    fragments: list[str] = []

    for item in _exception_chain(exception):
        fragments.append(type(item).__name__.casefold())

        try:
            fragments.append(str(item).casefold())
        except Exception:
            # Classification must not fail because an exception has a
            # broken or unusual __str__ implementation.
            continue

    return " ".join(fragments)


def _contains_any(
    searchable_text: str,
    markers: tuple[str, ...],
) -> bool:
    return any(
        marker in searchable_text
        for marker in markers
    )


def classify_crew_exception(
    exception: BaseException,
) -> CrewFailureDetails:
    """
    Convert arbitrary provider exceptions into safe terminal messages.

    Classification intentionally uses names and text rather than direct
    provider exception imports. This keeps the application independent
    of optional SDK exception hierarchies.
    """

    searchable = _searchable_exception_text(exception)

    authentication_markers = (
        "authenticationerror",
        "permissiondenied",
        "unauthorized",
        "invalid api key",
        "invalid_api_key",
        "incorrect api key",
        "status code 401",
        "status_code=401",
        "status 401",
        "status code 403",
        "status_code=403",
        "status 403",
    )

    rate_limit_markers = (
        "ratelimiterror",
        "rate limit",
        "rate_limit",
        "too many requests",
        "status code 429",
        "status_code=429",
        "status 429",
    )

    timeout_markers = (
        "timeouterror",
        "apitimeouterror",
        "readtimeout",
        "connecttimeout",
        "timed out",
        "timeout",
    )

    model_markers = (
        "model not found",
        "model_not_found",
        "unknown model",
        "invalid model",
        "model unavailable",
        "does not exist or you do not have access",
    )

    connection_markers = (
        "apiconnectionerror",
        "connectionerror",
        "connection refused",
        "connection reset",
        "network error",
        "networkerror",
        "name resolution",
        "dns",
        "temporary failure in name resolution",
    )

    request_markers = (
        "badrequesterror",
        "invalidrequesterror",
        "context length",
        "context_length",
        "maximum context",
        "request too large",
        "status code 400",
        "status_code=400",
        "status 400",
    )

    if _contains_any(searchable, authentication_markers):
        return CrewFailureDetails(
            kind=CrewFailureKind.AUTHENTICATION,
            message=(
                "Groq authentication failed. "
                "Check GROQ_API_KEY and retry."
            ),
            retryable=False,
        )

    if _contains_any(searchable, rate_limit_markers):
        return CrewFailureDetails(
            kind=CrewFailureKind.RATE_LIMIT,
            message=(
                "Groq rate limit reached. "
                "Wait briefly before retrying."
            ),
            retryable=True,
        )

    if _contains_any(searchable, timeout_markers):
        return CrewFailureDetails(
            kind=CrewFailureKind.TIMEOUT,
            message=(
                "The Groq request timed out. "
                "The instruction can be retried."
            ),
            retryable=True,
        )

    if _contains_any(searchable, model_markers):
        return CrewFailureDetails(
            kind=CrewFailureKind.MODEL,
            message=(
                "The configured Groq model is unavailable. "
                "Check GROQ_MODEL."
            ),
            retryable=False,
        )

    if _contains_any(searchable, connection_markers):
        return CrewFailureDetails(
            kind=CrewFailureKind.CONNECTION,
            message=(
                "The application could not reach Groq. "
                "Check the network connection and retry."
            ),
            retryable=True,
        )

    if _contains_any(searchable, request_markers):
        return CrewFailureDetails(
            kind=CrewFailureKind.REQUEST,
            message=(
                "Groq rejected the request. "
                "Check the configured model and prompt size."
            ),
            retryable=False,
        )

    return CrewFailureDetails(
        kind=CrewFailureKind.PROVIDER,
        message=(
            "The LLM provider could not complete the request. "
            "Retry later."
        ),
        retryable=True,
    )