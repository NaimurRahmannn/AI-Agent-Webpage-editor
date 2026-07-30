"""Integration tests for Phase 9 conversational clarification workflow.

All tests mock LLM outputs and subprocess execution. No network, live Groq,
live Gemini, npm, or browser execution is performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from web.clarification import (
    ClarificationAttemptsExceeded,
    ClarificationManager,
    ClarificationResolutionError,
)
from web.models import (
    ClarificationOption,
    ClarificationRequest,
    LocatorResult,
    ProposedPatch,
)
from web.orchestration import process_turn
from web.settings import Settings
from web.state import SessionState


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
  <a class="brand" href="#">Weft Studio</a>
  <a class="cta" href="#contact">Start a project</a>
</body>
</html>
"""


@pytest.fixture()
def workspace_settings(tmp_path: Path) -> Settings:
    """Create Settings pointing to a temporary workspace with sample files."""
    html_path = tmp_path / "index.html"
    css_path = tmp_path / "style.css"
    html_path.write_text(SAMPLE_HTML, encoding="utf-8")
    css_path.write_text("body { margin: 0; }", encoding="utf-8")

    return Settings(
        project_root=tmp_path,
        allowed_files=("index.html", "style.css"),
        backup_limit=3,
        session_history_limit=5,
        groq_api_key="test-groq-key",
        groq_model="groq/test-model",
        gemini_api_key="test-gemini-key",
        gemini_cli_enabled=True,
    )


def test_ambiguous_request_creates_needs_clarification_turn_result(
    workspace_settings: Settings,
) -> None:
    """An ambiguous turn with clarification options returns needs_clarification."""
    req = ClarificationRequest(
        question="Which link should I change?",
        options=(
            ClarificationOption(id="1", label="Brand link: Weft Studio", file="index.html", target="brand link"),
            ClarificationOption(id="2", label="CTA link: Start a project", file="index.html", target="CTA link"),
        ),
        message="Multiple links found.",
    )
    locator = LocatorResult(
        status="ambiguous",
        message="Multiple links found.",
        clarification=req,
    )
    patch = ProposedPatch(
        status="ambiguous",
        summary="Multiple links found.",
        message="Multiple links found.",
    )
    fake_output = FakeCrewOutput(
        pydantic=patch,
        tasks_output=[FakeTaskOutput(locator), FakeTaskOutput(patch)],
    )

    session = SessionState(history_limit=5)
    sources = {"index.html": SAMPLE_HTML, "style.css": "body { margin: 0; }"}

    result = process_turn(
        settings=workspace_settings,
        session_state=session,
        instruction="Change the link text.",
        sources=sources,
        crew_executor=lambda s, i: fake_output,
    )

    assert result.status == "needs_clarification"
    assert result.clarification_request is not None
    assert result.clarification_request.question == "Which link should I change?"

    # Initial ambiguous turn safety checks
    assert not list(workspace_settings.project_root.glob("*.bak*"))
    assert (workspace_settings.project_root / "index.html").read_text(encoding="utf-8") == SAMPLE_HTML
    assert session.successful_turn_count == 0


@patch("web.tools.gemini_cli_tool.GeminiCliReviewTool._run")
def test_clarification_flow_resolution_and_application(
    mock_gemini_run: MagicMock,
    workspace_settings: Settings,
) -> None:
    """Demonstrate full clarification lifecycle: ambiguous turn -> user resolution -> rerun -> patch application."""
    mock_gemini_run.return_value = '{"verdict": "approved", "message": "OK"}'

    # Turn 1: Ambiguous
    req = ClarificationRequest(
        question="Which link should I change?",
        options=(
            ClarificationOption(id="1", label="Brand link: Weft Studio", file="index.html", target="brand link"),
            ClarificationOption(id="2", label="CTA link: Start a project", file="index.html", target="CTA link", selector=".cta"),
        ),
        message="Multiple links found.",
    )
    loc1 = LocatorResult(status="ambiguous", message="Multiple links.", clarification=req)
    pat1 = ProposedPatch(status="ambiguous", summary="Multiple links.", message="Multiple links.")
    out1 = FakeCrewOutput(pydantic=pat1, tasks_output=[FakeTaskOutput(loc1), FakeTaskOutput(pat1)])

    session = SessionState(history_limit=5)
    sources = {"index.html": SAMPLE_HTML, "style.css": "body { margin: 0; }"}

    r1 = process_turn(
        settings=workspace_settings,
        session_state=session,
        instruction="Change the link text.",
        sources=sources,
        crew_executor=lambda s, i: out1,
    )
    assert r1.status == "needs_clarification"

    # Store pending clarification
    mgr = ClarificationManager()
    mgr.set_pending(r1.clarification_request, "Change the link text.")

    # User answers "2" (CTA link)
    selected_option, clarified_instruction = mgr.resolve_answer("2")
    assert selected_option.id == "2"
    assert "CLARIFICATION SELECTION" in clarified_instruction
    assert mgr.has_pending() is False

    # Simulate source change before Turn 2
    updated_html = SAMPLE_HTML.replace("Start a project", "Get started now")
    (workspace_settings.project_root / "index.html").write_text(updated_html, encoding="utf-8")
    fresh_sources = {"index.html": updated_html, "style.css": "body { margin: 0; }"}

    # Turn 2: Clarified execution with fresh reread sources
    loc2 = LocatorResult(
        status="located",
        file="index.html",
        target="CTA link",
        selector=".cta",
        exact_source='<a class="cta" href="#contact">Get started now</a>',
        message="Located CTA link.",
    )
    pat2 = ProposedPatch(
        status="ready",
        file="index.html",
        old_text='<a class="cta" href="#contact">Get started now</a>',
        new_text='<a class="cta" href="#contact">Contact Us</a>',
        target="CTA link text",
        selector=".cta",
        summary="Change CTA link text to Contact Us.",
    )
    out2 = FakeCrewOutput(pydantic=pat2, tasks_output=[FakeTaskOutput(loc2), FakeTaskOutput(pat2)])

    r2 = process_turn(
        settings=workspace_settings,
        session_state=session,
        instruction=clarified_instruction,
        sources=fresh_sources,
        crew_executor=lambda s, i: out2,
    )

    assert r2.status == "applied"
    assert r2.file == "index.html"
    assert r2.backup_file == "index.html.bak"

    # Disk file updated by Python patcher
    final_html = (workspace_settings.project_root / "index.html").read_text(encoding="utf-8")
    assert "Contact Us" in final_html
    assert "Get started now" not in final_html

    # Backup created
    assert (workspace_settings.project_root / "index.html.bak").exists()

    # Memory updated
    assert session.successful_turn_count == 1
    assert session.last_target.target == "CTA link text"


