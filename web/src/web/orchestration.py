from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from web.crew import (
    LLMConfigurationError,
    WebEditingCrew,
    build_crew_inputs,
)
from web.models import ClarificationRequest, LocatorResult, ProposedPatch
from web.reliability import (
    CrewFailureDetails,
    CrewFailureKind,
    classify_crew_exception,
)
from web.settings import Settings
from web.state import SessionState
from web.tools.patcher import (
    PatchApplicationResult,
    PreparedPatch,
    apply_patch,
    commit_prepared_patch,
    prepare_patch,
)


class TurnError(RuntimeError):
    """Base error for one conversational editing turn."""


class CrewExecutionError(TurnError):
    """Raised when CrewAI or the LLM provider cannot complete a turn."""

    def __init__(
        self,
        failure: CrewFailureDetails | str,
    ) -> None:
        if isinstance(failure, str):
            failure = CrewFailureDetails(
                kind=CrewFailureKind.PROVIDER,
                message=failure,
                retryable=True,
            )

        self.failure = failure
        super().__init__(failure.message)


class CrewOutputError(TurnError):
    """Raised when CrewAI returns unsafe or inconsistent output."""


class CrewExecutor(Protocol):
    def __call__(
        self,
        settings: Settings,
        inputs: Mapping[str, str],
    ) -> object:
        """Execute one fresh crew and return its output."""


