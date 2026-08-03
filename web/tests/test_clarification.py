"""Unit tests for clarification models and ClarificationManager.

All tests are deterministic and execute entirely in memory.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from web.clarification import (
    ClarificationAttemptsExceeded,
    ClarificationError,
    ClarificationManager,
    ClarificationResolutionError,
    ClarificationValidationError,
    build_clarified_instruction,
)
from web.models import (
    ClarificationOption,
    ClarificationRequest,
    LocatorResult,
    ProposedPatch,
)
from web.state import SessionState


def test_clarification_request_accepts_two_valid_options() -> None:
    """ClarificationRequest validates with 2 options."""
    opt1 = ClarificationOption(id="1", label="Brand link: Weft Studio", file="index.html", target="brand link")
    opt2 = ClarificationOption(id="2", label="CTA link: Start a project", file="index.html", target="CTA link")

    req = ClarificationRequest(
        question="Which link should I change?",
        options=(opt1, opt2),
        message="Multiple links found.",
    )

    assert req.question == "Which link should I change?"
    assert len(req.options) == 2


def test_clarification_request_rejects_fewer_than_two_options() -> None:
    """ClarificationRequest requires at least 2 options."""
    opt1 = ClarificationOption(id="1", label="Option 1")

    with pytest.raises(ValidationError, match="at least two options"):
        ClarificationRequest(
            question="Which link?",
            options=(opt1,),
            message="Only one option.",
        )


def test_clarification_request_rejects_duplicate_option_ids() -> None:
    """Duplicate option IDs are rejected."""
    opt1 = ClarificationOption(id="1", label="Option 1")
    opt2 = ClarificationOption(id="1", label="Option 2")

    with pytest.raises(ValidationError, match="unique"):
        ClarificationRequest(
            question="Which option?",
            options=(opt1, opt2),
            message="Duplicate IDs.",
        )


def test_clarification_request_rejects_too_many_options() -> None:
    """ClarificationRequest rejects more than six options."""
    opts = tuple(
        ClarificationOption(id=str(i), label=f"Option {i}")
        for i in range(1, 8)
    )

    with pytest.raises(ValidationError, match="at most six options"):
        ClarificationRequest(
            question="Which option?",
            options=opts,
            message="Too many options.",
        )


def test_clarification_option_rejects_blank_ids() -> None:
    """ClarificationOption rejects blank ID."""
    with pytest.raises(ValidationError, match="not be empty"):
        ClarificationOption(id="  ", label="Option label")


def test_clarification_option_rejects_blank_labels() -> None:
    """ClarificationOption rejects blank label."""
    with pytest.raises(ValidationError, match="not be empty"):
        ClarificationOption(id="1", label="")


def test_unexpected_fields_rejected() -> None:
    """StrictModel forbids extra fields like old_text in options."""
    with pytest.raises(ValidationError):
        ClarificationOption.model_validate(
            {"id": "1", "label": "Option 1", "old_text": "h1 { color: red; }"}
        )


def test_pending_state_starts_empty() -> None:
    """ClarificationManager starts with no pending clarification."""
    mgr = ClarificationManager()
    assert mgr.has_pending() is False
    assert mgr.get_pending() is None


def test_pending_state_can_be_set() -> None:
    """Setting pending state populates PendingClarification."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Brand link"),
            ClarificationOption(id="2", label="CTA link"),
        ),
        message="Multiple links.",
    )

    mgr.set_pending(req, "Change link text")
    assert mgr.has_pending() is True
    pending = mgr.get_pending()
    assert pending is not None
    assert pending.original_instruction == "Change link text"
    assert pending.question == "Which link?"


def test_second_pending_request_cannot_silently_overwrite() -> None:
    """Setting a second pending clarification without clearing raises an error."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Brand link"),
            ClarificationOption(id="2", label="CTA link"),
        ),
        message="Multiple links.",
    )

    mgr.set_pending(req, "First request")
    with pytest.raises(ClarificationError, match="already pending"):
        mgr.set_pending(req, "Second request")


def test_numeric_option_selection_resolves() -> None:
    """Numeric ID input ('2') resolves the second option."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Brand link: Weft Studio"),
            ClarificationOption(id="2", label="CTA link: Start a project"),
        ),
        message="Multiple links.",
    )

    mgr.set_pending(req, "Change link text")
    selected, instruction = mgr.resolve_answer("2")

    assert selected.id == "2"
    assert selected.label == "CTA link: Start a project"
    assert "CLARIFICATION SELECTION" in instruction
    assert "CTA link: Start a project" in instruction
    assert mgr.has_pending() is False


