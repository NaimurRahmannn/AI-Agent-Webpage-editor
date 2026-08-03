"""End-to-end mocked integration tests.

Every test exercises the complete ``process_turn`` flow with mocked CrewAI
outputs and the **real** deterministic patcher.  No test calls Groq, accesses
the network, uses browser automation, depends on execution order, or modifies
the committed sample workspace.  All filesystem work happens in pytest
temporary directories.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch as monkeypatch_attr

import pytest

from web.models import LocatorResult, ProposedPatch
from web.orchestration import (
    CrewExecutionError,
    CrewOutputError,
    TurnResult,
    process_turn,
)
from web.settings import Settings
from web.state import SessionState
from web.tools.patcher import (
    PatchBackupError,
    PatchSourceChangedError,
    PatchValidationError,
    PatchWriteError,
)


# ---------------------------------------------------------------------------
# Shared helpers and fakes
# ---------------------------------------------------------------------------

@dataclass
class FakeTaskOutput:
    """Mimics ``crewai.tasks.task_output.TaskOutput``."""
    pydantic: Any


@dataclass
class FakeCrewOutput:
    """Mimics ``crewai.crew.CrewOutput``."""
    pydantic: Any
    tasks_output: list[FakeTaskOutput]


# Sample workspace content — deterministic, self-contained.

SAMPLE_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Weft Studio</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="site-header">
    <a class="brand" href="#">Weft Studio</a>
  </header>

  <main>
    <section class="hero" id="work">
      <h1>Build better products.</h1>

      <p class="hero-copy">
        We design calm, useful digital products for teams solving
        difficult problems.
      </p>

      <a class="cta" href="#contact">Start a project</a>
    </section>
  </main>

  <footer class="site-footer" id="contact">
    <p>hello@weft.example</p>
  </footer>
</body>
</html>
"""

SAMPLE_CSS = """\
:root {
  --page-bg: #f4f1ea;
  --accent: #2d6a4f;
}

body {
  margin: 0;
  color: var(--text);
  background: var(--page-bg);
}

.cta {
  display: inline-block;
  padding: 14px 20px;
  border-radius: 1000px;
  color: #ffffff;
  background: #ff0000;
  font-weight: 700;
  text-decoration: none;
}

.site-footer {
  padding-block: 32px;
  color: var(--muted);
}
"""


def _make_settings(tmp_path: Path) -> Settings:
    """Create Settings pointing at the tmp workspace."""
    return Settings(
        project_root=tmp_path,
        allowed_files=("index.html", "style.css"),
        backup_limit=3,
        session_history_limit=5,
        groq_api_key="test-key-not-real",
        groq_model="groq/test-model",
    )


def _write_workspace(tmp_path: Path) -> dict[str, str]:
    """Write sample files into *tmp_path* and return the sources dict."""
    html_path = tmp_path / "index.html"
    css_path = tmp_path / "style.css"
    html_path.write_text(SAMPLE_HTML, encoding="utf-8")
    css_path.write_text(SAMPLE_CSS, encoding="utf-8")
    return {
        "index.html": SAMPLE_HTML,
        "style.css": SAMPLE_CSS,
    }


def _make_crew_output(
    locator: LocatorResult,
    patch: ProposedPatch,
) -> FakeCrewOutput:
    return FakeCrewOutput(
        pydantic=patch,
        tasks_output=[
            FakeTaskOutput(locator),
            FakeTaskOutput(patch),
        ],
    )


def _ready_pair(
    *,
    file: str,
    old_text: str,
    new_text: str,
    target: str = "test target",
    selector: str | None = None,
    prop: str | None = None,
) -> tuple[LocatorResult, ProposedPatch]:
    """Build a consistent located/ready pair."""
    locator = LocatorResult(
        status="located",
        file=file,
        target=target,
        selector=selector,
        property=prop,
        exact_source=old_text,
        message=f"Located {target}.",
    )
    patch = ProposedPatch(
        status="ready",
        file=file,
        old_text=old_text,
        new_text=new_text,
        target=target,
        selector=selector,
        property=prop,
        summary=f"Edit {target}.",
    )
    return locator, patch


