"""Tests for audit-final-package CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.cli import main
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionAnswer,
    FinalQuestionItem,
    FinalQuestionPackage,
    FinalQuestionSolution,
    FinalQuestionSourceTrace,
    FinalQuestionValidationReport,
    FinalizeStatus,
    ValidationStatus,
)


def _trace() -> FinalQuestionSourceTrace:
    return FinalQuestionSourceTrace(start_line=1, end_line=2, line_numbers=[1, 2])


def _write_final(package_dir: Path) -> Path:
    final = package_dir / "final"
    final.mkdir(parents=True)
    item = FinalQuestionItem(
        question_id="q_0001",
        question_number=1,
        question_number_raw="Q1",
        question_text_raw="Q1. Test",
        raw_text="Q1. Test",
        answer=FinalQuestionAnswer(available=False),
        solution=FinalQuestionSolution(available=False),
        source_trace=_trace(),
        validation_status=ValidationStatus.QUESTION_ONLY_VALIDATED,
        confidence=0.9,
    )
    package = FinalQuestionPackage(
        source_file_name="test.pdf",
        total_questions=1,
        valid_questions=1,
        question_only_count=1,
        items=[item],
    )
    validation = FinalQuestionValidationReport(
        status=FinalizeStatus.SUCCEEDED,
        total_questions=1,
        question_only_validated_count=1,
    )
    (final / "questions.json").write_text(
        json.dumps(package.model_dump(mode="json")),
        encoding="utf-8",
    )
    (final / "validation-report.json").write_text(
        json.dumps(validation.model_dump(mode="json")),
        encoding="utf-8",
    )
    return package_dir


def test_cli_audit_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = _write_final(tmp_path / "extraction_package")
    exit_code = main(["audit-final-package", "--package", str(package)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "passed" in captured.out
    assert (package / "audit" / "final-package-audit.json").exists()
    assert (package / "audit" / "final-package-audit.md").exists()


def test_cli_audit_missing_final(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    package.mkdir()
    exit_code = main(["audit-final-package", "--package", str(package)])
    assert exit_code == 1


def test_cli_audit_expected_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = _write_final(tmp_path / "extraction_package")
    exit_code = main([
        "audit-final-package",
        "--package", str(package),
        "--expected-count", "1",
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert '"expected_count_match": true' in captured.out
