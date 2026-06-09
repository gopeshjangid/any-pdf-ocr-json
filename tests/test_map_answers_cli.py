"""Tests for map-answers-solutions CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.cli import main
from meritranker_data_ingestion.schemas.classification import LineType, MarkdownLineRecord
from meritranker_data_ingestion.schemas.question_candidates import (
    CandidateReviewStatus,
    QuestionCandidate,
    QuestionSourceTrace,
)


def _setup_package(tmp_path: Path, *, with_candidates: bool = True) -> Path:
    package = tmp_path / "extraction_package"
    classified = package / "classified"
    classified.mkdir(parents=True)

    lines = [
        MarkdownLineRecord(
            line_number=1,
            raw_text="Q1. Test",
            normalized_preview="Q1. Test",
            line_type=LineType.QUESTION_ANCHOR,
            detected_label="Q1",
            confidence=0.9,
        ),
        MarkdownLineRecord(
            line_number=2,
            raw_text="Solutions",
            normalized_preview="Solutions",
            line_type=LineType.SOLUTION_SECTION_HEADING,
            confidence=0.9,
        ),
        MarkdownLineRecord(
            line_number=3,
            raw_text="S1. Ans.(a) sol",
            normalized_preview="S1. Ans.(a) sol",
            line_type=LineType.SOLUTION_ANCHOR,
            detected_label="S1",
            confidence=0.9,
        ),
    ]
    (classified / "lines.json").write_text(
        json.dumps([ln.model_dump(mode="json") for ln in lines]),
        encoding="utf-8",
    )
    (classified / "blocks.json").write_text("[]", encoding="utf-8")

    if with_candidates:
        questions = package / "questions"
        questions.mkdir()
        candidate = QuestionCandidate(
            question_id="q_0001",
            question_number=1,
            question_number_raw="Q1",
            raw_text="Q1. Test",
            question_text_raw="Q1. Test",
            source_trace=QuestionSourceTrace(start_line=1, end_line=1, line_numbers=[1]),
            confidence=0.9,
            review_status=CandidateReviewStatus.CANDIDATE_VALID,
        )
        (questions / "question-candidates.json").write_text(
            json.dumps([candidate.model_dump(mode="json")]),
            encoding="utf-8",
        )

    return package


def test_cli_map_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = _setup_package(tmp_path)
    exit_code = main(["map-answers-solutions", "--package", str(package)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "succeeded" in captured.out
    assert (package / "mappings" / "answer-solution-map.json").exists()


def test_cli_map_missing_candidates(tmp_path: Path) -> None:
    package = _setup_package(tmp_path, with_candidates=False)
    exit_code = main(["map-answers-solutions", "--package", str(package)])
    assert exit_code == 1


def test_cli_map_missing_package(tmp_path: Path) -> None:
    exit_code = main(["map-answers-solutions", "--package", str(tmp_path / "missing")])
    assert exit_code == 1
