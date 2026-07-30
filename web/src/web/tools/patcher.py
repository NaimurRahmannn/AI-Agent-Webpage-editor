from __future__ import annotations

import difflib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from web.models import ProposedPatch
from web.settings import (
    ALLOWED_SOURCE_SUFFIXES,
    Settings,
    resolve_allowed_paths,
)


class PatchError(RuntimeError):
    """Base error for deterministic patch operations."""


class PatchValidationError(PatchError):
    """Raised when a proposed patch is unsafe or cannot be applied."""


class PatchSourceChangedError(PatchValidationError):
    """Raised when the target changes during patch preparation."""


class PatchBackupError(PatchError):
    """Raised when backup rotation or verification fails."""


class PatchWriteError(PatchError):
    """Raised when temporary writing or atomic replacement fails."""


@dataclass(frozen=True, slots=True)
class PatchApplicationResult:
    """Details of one successfully applied source replacement."""

    file: str
    summary: str
    backup_file: str
    diff: str


def _backup_path(target: Path, generation: int) -> Path:
    """
    Return the backup path for a generation.

    Generation zero is the newest backup:

    - style.css.bak
    - style.css.bak.1
    - style.css.bak.2
    """

    suffix = ".bak" if generation == 0 else f".bak.{generation}"
    return target.with_name(f"{target.name}{suffix}")


def resolve_patch_target(
    settings: Settings,
    patch: ProposedPatch,
) -> tuple[str, Path]:
    """Resolve one ready patch to one safe allowlisted source path."""

    if patch.status != "ready":
        raise PatchValidationError(
            "only a ready patch can be applied"
        )

    if patch.file is None:
        raise PatchValidationError("ready patch file is missing")

    relative_name = patch.file
    suffix = Path(relative_name).suffix.lower()

    if suffix not in ALLOWED_SOURCE_SUFFIXES:
        raise PatchValidationError(
            "patch file must have an .html or .css extension"
        )

    allowed_paths = resolve_allowed_paths(settings)

    if relative_name not in allowed_paths:
        raise PatchValidationError(
            f"patch file is not allowlisted: {relative_name}"
        )

    raw_target = settings.project_root / relative_name

    if raw_target.is_symlink():
        raise PatchValidationError(
            "symbolic-link source targets are not supported: "
            f"{relative_name}"
        )

    target = allowed_paths[relative_name]

    if not target.exists():
        raise PatchValidationError(
            f"target file does not exist: {relative_name}"
        )

    if not target.is_file():
        raise PatchValidationError(
            f"target path is not a regular file: {relative_name}"
        )

    return relative_name, target


def read_utf8_source(
    target: Path,
    relative_name: str,
) -> tuple[bytes, str]:
    """
    Read the exact source bytes and decode them as UTF-8.

    Reading bytes first preserves the existing line endings in portions
    of the file that are not replaced.
    """

    try:
        source_bytes = target.read_bytes()
    except OSError as exc:
        raise PatchValidationError(
            f"could not read target file {relative_name}: {exc}"
        ) from exc

    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchValidationError(
            f"target file is not valid UTF-8: {relative_name}"
        ) from exc

    return source_bytes, source_text


def find_unique_occurrence(
    source: str,
    old_text: str,
) -> int:
    """
    Return the position of the sole exact source match.

    The second search begins one character after the first match so that
    overlapping matches are also treated as ambiguous.
    """

    if old_text == "":
        raise PatchValidationError("old_text must not be empty")

    first_match = source.find(old_text)

    if first_match < 0:
        raise PatchValidationError("old_text matched zero times")

    second_match = source.find(old_text, first_match + 1)

    if second_match >= 0:
        raise PatchValidationError(
            "old_text matched more than once"
        )

    return first_match


def build_unified_diff(
    before: str,
    after: str,
    relative_name: str,
    context_lines: int = 3,
) -> str:
    """Build a concise unified diff for terminal output."""

    diff_lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{relative_name}",
        tofile=f"b/{relative_name}",
        n=context_lines,
    )

    rendered: list[str] = []

    for line in diff_lines:
        rendered.append(line)

        if line.endswith(("\n", "\r")):
            continue

        rendered.append("\n")

        if (
            line.startswith(("+", "-", " "))
            and not line.startswith(("+++", "---"))
        ):
            rendered.append(
                "\\ No newline at end of file\n"
            )

    return "".join(rendered).rstrip("\r\n")


