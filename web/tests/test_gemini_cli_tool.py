"""Unit tests for the GeminiCliReviewTool and Settings configuration in Phase 8.

All tests mock subprocess execution and filesystem utilities. No network,
browser, npm, or live Gemini CLI calls are performed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from web.settings import Settings
from web.tools.gemini_cli_tool import (
    GeminiCliReviewInput,
    GeminiCliReviewTool,
    GeminiReviewResult,
)


@pytest.fixture()
def valid_settings(tmp_path: Path) -> Settings:
    """Create valid Settings with Gemini CLI enabled."""
    return Settings(
        project_root=tmp_path,
        groq_api_key="test-groq-key",
        groq_model="groq/test-model",
        gemini_api_key="test-gemini-key",
        gemini_cli_enabled=True,
        gemini_cli_model="flash",
        gemini_cli_timeout_seconds=30,
        gemini_cli_max_output_chars=10000,
    )


def test_settings_gemini_disabled_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini CLI is disabled by default and does not require gemini_api_key."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("gemini_api_key", raising=False)

    settings = Settings(
        _env_file=None,
        project_root=tmp_path,
        groq_api_key="test-groq-key",
        groq_model="groq/test-model",
    )
    assert settings.gemini_cli_enabled is False
    assert settings.gemini_api_key is None


def test_settings_missing_gemini_api_key_when_enabled(tmp_path: Path) -> None:
    """Enabling Gemini CLI without a key raises a validation error."""
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        Settings(
            project_root=tmp_path,
            groq_api_key="test-groq-key",
            groq_model="groq/test-model",
            gemini_cli_enabled=True,
            gemini_api_key=None,
        )


def test_settings_placeholder_gemini_api_key_rejected(tmp_path: Path) -> None:
    """Placeholder GEMINI_API_KEY values are rejected when enabled."""
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        Settings(
            project_root=tmp_path,
            groq_api_key="test-groq-key",
            groq_model="groq/test-model",
            gemini_cli_enabled=True,
            gemini_api_key="your_gemini_api_key",
        )


def test_settings_repr_does_not_expose_gemini_api_key(valid_settings: Settings) -> None:
    """API key must never appear in Settings repr or str."""
    assert "test-gemini-key" not in repr(valid_settings)
    assert "test-gemini-key" not in str(valid_settings)


def test_gemini_tool_input_schema() -> None:
    """Verify GeminiCliReviewInput field types."""
    inp = GeminiCliReviewInput(
        instruction="Change color",
        locator_result_json='{"status": "located"}',
        candidate_patch_json='{"status": "ready"}',
        current_file_content="body { color: red; }",
    )
    assert inp.instruction == "Change color"


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_valid_gemini_review_approved(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Approved review output from Gemini CLI is correctly parsed."""
    inner_review = {
        "verdict": "approved",
        "message": "The patch is minimal and safe.",
        "suggested_new_text": None,
    }
    outer_output = json.dumps({"response": json.dumps(inner_review)})

    mock_run.return_value = MagicMock(returncode=0, stdout=outer_output, stderr="")

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Make button green",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "approved"
    assert "minimal and safe" in result.message


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_gemini_review_revision_required(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Revision-required verdict is captured."""
    inner_review = {
        "verdict": "revision_required",
        "message": "Prefer hex color code over color name.",
        "suggested_new_text": "color: #00ff00;",
    }
    outer_output = json.dumps({"response": json.dumps(inner_review)})

    mock_run.return_value = MagicMock(returncode=0, stdout=outer_output, stderr="")

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Make button green",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "revision_required"
    assert result.suggested_new_text == "color: #00ff00;"


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_gemini_review_unsafe(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Unsafe verdict is captured."""
    inner_review = {
        "verdict": "unsafe",
        "message": "Request introduces JavaScript event handler.",
    }
    outer_output = json.dumps({"response": json.dumps(inner_review)})

    mock_run.return_value = MagicMock(returncode=0, stdout=outer_output, stderr="")

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Add click handler",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="<button>Click</button>",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unsafe"


@patch("shutil.which", return_value=None)
def test_gemini_executable_not_found(
    mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Missing gemini executable returns unavailable verdict."""
    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"
    assert "not found" in result.message.lower()


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gemini", timeout=30))
def test_subprocess_timeout(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Subprocess timeout yields unavailable verdict."""
    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"
    assert "timed out" in result.message.lower()


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_nonzero_exit_status(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Non-zero returncode yields unavailable verdict."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Internal CLI Error")

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"
    assert "non-zero exit status" in result.message.lower()
    # Stderr must NOT be exposed
    assert "Internal CLI Error" not in raw


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_empty_stdout(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Empty stdout yields unavailable verdict."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"
    assert "empty" in result.message.lower()


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_oversized_stdout(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Oversized stdout yields unavailable verdict."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout="A" * 20000, stderr=""
    )
    valid_settings.gemini_cli_max_output_chars = 500

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"
    assert "exceeded" in result.message.lower()


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_invalid_outer_json(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Non-JSON stdout yields unavailable verdict."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout="Plain text response", stderr=""
    )

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_gemini_outer_error_object(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Outer error object from Gemini CLI yields unavailable verdict."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({"error": {"message": "API Key quota exceeded"}}),
        stderr="",
    )

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"
    assert "outer error" in result.message.lower()


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_missing_response_field(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Missing response field yields unavailable verdict."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout=json.dumps({"status": "ok"}), stderr=""
    )

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_response_invalid_review_json(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Inner response string with invalid JSON yields unavailable verdict."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout=json.dumps({"response": "not json"}), stderr=""
    )

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_unexpected_review_fields_handled(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Response containing invalid verdict value is rejected."""
    bad_review = {"verdict": "unknown_verdict", "message": "hello"}
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({"response": json.dumps(bad_review)}),
        stderr="",
    )

    tool = GeminiCliReviewTool(settings=valid_settings)
    raw = tool._run(
        instruction="Change color",
        locator_result_json="{}",
        candidate_patch_json="{}",
        current_file_content="body {}",
    )

    result = GeminiReviewResult.model_validate_json(raw)
    assert result.verdict == "unavailable"


@patch("shutil.which", return_value="/usr/bin/gemini")
@patch("subprocess.run")
def test_subprocess_invocation_security_and_parameters(
    mock_run: MagicMock, mock_which: MagicMock, valid_settings: Settings
) -> None:
    """Verify subprocess safety: shell=False, --approval-mode plan, cwd, stdin input."""
    inner_review = {"verdict": "approved", "message": "OK"}
    mock_run.return_value = MagicMock(
        returncode=0, stdout=json.dumps({"response": json.dumps(inner_review)})
    )

    tool = GeminiCliReviewTool(settings=valid_settings)
    tool._run(
        instruction="Change color to blue; rm -rf /",
        locator_result_json='{"file": "index.html"}',
        candidate_patch_json='{"new_text": "blue"}',
        current_file_content="<p>hello</p>",
    )

    assert mock_run.called
    kwargs = mock_run.call_args.kwargs
    args = mock_run.call_args.args[0]

    # Safety checks
    assert kwargs.get("shell") is False
    assert kwargs.get("cwd") == str(valid_settings.project_root)
    assert "--approval-mode" in args
    assert args[args.index("--approval-mode") + 1] == "plan"
    assert "--output-format" in args
    assert args[args.index("--output-format") + 1] == "json"

    # User input is NOT passed as shell flag/arg
    assert "Change color to blue; rm -rf /" not in args

    # Dynamic data sent through stdin
    stdin_input = kwargs.get("input")
    assert stdin_input is not None
    assert "Change color to blue; rm -rf /" in stdin_input

    # Environment isolation check
    env = kwargs.get("env")
    assert env is not None
    assert env.get("GEMINI_API_KEY") == "test-gemini-key"
    assert "GROQ_API_KEY" not in env  # Groq key must not be passed to Gemini CLI