def test_unsupported_requests_never_create_clarification(
    workspace_settings: Settings,
) -> None:
    """Unsupported requests (JS, broad redesign, multi-file, disallowed path) return unsupported."""
    requests = [
        ("Add onclick handler", "JavaScript changes are unsupported."),
        ("Redesign page", "Broad redesigns are unsupported."),
        ("Edit index.html and style.css", "Multi-file changes are unsupported."),
    ]

    for inst, msg in requests:
        loc = LocatorResult(status="unsupported", message=msg)
        pat = ProposedPatch(status="unsupported", summary=msg, message=msg)
        out = FakeCrewOutput(pydantic=pat, tasks_output=[FakeTaskOutput(loc), FakeTaskOutput(pat)])

        session = SessionState(history_limit=5)
        sources = {"index.html": SAMPLE_HTML, "style.css": "body { margin: 0; }"}

        res = process_turn(
            settings=workspace_settings,
            session_state=session,
            instruction=inst,
            sources=sources,
            crew_executor=lambda s, i: out,
        )

        assert res.status == "unsupported"
        assert res.clarification_request is None
        assert not list(workspace_settings.project_root.glob("*.bak*"))
        assert session.successful_turn_count == 0


def test_failed_clarified_patch_does_not_update_memory(
    workspace_settings: Settings,
) -> None:
    """If a clarified patch fails validation, successful memory is untouched."""
    session = SessionState(history_limit=5)
    mgr = ClarificationManager()

    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Brand link"),
            ClarificationOption(id="2", label="CTA link"),
        ),
        message="Multiple links.",
    )
    mgr.set_pending(req, "Change link text")
    selected, clarified_inst = mgr.resolve_answer("1")

    # Mock locator returning non-existent exact_source -> patcher validation fails
    loc = LocatorResult(
        status="located",
        file="index.html",
        target="Brand link",
        exact_source="<nonexistent>text</nonexistent>",
        message="Located brand link.",
    )
    pat = ProposedPatch(
        status="ready",
        file="index.html",
        old_text="<nonexistent>text</nonexistent>",
        new_text="<changed>",
        target="Brand link",
        summary="Edit brand link.",
    )
    out = FakeCrewOutput(pydantic=pat, tasks_output=[FakeTaskOutput(loc), FakeTaskOutput(pat)])

    sources = {"index.html": SAMPLE_HTML, "style.css": "body { margin: 0; }"}

    with pytest.raises(Exception):
        process_turn(
            settings=workspace_settings,
            session_state=session,
            instruction=clarified_inst,
            sources=sources,
            crew_executor=lambda s, i: out,
        )

    assert session.successful_turn_count == 0
    assert not list(workspace_settings.project_root.glob("*.bak*"))


def test_cancellation_clears_pending_state(workspace_settings: Settings) -> None:
    """Cancelling a clarification allows the next normal instruction to run."""
    mgr = ClarificationManager()
    req = ClarificationRequest(
        question="Which link?",
        options=(
            ClarificationOption(id="1", label="Option 1"),
            ClarificationOption(id="2", label="Option 2"),
        ),
        message="Multiple options.",
    )

    mgr.set_pending(req, "Change link text")
    assert mgr.has_pending() is True

    mgr.cancel()
    assert mgr.has_pending() is False
    assert not list(workspace_settings.project_root.glob("*.bak*"))
