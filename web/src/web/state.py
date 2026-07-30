from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Final

from web.models import (
    LastTarget,
    ProposedPatch,
    SessionMemorySnapshot,
    SuccessfulTurn,
)


MAX_INSTRUCTION_CHARS: Final = 500
MAX_SUMMARY_CHARS: Final = 300
MAX_FILE_CHARS: Final = 240
MAX_TARGET_CHARS: Final = 240
MAX_SELECTOR_CHARS: Final = 240
MAX_PROPERTY_CHARS: Final = 120
MAX_CONTEXT_CHARS: Final = 6_000


def _compact_text(value: str, maximum: int) -> str:
    """
    Collapse whitespace and limit text retained in conversational memory.

    This prevents one unusually large instruction or summary from making
    the supposedly bounded memory context grow without limit.
    """

    compact = " ".join(value.split())

    if len(compact) <= maximum:
        return compact

    if maximum <= 1:
        return compact[:maximum]

    return f"{compact[: maximum - 1]}…"


def _compact_optional(
    value: str | None,
    maximum: int,
) -> str | None:
    if value is None:
        return None

    compact = _compact_text(value, maximum)
    return compact or None


@dataclass(slots=True)
class SessionState:
    """
    Bounded memory retained only during the current Python process.

    This class stores summaries and target metadata only. It does not
    store complete file snapshots, patch bodies, API responses, or
    persistent history.
    """

    history_limit: int
    _successful_turns: deque[SuccessfulTurn] = field(init=False)
    _last_target: LastTarget | None = field(
        init=False,
        default=None,
    )

    def __post_init__(self) -> None:
        if self.history_limit < 1:
            raise ValueError("history_limit must be at least 1")

        self._successful_turns = deque(maxlen=self.history_limit)

    @property
    def successful_turn_count(self) -> int:
        """Return the number of retained successful turns."""

        return len(self._successful_turns)

    @property
    def successful_turns(self) -> tuple[SuccessfulTurn, ...]:
        """Return a copy of retained successful turns."""

        return tuple(self._successful_turns)

    @property
    def last_target(self) -> LastTarget | None:
        """Return the most recently edited target metadata."""

        return self._last_target

    def snapshot(self) -> SessionMemorySnapshot:
        """Return a validated copy of the current session memory."""

        return SessionMemorySnapshot(
            recent_successful_turns=tuple(self._successful_turns),
            last_target=self._last_target,
        )

    def record_success(
        self,
        instruction: str,
        patch: ProposedPatch,
    ) -> None:
        """
        Record a patch only after it has been successfully written.

        Future orchestration must call this method after validation,
        backup creation, atomic writing, and diff generation succeed.
        """

        if patch.status != "ready":
            raise ValueError(
                "only a ready patch can be recorded as successful"
            )

        if patch.file is None:
            raise ValueError("ready patch file is missing")

        if patch.target is None:
            raise ValueError("ready patch target is missing")

        turn = SuccessfulTurn(
            instruction=_compact_text(
                instruction,
                MAX_INSTRUCTION_CHARS,
            ),
            summary=_compact_text(
                patch.summary,
                MAX_SUMMARY_CHARS,
            ),
            file=_compact_text(
                patch.file,
                MAX_FILE_CHARS,
            ),
            target=_compact_text(
                patch.target,
                MAX_TARGET_CHARS,
            ),
            selector=_compact_optional(
                patch.selector,
                MAX_SELECTOR_CHARS,
            ),
            property=_compact_optional(
                patch.property,
                MAX_PROPERTY_CHARS,
            ),
        )

        self._successful_turns.append(turn)

        self._last_target = LastTarget(
            file=turn.file,
            target=turn.target,
            selector=turn.selector,
            property=turn.property,
        )

    def build_context(self) -> str:
        """
        Render bounded memory for future locator and editor prompts.

        The context explicitly tells agents that memory is advisory and
        current files remain authoritative.
        """

        lines = [
            "SESSION MEMORY",
            "Current source files are the source of truth.",
            "Use this memory only to resolve conversational follow-ups.",
            "It describes successful edits only.",
        ]

        if self._last_target is None:
            lines.extend(
                [
                    "",
                    "Last successful target:",
                    "- none",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Last successful target:",
                    f"- file: {self._last_target.file}",
                    f"- target: {self._last_target.target}",
                    (
                        f"- selector: {self._last_target.selector}"
                        if self._last_target.selector
                        else "- selector: none"
                    ),
                    (
                        f"- property: {self._last_target.property}"
                        if self._last_target.property
                        else "- property: none"
                    ),
                ]
            )

        lines.extend(["", "Recent successful turns:"])

        if not self._successful_turns:
            lines.append("- none")
        else:
            for index, turn in enumerate(
                self._successful_turns,
                start=1,
            ):
                lines.extend(
                    [
                        f"{index}. instruction: {turn.instruction}",
                        f"   summary: {turn.summary}",
                        f"   file: {turn.file}",
                        f"   target: {turn.target}",
                        (
                            f"   selector: {turn.selector}"
                            if turn.selector
                            else "   selector: none"
                        ),
                        (
                            f"   property: {turn.property}"
                            if turn.property
                            else "   property: none"
                        ),
                    ]
                )

        rendered = "\n".join(lines)

        if len(rendered) <= MAX_CONTEXT_CHARS:
            return rendered

        return f"{rendered[: MAX_CONTEXT_CHARS - 1]}…"