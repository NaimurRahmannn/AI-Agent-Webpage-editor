from __future__ import annotations

import sys
from collections.abc import Mapping

from web.settings import Settings, resolve_allowed_paths


EXIT_COMMANDS = {"exit", "quit"}


class SourceReadError(RuntimeError):
    """Raised when a configured source file cannot be read."""


def read_current_sources(settings: Settings) -> Mapping[str, str]:
    """
    Read every allowlisted source file directly from disk.

    This function is called for every non-empty user instruction.
    Source contents are never cached between turns.
    """

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


def print_banner(settings: Settings) -> None:
    """Print safe startup information without exposing secrets."""

    groq_status = "configured" if settings.llm_is_configured else "not configured"
    model_name = settings.groq_model or "not set"

    print()
    print("CrewAI Conversational HTML/CSS Editor")
    print("--------------------------------------")
    print(f"Project root: {settings.project_root}")
    print(f"Allowed files: {', '.join(settings.allowed_files)}")
    print(f"Groq: {groq_status}")
    print(f"Model: {model_name}")
    print()
    print("Phase 1 mode: read-only session shell")
    print("Type an editing instruction, or type 'exit' or 'quit'.")
    print()


def run_session(settings: Settings) -> None:
    """
    Run the long-lived terminal session.

    Phase 1 only reads the current files. It does not invoke CrewAI,
    construct a patch, create a backup, or modify source files.
    """

    print_banner(settings)

    while True:
        try:
            instruction = input("web-editor> ").strip()
        except EOFError:
            print("\nSession closed.")
            return
        except KeyboardInterrupt:
            print()
            print("Instruction cancelled. Type 'exit' to close the session.")
            continue

        if not instruction:
            continue

        if instruction.casefold() in EXIT_COMMANDS:
            print("Session closed.")
            return

        try:
            sources = read_current_sources(settings)
        except (SourceReadError, ValueError) as exc:
            print(f"Source read failed: {exc}", file=sys.stderr)
            print("No files were changed.")
            print()
            continue

        total_characters = sum(len(content) for content in sources.values())

        print(
            f"Reread {len(sources)} configured file(s) "
            f"from disk ({total_characters} characters)."
        )
        print(f"Instruction received: {instruction}")
        print("Agent interpretation will be added in Phase 4.")
        print("No files were changed.")
        print()