from __future__ import annotations

import os
from pathlib import Path

import pytest

from web.settings import Settings


@pytest.fixture(autouse=True)
def isolate_settings_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent local dotenv and process settings from leaking into tests."""

    monkeypatch.setitem(Settings.model_config, "env_file", None)

    setting_names = {
        field_name.casefold()
        for field_name in Settings.model_fields
    }

    for environment_name in tuple(os.environ):
        if environment_name.casefold() in setting_names:
            monkeypatch.delenv(environment_name)


@pytest.fixture(autouse=True)
def isolate_crewai_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep CrewAI SQLite/cache files inside pytest's writable temp tree."""

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
