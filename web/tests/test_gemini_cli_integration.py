"""Integration tests for Phase 8 Gemini CLI read-only patch reviewer integration.

All tests mock LLM and subprocess execution. No network calls or live CLI
invocations are performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from web.crew import WebEditingCrew
from web.models import LocatorResult, ProposedPatch
from web.orchestration import process_turn
from web.settings import Settings
from web.state import SessionState
from web.tools.gemini_cli_tool import GeminiCliReviewTool, GeminiReviewResult


@dataclass
class FakeTaskOutput:
    pydantic: Any


@dataclass
class FakeCrewOutput:
    pydantic: Any
    tasks_output: list[FakeTaskOutput]


SAMPLE_CSS = """\
.cta {
  display: inline-block;
  padding: 14px 20px;
  color: #ffffff;
  background: #ff0000;
}
"""


@pytest.fixture()
def gemini_settings(tmp_path: Path) -> Settings:
    """Create Settings with Gemini CLI enabled and workspace initialized."""
    css_path = tmp_path / "style.css"
    html_path = tmp_path / "index.html"
    css_path.write_text(SAMPLE_CSS, encoding="utf-8")
    html_path.write_text("<!doctype html><html></html>", encoding="utf-8")

    return Settings(
        project_root=tmp_path,
        allowed_files=("index.html", "style.css"),
        backup_limit=3,
        session_history_limit=5,
        groq_api_key="test-groq-key",
        groq_model="groq/test-model",
        gemini_api_key="test-gemini-key",
        gemini_cli_enabled=True,
        gemini_cli_model="flash",
        gemini_cli_timeout_seconds=30,
        gemini_cli_max_output_chars=10000,
    )


def test_crew_agent_tool_attachment(gemini_settings: Settings) -> None:
    """Verify tool attachment rules on WebEditingCrew agents."""
    crew_def = WebEditingCrew(gemini_settings)

    # Locator agent MUST have no tools
    locator = crew_def.locator_agent()
    assert locator.tools == []

    # Editor agent MUST have GeminiCliReviewTool when enabled
    editor = crew_def.editor_agent()
    assert len(editor.tools) == 1
    assert isinstance(editor.tools[0], GeminiCliReviewTool)


def test_crew_agent_tool_attachment_disabled(tmp_path: Path) -> None:
    """Verify tool attachment when Gemini CLI is disabled."""
    settings = Settings(
        project_root=tmp_path,
        groq_api_key="test-groq-key",
        groq_model="groq/test-model",
        gemini_cli_enabled=False,
    )
    crew_def = WebEditingCrew(settings)

    assert crew_def.locator_agent().tools == []
    assert crew_def.editor_agent().tools == []


def test_crew_sequential_process_and_context(gemini_settings: Settings) -> None:
    """Verify Process.sequential and editor task context."""
    crew_def = WebEditingCrew(gemini_settings)
    editing_crew = crew_def.crew()

    assert editing_crew.process == "sequential"

    loc_task = crew_def.locator_task()
    ed_task = crew_def.editor_task()

    assert ed_task.context == [loc_task]


@patch("web.tools.gemini_cli_tool.GeminiCliReviewTool._run")
def test_approved_patch_integration_flow(
    mock_tool_run: Any, gemini_settings: Settings
) -> None:
    """Demonstrate full turn: locator identifies CSS, editor uses approved review, real patcher applies."""
    mock_tool_run.return_value = GeminiReviewResult(
        verdict="approved",
        message="Patch is minimal and valid.",
    ).model_dump_json()

    locator = LocatorResult(
        status="located",
        file="style.css",
        target="CTA background color",
        selector=".cta",
        property="background",
        exact_source="background: #ff0000;",
        message="Located background declaration.",
    )

    patch = ProposedPatch(
        status="ready",
        file="style.css",
        old_text="background: #ff0000;",
        new_text="background: #2d6a4f;",
        target="CTA background color",
        selector=".cta",
        property="background",
        summary="Change CTA background to accent green.",
    )

    fake_output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[FakeTaskOutput(locator), FakeTaskOutput(patch)],
    )

    session = SessionState(history_limit=5)
    sources = {
        "index.html": "<!doctype html><html></html>",
        "style.css": SAMPLE_CSS,
    }

    result = process_turn(
        settings=gemini_settings,
        session_state=session,
        instruction="Change the CTA background to green.",
        sources=sources,
        crew_executor=lambda s, i: fake_output,
    )

    assert result.status == "applied"
    assert result.file == "style.css"
    assert result.backup_file == "style.css.bak"
    assert "-  background: #ff0000;" in result.diff
    assert "+  background: #2d6a4f;" in result.diff

    # Verify real file on disk was modified by Python patcher
    updated_css = (gemini_settings.project_root / "style.css").read_text(encoding="utf-8")
    assert "background: #2d6a4f;" in updated_css
    assert "background: #ff0000;" not in updated_css

    # Memory committed
    assert session.successful_turn_count == 1
    assert session.last_target is not None
    assert session.last_target.target == "CTA background color"


@patch("web.tools.gemini_cli_tool.GeminiCliReviewTool._run")
def test_unsafe_patch_rejection_integration_flow(
    mock_tool_run: Any, gemini_settings: Settings
) -> None:
    """Demonstrate full turn rejection: reviewer flags unsafe JavaScript request."""
    mock_tool_run.return_value = GeminiReviewResult(
        verdict="unsafe",
        message="Request introduces forbidden JavaScript event handler.",
    ).model_dump_json()

    locator = LocatorResult(
        status="unsupported",
        message="JavaScript changes are unsupported.",
    )

    patch = ProposedPatch(
        status="unsupported",
        summary="Rejected unsafe JavaScript request.",
        message="JavaScript changes are unsupported.",
    )

    fake_output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[FakeTaskOutput(locator), FakeTaskOutput(patch)],
    )

    session = SessionState(history_limit=5)
    sources = {
        "index.html": "<!doctype html><html></html>",
        "style.css": SAMPLE_CSS,
    }

    result = process_turn(
        settings=gemini_settings,
        session_state=session,
        instruction="Add an onclick listener to the CTA button.",
        sources=sources,
        crew_executor=lambda s, i: fake_output,
    )

    assert result.status == "unsupported"
    assert session.successful_turn_count == 0

    # Verify source file is untouched
    css_content = (gemini_settings.project_root / "style.css").read_text(encoding="utf-8")
    assert css_content == SAMPLE_CSS

    # Verify no backup files created
    assert not list(gemini_settings.project_root.glob("*.bak*"))
