from __future__ import annotations

import sys
from collections.abc import Mapping

from web.crew import LLMConfigurationError
from web.orchestration import (
    CrewExecutionError,
    CrewOutputError,
    TurnResult,
    process_turn,
)
from web.settings import (
    Settings,
    resolve_allowed_paths,
)
from web.state import SessionState
from web.tools.patcher import (
    PatchBackupError,
    PatchError,
    PatchSourceChangedError,
    PatchValidationError,
    PatchWriteError,
)


EXIT_COMMANDS = {"exit", "quit"}


class SourceReadError(RuntimeError):
    """Raised when a configured source file cannot be read."""


def read_current_sources(
    settings: Settings,
) -> Mapping[str, str]:
    """Read every configured source file fresh from disk."""

    sources: dict[str, str] = {}

    for relative_name, path in resolve_allowed_paths(
        settings
    ).items():
        if not path.exists():
            raise SourceReadError(
                "configured source file does not exist: "
                f"{relative_name}"
            )

        if not path.is_file():
            raise SourceReadError(
                "configured source path is not a file: "
                f"{relative_name}"
            )

        try:
            sources[relative_name] = path.read_text(
                encoding="utf-8"
            )
        except UnicodeError as exc:
            raise SourceReadError(
                f"{relative_name} is not valid UTF-8"
            ) from exc
        except OSError as exc:
            raise SourceReadError(
                f"could not read {relative_name}: {exc}"
            ) from exc

    return sources


def source_snapshot_matches(
    settings: Settings,
    expected_sources: Mapping[str, str],
) -> bool | None:
    """
    Check whether current source still matches a previous snapshot.

    None means the verification itself could not be completed.
    """

    try:
        current_sources = read_current_sources(
            settings
        )
    except (SourceReadError, ValueError):
        return None

    return dict(current_sources) == dict(expected_sources)


def print_banner(settings: Settings) -> None:
    """Print safe startup information."""

    groq_status = (
        "configured"
        if settings.llm_is_configured
        else "not configured"
    )
    model_name = settings.groq_model or "not set"

    print()
    print("CrewAI Conversational HTML/CSS Editor")
    print("--------------------------------------")
    print(f"Project root: {settings.project_root}")
    print(
        "Allowed files: "
        f"{', '.join(settings.allowed_files)}"
    )
    print(f"Groq: {groq_status}")
    print(f"Model: {model_name}")
    print(
        "Session memory limit: "
        f"{settings.session_history_limit} successful turns"
    )
    print()
    print(
        "Phase 6 mode: conversational editing "
        "with resilient error handling"
    )
    print(
        "Valid patches are applied automatically "
        "without an approval prompt."
    )
    print(
        "Type an editing instruction, "
        "or type 'exit' or 'quit'."
    )
    print()


def print_rejection(
    result: TurnResult,
) -> None:
    """Print an ambiguous or unsupported outcome."""

    heading = (
        "Ambiguous request"
        if result.status == "ambiguous"
        else "Unsupported request"
    )

    explanation = result.message or result.summary

    print(f"{heading}: {explanation}")
    print(f"Summary: {result.summary}")
    print("No source files were changed.")
    print()


def print_application(
    result: TurnResult,
) -> None:
    """Print a successful patch result."""

    print()
    print(f"Applied: {result.summary}")
    print(f"File: {result.file}")
    print(f"Backup: {result.backup_file}")
    print()
    print("Diff:")

    if result.diff:
        print(result.diff)
    else:
        print("(No diff text was generated.)")

    print()


def print_turn_failure(
    message: str,
    *,
    source_unchanged: bool | None = True,
) -> None:
    """Print a safe failure with an accurate source-status statement."""

    print(message, file=sys.stderr)

    if source_unchanged is True:
        print("No source files were changed.")
    elif source_unchanged is False:
        print(
            "The source differs from the snapshot read at "
            "the start of this turn. Inspect it before retrying."
        )
    else:
        print(
            "Source status could not be verified. "
            "Inspect the configured files before retrying."
        )

    print()


def run_session(settings: Settings) -> None:
    """Run the long-lived conversational editing session."""

    session_state = SessionState(
        history_limit=settings.session_history_limit
    )

    print_banner(settings)

    while True:
        try:
            instruction = input(
                "web-editor> "
            ).strip()
        except EOFError:
            print("\nSession closed.")
            return
        except KeyboardInterrupt:
            print()
            print(
                "Instruction cancelled. "
                "Type 'exit' to close the session."
            )
            continue

        if not instruction:
            continue

        if instruction.casefold() in EXIT_COMMANDS:
            print("Session closed.")
            return

        try:
            sources = read_current_sources(settings)
        except (SourceReadError, ValueError) as exc:
            print_turn_failure(
                f"Source read failed: {exc}"
            )
            continue

        total_characters = sum(
            len(content)
            for content in sources.values()
        )

        print(
            f"Reread {len(sources)} configured file(s) "
            f"from disk ({total_characters} characters)."
        )
        print("Running locator and editor agents...")

        try:
            result = process_turn(
                settings=settings,
                session_state=session_state,
                instruction=instruction,
                sources=sources,
            )

        except KeyboardInterrupt:
            print()

            snapshot_status = source_snapshot_matches(
                settings,
                sources,
            )

            print_turn_failure(
                "The turn was interrupted.",
                source_unchanged=snapshot_status,
            )
            continue

        except LLMConfigurationError as exc:
            print_turn_failure(
                f"Configuration error: {exc}"
            )
            continue

        except CrewExecutionError as exc:
            print_turn_failure(str(exc))
            continue

        except CrewOutputError as exc:
            print_turn_failure(
                f"Crew output rejected: {exc}"
            )
            continue

        except PatchSourceChangedError as exc:
            print_turn_failure(
                f"Source changed: {exc}"
            )
            continue

        except PatchValidationError as exc:
            print_turn_failure(
                f"Patch validation failed: {exc}"
            )
            continue

        except PatchBackupError as exc:
            print_turn_failure(
                f"Backup failed: {exc}"
            )
            continue

        except PatchWriteError as exc:
            print_turn_failure(
                f"Source write failed: {exc}"
            )
            continue

        except PatchError as exc:
            print_turn_failure(
                f"Patch failed: {exc}"
            )
            continue

        except ValueError as exc:
            print_turn_failure(
                f"Turn validation failed: {exc}"
            )
            continue

        except Exception as exc:
            snapshot_status = source_snapshot_matches(
                settings,
                sources,
            )

            print_turn_failure(
                "Unexpected turn failure "
                f"({type(exc).__name__}).",
                source_unchanged=snapshot_status,
            )
            continue

        if result.status == "applied":
            print_application(result)
        else:
            print_rejection(result)