def _rejected_pair(
    status: str,
    message: str = "The request cannot be handled.",
) -> tuple[LocatorResult, ProposedPatch]:
    locator = LocatorResult(
        status=status,
        message=message,
    )
    patch = ProposedPatch(
        status=status,
        summary=f"Request classified as {status}.",
        message=message,
    )
    return locator, patch


def _fake_executor(crew_output: FakeCrewOutput):
    """Return a callable that captures inputs and returns *crew_output*."""
    captured: dict[str, str] = {}

    def executor(settings, inputs):
        captured.update(inputs)
        return crew_output

    executor.captured = captured  # type: ignore[attr-defined]
    return executor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Path, dict[str, str], Settings]:
    """Write sample workspace and return (dir, sources, settings)."""
    sources = _write_workspace(tmp_path)
    settings = _make_settings(tmp_path)
    return tmp_path, sources, settings


@pytest.fixture()
def session() -> SessionState:
    return SessionState(history_limit=5)


# ===================================================================
# 1.  SUCCESS SCENARIOS
# ===================================================================


class TestDirectEdits:
    """Direct single-turn edits that exercise the real patcher."""

    def test_direct_html_text_edit(
        self, workspace, session,
    ) -> None:
        """Change the heading text in index.html."""
        tmp_path, sources, settings = workspace

        locator, patch = _ready_pair(
            file="index.html",
            old_text="<h1>Build better products.</h1>",
            new_text="<h1>Ship great products.</h1>",
            target="main heading text",
        )
        output = _make_crew_output(locator, patch)
        executor = _fake_executor(output)

        result = process_turn(
            settings=settings,
            session_state=session,
            instruction="Change the main heading to Ship great products.",
            sources=sources,
            crew_executor=executor,
        )

        assert result.status == "applied"
        assert result.file == "index.html"
        assert result.backup_file == "index.html.bak"
        assert "<h1>Ship great products.</h1>" in result.diff
        assert "<h1>Build better products.</h1>" in result.diff

        updated = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "<h1>Ship great products.</h1>" in updated
        assert "<h1>Build better products.</h1>" not in updated

        backup = (tmp_path / "index.html.bak").read_text(encoding="utf-8")
        assert backup == SAMPLE_HTML

        assert session.successful_turn_count == 1
        assert session.last_target is not None
        assert session.last_target.file == "index.html"

    def test_direct_html_attribute_edit(
        self, workspace, session,
    ) -> None:
        """Change the CTA href attribute in index.html."""
        tmp_path, sources, settings = workspace

        locator, patch = _ready_pair(
            file="index.html",
            old_text='<a class="cta" href="#contact">Start a project</a>',
            new_text='<a class="cta" href="#work">Start a project</a>',
            target="CTA link href attribute",
        )
        output = _make_crew_output(locator, patch)

        result = process_turn(
            settings=settings,
            session_state=session,
            instruction="Change the CTA link to point to #work.",
            sources=sources,
            crew_executor=_fake_executor(output),
        )

        assert result.status == "applied"
        updated = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert 'href="#work"' in updated
        assert session.successful_turn_count == 1

    def test_direct_css_declaration_edit(
        self, workspace, session,
    ) -> None:
        """Change the CTA background color in style.css."""
        tmp_path, sources, settings = workspace

        locator, patch = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: #2d6a4f;",
            target="CTA background color",
            selector=".cta",
            prop="background",
        )
        output = _make_crew_output(locator, patch)

        result = process_turn(
            settings=settings,
            session_state=session,
            instruction="Change the CTA button color to the accent green.",
            sources=sources,
            crew_executor=_fake_executor(output),
        )

        assert result.status == "applied"
        assert result.file == "style.css"
        assert "-  background: #ff0000;" in result.diff
        assert "+  background: #2d6a4f;" in result.diff

        updated = (tmp_path / "style.css").read_text(encoding="utf-8")
        assert "background: #2d6a4f;" in updated
        assert "background: #ff0000;" not in updated
        assert session.last_target.selector == ".cta"
        assert session.last_target.property == "background"


