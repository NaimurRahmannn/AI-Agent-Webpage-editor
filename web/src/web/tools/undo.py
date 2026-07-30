"""Deterministic, safe undo capability for Phase 10.

Restores configured allowlisted source files from rotating backups using atomic
replacement and reverse unified diff rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from web.settings import ALLOWED_SOURCE_SUFFIXES, Settings, resolve_allowed_paths
from web.tools.patcher import (
    _backup_path,
    atomic_replace_text,
    build_unified_diff,
    create_rotating_backup,
    read_utf8_source,
    verify_backup,
)
from web.tools.syntax_validator import validate_source_syntax


class UndoError(RuntimeError):
    """Base error for undo operations."""


class UndoTargetError(UndoError):
    """Raised when the undo target cannot be resolved or is invalid."""


class UndoBackupNotFoundError(UndoError):
    """Raised when no backup exists for the requested target file."""


class UndoValidationError(UndoError):
    """Raised when backup syntax validation or path checks fail."""


class UndoSourceChangedError(UndoError):
    """Raised when source file changes during undo execution."""


class UndoWriteError(UndoError):
    """Raised when atomic replacement during undo fails."""


@dataclass(frozen=True, slots=True)
class UndoResult:
    """Details of a completed undo restoration."""

    file: str
    restored_from: str
    backup_file: str
    diff: str
    summary: str


def perform_undo(
    settings: Settings,
    target_file: str,
) -> UndoResult:
    """
    Safely undo the last modification to an allowlisted file using backups.

    1. Resolves target through allowlist.
    2. Finds newest backup (.bak).
    3. Validates backup UTF-8 content and syntax.
    4. Constructs reverse unified diff (current -> backup).
    5. Rotates existing backups so pre-undo source becomes the new .bak.
    6. Atomically replaces target with backup content.
    """

    relative_name = target_file.strip()
    suffix = Path(relative_name).suffix.lower()

    if suffix not in ALLOWED_SOURCE_SUFFIXES:
        raise UndoTargetError(f"undo target must be HTML or CSS: {relative_name}")

    allowed_paths = resolve_allowed_paths(settings)
    if relative_name not in allowed_paths:
        raise UndoTargetError(f"undo target file is not allowlisted: {relative_name}")

    target = allowed_paths[relative_name]
    raw_target = settings.project_root / relative_name

    if raw_target.is_symlink():
        raise UndoTargetError(f"symbolic-link targets not supported: {relative_name}")

    if not target.exists() or not target.is_file():
        raise UndoTargetError(f"target file does not exist or is not a regular file: {relative_name}")

    newest_backup = _backup_path(target, 0)
    if newest_backup.is_symlink():
        raise UndoValidationError(f"backup file is a symbolic link: {newest_backup.name}")

    if not newest_backup.exists() or not newest_backup.is_file():
        raise UndoBackupNotFoundError(f"no backup found for {relative_name} ({newest_backup.name})")

    curr_bytes, curr_text = read_utf8_source(target, relative_name)
    bak_bytes, bak_text = read_utf8_source(newest_backup, newest_backup.name)

    syntax_res = validate_source_syntax(
        filename=relative_name,
        content=bak_text,
        settings=settings,
    )

    if not syntax_res.valid:
        issue_msg = syntax_res.issues[0].message if syntax_res.issues else "invalid syntax"
        raise UndoValidationError(
            f"Backup syntax validation failed for {relative_name}: {issue_msg}"
        )

    reverse_diff = build_unified_diff(
        before=curr_text,
        after=bak_text,
        relative_name=relative_name,
    )

    # Rotate backups: current target source becomes the new .bak
    new_primary_backup = create_rotating_backup(target, settings.backup_limit)
    verify_backup(new_primary_backup, curr_bytes)

    atomic_replace_text(
        target=target,
        new_text=bak_text,
        expected_source=curr_bytes,
    )

    summary = f"Restored {relative_name} from {newest_backup.name}"

    return UndoResult(
        file=relative_name,
        restored_from=newest_backup.name,
        backup_file=new_primary_backup.name,
        diff=reverse_diff,
        summary=summary,
    )
