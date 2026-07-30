from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from web.models import LocatorResult, ProposedPatch
from web.orchestration import (
    CrewExecutionError,
    CrewOutputError,
    TurnResult,
    process_turn,
    validate_crew_output,
)
from web.reliability import CrewFailureKind
from web.settings import Settings
from web.state import SessionState
from web.tools.patcher import (
    PatchSourceChangedError,
    PatchWriteError,
)


@dataclass
class FakeTaskOutput:
    pydantic: Any


@dataclass
class FakeCrewOutput:
    pydantic: Any
    tasks_output: list[FakeTaskOutput]


def make_settings(tmp_path) -> Settings:
    return Settings(
        project_root=tmp_path,
        allowed_files=(
            "index.html",
            "style.css",
        ),
        backup_limit=3,
        session_history_limit=3,
        groq_api_key="test-key",
        groq_model="groq/test-model",
    )


def ready_outputs(
    *,
    old_text: str = "color: red;",
    new_text: str = "color: blue;",
    file: str = "style.css",
) -> tuple[
    LocatorResult,
    ProposedPatch,
    FakeCrewOutput,
]:
    locator = LocatorResult(
        status="located",
        file=file,
        target="test color declaration",
        selector=".test",
        property="color",
        exact_source=old_text,
        message="Located the test color declaration.",
    )

    patch = ProposedPatch(
        status="ready",
        file=file,
        old_text=old_text,
        new_text=new_text,
        target="test color declaration",
        selector=".test",
        property="color",
        summary="Change the test color.",
    )

    crew_output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(patch),
        ],
    )

    return locator, patch, crew_output


def rejected_output(
    status: str,
) -> FakeCrewOutput:
    locator = LocatorResult(
        status=status,
        message=f"Locator classified request as {status}.",
    )

    patch = ProposedPatch(
        status=status,
        summary=f"The request is {status}.",
        message=f"The request cannot proceed because it is {status}.",
    )

    return FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(patch),
        ],
    )


def test_process_turn_applies_patch_and_records_memory(
    tmp_path,
) -> None:
    target = tmp_path / "style.css"
    original = (
        ".test {\n"
        "  color: red;\n"
        "}\n"
    )
    target.write_text(
        original,
        encoding="utf-8",
    )

    _, _, crew_output = ready_outputs()

    captured_inputs: dict[str, str] = {}

    def fake_executor(
        settings,
        inputs,
    ):
        captured_inputs.update(inputs)
        return crew_output

    state = SessionState(history_limit=3)

    result = process_turn(
        settings=make_settings(tmp_path),
        session_state=state,
        instruction="Make the test text blue.",
        sources={
            "style.css": original,
            "index.html": "<main></main>\n",
        },
        crew_executor=fake_executor,
    )

    assert isinstance(result, TurnResult)
    assert result.status == "applied"
    assert result.file == "style.css"
    assert result.backup_file == "style.css.bak"
    assert "-  color: red;" in result.diff
    assert "+  color: blue;" in result.diff

    assert target.read_text(
        encoding="utf-8"
    ) == (
        ".test {\n"
        "  color: blue;\n"
        "}\n"
    )

    assert (
        tmp_path / "style.css.bak"
    ).read_text(
        encoding="utf-8"
    ) == original

    assert state.successful_turn_count == 1
    assert state.last_target is not None
    assert state.last_target.file == "style.css"
    assert state.last_target.property == "color"

    assert (
        captured_inputs["instruction"]
        == "Make the test text blue."
    )
    assert (
        "BEGIN FILE: style.css"
        in captured_inputs["source_bundle"]
    )


@pytest.mark.parametrize(
    "status",
    ["ambiguous", "unsupported"],
)
def test_rejected_turn_does_not_call_patcher(
    tmp_path,
    status,
) -> None:
    state = SessionState(history_limit=3)
    crew_output = rejected_output(status)

    def fake_executor(settings, inputs):
        return crew_output

    def forbidden_patcher(*args, **kwargs):
        raise AssertionError(
            "patcher must not be called"
        )

    result = process_turn(
        settings=make_settings(tmp_path),
        session_state=state,
        instruction="Do something unclear.",
        sources={
            "index.html": "<main></main>",
            "style.css": "body {}",
        },
        crew_executor=fake_executor,
        patch_applier=forbidden_patcher,
    )

    assert result.status == status
    assert state.successful_turn_count == 0
    assert state.last_target is None
    assert not list(tmp_path.glob("*.bak*"))