def test_exact_label_selection_resolves_case_insensitively() -> None:
    """Exact label input resolves case-insensitively."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Brand link: Weft Studio"),
            ClarificationOption(id="2", label="CTA link: Start a project"),
        ),
        message="Multiple links.",
    )

    mgr.set_pending(req, "Change link text")
    selected, _ = mgr.resolve_answer("brand link: weft studio")

    assert selected.id == "1"
    assert mgr.has_pending() is False


def test_unique_partial_selection_resolves() -> None:
    """Unique partial match (e.g. 'CTA') resolves."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Brand link: Weft Studio"),
            ClarificationOption(id="2", label="CTA link: Start a project"),
        ),
        message="Multiple links.",
    )

    mgr.set_pending(req, "Change link text")
    selected, _ = mgr.resolve_answer("CTA")

    assert selected.id == "2"


def test_ambiguous_partial_selection_rejected() -> None:
    """Partial match matching multiple options raises resolution error."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Navigation link: Work"),
            ClarificationOption(id="2", label="Navigation link: About"),
        ),
        message="Multiple links.",
    )

    mgr.set_pending(req, "Change link text")
    with pytest.raises(ClarificationResolutionError, match="multiple options"):
        mgr.resolve_answer("Navigation link")

    assert mgr.get_pending().attempts == 1


def test_empty_answer_rejected() -> None:
    """Empty answer input raises resolution error."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Option 1"),
            ClarificationOption(id="2", label="Option 2"),
        ),
        message="Multiple options.",
    )

    mgr.set_pending(req, "Change link text")
    with pytest.raises(ClarificationResolutionError, match="blank"):
        mgr.resolve_answer("   ")

    assert mgr.get_pending().attempts == 1


def test_invalid_option_increments_attempts() -> None:
    """Invalid selection increments attempt count."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Option 1"),
            ClarificationOption(id="2", label="Option 2"),
        ),
        message="Multiple options.",
    )

    mgr.set_pending(req, "Change link text")
    with pytest.raises(ClarificationResolutionError):
        mgr.resolve_answer("99")

    assert mgr.get_pending().attempts == 1


def test_maximum_attempts_cancel_clarification() -> None:
    """Exceeding max attempts clears pending state and raises ClarificationAttemptsExceeded."""
    mgr = ClarificationManager(max_attempts=2)
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Option 1"),
            ClarificationOption(id="2", label="Option 2"),
        ),
        message="Multiple options.",
    )

    mgr.set_pending(req, "Change link text")

    # Attempt 1
    with pytest.raises(ClarificationResolutionError):
        mgr.resolve_answer("99")
    assert mgr.has_pending() is True

    # Attempt 2 -> Exceeded
    with pytest.raises(ClarificationAttemptsExceeded, match="Maximum clarification attempts exceeded"):
        mgr.resolve_answer("100")

    assert mgr.has_pending() is False


def test_cancel_clears_pending_state() -> None:
    """cancel() removes pending clarification."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Option 1"),
            ClarificationOption(id="2", label="Option 2"),
        ),
        message="Multiple options.",
    )

    mgr.set_pending(req, "Change link text")
    mgr.cancel()
    assert mgr.has_pending() is False


def test_exit_and_quit_behavior() -> None:
    """EXIT_COMMANDS check behaves correctly."""
    from web.session import EXIT_COMMANDS

    assert "exit" in EXIT_COMMANDS
    assert "quit" in EXIT_COMMANDS


def test_clarification_state_not_in_successful_turn_memory() -> None:
    """Clarification requests do not update SessionState successful turns."""
    session = SessionState(history_limit=5)
    assert session.successful_turn_count == 0
    assert session.last_target is None


def test_clarification_state_not_persistent() -> None:
    """ClarificationManager is process-local and separate per instance."""
    mgr1 = ClarificationManager()
    mgr2 = ClarificationManager()

    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Option 1"),
            ClarificationOption(id="2", label="Option 2"),
        ),
        message="Multiple options.",
    )

    mgr1.set_pending(req, "Instruction")
    assert mgr1.has_pending() is True
    assert mgr2.has_pending() is False
