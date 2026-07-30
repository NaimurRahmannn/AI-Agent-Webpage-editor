import pytest
from pydantic import ValidationError

from web.settings import Settings
from web.state import SessionState


def test_settings_history_limit_creates_session_state(
    tmp_path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        session_history_limit=7,
    )

    state = SessionState(
        history_limit=settings.session_history_limit
    )

    assert state.history_limit == 7


def test_settings_rejects_zero_history_limit(
    tmp_path,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            project_root=tmp_path,
            session_history_limit=0,
        )


def test_session_state_rejects_zero_history_limit() -> None:
    with pytest.raises(
        ValueError,
        match="history_limit must be at least 1",
    ):
        SessionState(history_limit=0)