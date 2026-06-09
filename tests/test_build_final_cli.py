"""Tests for build-final-package CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.cli import main
from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    MappingStatus,
    QuestionAnswerSolutionMapping,
)
from meritranker_data_ingestion.schemas.question_candidates import (
    CandidateReviewStatus,
    QuestionCandidate,
    QuestionOptionCandidate,
    QuestionSourceTrace,
)


def _setup_package(tmp_path: Path, *, with_mapping: bool = True) -> Path:
    package = tmp_path / "extraction_package"
    questions = package / "questions"
    questions.mkdir(parents=True)

    candidate = QuestionCandidate(
        question_id="q_0001",
        question_number=1,
        question_number_raw="Q1",
        raw_text="Q1. Test",
        question_text_raw="Q1. Test",
        options=[
            QuestionOptionCandidate(
                key="A", key_raw="(a)", text_raw="1", start_line=2, end_line=2, confidence=0.9,
            ),
        ],
        source_trace=QuestionSourceTrace(start_line=1, end_line=2, line_numbers=[1, 2]),
        confidence=0.9,
        review_status=CandidateReviewStatus.CANDIDATE_VALID,
    )
    (questions / "question-candidates.json").write_text(
        json.dumps([candidate.model_dump(mode="json")]),
        encoding="utf-8",
    )

    if with_mapping:
        mappings = package / "mappings"
        mappings.mkdir()
        mapping = QuestionAnswerSolutionMapping(
            question_id="q_0001",
            question_number=1,
            mapping_status=MappingStatus.NOT_AVAILABLE,
            confidence=0.9,
        )
        (mappings / "answer-solution-map.json").write_text(
            json.dumps([mapping.model_dump(mode="json")]),
            encoding="utf-8",
        )

    return package


def test_cli_build_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = _setup_package(tmp_path)
    exit_code = main(["build-final-package", "--package", str(package)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "succeeded" in captured.out
    assert (package / "final" / "questions.json").exists()


def test_cli_build_missing_candidates(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    package.mkdir()
    exit_code = main(["build-final-package", "--package", str(package)])
    assert exit_code == 1


def test_cli_build_missing_mapping(tmp_path: Path) -> None:
    package = _setup_package(tmp_path, with_mapping=False)
    exit_code = main(["build-final-package", "--package", str(package)])
    assert exit_code == 1
