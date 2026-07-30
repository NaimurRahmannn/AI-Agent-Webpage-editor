"""Unit and integration tests for Part 3 preview mode functionality.

All tests execute in memory using pytest temporary directories and mocked crew output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import pytest

from web.models import LocatorResult, ProposedPatch
from web.orchestration import process_turn
from web.preview import (
    NoPendingPreviewError,
    PendingPreview,
    PendingPreviewExistsError,
    PreviewState,
    StalePreviewError,
)
from web.settings import Settings
from web.state import SessionState
from web.tools.patcher import commit_prepared_patch, prepare_patch


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
<head><title>Test Page</title></head>
<body>
  <h1>Old Title</h1>
</body>
</html>
"""


@pytest.fixture()
def preview_settings(tmp_path: Path) -> Settings:
    html_path = tmp_path / "index.html"
    css_path = tmp_path / "style.css"
    html_path.write_text(SAMPLE_HTML, encoding="utf-8")
    css_path.write_text("body { color: black; }", encoding="utf-8")

    return Settings(
        project_root=tmp_path,
        allowed_files=("index.html", "style.css"),
        patch_mode="preview",
        syntax_validation_enabled=True,
    )


def test_automatic_mode_preserves_current_behavior(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(SAMPLE_HTML, encoding="utf-8")
    (tmp_path / "style.css").write_text("body { color: black; }", encoding="utf-8")
    settings = Settings(project_root=tmp_path, patch_mode="automatic")

    loc = LocatorResult(status="located", file="index.html", exact_source="<h1>Old Title</h1>", target="h1", message="OK")
    pat = ProposedPatch(status="ready", file="index.html", old_text="<h1>Old Title</h1>", new_text="<h1>New Title</h1>", target="h1", summary="Edit h1")
    out = FakeCrewOutput(pydantic=pat, tasks_output=[FakeTaskOutput(loc), FakeTaskOutput(pat)])

    session = SessionState(history_limit=5)
    sources = {"index.html": SAMPLE_HTML, "style.css": "body { color: black; }"}

    res = process_turn(
        settings=settings,
        session_state=session,
        instruction="Change title",
        sources=sources,
        crew_executor=lambda s, i: out,
    )

    assert res.status == "applied"
    assert "New Title" in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert session.successful_turn_count == 1


def test_preview_mode_creates_preview_ready_result(preview_settings: Settings) -> None:
    loc = LocatorResult(status="located", file="index.html", exact_source="<h1>Old Title</h1>", target="h1", message="OK")
    pat = ProposedPatch(status="ready", file="index.html", old_text="<h1>Old Title</h1>", new_text="<h1>New Title</h1>", target="h1", summary="Edit h1")
    out = FakeCrewOutput(pydantic=pat, tasks_output=[FakeTaskOutput(loc), FakeTaskOutput(pat)])

    session = SessionState(history_limit=5)
    sources = {"index.html": SAMPLE_HTML, "style.css": "body { color: black; }"}

    res = process_turn(
        settings=preview_settings,
        session_state=session,
        instruction="Change title",
        sources=sources,
        crew_executor=lambda s, i: out,
    )

    assert res.status == "preview_ready"
    assert res.prepared_patch is not None
    assert res.prepared_patch.file == "index.html"
    assert "New Title" in res.prepared_patch.diff

    # No files modified, no backups created, no memory recorded
    assert "Old Title" in (preview_settings.project_root / "index.html").read_text(encoding="utf-8")
    assert not list(preview_settings.project_root.glob("*.bak*"))
    assert session.successful_turn_count == 0


def test_preview_state_manager(preview_settings: Settings) -> None:
    state = PreviewState()
    assert state.has_pending() is False

    pat = ProposedPatch(status="ready", file="index.html", old_text="<h1>Old Title</h1>", new_text="<h1>New Title</h1>", target="h1", summary="Edit h1")
    prepared = prepare_patch(preview_settings, pat)

    pending = PendingPreview(
        instruction="Edit title",
        patch=pat,
        prepared_patch=prepared,
        created_for_file="index.html",
    )

    state.set_pending(pending)
    assert state.has_pending() is True
    assert state.get_pending() == pending

    with pytest.raises(PendingPreviewExistsError):
        state.set_pending(pending)

    state.clear()
    assert state.has_pending() is False


def test_apply_commits_preview_and_updates_memory(preview_settings: Settings) -> None:
    loc = LocatorResult(status="located", file="index.html", exact_source="<h1>Old Title</h1>", target="h1", message="OK")
    pat = ProposedPatch(status="ready", file="index.html", old_text="<h1>Old Title</h1>", new_text="<h1>New Title</h1>", target="h1", summary="Edit h1")
    out = FakeCrewOutput(pydantic=pat, tasks_output=[FakeTaskOutput(loc), FakeTaskOutput(pat)])

    session = SessionState(history_limit=5)
    sources = {"index.html": SAMPLE_HTML, "style.css": "body { color: black; }"}

    res = process_turn(
        settings=preview_settings,
        session_state=session,
        instruction="Change title",
        sources=sources,
        crew_executor=lambda s, i: out,
    )

    assert res.prepared_patch is not None

    app_res = commit_prepared_patch(preview_settings, res.prepared_patch)
    session.record_success("Change title", pat)

    assert app_res.file == "index.html"
    assert "New Title" in (preview_settings.project_root / "index.html").read_text(encoding="utf-8")
    assert (preview_settings.project_root / "index.html.bak").exists()
    assert session.successful_turn_count == 1


def test_stale_preview_rejection(preview_settings: Settings) -> None:
    loc = LocatorResult(status="located", file="index.html", exact_source="<h1>Old Title</h1>", target="h1", message="OK")
    pat = ProposedPatch(status="ready", file="index.html", old_text="<h1>Old Title</h1>", new_text="<h1>New Title</h1>", target="h1", summary="Edit h1")
    out = FakeCrewOutput(pydantic=pat, tasks_output=[FakeTaskOutput(loc), FakeTaskOutput(pat)])

    session = SessionState(history_limit=5)
    sources = {"index.html": SAMPLE_HTML, "style.css": "body { color: black; }"}

    res = process_turn(
        settings=preview_settings,
        session_state=session,
        instruction="Change title",
        sources=sources,
        crew_executor=lambda s, i: out,
    )

    # Modify file externally before commit
    (preview_settings.project_root / "index.html").write_text("<!doctype html><html><body><h1>Externally Changed</h1></body></html>", encoding="utf-8")

    with pytest.raises(Exception, match="Source file changed"):
        commit_prepared_patch(preview_settings, res.prepared_patch)