def create_rotating_backup(
    target: Path,
    backup_limit: int,
) -> Path:
    """
    Rotate existing backups and copy the current target to `.bak`.

    For a limit of three, the retained files are:

    - file.bak
    - file.bak.1
    - file.bak.2
    """

    if backup_limit < 1:
        raise PatchBackupError(
            "backup_limit must be at least 1"
        )

    backup_paths = [
        _backup_path(target, generation)
        for generation in range(backup_limit)
    ]

    for backup_path in backup_paths:
        if backup_path.is_symlink():
            raise PatchBackupError(
                "backup path must not be a symbolic link: "
                f"{backup_path.name}"
            )

        if backup_path.exists() and not backup_path.is_file():
            raise PatchBackupError(
                "backup path is not a regular file: "
                f"{backup_path.name}"
            )

    try:
        for generation in range(
            backup_limit - 1,
            0,
            -1,
        ):
            previous = _backup_path(
                target,
                generation - 1,
            )
            destination = _backup_path(
                target,
                generation,
            )

            if previous.exists():
                os.replace(previous, destination)

        primary_backup = _backup_path(target, 0)
        shutil.copy2(target, primary_backup)
    except OSError as exc:
        raise PatchBackupError(
            f"could not create rotating backup "
            f"for {target.name}: {exc}"
        ) from exc

    return primary_backup


def verify_backup(
    backup: Path,
    expected_source: bytes,
) -> None:
    """
    Confirm that the backup contains the source that was validated.

    A mismatch means the source changed between the initial read and
    backup creation, or the backup was not copied correctly.
    """

    try:
        backup_bytes = backup.read_bytes()
    except OSError as exc:
        raise PatchBackupError(
            f"could not verify backup {backup.name}: {exc}"
        ) from exc

    if backup_bytes != expected_source:
        raise PatchSourceChangedError(
            "source changed before backup verification; "
            "patch was not written"
        )


def atomic_replace_text(
    target: Path,
    new_text: str,
    expected_source: bytes,
) -> None:
    """
    Write a same-directory temporary file and replace the target.

    The target is reread immediately before replacement. If its contents
    no longer match the previously validated source, the temporary file
    is deleted and the replacement is cancelled.
    """

    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.weft-",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

            temporary_file.write(
                new_text.encode("utf-8")
            )
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        original_mode = stat.S_IMODE(
            target.stat().st_mode
        )
        os.chmod(temporary_path, original_mode)

        try:
            current_source = target.read_bytes()
        except OSError as exc:
            raise PatchWriteError(
                f"could not re-read {target.name} "
                f"before replacement: {exc}"
            ) from exc

        if current_source != expected_source:
            raise PatchSourceChangedError(
                "source changed after validation; "
                "patch was not written"
            )

        os.replace(temporary_path, target)
        temporary_path = None

    except PatchError:
        raise

    except (OSError, UnicodeError) as exc:
        raise PatchWriteError(
            f"could not atomically replace "
            f"{target.name}: {exc}"
        ) from exc

    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                # The original source remains untouched even if
                # temporary-file cleanup fails.
                pass


def apply_patch(
    settings: Settings,
    patch: ProposedPatch,
    *,
    expected_source_text: str | None = None,
) -> PatchApplicationResult:
    """
    Validate, back up, and apply one exact unique replacement.

    When expected_source_text is supplied, the current source must still
    match the snapshot provided to the CrewAI agents. This prevents a patch
    generated from stale source from being applied.

    Validation and diff construction happen before backup creation.
    Source writing happens only after the backup has been verified.
    """

    relative_name, target = resolve_patch_target(
        settings,
        patch,
    )

    # These checks intentionally repeat model validation at the final
    # deterministic write boundary.
    if patch.old_text is None or patch.old_text == "":
        raise PatchValidationError(
            "ready patch old_text is missing"
        )

    if patch.new_text is None:
        raise PatchValidationError(
            "ready patch new_text is missing"
        )

    if patch.old_text == patch.new_text:
        raise PatchValidationError(
            "old_text and new_text must be different"
        )
    source_bytes, source_text = read_utf8_source(
    target,
    relative_name,
)

    if (
        expected_source_text is not None
        and source_text != expected_source_text
    ):
        raise PatchSourceChangedError(
            "source changed after it was supplied to the crew; "
            "patch was not written"
        )

    match_index = find_unique_occurrence(
        source_text,
        patch.old_text,
    )

    updated_text = (
        source_text[:match_index]
        + patch.new_text
        + source_text[
            match_index + len(patch.old_text) :
        ]
    )

    unified_diff = build_unified_diff(
        source_text,
        updated_text,
        relative_name,
    )

    backup = create_rotating_backup(
        target,
        settings.backup_limit,
    )

    verify_backup(
        backup,
        source_bytes,
    )

    atomic_replace_text(
        target,
        updated_text,
        expected_source=source_bytes,
    )

    backup_relative = backup.relative_to(
        settings.project_root
    )

    return PatchApplicationResult(
        file=relative_name,
        summary=patch.summary,
        backup_file=backup_relative.as_posix(),
        diff=unified_diff,
    )