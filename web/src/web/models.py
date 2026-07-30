from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unexpected LLM-generated fields."""

    model_config = ConfigDict(extra="forbid")


class LocatorResult(StrictModel):
    """
    Structured output produced by the future locator agent.

    A located result identifies one exact source fragment in one file.
    Ambiguous and unsupported results explain why editing cannot continue.
    """

    status: Literal["located", "ambiguous", "unsupported"]
    file: str | None = None
    target: str | None = None
    selector: str | None = None
    property: str | None = None
    exact_source: str | None = None
    message: str

    @field_validator(
        "file",
        "target",
        "selector",
        "property",
        mode="before",
    )
    @classmethod
    def normalize_optional_metadata(
        cls,
        value: object,
    ) -> object:
        """Convert blank optional metadata strings to None."""

        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None

        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        """Require a useful locator explanation."""

        normalized = value.strip()

        if not normalized:
            raise ValueError("message must not be empty")

        return normalized

    @model_validator(mode="after")
    def validate_status_payload(self) -> LocatorResult:
        """Require location data when the target was located."""

        if self.status != "located":
            return self

        missing_fields: list[str] = []

        if self.file is None:
            missing_fields.append("file")

        if self.target is None:
            missing_fields.append("target")

        if self.exact_source is None or self.exact_source == "":
            missing_fields.append("exact_source")

        if missing_fields:
            joined = ", ".join(missing_fields)
            raise ValueError(
                f"located result is missing required fields: {joined}"
            )

        return self


class ProposedPatch(StrictModel):
    """
    Structured output produced by the future editor agent.

    A ready patch contains one exact replacement in one configured file.
    Python will validate uniqueness and apply the replacement in Phase 3.
    """

    status: Literal["ready", "ambiguous", "unsupported"]
    file: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    target: str | None = None
    selector: str | None = None
    property: str | None = None
    summary: str
    message: str | None = None

    @field_validator(
        "file",
        "target",
        "selector",
        "property",
        mode="before",
    )
    @classmethod
    def normalize_optional_metadata(
        cls,
        value: object,
    ) -> object:
        """Convert blank optional metadata strings to None."""

        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None

        return value

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        """Require a concise human-readable patch summary."""

        normalized = value.strip()

        if not normalized:
            raise ValueError("summary must not be empty")

        return normalized

    @field_validator("message")
    @classmethod
    def normalize_optional_message(
        cls,
        value: str | None,
    ) -> str | None:
        """Normalize blank optional messages to None."""

        if value is None:
            return None

        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_status_payload(self) -> ProposedPatch:
        """Require a complete exact replacement for ready patches."""

        if self.status != "ready":
            return self

        missing_fields: list[str] = []

        if self.file is None:
            missing_fields.append("file")

        if self.target is None:
            missing_fields.append("target")

        if self.old_text is None or self.old_text == "":
            missing_fields.append("old_text")

        if self.new_text is None:
            missing_fields.append("new_text")

        if missing_fields:
            joined = ", ".join(missing_fields)
            raise ValueError(
                f"ready patch is missing required fields: {joined}"
            )

        if self.old_text == self.new_text:
            raise ValueError(
                "ready patch old_text and new_text must be different"
            )

        return self


class SuccessfulTurn(StrictModel):
    """
    Compact record of one successfully applied editing instruction.

    Full source replacements are deliberately excluded. Current files,
    rather than stored history, remain the source of truth.
    """

    instruction: str
    summary: str
    file: str
    target: str
    selector: str | None = None
    property: str | None = None

    @field_validator(
        "instruction",
        "summary",
        "file",
        "target",
    )
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("successful-turn text must not be empty")

        return normalized

    @field_validator("selector", "property")
    @classmethod
    def normalize_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        return normalized or None


class LastTarget(StrictModel):
    """
    Metadata describing the most recently edited source target.

    It supports follow-up instructions such as "make it darker" without
    storing stale source content.
    """

    file: str
    target: str
    selector: str | None = None
    property: str | None = None

    @field_validator("file", "target")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("last-target text must not be empty")

        return normalized

    @field_validator("selector", "property")
    @classmethod
    def normalize_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        return normalized or None


class SessionMemorySnapshot(StrictModel):
    """
    Immutable-style representation of current in-session memory.

    SessionState returns a new snapshot instead of exposing its internal
    deque directly.
    """

    recent_successful_turns: tuple[SuccessfulTurn, ...] = ()
    last_target: LastTarget | None = None