class TestConversationalFollowUps:
    """Multi-turn sequences proving memory, re-read, and freshness."""

    def test_first_follow_up_uses_memory_and_reread_source(
        self, workspace, session,
    ) -> None:
        """After a direct edit, a follow-up proves session memory works."""
        tmp_path, sources, settings = workspace

        # --- Turn 1: direct CSS edit ---
        loc1, pat1 = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: #2d6a4f;",
            target="CTA background color",
            selector=".cta",
            prop="background",
        )
        out1 = _make_crew_output(loc1, pat1)
        exec1 = _fake_executor(out1)

        r1 = process_turn(
            settings=settings,
            session_state=session,
            instruction="Change the CTA background to green.",
            sources=sources,
            crew_executor=exec1,
        )
        assert r1.status == "applied"
        assert session.successful_turn_count == 1
        assert session.last_target is not None
        assert session.last_target.target == "CTA background color"

        # Reread current source (simulates the REPL re-reading on next turn)
        current_css = (tmp_path / "style.css").read_text(encoding="utf-8")
        current_sources = {
            "index.html": sources["index.html"],
            "style.css": current_css,
        }

        # --- Turn 2: conversational follow-up ---
        loc2, pat2 = _ready_pair(
            file="style.css",
            old_text="background: #2d6a4f;",
            new_text="background: #1b4332;",
            target="CTA background color",
            selector=".cta",
            prop="background",
        )
        out2 = _make_crew_output(loc2, pat2)
        exec2 = _fake_executor(out2)

        r2 = process_turn(
            settings=settings,
            session_state=session,
            instruction="Make it even darker.",
            sources=current_sources,
            crew_executor=exec2,
        )

        assert r2.status == "applied"
        assert session.successful_turn_count == 2

        # The crew received the CURRENT file with the first edit applied
        assert "background: #2d6a4f;" in exec2.captured["source_bundle"]
        # Old original is NOT in the source bundle
        assert "background: #ff0000;" not in exec2.captured["source_bundle"]

        # Memory was included
        assert "likely follow-up: yes" in exec2.captured["session_memory"]
        assert "CTA background color" in exec2.captured["session_memory"]

        # File on disk reflects both edits
        final_css = (tmp_path / "style.css").read_text(encoding="utf-8")
        assert "background: #1b4332;" in final_css
        assert "background: #ff0000;" not in final_css
        assert "background: #2d6a4f;" not in final_css

    def test_second_follow_up_continues_chain(
        self, workspace, session,
    ) -> None:
        """Two successive follow-ups after a direct edit all succeed."""
        tmp_path, sources, settings = workspace

        # --- Turn 1 ---
        loc1, pat1 = _ready_pair(
            file="style.css",
            old_text="padding: 14px 20px;",
            new_text="padding: 16px 24px;",
            target="CTA padding",
            selector=".cta",
            prop="padding",
        )
        process_turn(
            settings=settings,
            session_state=session,
            instruction="Increase the CTA padding.",
            sources=sources,
            crew_executor=_fake_executor(_make_crew_output(loc1, pat1)),
        )
        assert session.successful_turn_count == 1

        css_v2 = (tmp_path / "style.css").read_text(encoding="utf-8")
        sources_v2 = {"index.html": sources["index.html"], "style.css": css_v2}

        # --- Turn 2 (first follow-up) ---
        loc2, pat2 = _ready_pair(
            file="style.css",
            old_text="padding: 16px 24px;",
            new_text="padding: 18px 28px;",
            target="CTA padding",
            selector=".cta",
            prop="padding",
        )
        exec2 = _fake_executor(_make_crew_output(loc2, pat2))
        process_turn(
            settings=settings,
            session_state=session,
            instruction="Make it bigger.",
            sources=sources_v2,
            crew_executor=exec2,
        )
        assert session.successful_turn_count == 2
        assert "likely follow-up: yes" in exec2.captured["session_memory"]
        assert "padding: 16px 24px;" in exec2.captured["source_bundle"]

        css_v3 = (tmp_path / "style.css").read_text(encoding="utf-8")
        sources_v3 = {"index.html": sources["index.html"], "style.css": css_v3}

        # --- Turn 3 (second follow-up) ---
        loc3, pat3 = _ready_pair(
            file="style.css",
            old_text="padding: 18px 28px;",
            new_text="padding: 20px 32px;",
            target="CTA padding",
            selector=".cta",
            prop="padding",
        )
        exec3 = _fake_executor(_make_crew_output(loc3, pat3))
        r3 = process_turn(
            settings=settings,
            session_state=session,
            instruction="Even more padding.",
            sources=sources_v3,
            crew_executor=exec3,
        )

        assert r3.status == "applied"
        assert session.successful_turn_count == 3

        # Confirm the locator received the latest source
        assert "padding: 18px 28px;" in exec3.captured["source_bundle"]
        # None of the old values remain in the supplied source
        assert "padding: 14px 20px;" not in exec3.captured["source_bundle"]
        assert "padding: 16px 24px;" not in exec3.captured["source_bundle"]

        # The third edit's memory includes last-target metadata
        assert "CTA padding" in exec3.captured["session_memory"]

        # Final file state
        final = (tmp_path / "style.css").read_text(encoding="utf-8")
        assert "padding: 20px 32px;" in final


