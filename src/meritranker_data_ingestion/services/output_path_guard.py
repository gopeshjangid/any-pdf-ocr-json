"""Safe output path validation for destructive cleanup (Part 13G)."""

from __future__ import annotations

from pathlib import Path


class OutputPathGuardError(Exception):
    """Raised when an output path is unsafe for cleanup."""


def validate_clean_output_path(output_dir: Path) -> Path:
    """Validate that output_dir is safe to delete with --clean-output."""
    if not str(output_dir).strip():
        raise OutputPathGuardError("Output path is empty.")

    resolved = output_dir.expanduser().resolve()

    if resolved == Path("/").resolve():
        raise OutputPathGuardError("Refusing to delete filesystem root '/'.")

    home = Path.home().resolve()
    if resolved == home:
        raise OutputPathGuardError("Refusing to delete user home directory.")

    project_root = Path(__file__).resolve().parents[2]
    if resolved == project_root:
        raise OutputPathGuardError("Refusing to delete project root directory.")

    if not resolved.name or resolved.name in {".", ".."}:
        raise OutputPathGuardError(f"Unsafe output path: {resolved}")

    return resolved


def clean_output_directory(output_dir: Path) -> Path:
    """Delete output directory after safety checks."""
    import shutil

    safe = validate_clean_output_path(output_dir)
    if safe.exists():
        shutil.rmtree(safe)
    safe.mkdir(parents=True, exist_ok=True)
    return safe