def test_crew_exception_is_safely_classified(
    tmp_path,
) -> None:
    state = SessionState(history_limit=3)

    def failing_executor(settings, inputs):
        raise TimeoutError(
            "request timed out with secret request data"
        )

    with pytest.raises(
        CrewExecutionError,
    ) as captured:
        process_turn(
            settings=make_settings(tmp_path),
            session_state=state,
            instruction="Change the heading.",
            sources={
                "index.html": "<h1>Hello</h1>",
                "style.css": "body {}",
            },
            crew_executor=failing_executor,
        )

    failure = captured.value.failure

    assert failure.kind is CrewFailureKind.TIMEOUT
    assert failure.retryable is True
    assert "secret request data" not in str(
        captured.value
    )

    assert state.successful_turn_count == 0
    assert not list(tmp_path.glob("*.bak*"))


def test_missing_structured_task_output_is_rejected() -> None:
    output = FakeCrewOutput(
        pydantic=None,
        tasks_output=[
            FakeTaskOutput(None),
            FakeTaskOutput(None),
        ],
    )

    with pytest.raises(
        CrewOutputError,
        match="LocatorResult",
    ):
        validate_crew_output(output)


def test_requires_exactly_two_task_outputs() -> None:
    _, patch, _ = ready_outputs()

    output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(patch),
        ],
    )

    with pytest.raises(
        CrewOutputError,
        match="exactly two",
    ):
        validate_crew_output(output)


def test_final_output_must_match_editor_task() -> None:
    locator, editor_patch, _ = ready_outputs()

    different_patch = ProposedPatch(
        status="ready",
        file="style.css",
        old_text="color: red;",
        new_text="color: green;",
        target="test color declaration",
        summary="Change the test color to green.",
    )

    output = FakeCrewOutput(
        pydantic=different_patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(editor_patch),
        ],
    )

    with pytest.raises(
        CrewOutputError,
        match="does not match editor",
    ):
        validate_crew_output(output)


def test_patch_status_must_follow_locator_status() -> None:
    locator = LocatorResult(
        status="ambiguous",
        message="Several elements match.",
    )

    patch = ProposedPatch(
        status="unsupported",
        summary="Request is unsupported.",
        message="Unsupported request.",
    )

    output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(patch),
        ],
    )

    with pytest.raises(
        CrewOutputError,
        match="inconsistent",
    ):
        validate_crew_output(output)


def test_ready_patch_file_must_match_locator() -> None:
    locator = LocatorResult(
        status="located",
        file="index.html",
        target="heading",
        exact_source="<h1>Hello</h1>",
        message="Located the heading.",
    )

    patch = ProposedPatch(
        status="ready",
        file="style.css",
        old_text="<h1>Hello</h1>",
        new_text="<h1>Welcome</h1>",
        target="heading",
        summary="Change the heading.",
    )

    output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(patch),
        ],
    )

    with pytest.raises(
        CrewOutputError,
        match="does not match locator file",
    ):
        validate_crew_output(output)


def test_old_text_must_match_locator_exact_source() -> None:
    locator = LocatorResult(
        status="located",
        file="style.css",
        target="color declaration",
        exact_source="color: red;",
        message="Located the declaration.",
    )

    patch = ProposedPatch(
        status="ready",
        file="style.css",
        old_text="color: crimson;",
        new_text="color: blue;",
        target="color declaration",
        summary="Change the color.",
    )

    output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(patch),
        ],
    )

    with pytest.raises(
        CrewOutputError,
        match="does not match locator exact_source",
    ):
        validate_crew_output(output)


def test_source_snapshot_change_is_rejected(
    tmp_path,
) -> None:
    target = tmp_path / "style.css"
    target.write_text(
        "color: green;",
        encoding="utf-8",
    )

    _, _, crew_output = ready_outputs()

    def fake_executor(settings, inputs):
        return crew_output

    state = SessionState(history_limit=3)

    with pytest.raises(
        PatchSourceChangedError,
        match="supplied to the crew",
    ):
        process_turn(
            settings=make_settings(tmp_path),
            session_state=state,
            instruction="Make it blue.",
            sources={
                "style.css": "color: red;",
                "index.html": "<main></main>",
            },
            crew_executor=fake_executor,
        )

    assert target.read_text(
        encoding="utf-8"
    ) == "color: green;"

    assert not (
        tmp_path / "style.css.bak"
    ).exists()

    assert state.successful_turn_count == 0


def test_patcher_failure_does_not_update_memory(
    tmp_path,
) -> None:
    _, _, crew_output = ready_outputs()

    def fake_executor(settings, inputs):
        return crew_output

    def failing_patcher(
        settings,
        patch,
        *,
        expected_source_text=None,
    ):
        raise PatchWriteError(
            "simulated write failure"
        )

    state = SessionState(history_limit=3)

    with pytest.raises(
        PatchWriteError,
        match="simulated write failure",
    ):
        process_turn(
            settings=make_settings(tmp_path),
            session_state=state,
            instruction="Make it blue.",
            sources={
                "style.css": "color: red;",
                "index.html": "<main></main>",
            },
            crew_executor=fake_executor,
            patch_applier=failing_patcher,
        )

    assert state.successful_turn_count == 0
    assert state.last_target is None


