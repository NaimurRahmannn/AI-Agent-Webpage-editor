from __future__ import annotations

import web.session as session_module
from web.orchestration import (
    CrewExecutionError,
    TurnResult,
)
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
    assert "No files were changed." in captured.out
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