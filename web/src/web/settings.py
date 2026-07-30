from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_ROOT = REPOSITORY_ROOT / "src" / "web" / "workspace"

ALLOWED_SOURCE_SUFFIXES = {".html", ".css"}

API_KEY_PLACEHOLDERS = {
    "replace_with_your_key",
    "your_groq_api_key",
    "your_gemini_api_key",
    "replace_with_your_gemini_key",
}

MODEL_PLACEHOLDERS = {
    "replace_with_a_model_available_in_your_groq_account",
    "groq/replace_with_model_id",
}


class Settings(BaseSettings):
    """Environment-backed application configuration."""

    groq_api_key: SecretStr | None = None
    groq_model: str | None = None

    project_root: Path = Field(default=DEFAULT_PROJECT_ROOT)

    allowed_files: tuple[str, ...] = (
        "index.html",
        "style.css",
    )

    # Reserved for the bounded session memory implemented in Phase 6.
    session_history_limit: int = Field(default=5, ge=1, le=20)

    # Reserved for rotating backups implemented in Phase 3.
    backup_limit: int = Field(default=3, ge=1, le=10)

    # Phase 8: Embedded Gemini CLI read-only patch reviewer configuration
    gemini_api_key: SecretStr | None = None
    gemini_cli_enabled: bool = False
    gemini_cli_model: str = "flash"
    gemini_cli_timeout_seconds: int = Field(default=60, ge=5, le=300)
    gemini_cli_max_output_chars: int = Field(
        default=20000, ge=100, le=500000
    )

    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("project_root", mode="before")
    @classmethod
    def normalize_project_root(cls, value: object) -> Path:
        """
        Resolve relative project-root values from the repository root.

        This makes PROJECT_ROOT=src/web/workspace behave consistently
        regardless of the terminal's current working directory.
        """

        path = Path(str(value)).expanduser()

        if not path.is_absolute():
            path = REPOSITORY_ROOT / path

        return path.resolve()

    @field_validator("allowed_files")
    @classmethod
    def validate_allowed_files(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Validate the static file allowlist."""

        if not values:
            raise ValueError("allowed_files must not be empty")

        if len(values) != len(set(values)):
            raise ValueError("allowed_files must not contain duplicates")

        for value in values:
            relative_path = Path(value)

            if relative_path.is_absolute():
                raise ValueError(
                    f"allowed file must be relative: {value}"
                )

            if ".." in relative_path.parts:
                raise ValueError(
                    f"allowed file cannot contain '..': {value}"
                )

            if relative_path.suffix.lower() not in ALLOWED_SOURCE_SUFFIXES:
                raise ValueError(
                    f"allowed file must be HTML or CSS: {value}"
                )

        return values

    @field_validator("gemini_cli_model")
    @classmethod
    def validate_gemini_cli_model(cls, value: str) -> str:
        """Validate the Gemini CLI model identifier."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("gemini_cli_model must not be empty")
        return stripped

    @model_validator(mode="after")
    def validate_gemini_config(self) -> Settings:
        """Validate Gemini CLI configuration when enabled."""

        if self.gemini_cli_enabled:
            if self.gemini_api_key is None:
                raise ValueError(
                    "GEMINI_API_KEY is missing when GEMINI_CLI_ENABLED is true"
                )

            key_val = self.gemini_api_key.get_secret_value().strip()
            if not key_val or key_val in API_KEY_PLACEHOLDERS:
                raise ValueError(
                    "GEMINI_API_KEY must be set to a valid API key when GEMINI_CLI_ENABLED is true"
                )

        return self

    @property
    def llm_is_configured(self) -> bool:
        """Check Groq configuration without displaying the API key."""

        api_key = (
            self.groq_api_key.get_secret_value().strip()
            if self.groq_api_key is not None
            else ""
        )
        model = self.groq_model.strip() if self.groq_model else ""

        return bool(
            api_key
            and model
            and api_key not in API_KEY_PLACEHOLDERS
            and model not in MODEL_PLACEHOLDERS
        )


def resolve_allowed_paths(settings: Settings) -> Mapping[str, Path]:
    """
    Resolve the configured source files and enforce the project boundary.

    This is a read-time safety check. Phase 3 will repeat stricter checks
    immediately before every write.
    """

    root = settings.project_root.resolve()

    if not root.exists():
        raise ValueError(f"project root does not exist: {root}")

    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")

    resolved_paths: dict[str, Path] = {}

    for relative_name in settings.allowed_files:
        candidate = (root / relative_name).resolve()

        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"allowed file escapes the project root: {relative_name}"
            ) from exc

        if candidate.suffix.lower() not in ALLOWED_SOURCE_SUFFIXES:
            raise ValueError(
                f"unsupported source extension: {relative_name}"
            )

        resolved_paths[relative_name] = candidate

    return resolved_paths