class PatchApplier(Protocol):
    def __call__(
        self,
        settings: Settings,
        patch: ProposedPatch,
        *,
        expected_source_text: str | None = None,
    ) -> PatchApplicationResult:
        """Apply one validated patch."""


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Result of one completed conversational turn."""

    status: Literal[
        "applied",
        "preview_ready",
        "ambiguous",
        "unsupported",
        "needs_clarification",
    ]

    summary: str
    message: str | None = None
    file: str | None = None
    backup_file: str | None = None
    diff: str | None = None
    clarification_request: ClarificationRequest | None = None
    proposed_patch: ProposedPatch | None = None
    prepared_patch: PreparedPatch | None = None


def execute_crew(
    settings: Settings,
    inputs: Mapping[str, str],
) -> object:
    """Construct and execute one fresh sequential crew."""

    try:
        definition = WebEditingCrew(settings)
        editing_crew = definition.crew()

        return editing_crew.kickoff(inputs=dict(inputs))

    except LLMConfigurationError:
        raise

    except Exception as exc:
        raise CrewExecutionError(classify_crew_exception(exc)) from exc


def _extract_task_models(
    crew_output: object,
) -> tuple[LocatorResult, ProposedPatch]:
    """Extract the two required Pydantic task outputs."""

    task_outputs = getattr(
        crew_output,
        "tasks_output",
        None,
    )

    if not isinstance(task_outputs, Sequence) or isinstance(
        task_outputs, (str, bytes)
    ):
        raise CrewOutputError("crew result did not contain task outputs")

    if len(task_outputs) != 2:
        raise CrewOutputError(
            "crew result must contain exactly two task outputs"
        )

    locator_output = getattr(
        task_outputs[0],
        "pydantic",
        None,
    )
    editor_output = getattr(
        task_outputs[1],
        "pydantic",
        None,
    )

    if not isinstance(
        locator_output,
        LocatorResult,
    ):
        raise CrewOutputError("locator task did not return LocatorResult")

    if not isinstance(
        editor_output,
        ProposedPatch,
    ):
        raise CrewOutputError("editor task did not return ProposedPatch")

    return locator_output, editor_output


def _validate_status_relationship(
    locator: LocatorResult,
    patch: ProposedPatch,
) -> None:
    expected_patch_status = {
        "located": "ready",
        "ambiguous": "ambiguous",
        "unsupported": "unsupported",
    }[locator.status]

    if patch.status != expected_patch_status:
        raise CrewOutputError(
            "editor status is inconsistent with locator status"
        )


def _validate_rejected_locator(
    locator: LocatorResult,
) -> None:
    """Rejected locator output must not identify editable source."""

    location_values = (
        locator.file,
        locator.exact_source,
    )

    if any(value is not None for value in location_values):
        raise CrewOutputError(
            "rejected locator result must not contain source data"
        )


def _validate_rejected_patch(
    patch: ProposedPatch,
) -> None:
    """Rejected editor output must not carry replacement data."""

    replacement_values = (
        patch.file,
        patch.old_text,
        patch.new_text,
    )

    if any(value is not None for value in replacement_values):
        raise CrewOutputError(
            "rejected patch must not contain replacement data"
        )

    if patch.message is None:
        raise CrewOutputError(
            "rejected patch must include an explanatory message"
        )


def _validate_ready_patch_against_locator(
    locator: LocatorResult,
    patch: ProposedPatch,
) -> None:
    """Ensure the editor faithfully follows the locator result."""

    if locator.file is None:
        raise CrewOutputError("located result is missing its file")

    if locator.exact_source is None:
        raise CrewOutputError("located result is missing exact source")

    if patch.file != locator.file:
        raise CrewOutputError("editor patch file does not match locator file")

    if patch.old_text != locator.exact_source:
        raise CrewOutputError(
            "editor old_text does not match locator exact_source"
        )

    if patch.target != locator.target:
        raise CrewOutputError(
            "editor target does not match locator target"
        )

    if patch.selector != locator.selector:
        raise CrewOutputError(
            "editor selector does not match locator selector"
        )

    if patch.property != locator.property:
        raise CrewOutputError(
            "editor property does not match locator property"
        )


def validate_crew_output(
    crew_output: object,
) -> tuple[LocatorResult, ProposedPatch]:
    """
    Validate both task outputs and the final crew output.

    Raw-text parsing is deliberately not used as a fallback.
    """

    locator, editor_patch = _extract_task_models(crew_output)

    final_patch = getattr(
        crew_output,
        "pydantic",
        None,
    )

    if not isinstance(
        final_patch,
        ProposedPatch,
    ):
        raise CrewOutputError("final crew result did not return ProposedPatch")

    if final_patch.model_dump() != editor_patch.model_dump():
        raise CrewOutputError("final result does not match editor task output")

    _validate_status_relationship(
        locator,
        editor_patch,
    )

    if editor_patch.status == "ready":
        _validate_ready_patch_against_locator(
            locator,
            editor_patch,
        )
    else:
        _validate_rejected_locator(locator)
        _validate_rejected_patch(editor_patch)

    return locator, editor_patch


def process_turn(
    *,
    settings: Settings,
    session_state: SessionState,
    instruction: str,
    sources: Mapping[str, str],
    crew_executor: CrewExecutor = execute_crew,
    patch_applier: PatchApplier = apply_patch,
) -> TurnResult:
    """
    Execute one complete conversational editing turn.

    In automatic mode, patches are validated and committed immediately.
    In preview mode, patches are validated and returned as preview_ready.
    Memory is committed only after the deterministic patcher succeeds on disk.
    """

    inputs = build_crew_inputs(
        instruction=instruction,
        sources=sources,
        session_memory=session_state.build_context(instruction=instruction),
    )

    try:
        crew_output = crew_executor(
            settings,
            inputs,
        )

    except LLMConfigurationError:
        raise

    except CrewExecutionError:
        raise

    except Exception as exc:
        raise CrewExecutionError(classify_crew_exception(exc)) from exc

    locator, patch = validate_crew_output(crew_output)

    if patch.status == "ambiguous":
        if locator.clarification is not None:
            return TurnResult(
                status="needs_clarification",
                summary=locator.message or "Clarification needed.",
                message=locator.message,
                clarification_request=locator.clarification,
            )

        return TurnResult(
            status="ambiguous",
            summary=patch.summary,
            message=patch.message,
        )

    if patch.status == "unsupported":
        return TurnResult(
            status="unsupported",
            summary=patch.summary,
            message=patch.message,
        )

    if patch.file is None:
        raise CrewOutputError("ready patch file is missing")

    if patch.file not in sources:
        raise CrewOutputError(
            "ready patch refers to a file absent from the current source snapshot"
        )

    if patch_applier is not apply_patch:
        application = patch_applier(
            settings,
            patch,
            expected_source_text=sources[patch.file],
        )
        session_state.record_success(instruction, patch)
        return TurnResult(
            status="applied",
            summary=application.summary,
            file=application.file,
            backup_file=application.backup_file,
            diff=application.diff,
        )

    prepared = prepare_patch(
        settings=settings,
        patch=patch,
        expected_source_text=sources[patch.file],
    )

    if settings.patch_mode == "preview":
        return TurnResult(
            status="preview_ready",
            summary=prepared.summary,
            file=prepared.file,
            diff=prepared.diff,
            proposed_patch=patch,
            prepared_patch=prepared,
        )

    application = commit_prepared_patch(settings, prepared)

    session_state.record_success(
        instruction,
        patch,
    )

    return TurnResult(
        status="applied",
        summary=application.summary,
        file=application.file,
        backup_file=application.backup_file,
        diff=application.diff,
        proposed_patch=patch,
        prepared_patch=prepared,
    )
