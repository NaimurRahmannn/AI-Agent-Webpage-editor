"""Unit tests for Part 4 safe undo functionality.

All tests execute in memory using pytest temporary directories.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from web.settings import Settings
from web.tools.undo import (
    UndoBackupNotFoundError,
    UndoTargetError,
    UndoValidationError,
    perform_undo,
)


@pytest.fixture()
def undo_workspace(tmp_path: Path) -> Settings:
    html_path = tmp_path / "index.html"
    css_path = tmp_path / "style.css"
    html_path.write_text("<!doctype html><html><body><h1>Modified</h1></body></html>", encoding="utf-8")
    css_path.write_text("body { color: blue; }", encoding="utf-8")

    bak_html = tmp_path / "index.html.bak"
    bak_html.write_text("<!doctype html><html><body><h1>Original</h1></body></html>", encoding="utf-8")

    bak_css = tmp_path / "style.css.bak"
    bak_css.write_text("body { color: red; }", encoding="utf-8")

    return Settings(
        project_root=tmp_path,
        allowed_files=("index.html", "style.css"),
        backup_limit=3,
        syntax_validation_enabled=True,
    )


def test_undo_restores_newest_backup(undo_workspace: Settings) -> None:
    res = perform_undo(undo_workspace, "index.html")
    assert res.file == "index.html"
    assert "Original" in (undo_workspace.project_root / "index.html").read_text(encoding="utf-8")
    assert "Modified" in (undo_workspace.project_root / "index.html.bak").read_text(encoding="utf-8")
    assert "Original" in res.diff


def test_undo_style_css(undo_workspace: Settings) -> None:
    res = perform_undo(undo_workspace, "style.css")
    assert res.file == "style.css"
    assert "red" in (undo_workspace.project_root / "style.css").read_text(encoding="utf-8")


def test_undo_missing_backup_rejected(tmp_path: Path) -> None:
    html_path = tmp_path / "index.html"
    html_path.write_text("<!doctype html><html><body>Test</body></html>", encoding="utf-8")
    settings = Settings(project_root=tmp_path, allowed_files=("index.html",))

    with pytest.raises(UndoBackupNotFoundError, match="no backup found"):
        perform_undo(settings, "index.html")


def test_undo_disallowed_file_rejected(undo_workspace: Settings) -> None:
    with pytest.raises(UndoTargetError, match="must be HTML or CSS"):
        perform_undo(undo_workspace, "script.js")

    with pytest.raises(UndoTargetError, match="not allowlisted"):
        perform_undo(undo_workspace, "other.html")


def test_undo_invalid_syntax_backup_rejected(tmp_path: Path) -> None:
    html_path = tmp_path / "index.html"
    html_path.write_text("<!doctype html><html><body>Valid</body></html>", encoding="utf-8")
    bak_path = tmp_path / "index.html.bak"
    bak_path.write_text("<div><p>Unclosed tag</span></div>", encoding="utf-8")

    settings = Settings(project_root=tmp_path, allowed_files=("index.html",), syntax_validation_enabled=True)

    with pytest.raises(UndoValidationError, match="syntax validation failed"):
        perform_undo(settings, "index.html")