# ===================================================================
# 2.  REJECTION SCENARIOS
# ===================================================================


class TestRejections:
    """Rejected requests must leave source, backups, and memory untouched."""

    def _assert_clean_rejection(
        self,
        result: TurnResult,
        expected_status: str,
        tmp_path: Path,
        original_sources: dict[str, str],
        session: SessionState,
    ) -> None:
        """Common rejection assertions."""
        assert result.status == expected_status

        # Source files unchanged
        for name, original in original_sources.items():
            assert (tmp_path / name).read_text(encoding="utf-8") == original

        # No backup created
        assert not list(tmp_path.glob("*.bak*"))

        # Memory not updated
        assert session.successful_turn_count == 0
        assert session.last_target is None

    def _run_rejected_turn(
        self,
        workspace,
        session: SessionState,
        status: str,
        instruction: str = "Do something.",
        message: str = "The request cannot be handled.",
    ) -> TurnResult:
        tmp_path, sources, settings = workspace
        loc, pat = _rejected_pair(status, message)
        output = _make_crew_output(loc, pat)

        result = process_turn(
            settings=settings,
            session_state=session,
            instruction=instruction,
            sources=sources,
            crew_executor=_fake_executor(output),
        )
        self._assert_clean_rejection(
            result, status, tmp_path, sources, session,
        )
        return result

    def test_reject_ambiguous_target(self, workspace, session) -> None:
        result = self._run_rejected_turn(
            workspace, session, "ambiguous",
            instruction="Change the color.",
            message="Multiple elements have color declarations.",
        )
        assert "ambiguous" in result.summary.lower()

    def test_reject_broad_redesign(self, workspace, session) -> None:
        self._run_rejected_turn(
            workspace, session, "unsupported",
            instruction="Redesign the entire page.",
            message="Broad redesigns are unsupported.",
        )

    def test_reject_javascript_request(self, workspace, session) -> None:
        self._run_rejected_turn(
            workspace, session, "unsupported",
            instruction="Add a click handler to the CTA button.",
            message="JavaScript changes are unsupported.",
        )

    def test_reject_multi_file_request(self, workspace, session) -> None:
        self._run_rejected_turn(
            workspace, session, "unsupported",
            instruction="Change the heading in HTML and its color in CSS.",
            message="Multi-file edits are unsupported.",
        )

    def test_reject_browser_dependent(self, workspace, session) -> None:
        self._run_rejected_turn(
            workspace, session, "unsupported",
            instruction="Make the button look exactly like the screenshot.",
            message="Visual verification is unsupported.",
        )

    def test_reject_disallowed_path(self, workspace, session) -> None:
        """A ready patch targeting an un-allowlisted file is rejected."""
        tmp_path, sources, settings = workspace

        locator = LocatorResult(
            status="located",
            file="../other/secret.html",
            target="password file",
            exact_source="secret data",
            message="Located the file.",
        )
        patch = ProposedPatch(
            status="ready",
            file="../other/secret.html",
            old_text="secret data",
            new_text="hacked",
            target="password file",
            summary="Edit password file.",
        )
        output = _make_crew_output(locator, patch)

        with pytest.raises(PatchValidationError, match="allowlisted"):
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Edit secret file.",
                sources={**sources, "../other/secret.html": "secret data"},
                crew_executor=_fake_executor(output),
            )

        # Source safety checks
        for name, original in sources.items():
            assert (tmp_path / name).read_text(encoding="utf-8") == original
        assert session.successful_turn_count == 0

    def test_reject_non_allowlisted_file(self, workspace, session) -> None:
        """A ready patch targeting a .js file is rejected by extension check."""
        tmp_path, sources, settings = workspace

        locator = LocatorResult(
            status="located",
            file="script.js",
            target="script",
            exact_source="alert('hello')",
            message="Located the script.",
        )
        patch = ProposedPatch(
            status="ready",
            file="script.js",
            old_text="alert('hello')",
            new_text="console.log('hello')",
            target="script",
            summary="Edit script.",
        )
        output = _make_crew_output(locator, patch)

        with pytest.raises(PatchValidationError):
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Change the alert.",
                sources={**sources, "script.js": "alert('hello')"},
                crew_executor=_fake_executor(output),
            )

        assert session.successful_turn_count == 0
        assert not list(tmp_path.glob("*.bak*"))

    def test_reject_inconsistent_locator_editor(
        self, workspace, session,
    ) -> None:
        """Locator says ambiguous but editor says ready → CrewOutputError."""
        tmp_path, sources, settings = workspace

        locator = LocatorResult(
            status="ambiguous",
            message="Multiple targets found.",
        )
        patch = ProposedPatch(
            status="ready",
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: blue;",
            target="CTA background",
            summary="Change CTA color.",
        )
        output = FakeCrewOutput(
            pydantic=patch,
            tasks_output=[
                FakeTaskOutput(locator),
                FakeTaskOutput(patch),
            ],
        )

        with pytest.raises(CrewOutputError, match="inconsistent"):
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Change the color.",
                sources=sources,
                crew_executor=_fake_executor(output),
            )

        assert session.successful_turn_count == 0

    def test_reject_missing_pydantic_output(
        self, workspace, session,
    ) -> None:
        """Missing Pydantic output on tasks → CrewOutputError."""
        tmp_path, sources, settings = workspace

        output = FakeCrewOutput(
            pydantic=None,
            tasks_output=[
                FakeTaskOutput(None),
                FakeTaskOutput(None),
            ],
        )

        with pytest.raises(CrewOutputError):
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Change something.",
                sources=sources,
                crew_executor=_fake_executor(output),
            )

        assert session.successful_turn_count == 0
        assert not list(tmp_path.glob("*.bak*"))

    def test_next_turn_works_after_rejection(
        self, workspace, session,
    ) -> None:
        """After a rejection the REPL can still process the next turn."""
        tmp_path, sources, settings = workspace

        # First: rejected
        self._run_rejected_turn(
            workspace, session, "ambiguous",
            instruction="Change the color.",
        )

        # Second: successful
        loc, pat = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: blue;",
            target="CTA background",
            selector=".cta",
            prop="background",
        )
        result = process_turn(
            settings=settings,
            session_state=session,
            instruction="Change the CTA background to blue.",
            sources=sources,
            crew_executor=_fake_executor(_make_crew_output(loc, pat)),
        )
        assert result.status == "applied"
        assert session.successful_turn_count == 1


