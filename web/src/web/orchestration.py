from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from web.crew import build_crew_inputs
from web.models import LocatorResult, ProposedPatch
from web.settings import Settings
from web.state import SessionState
from web.tools.patcher import PatchApplicationResult, apply_patch


class CrewExecutionError(RuntimeError):
    """Raised when CrewAI execution fails before producing usable output."""


class CrewOutputError(ValueError):
    """Raised when CrewAI output fails deterministic validation."""


@dataclass(frozen=True, slots=True)
class TurnResult:
    """User-facing result for one conversational editing turn."""

    status: Literal["applied", "ambiguous", "unsupported"]
    summary: str
    message: str | None = None
    file: str | None = None
    backup_file: str | None = None
    diff: str | None = None


CrewExecutor = Callable[[Settings, Mapping[str, str]], Any]
PatchApplier = Callable[..., PatchApplicationResult]


def execute_crew(
    settings: Settings,
    inputs: Mapping[str, str],
) -> Any:
    """Run the configured CrewAI crew for one turn."""

    from web.crew import WebEditingCrew

    return WebEditingCrew(settings).crew().kickoff(
        inputs=dict(inputs)
    )


def _task_pydantic(output: Any, index: int) -> Any:
    try:
        return output.tasks_output[index].pydantic
    except (AttributeError, IndexError, TypeError) as exc:
        raise CrewOutputError(
            "crew output must include exactly two structured task outputs"
        ) from exc


def validate_crew_output(
    output: Any,
) -> tuple[LocatorResult, ProposedPatch]:
    """
    Validate the locator task, editor task, and final crew result.

    The LLM proposes structure, but this function enforces the contract
    before any filesystem write can happen.
    """

    tasks_output = getattr(output, "tasks_output", None)

    if not isinstance(tasks_output, list) or len(tasks_output) != 2:
        raise CrewOutputError(
            "crew output must include exactly two task outputs"
        )

    locator = _task_pydantic(output, 0)
    patch = _task_pydantic(output, 1)
    final_output = getattr(output, "pydantic", None)

    if not isinstance(locator, LocatorResult):
        raise CrewOutputError(
            "first task output must be a LocatorResult"
        )

    if not isinstance(patch, ProposedPatch):
        raise CrewOutputError(
            "second task output must be a ProposedPatch"
        )

    if final_output != patch:
        raise CrewOutputError(
            "final crew output does not match editor task output"
        )

    if locator.status == "located":
        if patch.status != "ready":
            raise CrewOutputError(
                "locator and editor statuses are inconsistent"
            )

        if patch.file != locator.file:
            raise CrewOutputError(
                "ready patch file does not match locator file"
            )

        if patch.old_text != locator.exact_source:
            raise CrewOutputError(
                "ready patch old_text does not match locator exact_source"
            )

        return locator, patch

    if patch.status != locator.status:
        raise CrewOutputError(
            "locator and editor statuses are inconsistent"
        )

    return locator, patch


def process_turn(
    *,
    settings: Settings,
    session_state: SessionState,
    instruction: str,
    sources: Mapping[str, str],
    crew_executor: CrewExecutor = execute_crew,
    patch_applier: PatchApplier = apply_patch,
) -> TurnResult:
    """Run one locate/edit/apply cycle and update memory after success."""

    inputs = build_crew_inputs(
        instruction=instruction,
        sources=sources,
        session_memory=session_state.build_context(),
    )

    try:
        crew_output = crew_executor(settings, inputs)
    except Exception as exc:
        raise CrewExecutionError(
            f"Crew execution failed ({type(exc).__name__}): {exc}"
        ) from exc

    locator, patch = validate_crew_output(crew_output)

    if patch.status != "ready":
        return TurnResult(
            status=patch.status,
            summary=patch.summary,
            message=patch.message or locator.message,
        )

    if patch.file is None:
        raise CrewOutputError("ready patch file is missing")

    if patch.file not in sources:
        raise CrewOutputError(
            f"ready patch file was not supplied to the crew: {patch.file}"
        )

    application = patch_applier(
        settings,
        patch,
        expected_source_text=sources[patch.file],
    )

    session_state.record_success(
        instruction=instruction,
        patch=patch,
    )

    return TurnResult(
        status="applied",
        summary=application.summary,
        file=application.file,
        backup_file=application.backup_file,
        diff=application.diff,
    )
