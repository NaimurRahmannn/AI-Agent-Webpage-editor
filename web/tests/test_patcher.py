from __future__ import annotations

import pytest

from web.models import ProposedPatch
from web.settings import Settings
from web.tools.patcher import (
    PatchSourceChangedError,
    PatchValidationError,
    apply_patch,
)


def make_settings(
    tmp_path,
    allowed_files: tuple[str, ...] = (
        "index.html",
        "style.css",
    ),
) -> Settings:
    return Settings(
        project_root=tmp_path,
        allowed_files=allowed_files,
        backup_limit=3,
    )


def ready_patch(
    *,
    file: str = "style.css",
    old_text: str = "color: red;",
    new_text: str = "color: blue;",
) -> ProposedPatch:
    return ProposedPatch(
        status="ready",
        file=file,
        old_text=old_text,
        new_text=new_text,
        target="test target",
        summary="Apply the test change.",
    )


def test_rejects_non_ready_patch(tmp_path) -> None:
    patch = ProposedPatch(
        status="ambiguous",
        summary="The request is ambiguous.",
        message="Multiple targets could match.",
    )

    with pytest.raises(
        PatchValidationError,
        match="only a ready patch",
    ):
        apply_patch(
            make_settings(tmp_path),
            patch,
        )


def test_rejects_disallowed_html_file(tmp_path) -> None:
    other_file = tmp_path / "other.html"
    other_file.write_text(
        "<p>old</p>",
        encoding="utf-8",
    )

    with pytest.raises(
        PatchValidationError,
        match="not allowlisted",
    ):
        apply_patch(
            make_settings(tmp_path),
            ready_patch(
                file="other.html",
                old_text="old",
                new_text="new",
            ),
        )

    assert other_file.read_text(
        encoding="utf-8"
    ) == "<p>old</p>"


def test_rejects_javascript_file(tmp_path) -> None:
    with pytest.raises(
        PatchValidationError,
        match=r"\.html or \.css",
    ):
        apply_patch(
            make_settings(tmp_path),
            ready_patch(file="script.js"),
        )


def test_rejects_missing_target_file(tmp_path) -> None:
    with pytest.raises(
        PatchValidationError,
        match="does not exist",
    ):
        apply_patch(
            make_settings(tmp_path),
            ready_patch(),
        )


def test_rejects_directory_as_target(tmp_path) -> None:
    target = tmp_path / "style.css"
    target.mkdir()

    with pytest.raises(
        PatchValidationError,
        match="not a regular file",
    ):
        apply_patch(
            make_settings(tmp_path),
            ready_patch(),
        )


def test_rejects_symbolic_link_target(tmp_path) -> None:
    real_file = tmp_path / "real.css"
    real_file.write_text(
        "color: red;",
        encoding="utf-8",
    )

    linked_file = tmp_path / "style.css"

    try:
        linked_file.symlink_to(real_file)
    except OSError:
        pytest.skip(
            "symbolic links are unavailable "
            "in this test environment"
        )

    with pytest.raises(
        PatchValidationError,
        match="symbolic-link",
    ):
        apply_patch(
            make_settings(tmp_path),
            ready_patch(),
        )

    assert real_file.read_text(
        encoding="utf-8"
    ) == "color: red;"


def test_rejects_non_utf8_target(tmp_path) -> None:
    target = tmp_path / "style.css"
    original = b"\xff\xfe\x00"

    target.write_bytes(original)

    with pytest.raises(
        PatchValidationError,
        match="not valid UTF-8",
    ):
        apply_patch(
            make_settings(tmp_path),
            ready_patch(),
        )

    assert target.read_bytes() == original


def test_rejects_zero_matches_without_backup(
    tmp_path,
) -> None:
    target = tmp_path / "style.css"
    target.write_text(
        "color: green;",
        encoding="utf-8",
    )

    with pytest.raises(
        PatchValidationError,
        match="zero times",
    ):
        apply_patch(
            make_settings(tmp_path),
            ready_patch(),
        )

    assert target.read_text(
        encoding="utf-8"
    ) == "color: green;"

    assert not (
        tmp_path / "style.css.bak"
    ).exists()


def test_rejects_multiple_matches_without_backup(
    tmp_path,
) -> None:
    target = tmp_path / "style.css"
    original = (
        ".first {\n"
        "  color: red;\n"
        "}\n"
        "\n"
        ".second {\n"
        "  color: red;\n"
        "}\n"
    )

    target.write_text(
        original,
        encoding="utf-8",
    )

    with pytest.raises(
        PatchValidationError,
        match="more than once",
    ):
        apply_patch(
            make_settings(tmp_path),
            ready_patch(),
        )

    assert target.read_text(
        encoding="utf-8"
    ) == original

    assert not (
        tmp_path / "style.css.bak"
    ).exists()


def test_rejects_overlapping_matches(tmp_path) -> None:
    target = tmp_path / "style.css"
    target.write_text(
        "aaa",
        encoding="utf-8",
    )

    with pytest.raises(
        PatchValidationError,
        match="more than once",
    ):
        apply_patch(
            make_settings(tmp_path),
            ready_patch(
                old_text="aa",
                new_text="b",
            ),
        )

    assert target.read_text(
        encoding="utf-8"
    ) == "aaa"

    
def test_expected_snapshot_mismatch_creates_no_backup(
    tmp_path,
) -> None:
    target = tmp_path / "style.css"
    target.write_text(
        "color: green;",
        encoding="utf-8",
    )

    patch = ready_patch(
        old_text="color: green;",
        new_text="color: blue;",
    )

    with pytest.raises(
        PatchSourceChangedError,
        match="supplied to the crew",
    ):
        apply_patch(
            make_settings(tmp_path),
            patch,
            expected_source_text="color: red;",
        )

    assert target.read_text(
        encoding="utf-8"
    ) == "color: green;"

    assert not (
        tmp_path / "style.css.bak"
    ).exists()
