from __future__ import annotations

import web.session as session_module
from web.orchestration import (
    CrewExecutionError,
    TurnResult,
)
from web.models import ClarificationOption, ClarificationRequest
from web.settings import Settings


def test_session_continues_after_failed_turn(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        allowed_files=(
            "index.html",
            "style.css",
        ),
        groq_api_key="test-key",
        groq_model="groq/test-model",
    )

    instructions = iter(
        [
            "first instruction",
            "second instruction",
            "quit",
        ]
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: next(instructions),
    )

    monkeypatch.setattr(
        session_module,
        "read_current_sources",
        lambda settings: {
            "index.html": "<main></main>",
            "style.css": "body {}",
        },
    )

    processed: list[str] = []

    def fake_process_turn(
        *,
        settings,
        session_state,
        instruction,
        sources,
    ):
        processed.append(instruction)

        if instruction == "first instruction":
            raise CrewExecutionError(
                "Crew execution failed (RuntimeError)."
            )

        return TurnResult(
            status="ambiguous",
            summary="The second request is ambiguous.",
            message="More than one target could match.",
        )

    monkeypatch.setattr(
        session_module,
        "process_turn",
        fake_process_turn,
    )

    session_module.run_session(settings)

    captured = capsys.readouterr()

    assert processed == [
        "first instruction",
        "second instruction",
    ]

    assert (
        "Crew execution failed (RuntimeError)."
        in captured.err
    )

    assert "Ambiguous request" in captured.out
    assert "No source files were changed." in captured.out
    assert "Session closed." in captured.out


def test_session_prints_successful_application(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        groq_api_key="test-key",
        groq_model="groq/test-model",
    )

    instructions = iter(
        [
            "make it blue",
            "quit",
        ]
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: next(instructions),
    )

    monkeypatch.setattr(
        session_module,
        "read_current_sources",
        lambda settings: {
            "index.html": "<main></main>",
            "style.css": "color: red;",
        },
    )

    monkeypatch.setattr(
        session_module,
        "process_turn",
        lambda **kwargs: TurnResult(
            status="applied",
            summary="Change the color to blue.",
            file="style.css",
            backup_file="style.css.bak",
            diff=(
                "--- a/style.css\n"
                "+++ b/style.css\n"
                "@@ -1 +1 @@\n"
                "-color: red;\n"
                "+color: blue;"
            ),
        ),
    )

    session_module.run_session(settings)

    captured = capsys.readouterr()

    assert "Applied: Change the color to blue." in captured.out
    assert "File: style.css" in captured.out
    assert "Backup: style.css.bak" in captured.out
    assert "-color: red;" in captured.out
    assert "+color: blue;" in captured.out


def test_session_preserves_original_instruction_after_clarification(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        groq_api_key="test-key",
        groq_model="groq/test-model",
    )

    instructions = iter(
        [
            "Change the link text",
            "2",
            "quit",
        ]
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: next(instructions),
    )

    monkeypatch.setattr(
        session_module,
        "read_current_sources",
        lambda settings: {
            "index.html": "<a>Brand</a><a>CTA</a>",
            "style.css": "body {}",
        },
    )

    clarification = ClarificationRequest(
        question="Which link should I change?",
        options=(
            ClarificationOption(
                id="1",
                label="Brand link",
                file="index.html",
                target="Brand link",
            ),
            ClarificationOption(
                id="2",
                label="CTA link",
                file="index.html",
                target="CTA link",
            ),
        ),
        message="Multiple links found.",
    )

    processed: list[str] = []

    def fake_process_turn(
        *,
        settings,
        session_state,
        instruction,
        sources,
    ):
        processed.append(instruction)

        if len(processed) == 1:
            return TurnResult(
                status="needs_clarification",
                summary="Multiple links found.",
                message="Multiple links found.",
                clarification_request=clarification,
            )

        return TurnResult(
            status="applied",
            summary="Changed CTA link text.",
            file="index.html",
            backup_file="index.html.bak",
            diff="--- a/index.html\n+++ b/index.html",
        )

    monkeypatch.setattr(
        session_module,
        "process_turn",
        fake_process_turn,
    )

    session_module.run_session(settings)

    captured = capsys.readouterr()

    assert "Original request: Change the link text" in captured.out
    assert "Selected target: CTA link" in captured.out
    assert len(processed) == 2
    assert "ORIGINAL USER INSTRUCTION" in processed[1]
    assert "Change the link text" in processed[1]


def test_source_snapshot_match_detection(
    tmp_path,
) -> None:
    index = tmp_path / "index.html"
    style = tmp_path / "style.css"

    index.write_text(
        "<main></main>",
        encoding="utf-8",
    )
    style.write_text(
        "body {}",
        encoding="utf-8",
    )

    settings = Settings(
        project_root=tmp_path,
        allowed_files=(
            "index.html",
            "style.css",
        ),
    )

    expected = {
        "index.html": "<main></main>",
        "style.css": "body {}",
    }

    assert session_module.source_snapshot_matches(
        settings,
        expected,
    ) is True

    style.write_text(
        "body { color: red; }",
        encoding="utf-8",
    )

    assert session_module.source_snapshot_matches(
        settings,
        expected,
    ) is False


def test_session_recovers_from_unexpected_exception(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        groq_api_key="test-key",
        groq_model="groq/test-model",
    )

    instructions = iter(
        [
            "first instruction",
            "second instruction",
            "quit",
        ]
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: next(instructions),
    )

    sources = {
        "index.html": "<main></main>",
        "style.css": "body {}",
    }

    monkeypatch.setattr(
        session_module,
        "read_current_sources",
        lambda settings: sources,
    )

    processed: list[str] = []

    def fake_process_turn(
        *,
        settings,
        session_state,
        instruction,
        sources,
    ):
        processed.append(instruction)

        if instruction == "first instruction":
            raise RuntimeError(
                "sensitive internal diagnostic"
            )

        return TurnResult(
            status="unsupported",
            summary="The request is unsupported.",
            message="JavaScript editing is unsupported.",
        )

    monkeypatch.setattr(
        session_module,
        "process_turn",
        fake_process_turn,
    )

    session_module.run_session(settings)

    captured = capsys.readouterr()

    assert processed == [
        "first instruction",
        "second instruction",
    ]

    assert (
        "Unexpected turn failure (RuntimeError)."
        in captured.err
    )
    assert (
        "sensitive internal diagnostic"
        not in captured.err
    )

    assert "Unsupported request" in captured.out
    assert "Session closed." in captured.out
