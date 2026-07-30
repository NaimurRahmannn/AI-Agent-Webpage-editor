"""Process-local preview state manager for Phase 10.

Manages pending preview state when PATCH_MODE=preview. Enforces one pending preview
transaction at a time and provides resolution via :apply and :cancel.
"""

from __future__ import annotations

from dataclasses import dataclass

from web.models import ProposedPatch
from web.tools.patcher import PreparedPatch


class PreviewError(RuntimeError):
    """Base error for preview workflow operations."""


class PendingPreviewExistsError(PreviewError):
    """Raised when setting a pending preview while one already exists."""


class NoPendingPreviewError(PreviewError):
    """Raised when attempting to apply or view a preview when none is pending."""


class StalePreviewError(PreviewError):
    """Raised when the target source file changed on disk before preview apply."""


class PreviewValidationError(PreviewError):
    """Raised when preview validation fails."""


@dataclass(frozen=True, slots=True)
class PendingPreview:
    """Active pending preview transaction."""

    instruction: str
    patch: ProposedPatch
    prepared_patch: PreparedPatch
    created_for_file: str


class PreviewState:
    """
    Process-local state manager for pending previews.

    Only one pending preview can exist at a time. Preview state is not stored
    in session conversational memory.
    """

    def __init__(self) -> None:
        self._pending: PendingPreview | None = None

    def has_pending(self) -> bool:
        """Check whether a preview is currently pending."""
        return self._pending is not None

    def get_pending(self) -> PendingPreview | None:
        """Retrieve the active pending preview or None."""
        return self._pending

    def set_pending(self, pending: PendingPreview) -> None:
        """
        Set a new pending preview transaction.

        Raises PendingPreviewExistsError if a preview is already pending.
        """
        if self._pending is not None:
            raise PendingPreviewExistsError(
                "A preview is already pending. Use :apply or :cancel before entering a new instruction."
            )
        self._pending = pending

    def clear(self) -> None:
        """Clear active pending preview."""
        self._pending = None
