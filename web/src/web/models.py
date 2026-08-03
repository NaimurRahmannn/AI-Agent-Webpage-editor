from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unexpected LLM-generated fields."""

    model_config = ConfigDict(extra="forbid")


class ClarificationOption(StrictModel):
    """A structured candidate option for clarifying an ambiguous request."""

    id: str
    label: str
    file: str | None = None
    target: str | None = None
    selector: str | None = None
    property: str | None = None

    @field_validator("id", "label")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("clarification option text must not be empty")
        return normalized

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
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class ClarificationRequest(StrictModel):
    """Structured question and option set for clarifying an ambiguous turn."""

    question: str
    options: tuple[ClarificationOption, ...]
    message: str

    @field_validator("question", "message")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text must not be empty")
        return normalized

    @field_validator("options")
    @classmethod
    def validate_options(
        cls,
        values: tuple[ClarificationOption, ...],
    ) -> tuple[ClarificationOption, ...]:
        if len(values) < 2:
            raise ValueError(
                "clarification request must contain at least two options"
            )
        if len(values) > 6:
            raise ValueError(
                "clarification request must contain at most six options"
            )

        ids = [opt.id for opt in values]
        if len(ids) != len(set(ids)):
            raise ValueError("clarification option IDs must be unique")

        return values


class LocatorResult(StrictModel):
    """
    Structured output produced by the locator agent.

    A located result identifies one exact source fragment in one file.
    Ambiguous and unsupported results explain why editing cannot continue.
    Ambiguous results may include structured clarification options.
    """

    status: Literal["located", "ambiguous", "unsupported"]
    file: str | None = None
    target: str | None = None
    selector: str | None = None
    property: str | None = None
    exact_source: str | None = None
    message: str
    clarification: ClarificationRequest | None = None

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
        """Require location data when located; forbid clarification when not ambiguous."""

        if self.status != "ambiguous" and self.clarification is not None:
            raise ValueError(
                "clarification request is only permitted when status is ambiguous"
            )

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
    Structured output produced by the editor agent.

    A ready patch contains one exact replacement in one configured file.
    Python validates uniqueness and applies the replacement before writing.
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
