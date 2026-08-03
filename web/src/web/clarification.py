"""Conversational clarification state management.

Provides process-local, bounded state management for pending clarifications when
a user instruction is ambiguous but has candidate options.
"""

from __future__ import annotations

from web.models import ClarificationOption, ClarificationRequest, StrictModel


class ClarificationError(RuntimeError):
    """Base error for clarification workflow operations."""


class ClarificationValidationError(ClarificationError):
    """Raised when a clarification request or option is invalid."""


class ClarificationResolutionError(ClarificationError):
    """Raised when user input cannot resolve to a unique clarification option."""


class ClarificationAttemptsExceeded(ClarificationError):
    """Raised when user exceeds the maximum allowed clarification attempts."""


class PendingClarification(StrictModel):
    """State record for one active pending clarification in progress."""

    original_instruction: str
    question: str
    options: tuple[ClarificationOption, ...]
    message: str
    attempts: int = 0
    max_attempts: int = 3


def build_clarified_instruction(
    original_instruction: str,
    selected_option: ClarificationOption,
) -> str:
    """
    Construct a structured instruction for the rerun turn after clarification.

    Includes the original user request and explicit metadata from the selected option.
    Does NOT supply old replacement source code.
    """

    lines = [
        "ORIGINAL USER INSTRUCTION",
        original_instruction.strip(),
        "",
        "CLARIFICATION SELECTION",
        f"The user selected option {selected_option.id}:",
        f"- label: {selected_option.label}",
        f"- file: {selected_option.file or 'unknown'}",
        f"- target: {selected_option.target or selected_option.label}",
    ]

    if selected_option.selector:
        lines.append(f"- selector: {selected_option.selector}")
    if selected_option.property:
        lines.append(f"- property: {selected_option.property}")

    lines.extend(
        [
            "",
            "REQUIREMENTS",
            "Relocate this selected target in the current source.",
            "Do not reuse old source text from the ambiguous turn.",
            "Apply only the original requested change to the selected target.",
        ]
    )

    return "\n".join(lines)


class ClarificationManager:
    """
    Process-local state manager for pending clarifications.

    Enforces that only one pending clarification can exist at a time and bounds
    retry attempts before auto-cancelling.
    """

    def __init__(self, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts
        self._pending: PendingClarification | None = None

    def has_pending(self) -> bool:
        """Check whether a clarification is currently pending."""
        return self._pending is not None

    def get_pending(self) -> PendingClarification | None:
        """Retrieve the active pending clarification or None."""
        return self._pending

    def set_pending(
        self,
        request: ClarificationRequest,
        original_instruction: str,
    ) -> None:
        """
        Store a new pending clarification.

        Raises ClarificationError if a clarification is already pending.
        """
        if self._pending is not None:
            raise ClarificationError(
                "A clarification is already pending. Cancel it before setting a new one."
            )

        norm_inst = original_instruction.strip()
        if not norm_inst:
            raise ClarificationValidationError(
                "original_instruction must not be empty"
            )

        self._pending = PendingClarification(
            original_instruction=norm_inst,
            question=request.question,
            options=request.options,
            message=request.message,
            attempts=0,
            max_attempts=self.max_attempts,
        )

    def cancel(self) -> None:
        """Cancel the active pending clarification."""
        self._pending = None

    def clear(self) -> None:
        """Clear the active pending clarification."""
        self._pending = None

    def resolve_answer(
        self,
        user_answer: str,
    ) -> tuple[ClarificationOption, str]:
        """
        Resolve a user input string to a unique ClarificationOption and
        return (selected_option, clarified_instruction).

        Increments attempts on invalid input and raises ClarificationAttemptsExceeded
        if max_attempts is reached.
        """
        if self._pending is None:
            raise ClarificationError("No clarification is currently pending.")

        raw_answer = user_answer.strip()
        if not raw_answer:
            self._increment_attempts()
            raise ClarificationResolutionError("Answer cannot be blank.")

        norm_answer = raw_answer.casefold()

        # 1. Exact match on numeric/string option ID
        id_matches = [
            opt
            for opt in self._pending.options
            if opt.id.casefold() == norm_answer
        ]
        if len(id_matches) == 1:
            return self._finalize_resolution(id_matches[0])

        # 2. Exact match on label or target
        exact_matches = [
            opt
            for opt in self._pending.options
            if opt.label.casefold() == norm_answer
            or (opt.target and opt.target.casefold() == norm_answer)
        ]
        if len(exact_matches) == 1:
            return self._finalize_resolution(exact_matches[0])

        # 3. Unique partial match on label or target
        partial_matches = [
            opt
            for opt in self._pending.options
            if norm_answer in opt.label.casefold()
            or (opt.target and norm_answer in opt.target.casefold())
        ]
        if len(partial_matches) == 1:
            return self._finalize_resolution(partial_matches[0])

        # Failed match
        self._increment_attempts()

        if len(partial_matches) > 1:
            raise ClarificationResolutionError(
                f"'{user_answer}' matches multiple options. Please enter the option number."
            )
        else:
            raise ClarificationResolutionError(
                f"'{user_answer}' did not match any option. Enter an option number or target label."
            )

    def _increment_attempts(self) -> None:
        if self._pending is None:
            return

        self._pending.attempts += 1
        if self._pending.attempts >= self._pending.max_attempts:
            self.clear()
            raise ClarificationAttemptsExceeded(
                "Maximum clarification attempts exceeded. Clarification cancelled."
            )

    def _finalize_resolution(
        self,
        selected: ClarificationOption,
    ) -> tuple[ClarificationOption, str]:
        if self._pending is None:
            raise ClarificationError("No pending clarification.")

        original = self._pending.original_instruction
        self.clear()
        clarified_instruction = build_clarified_instruction(original, selected)
        return selected, clarified_instruction