# ===================================================================
# 3.  FAILURE-PATH SCENARIOS
# ===================================================================


class TestFailurePaths:
    """Recoverable failures must preserve source safety and memory."""

    def test_fail_crew_execution(self, workspace, session) -> None:
        """CrewAI/provider failure raises CrewExecutionError."""
        tmp_path, sources, settings = workspace

        def failing_executor(settings, inputs):
            raise RuntimeError("simulated groq outage with secret-key-123")

        with pytest.raises(CrewExecutionError) as exc_info:
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Change the heading.",
                sources=sources,
                crew_executor=failing_executor,
            )

        # Raw secret is not in the safe message
        assert "secret-key-123" not in str(exc_info.value)
        assert session.successful_turn_count == 0
        assert not list(tmp_path.glob("*.bak*"))

    def test_fail_source_changed_after_supply(
        self, workspace, session,
    ) -> None:
        """Source changes between supply and patch application."""
        tmp_path, sources, settings = workspace

        locator, patch = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: blue;",
            target="CTA background",
            selector=".cta",
            prop="background",
        )
        output = _make_crew_output(locator, patch)

        # Mutate the file after sources were captured
        (tmp_path / "style.css").write_text(
            SAMPLE_CSS.replace("#ff0000", "#00ff00"),
            encoding="utf-8",
        )

        with pytest.raises(PatchSourceChangedError):
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Change the CTA background.",
                sources=sources,
                crew_executor=_fake_executor(output),
            )

        assert session.successful_turn_count == 0
        assert not list(tmp_path.glob("*.bak*"))

    def test_fail_zero_match_patch(self, workspace, session) -> None:
        """old_text not found in source → PatchValidationError."""
        tmp_path, sources, settings = workspace

        locator, patch = _ready_pair(
            file="style.css",
            old_text="background: #999999;",  # does not exist
            new_text="background: blue;",
            target="nonexistent declaration",
        )
        output = _make_crew_output(locator, patch)

        with pytest.raises(PatchValidationError, match="zero times"):
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Change a non-existent color.",
                sources=sources,
                crew_executor=_fake_executor(output),
            )

        assert session.successful_turn_count == 0

    def test_fail_multiple_match_patch(self, workspace, session) -> None:
        """old_text found multiple times → PatchValidationError."""
        tmp_path, sources, settings = workspace

        # "var(--" appears multiple times in the sample CSS
        locator, patch = _ready_pair(
            file="style.css",
            old_text="var(--",
            new_text="var(--new-",
            target="variable reference",
        )
        output = _make_crew_output(locator, patch)

        with pytest.raises(PatchValidationError, match="more than once"):
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Change a variable.",
                sources=sources,
                crew_executor=_fake_executor(output),
            )

        assert session.successful_turn_count == 0

    def test_fail_backup_failure(self, workspace, session) -> None:
        """Backup creation failure → PatchBackupError, source safe."""
        tmp_path, sources, settings = workspace

        locator, patch = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: blue;",
            target="CTA background",
            selector=".cta",
            prop="background",
        )
        output = _make_crew_output(locator, patch)

        with monkeypatch_attr(
            "web.tools.patcher.create_rotating_backup",
            side_effect=PatchBackupError("disk full"),
        ):
            with pytest.raises(PatchBackupError, match="disk full"):
                process_turn(
                    settings=settings,
                    session_state=session,
                    instruction="Change the CTA background.",
                    sources=sources,
                    crew_executor=_fake_executor(output),
                )

        # Source is unchanged
        css = (tmp_path / "style.css").read_text(encoding="utf-8")
        assert css == SAMPLE_CSS
        assert session.successful_turn_count == 0

    def test_fail_atomic_write_failure(self, workspace, session) -> None:
        """Atomic write failure → PatchWriteError, source safe."""
        tmp_path, sources, settings = workspace

        locator, patch = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: blue;",
            target="CTA background",
            selector=".cta",
            prop="background",
        )
        output = _make_crew_output(locator, patch)

        with monkeypatch_attr(
            "web.tools.patcher.atomic_replace_text",
            side_effect=PatchWriteError("permission denied"),
        ):
            with pytest.raises(PatchWriteError, match="permission denied"):
                process_turn(
                    settings=settings,
                    session_state=session,
                    instruction="Change the CTA background.",
                    sources=sources,
                    crew_executor=_fake_executor(output),
                )

        assert session.successful_turn_count == 0

    def test_fail_unexpected_exception(self, workspace, session) -> None:
        """Unexpected non-crew exception is safely classified."""
        tmp_path, sources, settings = workspace

        def executor_with_unexpected_error(settings, inputs):
            raise MemoryError("out of memory with key=gsk_abc123")

        with pytest.raises(CrewExecutionError) as exc_info:
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Change something.",
                sources=sources,
                crew_executor=executor_with_unexpected_error,
            )

        # Raw diagnostics not in safe message
        assert "gsk_abc123" not in str(exc_info.value)
        assert session.successful_turn_count == 0

    def test_next_turn_works_after_failure(
        self, workspace, session,
    ) -> None:
        """The REPL remains usable after a recoverable failure."""
        tmp_path, sources, settings = workspace

        def failing(settings, inputs):
            raise TimeoutError("timed out")

        with pytest.raises(CrewExecutionError):
            process_turn(
                settings=settings,
                session_state=session,
                instruction="First attempt.",
                sources=sources,
                crew_executor=failing,
            )

        # Now a normal turn succeeds
        loc, pat = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: green;",
            target="CTA background",
        )
        result = process_turn(
            settings=settings,
            session_state=session,
            instruction="Change CTA to green.",
            sources=sources,
            crew_executor=_fake_executor(_make_crew_output(loc, pat)),
        )
        assert result.status == "applied"
        assert session.successful_turn_count == 1


