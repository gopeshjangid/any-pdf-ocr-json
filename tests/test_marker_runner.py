"""Tests for Marker command builder."""

from pathlib import Path

import pytest

from meritranker_data_ingestion.services.marker_runner import build_marker_command


def test_build_marker_command_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MERITRANKER_MARKER_COMMAND", raising=False)
    cmd = build_marker_command(Path("/in/test.pdf"), Path("/out/work"))
    assert cmd[0] == "marker_single"
    assert cmd[1] == "/in/test.pdf"
    assert cmd[2] == "--output_dir"
    assert cmd[3] == "/out/work"


def test_build_marker_command_custom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERITRANKER_MARKER_COMMAND", "/custom/bin/marker_single")
    cmd = build_marker_command(Path("a.pdf"), Path("b"))
    assert cmd[0] == "/custom/bin/marker_single"