def test_ready_patch_selector_must_match_locator() -> None:
    locator = LocatorResult(
        status="located",
        file="style.css",
        target="CTA background",
        selector=".cta",
        property="background",
        exact_source="background: green;",
        message="Located the CTA background.",
    )

    patch = ProposedPatch(
        status="ready",
        file="style.css",
        old_text="background: green;",
        new_text="background: darkgreen;",
        target="CTA background",
        selector=".other",
        property="background",
        summary="Darken the CTA.",
    )

    output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(patch),
        ],
    )

    with pytest.raises(
        CrewOutputError,
        match="selector does not match",
    ):
        validate_crew_output(output)


def test_ready_patch_property_must_match_locator() -> None:
    locator = LocatorResult(
        status="located",
        file="style.css",
        target="CTA background",
        selector=".cta",
        property="background",
        exact_source="background: green;",
        message="Located the CTA background.",
    )

    patch = ProposedPatch(
        status="ready",
        file="style.css",
        old_text="background: green;",
        new_text="background: darkgreen;",
        target="CTA background",
        selector=".cta",
        property="color",
        summary="Darken the CTA.",
    )

    output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(patch),
        ],
    )

    with pytest.raises(
        CrewOutputError,
        match="property does not match",
    ):
        validate_crew_output(output)


def test_rejected_locator_cannot_include_source_data() -> None:
    locator = LocatorResult(
        status="ambiguous",
        file="style.css",
        exact_source="color: red;",
        message="More than one target matches.",
    )

    patch = ProposedPatch(
        status="ambiguous",
        summary="The request is ambiguous.",
        message="More than one target matches.",
    )

    output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(patch),
        ],
    )

    with pytest.raises(
        CrewOutputError,
        match="must not contain source data",
    ):
        validate_crew_output(output)


def test_second_turn_receives_successful_follow_up_context(
    tmp_path,
) -> None:
    target = tmp_path / "style.css"

    original = (
        ".cta {\n"
        "  background: green;\n"
        "}\n"
    )

    target.write_text(
        original,
        encoding="utf-8",
    )

    first_locator = LocatorResult(
        status="located",
        file="style.css",
        target="CTA background declaration",
        selector=".cta",
        property="background",
        exact_source="background: green;",
        message="Located the CTA background.",
    )

    first_patch = ProposedPatch(
        status="ready",
        file="style.css",
        old_text="background: green;",
        new_text="background: #245c3b;",
        target="CTA background declaration",
        selector=".cta",
        property="background",
        summary="Darken the CTA background.",
    )

    first_output = FakeCrewOutput(
        pydantic=first_patch,
        tasks_output=[
            FakeTaskOutput(first_locator),
            FakeTaskOutput(first_patch),
        ],
    )

    state = SessionState(history_limit=3)
    settings = make_settings(tmp_path)

    process_turn(
        settings=settings,
        session_state=state,
        instruction="Make the CTA background darker.",
        sources={
            "index.html": "<main></main>\n",
            "style.css": original,
        },
        crew_executor=lambda settings, inputs: first_output,
    )

    current_css = target.read_text(
        encoding="utf-8"
    )

    second_locator = LocatorResult(
        status="located",
        file="style.css",
        target="CTA background declaration",
        selector=".cta",
        property="background",
        exact_source="background: #245c3b;",
        message=(
            "Relocated the previous CTA background "
            "in current source."
        ),
    )

    second_patch = ProposedPatch(
        status="ready",
        file="style.css",
        old_text="background: #245c3b;",
        new_text="background: #173d2d;",
        target="CTA background declaration",
        selector=".cta",
        property="background",
        summary="Darken the CTA background again.",
    )

    second_output = FakeCrewOutput(
        pydantic=second_patch,
        tasks_output=[
            FakeTaskOutput(second_locator),
            FakeTaskOutput(second_patch),
        ],
    )

    captured_inputs: dict[str, str] = {}

    def second_executor(settings, inputs):
        captured_inputs.update(inputs)
        return second_output

    result = process_turn(
        settings=settings,
        session_state=state,
        instruction="Make it even darker.",
        sources={
            "index.html": "<main></main>\n",
            "style.css": current_css,
        },
        crew_executor=second_executor,
    )

    assert result.status == "applied"
    assert state.successful_turn_count == 2

    assert (
        "likely follow-up: yes"
        in captured_inputs["session_memory"]
    )
    assert (
        "CTA background declaration"
        in captured_inputs["session_memory"]
    )
    assert (
        "background: #245c3b;"
        in captured_inputs["source_bundle"]
    )

    assert target.read_text(
        encoding="utf-8"
    ) == (
        ".cta {\n"
        "  background: #173d2d;\n"
        "}\n"
    )
