"""Tests for parse-question-candidates CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.cli import main
from meritranker_data_ingestion.schemas.classification import LineType, MarkdownLineRecord


def _line(n: int, raw: str, line_type: LineType, label: str | None = None) -> MarkdownLineRecord:
    return MarkdownLineRecord(
        line_number=n,
        raw_text=raw,
        normalized_preview=raw.strip(),
        line_type=line_type,
        detected_label=label,
        confidence=0.9,
    )


def _setup_package(tmp_path: Path, with_lines: bool = True) -> Path:
    package = tmp_path / "extraction_package"
    if with_lines:
        classified = package / "classified"
        classified.mkdir(parents=True)
        lines = [
            _line(1, "Q1. CLI test", LineType.QUESTION_ANCHOR, "Q1"),
            _line(2, "(a) opt", LineType.OPTION_CANDIDATE, "A"),
        ]
        (classified / "lines.json").write_text(
            json.dumps([ln.model_dump(mode="json") for ln in lines]),
            encoding="utf-8",
        )
        (classified / "blocks.json").write_text("[]", encoding="utf-8")
    else:
        package.mkdir(parents=True)
    return package


def test_cli_parse_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = _setup_package(tmp_path)
    exit_code = main(["parse-question-candidates", "--package", str(package)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "succeeded" in captured.out
    assert (package / "questions" / "question-candidates.json").exists()


def test_cli_parse_missing_classified(tmp_path: Path) -> None:
    package = _setup_package(tmp_path, with_lines=False)
    exit_code = main(["parse-question-candidates", "--package", str(package)])
    assert exit_code == 1


def test_cli_parse_missing_package(tmp_path: Path) -> None:
    exit_code = main(
        ["parse-question-candidates", "--package", str(tmp_path / "missing")],
    )
    assert exit_code == 1
