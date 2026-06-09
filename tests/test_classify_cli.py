"""Tests for classify-markdown CLI command."""

from __future__ import annotations

from pathlib import Path

import pytest

from meritranker_data_ingestion.cli import main

SAMPLE_MD = """\
Q1. First question
(a) opt1
## Solutions
S1. Ans.(a)
"""


def _make_package(tmp_path: Path, markdown: str | None = SAMPLE_MD) -> Path:
    package = tmp_path / "extraction_package"
    marker_dir = package / "marker"
    marker_dir.mkdir(parents=True)
    if markdown is not None:
        (marker_dir / "raw.md").write_text(markdown, encoding="utf-8")
    return package


def test_cli_classify_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = _make_package(tmp_path)

    exit_code = main(["classify-markdown", "--package", str(package)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "succeeded" in captured.out
    assert (package / "classified" / "lines.json").exists()


def test_cli_classify_missing_raw_md(tmp_path: Path) -> None:
    package = _make_package(tmp_path, markdown=None)

    exit_code = main(["classify-markdown", "--package", str(package)])

    assert exit_code == 1


def test_cli_classify_missing_package(tmp_path: Path) -> None:
    exit_code = main(
        ["classify-markdown", "--package", str(tmp_path / "missing_package")],
    )
    assert exit_code == 1
