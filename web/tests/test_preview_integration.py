"""End-to-end integration tests for preview, validation, and safe recovery.

All tests execute in memory using pytest temporary workspace directories and mocked LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from web.clarification import ClarificationManager
from web.models import LocatorResult, ProposedPatch
from web.orchestration import process_turn
from web.preview import PreviewState
from web.session import handle_colon_command
from web.settings import Settings
from web.state import SessionState
from web.tools.patcher import commit_prepared_patch


@dataclass
class FakeTaskOutput:
    pydantic: Any


@dataclass
class FakeCrewOutput:
    pydantic: Any
    tasks_output: list[FakeTaskOutput]


SAMPLE_HTML = """\
<!doctype html>
<html>
<head><title>Weft Studio</title></head>
<body>
  <h1>Weft Studio Web Editor</h1>
</body>
</html>
"""


@pytest.fixture()
def preview_workspace(tmp_path: Path) -> Settings:
    html_path = tmp_path / "index.html"
    css_path = tmp_path / "style.css"
    html_path.write_text(SAMPLE_HTML, encoding="utf-8")
    css_path.write_text("body { margin: 0; }", encoding="utf-8")

    return Settings(
        project_root=tmp_path,
        allowed_files=("index.html", "style.css"),
        patch_mode="preview",
        syntax_validation_enabled=True,
        backup_limit=3,
        session_history_limit=5,
    )


def test_status_command_output(
    preview_workspace: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = SessionState(history_limit=5)
    clarification_mgr = ClarificationManager()
    preview_state = PreviewState()

    handled = handle_colon_command(
        ":status",
        preview_workspace,
        session,
        clarification_mgr,
        preview_state,
    )
    assert handled is True

    captured = capsys.readouterr().out
    assert "System Status" in captured
    assert "Patch mode: preview" in captured
    assert "Syntax validation: enabled" in captured
    assert "SECRET" not in captured


def test_preview_and_apply_flow(preview_workspace: Settings) -> None:
    loc = LocatorResult(status="located", file="index.html", exact_source="<h1>Weft Studio Web Editor</h1>", target="h1", message="OK")
    pat = ProposedPatch(status="ready", file="index.html", old_text="<h1>Weft Studio Web Editor</h1>", new_text="<h1>New Headline</h1>", target="h1", summary="Update heading")
    out = FakeCrewOutput(pydantic=pat, tasks_output=[FakeTaskOutput(loc), FakeTaskOutput(pat)])

    session = SessionState(history_limit=5)
    sources = {"index.html": SAMPLE_HTML, "style.css": "body { margin: 0; }"}

    res = process_turn(
        settings=preview_workspace,
        session_state=session,
        instruction="Change heading",
        sources=sources,
        crew_executor=lambda s, i: out,
    )

    assert res.status == "preview_ready"
    assert res.prepared_patch is not None
    assert "New Headline" in res.prepared_patch.diff

    # Disk remains unchanged before apply
    assert "Weft Studio Web Editor" in (preview_workspace.project_root / "index.html").read_text(encoding="utf-8")
    assert not list(preview_workspace.project_root.glob("*.bak*"))

    # Apply preview patch
    commit_res = commit_prepared_patch(preview_workspace, res.prepared_patch)
    session.record_success("Change heading", pat)

    assert "New Headline" in (preview_workspace.project_root / "index.html").read_text(encoding="utf-8")
    assert (preview_workspace.project_root / "index.html.bak").exists()
    assert session.successful_turn_count == 1


def test_syntax_validation_prevents_malformed_patch_write(
    preview_workspace: Settings,
) -> None:
    loc = LocatorResult(status="located", file="index.html", exact_source="<h1>Weft Studio Web Editor</h1>", target="h1", message="OK")
    # Malformed HTML replacement
    pat = ProposedPatch(status="ready", file="index.html", old_text="<h1>Weft Studio Web Editor</h1>", new_text="<h1>Unclosed <span>Tag</h1>", target="h1", summary="Bad HTML")
    out = FakeCrewOutput(pydantic=pat, tasks_output=[FakeTaskOutput(loc), FakeTaskOutput(pat)])

    session = SessionState(history_limit=5)
    sources = {"index.html": SAMPLE_HTML, "style.css": "body { margin: 0; }"}

    with pytest.raises(Exception, match="Syntax validation failed"):
        process_turn(
            settings=preview_workspace,
            session_state=session,
            instruction="Bad HTML edit",
            sources=sources,
            crew_executor=lambda s, i: out,
        )

    assert "Weft Studio Web Editor" in (preview_workspace.project_root / "index.html").read_text(encoding="utf-8")
    assert not list(preview_workspace.project_root.glob("*.bak*"))
    assert session.successful_turn_count == 0


def test_undo_restores_previous_file_version(
    preview_workspace: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    loc = LocatorResult(status="located", file="index.html", exact_source="<h1>Weft Studio Web Editor</h1>", target="h1", message="OK")
    pat = ProposedPatch(status="ready", file="index.html", old_text="<h1>Weft Studio Web Editor</h1>", new_text="<h1>New Headline</h1>", target="h1", summary="Update heading")

    # Manually execute edit and commit to simulate turn 1
    session = SessionState(history_limit=5)
    sources = {"index.html": SAMPLE_HTML, "style.css": "body { margin: 0; }"}
    res = process_turn(settings=preview_workspace, session_state=session, instruction="Change heading", sources=sources, crew_executor=lambda s, i: FakeCrewOutput(pydantic=pat, tasks_output=[FakeTaskOutput(loc), FakeTaskOutput(pat)]))

    assert res.prepared_patch is not None
    commit_prepared_patch(preview_workspace, res.prepared_patch)
    session.record_success("Change heading", pat)

    assert "New Headline" in (preview_workspace.project_root / "index.html").read_text(encoding="utf-8")
    assert (preview_workspace.project_root / "index.html.bak").exists()

    # Perform undo via colon command
    clarification_mgr = ClarificationManager()
    preview_state = PreviewState()

    handled = handle_colon_command(
        ":undo index.html",
        preview_workspace,
        session,
        clarification_mgr,
        preview_state,
    )
    assert handled is True

    captured = capsys.readouterr().out
    assert "Undo completed" in captured

    # Original content restored
    assert "Weft Studio Web Editor" in (preview_workspace.project_root / "index.html").read_text(encoding="utf-8")
    assert session.successful_turn_count == 0
    assert session.last_target is None
