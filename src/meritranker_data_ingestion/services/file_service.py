"""Safe local file and path utilities."""

import shutil
from pathlib import Path

from meritranker_data_ingestion.config import PDF_SUFFIX


class PathValidationError(Exception):
    """Raised when input or output paths fail validation."""


def resolve_path(path: Path) -> Path:
    """Resolve a path, following symlinks to its canonical location."""
    return path.expanduser().resolve()


def validate_input_pdf(input_path: Path) -> Path:
    """
    Validate that input_path exists, is a regular file, and has a .pdf suffix.

    Returns the resolved absolute path.
    """
    resolved = resolve_path(input_path)

    if not resolved.exists():
        raise PathValidationError(f"Input file does not exist: {resolved}")

    if not resolved.is_file():
        raise PathValidationError(f"Input path is not a file: {resolved}")

    if resolved.suffix.lower() != PDF_SUFFIX:
        raise PathValidationError(
            f"Input file must have a {PDF_SUFFIX} extension, got: {resolved.suffix!r}",
        )

    return resolved


def validate_and_prepare_output_dir(output_path: Path) -> Path:
    """
    Validate and create the output directory.

    Returns the resolved absolute output directory path.
    """
    resolved = resolve_path(output_path)

    if resolved.exists() and not resolved.is_dir():
        raise PathValidationError(f"Output path exists but is not a directory: {resolved}")

    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def assert_output_contains(output_dir: Path, target: Path) -> Path:
    """
    Ensure target resolves inside output_dir (prevents writes outside sandbox).

    Returns the resolved target path.
    """
    resolved_output = resolve_path(output_dir)
    resolved_target = resolve_path(target)

    try:
        resolved_target.relative_to(resolved_output)
    except ValueError as exc:
        raise PathValidationError(
            f"Path {resolved_target} is outside output directory {resolved_output}",
        ) from exc

    return resolved_target


def copy_file_into_output(source: Path, destination: Path, output_dir: Path) -> Path:
    """
    Copy source file to destination, ensuring destination stays under output_dir.

    Uses shutil.copy2 for a byte-preserving copy (no content transformation).
    """
    resolved_source = resolve_path(source)
    assert_output_contains(output_dir, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved_source, destination)
    return resolve_path(destination)
