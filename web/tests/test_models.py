import pytest
from pydantic import ValidationError

from web.models import LocatorResult, ProposedPatch


def test_locator_accepts_complete_located_result() -> None:
    result = LocatorResult(
        status="located",
        file="style.css",
        target="CTA background declaration",
        selector=".cta",
        property="background",
        exact_source="background: var(--accent);",
        message="Located the CTA background declaration.",
    )

    assert result.status == "located"
    assert result.file == "style.css"
    assert result.selector == ".cta"


def test_locator_rejects_incomplete_located_result() -> None:
    with pytest.raises(
        ValidationError,
        match="located result is missing required fields",
    ):
        LocatorResult(
            status="located",
            file="style.css",
            target="CTA background declaration",
            exact_source=None,
            message="Located the declaration.",
        )


def test_locator_accepts_ambiguous_result_without_file() -> None:
    result = LocatorResult(
        status="ambiguous",
        message="More than one button could match the request.",
    )

    assert result.status == "ambiguous"
    assert result.file is None
    assert result.exact_source is None


def test_locator_rejects_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        LocatorResult(
            status="unsupported",
            message="JavaScript editing is unsupported.",
            unexpected_field="value",
        )


def test_patch_accepts_complete_ready_patch() -> None:
    patch = ProposedPatch(
        status="ready",
        file="style.css",
        old_text="background: var(--accent);",
        new_text="background: #173d2d;",
        target="CTA background declaration",
        selector=".cta",
        property="background",
        summary="Darken the CTA background.",
    )

    assert patch.status == "ready"
    assert patch.old_text == "background: var(--accent);"
    assert patch.new_text == "background: #173d2d;"


def test_patch_accepts_deletion_replacement() -> None:
    patch = ProposedPatch(
        status="ready",
        file="index.html",
        old_text="<span>Temporary</span>",
        new_text="",
        target="temporary span",
        summary="Remove the temporary span.",
    )

    assert patch.new_text == ""


def test_patch_rejects_empty_old_text() -> None:
    with pytest.raises(
        ValidationError,
        match="ready patch is missing required fields",
    ):
        ProposedPatch(
            status="ready",
            file="style.css",
            old_text="",
            new_text="color: red;",
            target="text color",
            summary="Change the text color.",
        )


def test_patch_rejects_identical_replacement() -> None:
    with pytest.raises(
        ValidationError,
        match="old_text and new_text must be different",
    ):
        ProposedPatch(
            status="ready",
            file="style.css",
            old_text="color: red;",
            new_text="color: red;",
            target="text color",
            summary="Change the text color.",
        )


def test_patch_accepts_unsupported_result() -> None:
    patch = ProposedPatch(
        status="unsupported",
        summary="JavaScript editing is outside the supported scope.",
        message="Only configured HTML and CSS files may be edited.",
    )

    assert patch.status == "unsupported"
    assert patch.file is None