# ===================================================================
# 4.  DEFINITION-OF-DONE MATRIX
# ===================================================================


class TestDefinitionOfDone:
    """Verify the project acceptance checklist programmatically."""

    def test_structured_outputs_required(self) -> None:
        """Crew output without Pydantic models is rejected."""
        output = FakeCrewOutput(
            pydantic=None,
            tasks_output=[FakeTaskOutput(None), FakeTaskOutput(None)],
        )
        with pytest.raises(CrewOutputError):
            from web.orchestration import validate_crew_output
            validate_crew_output(output)

    def test_one_replacement_per_turn(self, workspace, session) -> None:
        """A successful turn modifies exactly one file once."""
        tmp_path, sources, settings = workspace

        loc, pat = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: blue;",
            target="CTA background",
        )
        result = process_turn(
            settings=settings,
            session_state=session,
            instruction="Change CTA to blue.",
            sources=sources,
            crew_executor=_fake_executor(_make_crew_output(loc, pat)),
        )

        assert result.status == "applied"
        assert result.file == "style.css"
        # index.html is untouched
        assert (
            (tmp_path / "index.html").read_text(encoding="utf-8")
            == SAMPLE_HTML
        )

    def test_backup_created_before_write(self, workspace, session) -> None:
        """A successful edit creates a rotating backup."""
        tmp_path, sources, settings = workspace

        loc, pat = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: blue;",
            target="CTA background",
        )
        process_turn(
            settings=settings,
            session_state=session,
            instruction="Change CTA.",
            sources=sources,
            crew_executor=_fake_executor(_make_crew_output(loc, pat)),
        )

        backup = tmp_path / "style.css.bak"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == SAMPLE_CSS

    def test_diff_is_generated(self, workspace, session) -> None:
        """Applied result includes a unified diff."""
        _, sources, settings = workspace

        loc, pat = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: blue;",
            target="CTA background",
        )
        result = process_turn(
            settings=settings,
            session_state=session,
            instruction="Change CTA.",
            sources=sources,
            crew_executor=_fake_executor(_make_crew_output(loc, pat)),
        )

        assert result.diff is not None
        assert "---" in result.diff
        assert "+++" in result.diff

    def test_memory_is_bounded(self) -> None:
        """SessionState respects its history limit."""
        state = SessionState(history_limit=2)

        for i in range(5):
            patch = ProposedPatch(
                status="ready",
                file="style.css",
                old_text=f"color{i};",
                new_text=f"color{i + 1};",
                target=f"target-{i}",
                summary=f"Edit {i}.",
            )
            state.record_success(f"instruction {i}", patch)

        assert state.successful_turn_count == 2  # bounded

    def test_failed_turn_never_enters_memory(
        self, workspace, session,
    ) -> None:
        """Failed turns must not update session memory."""
        _, sources, settings = workspace

        def failing(s, i):
            raise RuntimeError("fail")

        with pytest.raises(CrewExecutionError):
            process_turn(
                settings=settings,
                session_state=session,
                instruction="Something.",
                sources=sources,
                crew_executor=failing,
            )

        assert session.successful_turn_count == 0
        assert session.last_target is None

    def test_groq_config_has_no_hardcoded_secrets(self) -> None:
        """Settings uses SecretStr, never exposes API key in repr."""
        settings = Settings(
            groq_api_key="gsk_supersecret",
            groq_model="groq/test",
            project_root=".",
        )
        text = repr(settings)
        assert "gsk_supersecret" not in text
        assert str(settings.groq_api_key) != "gsk_supersecret"

    def test_current_files_are_source_of_truth(
        self, workspace, session,
    ) -> None:
        """Session memory does not contain old source text."""
        _, sources, settings = workspace

        loc, pat = _ready_pair(
            file="style.css",
            old_text="background: #ff0000;",
            new_text="background: blue;",
            target="CTA background",
        )
        process_turn(
            settings=settings,
            session_state=session,
            instruction="Change CTA.",
            sources=sources,
            crew_executor=_fake_executor(_make_crew_output(loc, pat)),
        )

        context = session.build_context(instruction="Make it darker.")
        # Memory contains metadata, not old source text
        assert "CTA background" in context
        # Memory does not contain old_text or new_text verbatim
        assert "#ff0000" not in context
        assert "background: blue" not in context
