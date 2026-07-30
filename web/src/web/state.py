from __future__ import annotations

import re
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


REFERENTIAL_TOKENS: Final = frozenset(
    {
        "it",
        "its",
        "that",
        "this",
        "those",
        "these",
        "same",
        "again",
    }
)

COMPARATIVE_OPENERS: Final = frozenset(
    {
        "darker",
        "lighter",
        "brighter",
        "bigger",
        "smaller",
        "wider",
        "narrower",
        "rounder",
        "more",
        "less",
        "even",
    }
)


def _compact_text(
    value: str,
    maximum: int,
) -> str:
    """Collapse whitespace and limit retained memory text."""

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


def instruction_looks_like_follow_up(
    instruction: str,
) -> bool:
    """
    Detect wording that probably refers to a previous successful edit.

    This is an advisory heuristic only. The locator must still identify
    an exact target in the current files.
    """

    normalized = " ".join(
        instruction.casefold().split()
    )

    if not normalized:
        return False

    tokens = re.findall(
        r"[a-z0-9']+",
        normalized,
    )

    if not tokens:
        return False

    if any(
        token in REFERENTIAL_TOKENS
        for token in tokens
    ):
        return True

    # Short phrases such as "even darker" and "rounder please" are
    # commonly conversational follow-ups.
    if (
        len(tokens) <= 4
        and tokens[0] in COMPARATIVE_OPENERS
    ):
        return True

    return False


def _render_turn(
    index: int,
    turn: SuccessfulTurn,
) -> str:
    """Render one complete successful-turn record."""

    label = (
        f"{index}. most recent successful turn"
        if index == 1
        else f"{index}. earlier successful turn"
    )

    lines = [
        label,
        f"   instruction: {turn.instruction}",
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

    return "\n".join(lines)


@dataclass(slots=True)
class SessionState:
    """
    Bounded memory retained only during the current process.

    Current source files remain authoritative. This state stores compact
    metadata and summaries, not source snapshots or patch bodies.
    """

    history_limit: int
    _successful_turns: deque[SuccessfulTurn] = field(
        init=False
    )
    _last_target: LastTarget | None = field(
        init=False,
        default=None,
    )

    def __post_init__(self) -> None:
        if self.history_limit < 1:
            raise ValueError(
                "history_limit must be at least 1"
            )

        self._successful_turns = deque(
            maxlen=self.history_limit
        )

    @property
    def successful_turn_count(self) -> int:
        return len(self._successful_turns)

    @property
    def successful_turns(
        self,
    ) -> tuple[SuccessfulTurn, ...]:
        return tuple(self._successful_turns)

    @property
    def last_target(self) -> LastTarget | None:
        return self._last_target

    def snapshot(self) -> SessionMemorySnapshot:
        return SessionMemorySnapshot(
            recent_successful_turns=tuple(
                self._successful_turns
            ),
            last_target=self._last_target,
        )

    def record_success(
        self,
        instruction: str,
        patch: ProposedPatch,
    ) -> None:
        """
        Record one turn only after a successful source write.

        Future orchestration must call this after validation, backup
        creation, atomic replacement, and diff generation succeed.
        """

        if patch.status != "ready":
            raise ValueError(
                "only a ready patch can be recorded as successful"
            )

        if patch.file is None:
            raise ValueError(
                "ready patch file is missing"
            )

        if patch.target is None:
            raise ValueError(
                "ready patch target is missing"
            )

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

    def build_context(
        self,
        instruction: str | None = None,
    ) -> str:
        """
        Render bounded advisory context for locator/editor tasks.

        Successful turns are rendered newest first. Complete records are
        included until the context limit is reached.
        """

        follow_up = (
            instruction_looks_like_follow_up(
                instruction
            )
            if instruction is not None
            else False
        )

        lines = [
            "SESSION MEMORY",
            "Current source files are the source of truth.",
            (
                "Memory may only help resolve references to "
                "previous successful edits."
            ),
            (
                "Never reuse old source text from memory; "
                "relocate the target in current source."
            ),
            "",
            "Follow-up assessment:",
            (
                "- likely follow-up: yes"
                if follow_up
                else "- likely follow-up: no"
            ),
        ]

        if follow_up and self._last_target is not None:
            lines.append(
                "- guidance: consider the last successful "
                "target first, then relocate it in current source"
            )
        elif follow_up:
            lines.append(
                "- guidance: no successful target exists; "
                "reject an unresolved reference as ambiguous"
            )
        else:
            lines.append(
                "- guidance: treat the instruction as standalone "
                "unless its wording clearly references prior work"
            )

        lines.extend(
            [
                "",
                "Last successful target:",
            ]
        )

        if self._last_target is None:
            lines.append("- none")
        else:
            lines.extend(
                [
                    f"- file: {self._last_target.file}",
                    f"- target: {self._last_target.target}",
                    (
                        f"- selector: "
                        f"{self._last_target.selector}"
                        if self._last_target.selector
                        else "- selector: none"
                    ),
                    (
                        f"- property: "
                        f"{self._last_target.property}"
                        if self._last_target.property
                        else "- property: none"
                    ),
                ]
            )

        lines.extend(
            [
                "",
                "Recent successful turns:",
                "- newest first",
            ]
        )

        rendered = "\n".join(lines)

        if not self._successful_turns:
            candidate = f"{rendered}\n- none"
            return candidate[:MAX_CONTEXT_CHARS]

        omitted_count = 0

        for index, turn in enumerate(
            reversed(self._successful_turns),
            start=1,
        ):
            turn_block = _render_turn(index, turn)
            candidate = f"{rendered}\n{turn_block}"

            if len(candidate) > MAX_CONTEXT_CHARS:
                omitted_count = (
                    len(self._successful_turns)
                    - index
                    + 1
                )
                break

            rendered = candidate

        if omitted_count:
            omission = (
                f"\n- {omitted_count} older successful "
                "turn(s) omitted by the context limit"
            )

            if (
                len(rendered) + len(omission)
                <= MAX_CONTEXT_CHARS
            ):
                rendered += omission

        return rendered[:MAX_CONTEXT_CHARS]
