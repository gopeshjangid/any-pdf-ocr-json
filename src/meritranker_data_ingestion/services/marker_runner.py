"""Subprocess runner for external Marker CLI — no Marker Python imports."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from meritranker_data_ingestion.config import (
    MARKER_OUTPUT_DIR_FLAG,
    get_marker_command_base,
)


class MarkerRunResult:
    """Result of a Marker subprocess invocation."""

    def __init__(
        self,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        command: list[str],
        error_message: str | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.command = command
        self.error_message = error_message


class MarkerCommandRunner(Protocol):
    """Injectable runner for tests."""

    def run(
        self,
        command: list[str],
        *,
        log_path: Path,
    ) -> MarkerRunResult:
        ...


def build_marker_command(input_pdf: Path, marker_output_dir: Path) -> list[str]:
    """
    Build Marker CLI argv without shell interpolation.

    Default: marker_single <pdf> --output_dir <dir>
    Override executable via MERITRANKER_MARKER_COMMAND (shlex-split).
    """
    base = shlex.split(get_marker_command_base())
    return [
        *base,
        str(input_pdf),
        MARKER_OUTPUT_DIR_FLAG,
        str(marker_output_dir),
    ]


def default_marker_runner(
    command: list[str],
    *,
    log_path: Path,
) -> MarkerRunResult:
    """Execute Marker via subprocess; capture output to log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = f"Command: {' '.join(command)}\n\n"

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        log_path.write_text(
            header + f"Error: executable not found: {command[0]}\n",
            encoding="utf-8",
        )
        return MarkerRunResult(
            returncode=127,
            stdout="",
            stderr=str(exc),
            command=command,
            error_message=(
                f"Marker command not found: {command[0]!r}. "
                f"Install marker-pdf (pip install marker-pdf) or set "
                f"MERITRANKER_MARKER_COMMAND to the correct executable."
            ),
        )

    log_body = (
        header
        + f"Return code: {completed.returncode}\n\n"
        + "--- stdout ---\n"
        + (completed.stdout or "")
        + "\n--- stderr ---\n"
        + (completed.stderr or "")
    )
    log_path.write_text(log_body, encoding="utf-8")

    return MarkerRunResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        command=command,
    )


class SubprocessMarkerRunner:
    """Default MarkerCommandRunner implementation."""

    def run(
        self,
        command: list[str],
        *,
        log_path: Path,
    ) -> MarkerRunResult:
        return default_marker_runner(command, log_path=log_path)


RunnerFactory = Callable[[], MarkerCommandRunner]
