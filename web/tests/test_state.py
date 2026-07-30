import pytest

from web.models import ProposedPatch
from web.state import (
    MAX_CONTEXT_CHARS,
    MAX_INSTRUCTION_CHARS,
    SessionState,
    instruction_looks_like_follow_up,
)


def make_ready_patch(
    *,
    file: str = "style.css",
    target: str = "CTA background",
    selector: str | None = ".cta",
    property_name: str | None = "background",
    summary: str = "Darken the CTA background.",
) -> ProposedPatch:
    return ProposedPatch(
        status="ready",
        file=file,
        old_text="background: var(--accent);",
        new_text="background: #173d2d;",
        target=target,
        selector=selector,
        property=property_name,
        summary=summary,
    )


def test_state_starts_empty() -> None:
    state = SessionState(history_limit=3)

    assert state.successful_turn_count == 0
    assert state.successful_turns == ()
    assert state.last_target is None


def test_record_success_updates_history_and_last_target() -> None:
    state = SessionState(history_limit=3)
    patch = make_ready_patch()

    state.record_success(
        "Make the button darker.",
        patch,
    )

    assert state.successful_turn_count == 1

    turn = state.successful_turns[0]

    assert turn.instruction == "Make the button darker."
    assert turn.summary == "Darken the CTA background."
    assert turn.file == "style.css"
    assert turn.selector == ".cta"
    assert turn.property == "background"

    assert state.last_target is not None
    assert state.last_target.file == "style.css"
    assert state.last_target.target == "CTA background"
    assert state.last_target.selector == ".cta"


def test_history_discards_oldest_turn_at_limit() -> None:
    state = SessionState(history_limit=2)

    state.record_success(
        "First instruction",
        make_ready_patch(
            target="first target",
            summary="First change.",
        ),
    )
    state.record_success(
        "Second instruction",
        make_ready_patch(
            target="second target",
            summary="Second change.",
        ),
    )
    state.record_success(
        "Third instruction",
        make_ready_patch(
            target="third target",
            summary="Third change.",
        ),
    )

    instructions = [
        turn.instruction
        for turn in state.successful_turns
    ]

    assert instructions == [
        "Second instruction",
        "Third instruction",
    ]
    assert state.successful_turn_count == 2

    assert state.last_target is not None
    assert state.last_target.target == "third target"


def test_non_ready_patch_cannot_be_recorded() -> None:
    state = SessionState(history_limit=3)

    patch = ProposedPatch(
        status="ambiguous",
        summary="The requested button is ambiguous.",
        message="More than one matching button was found.",
    )

    with pytest.raises(
        ValueError,
        match="only a ready patch",
    ):
        state.record_success(
            "Make the button darker.",
            patch,
        )

    assert state.successful_turn_count == 0
    assert state.last_target is None


def test_snapshot_does_not_expose_internal_deque() -> None:
    state = SessionState(history_limit=3)

    state.record_success(
        "Make the button darker.",
        make_ready_patch(),
    )

    snapshot = state.snapshot()

    assert isinstance(
        snapshot.recent_successful_turns,
        tuple,
    )
    assert len(snapshot.recent_successful_turns) == 1
    assert snapshot.last_target == state.last_target


def test_context_marks_current_files_as_source_of_truth() -> None:
    state = SessionState(history_limit=3)

    context = state.build_context()

    assert "Current source files are the source of truth." in context
    assert "Last successful target:" in context
    assert "Recent successful turns:" in context
    assert "- none" in context


def test_context_contains_recent_successful_turn() -> None:
    state = SessionState(history_limit=3)

    state.record_success(
        "Make the button darker.",
        make_ready_patch(),
    )

    context = state.build_context()

    assert "instruction: Make the button darker." in context
    assert "summary: Darken the CTA background." in context
    assert "file: style.css" in context
    assert "selector: .cta" in context
    assert "property: background" in context


def test_large_instruction_is_truncated() -> None:
    state = SessionState(history_limit=3)
    instruction = "x" * (MAX_INSTRUCTION_CHARS + 200)

    state.record_success(
        instruction,
        make_ready_patch(),
    )

    stored = state.successful_turns[0].instruction

    assert len(stored) == MAX_INSTRUCTION_CHARS
    assert stored.endswith("…")


def test_rendered_context_has_absolute_character_limit() -> None:
    state = SessionState(history_limit=20)

    for index in range(20):
        state.record_success(
            f"Instruction {index} " + ("x" * 1_000),
            make_ready_patch(
                target=f"target {index} " + ("y" * 500),
                summary=f"Summary {index} " + ("z" * 500),
            ),
        )

    context = state.build_context()

    assert len(context) <= MAX_CONTEXT_CHARS


@pytest.mark.parametrize(
    "instruction",
    [
        "Make it darker.",
        "Round that more.",
        "Change the same one again.",
        "Even smaller.",
        "Rounder please.",
    ],
)
def test_detects_likely_follow_up(
    instruction,
) -> None:
    assert instruction_looks_like_follow_up(
        instruction
    )


@pytest.mark.parametrize(
    "instruction",
    [
        "Change the main heading.",
        "Make the CTA button darker.",
        "Set the feature cards to two columns.",
    ],
)
def test_does_not_mark_specific_request_as_follow_up(
    instruction,
) -> None:
    assert not instruction_looks_like_follow_up(
        instruction
    )


def test_follow_up_context_uses_last_target() -> None:
    state = SessionState(history_limit=3)

    state.record_success(
        "Make the button green.",
        make_ready_patch(),
    )

    context = state.build_context(
        instruction="Make it darker."
    )

    assert "likely follow-up: yes" in context
    assert "consider the last successful target" in context
    assert "file: style.css" in context
    assert "selector: .cta" in context


def test_unresolved_follow_up_has_ambiguity_guidance() -> None:
    state = SessionState(history_limit=3)

    context = state.build_context(
        instruction="Make it darker."
    )

    assert "likely follow-up: yes" in context
    assert "no successful target exists" in context
    assert "ambiguous" in context


def test_successful_turns_are_rendered_newest_first() -> None:
    state = SessionState(history_limit=3)

    state.record_success(
        "First instruction",
        make_ready_patch(
            target="first target",
            summary="First summary.",
        ),
    )
    state.record_success(
        "Second instruction",
        make_ready_patch(
            target="second target",
            summary="Second summary.",
        ),
    )

    context = state.build_context()

    second_position = context.index(
        "instruction: Second instruction"
    )
    first_position = context.index(
        "instruction: First instruction"
    )

    assert second_position < first_position
    assert "most recent successful turn" in context
