from __future__ import annotations

import sys
from collections.abc import Mapping

from web.clarification import (
    ClarificationAttemptsExceeded,
    ClarificationError,
    ClarificationManager,
    ClarificationResolutionError,
)
from web.crew import LLMConfigurationError
from web.models import ClarificationRequest
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
CANCEL_COMMANDS = {"cancel"}


class SourceReadError(RuntimeError):
    """Raised when a configured source file cannot be read."""


def read_current_sources(
    settings: Settings,
) -> Mapping[str, str]:
    """Read every configured source file fresh from disk."""

    sources: dict[str, str] = {}

    for relative_name, path in resolve_allowed_paths(settings).items():
        if not path.exists():
            raise SourceReadError(
                f"configured source file does not exist: {relative_name}"
            )

        if not path.is_file():
            raise SourceReadError(
                f"configured source path is not a file: {relative_name}"
            )

        try:
            sources[relative_name] = path.read_text(encoding="utf-8")
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
        current_sources = read_current_sources(settings)
    except (SourceReadError, ValueError):
        return None

    return dict(current_sources) == dict(expected_sources)


def print_banner(settings: Settings) -> None:
    """Print safe startup information."""

    groq_status = (
        "configured" if settings.llm_is_configured else "not configured"
    )
    model_name = settings.groq_model or "not set"

    print()
    print("CrewAI Conversational HTML/CSS Editor")
    print("--------------------------------------")
    print(f"Project root: {settings.project_root}")
    print(f"Allowed files: {', '.join(settings.allowed_files)}")
    print(f"Groq: {groq_status}")
    print(f"Model: {model_name}")
    print(
        f"Session memory limit: {settings.session_history_limit} successful turns"
    )
    print()
    print("Mode: conversational editing with resilient error handling")
    print(
        "Valid patches are applied automatically without an approval prompt."
    )
    print("Type an editing instruction, or type 'exit' or 'quit'.")
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


def print_clarification_request(
    request: ClarificationRequest,
) -> None:
    """Print a structured clarification question and candidate options."""

    print()
    print(request.question)
    for opt in request.options:
        print(f"{opt.id}. {opt.label}")
    print()
    print("Enter an option number or target label.")
    print("Type 'cancel' to cancel this clarification.")
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


def execute_turn_with_sources(
    settings: Settings,
    session_state: SessionState,
    instruction: str,
) -> TurnResult | None:
    """Reread sources fresh from disk and execute process_turn safely."""

    try:
        sources = read_current_sources(settings)
    except (SourceReadError, ValueError) as exc:
        print_turn_failure(f"Source read failed: {exc}")
        return None

    total_characters = sum(len(content) for content in sources.values())

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
        return result

    except KeyboardInterrupt:
        print()
        snapshot_status = source_snapshot_matches(settings, sources)
        print_turn_failure(
            "The turn was interrupted.",
            source_unchanged=snapshot_status,
        )
        return None

    except LLMConfigurationError as exc:
        print_turn_failure(f"Configuration error: {exc}")
        return None

    except CrewExecutionError as exc:
        print_turn_failure(str(exc))
        return None

    except CrewOutputError as exc:
        print_turn_failure(f"Crew output rejected: {exc}")
        return None

    except PatchSourceChangedError as exc:
        print_turn_failure(f"Source changed: {exc}")
        return None

    except PatchValidationError as exc:
        print_turn_failure(f"Patch validation failed: {exc}")
        return None

    except PatchBackupError as exc:
        print_turn_failure(f"Backup failed: {exc}")
        return None

    except PatchWriteError as exc:
        print_turn_failure(f"Source write failed: {exc}")
        return None

    except PatchError as exc:
        print_turn_failure(f"Patch failed: {exc}")
        return None

    except ValueError as exc:
        print_turn_failure(f"Turn validation failed: {exc}")
        return None

    except Exception as exc:
        snapshot_status = source_snapshot_matches(settings, sources)
        print_turn_failure(
            f"Unexpected turn failure ({type(exc).__name__}).",
            source_unchanged=snapshot_status,
        )
        return None


def run_session(settings: Settings) -> None:
    """Run the long-lived conversational editing session with clarification support."""

    session_state = SessionState(history_limit=settings.session_history_limit)
    clarification_mgr = ClarificationManager()

    print_banner(settings)

    while True:
        prompt = (
            "clarify> " if clarification_mgr.has_pending() else "web-editor> "
        )

        try:
            user_input = input(prompt).strip()
        except EOFError:
            print("\nSession closed.")
            return
        except KeyboardInterrupt:
            print()
            if clarification_mgr.has_pending():
                clarification_mgr.cancel()
                print("Clarification cancelled.")
            else:
                print(
                    "Instruction cancelled. Type 'exit' to close the session."
                )
            continue

        if not user_input:
            continue

        if user_input.casefold() in EXIT_COMMANDS:
            print("Session closed.")
            return

        if clarification_mgr.has_pending():
            if user_input.casefold() in CANCEL_COMMANDS:
                clarification_mgr.cancel()
                print("Clarification cancelled.")
                print()
                continue

            try:
                selected_option, clarified_instruction = (
                    clarification_mgr.resolve_answer(user_input)
                )
            except ClarificationResolutionError as exc:
                print(f"Invalid answer: {exc}")
                print("Enter an option number or target label.")
                print("Type 'cancel' to cancel this clarification.")
                print()
                continue
            except ClarificationAttemptsExceeded as exc:
                print(str(exc))
                print()
                continue
            except ClarificationError as exc:
                print(f"Clarification error: {exc}")
                print()
                continue

            pending_rec = clarification_mgr.get_pending()
            orig_inst = (
                pending_rec.original_instruction if pending_rec else ""
            )

            print()
            print("Resolved clarification:")
            if orig_inst:
                print(f"Original request: {orig_inst}")
            print(f"Selected target: {selected_option.label}")
            print()
            print("Rereading current files and running the editing crew...")

            result = execute_turn_with_sources(
                settings, session_state, clarified_instruction
            )

            if result is None:
                continue

            if result.status == "applied":
                print_application(result)
            elif result.status == "needs_clarification":
                if result.clarification_request:
                    clarification_mgr.set_pending(
                        result.clarification_request, orig_inst
                    )
                    print_clarification_request(result.clarification_request)
                else:
                    print_rejection(result)
            else:
                print_rejection(result)

        else:
            result = execute_turn_with_sources(
                settings, session_state, user_input
            )

            if result is None:
                continue

            if result.status == "applied":
                print_application(result)
            elif result.status == "needs_clarification":
                if result.clarification_request:
                    clarification_mgr.set_pending(
                        result.clarification_request, user_input
                    )
                    print_clarification_request(result.clarification_request)
                else:
                    print_rejection(result)
            else:
                print_rejection(result)