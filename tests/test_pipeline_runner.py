"""Tests for one-command pipeline runner (Part 8)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.schemas.answer_solution_mapping import MapperStatus
from meritranker_data_ingestion.schemas.classification import ClassificationStatus
from meritranker_data_ingestion.schemas.extraction import ExtractionStatus
from meritranker_data_ingestion.schemas.final_package_audit import AuditStatus
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionItem,
    FinalQuestionPackage,
    FinalQuestionSourceTrace,
    FinalQuestionValidationReport,
    FinalizeStatus,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.pipeline import PipelineRunStatus, PipelineStageStatus
from meritranker_data_ingestion.schemas.question_candidates import ParseStatus
from meritranker_data_ingestion.schemas.review_export import ReviewExportReport
from meritranker_data_ingestion.services.pipeline_runner import (
    PipelineError,
    PipelineOptions,
    build_pipeline_summary,
    run_pipeline,
)
from meritranker_data_ingestion.cli import build_parser, main


EXPECTED_STAGES = [
    "prepare",
    "classify-markdown",
    "inspect-raw-markdown",
    "parse-question-candidates",
    "map-answers-solutions",
    "build-final-package",
    "audit-final-package",
    "diagnose-question-coverage",
    "export-review-items",
    "build-ingestion-eligibility",
    "reconcile-artifacts",
    "build-pattern-input",
]


def _trace() -> FinalQuestionSourceTrace:
    return FinalQuestionSourceTrace(start_line=1, end_line=2, line_numbers=[1, 2])


def _final_item() -> FinalQuestionItem:
    from meritranker_data_ingestion.schemas.final_question_package import (
        FinalQuestionAnswer,
        FinalQuestionSolution,
    )

    return FinalQuestionItem(
        question_id="q_0001",
        question_number=1,
        question_number_raw="Q1",
        question_text_raw="What is 2+2?",
        raw_text="Q1. What is 2+2?",
        options=[],
        answer=FinalQuestionAnswer(available=False),
        solution=FinalQuestionSolution(available=False),
        assets=[],
        source_trace=_trace(),
        validation_status=ValidationStatus.VALIDATED,
        confidence=0.9,
        issues=[],
    )


def _mock_successes(output_dir: Path) -> dict:
    from meritranker_data_ingestion.schemas.answer_solution_mapping import (
        AnswerSolutionMappingResult,
    )
    from meritranker_data_ingestion.schemas.classification import MarkdownClassificationResult
    from meritranker_data_ingestion.schemas.extraction import ExtractionPackageManifest
    from meritranker_data_ingestion.schemas.final_package_audit import FinalPackageAuditReport
    from meritranker_data_ingestion.schemas.question_candidates import (
        QuestionCandidateParseResult,
    )

    package = FinalQuestionPackage(
        source_file_name="exam.pdf",
        parser_engine="marker",
        total_questions=1,
        valid_questions=1,
        items=[_final_item()],
    )
    validation = FinalQuestionValidationReport(
        status=FinalizeStatus.SUCCEEDED,
        total_questions=1,
        validated_count=1,
        needs_review_count=0,
        incomplete_count=0,
    )
    audit = FinalPackageAuditReport(
        status=AuditStatus.PASSED,
        total_questions=1,
        expected_question_count=100,
        expected_count_match=True,
        validated_count=1,
        needs_review_count=0,
        incomplete_count=0,
    )
    return {
        "manifest": ExtractionPackageManifest(
            input_pdf_path=Path("exam.pdf"),
            source_file_name="exam.pdf",
            output_dir=output_dir,
            parser_engine="marker",
            status=ExtractionStatus.SUCCEEDED,
        ),
        "classify": MarkdownClassificationResult(
            package_dir=str(output_dir / "extraction_package"),
            source_markdown="raw.md",
            status=ClassificationStatus.SUCCEEDED,
            total_lines=10,
            total_blocks=5,
            content_question_anchor_count=100,
        ),
        "parse": QuestionCandidateParseResult(
            package_dir=str(output_dir / "extraction_package"),
            status=ParseStatus.SUCCEEDED,
            total_candidates=100,
            valid_candidates=71,
        ),
        "map": AnswerSolutionMappingResult(
            package_dir=str(output_dir / "extraction_package"),
            status=MapperStatus.SUCCEEDED,
            total_question_candidates=100,
            mapped_count=99,
            content_lines_used=True,
        ),
        "package": package,
        "validation": validation,
        "audit": audit,
        "coverage": {
            "candidate_count": 100,
            "missing_at_candidate_stage": [],
        },
        "review": ReviewExportReport(
            package_dir=str(output_dir / "extraction_package"),
            total_final_questions=1,
            review_item_count=0,
        ),
    }


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "output"


def test_console_script_configured() -> None:
    import tomllib

    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    assert scripts.get("meritranker-ingest") == "meritranker_data_ingestion.cli:main"


def test_existing_commands_available() -> None:
    parser = build_parser()
    commands = {action.dest for action in parser._subparsers._actions if action.dest == "command"}
    # argparse stores subparser names on choices
    sub = next(a for a in parser._subparsers._actions if a.dest == "command")
    names = set(sub.choices.keys())
    for cmd in [
        "prepare",
        "classify-markdown",
        "inspect-raw-markdown",
        "parse-question-candidates",
        "map-answers-solutions",
        "build-final-package",
        "audit-final-package",
        "diagnose-question-coverage",
        "run-pipeline",
        "export-review-items",
        "build-ingestion-eligibility",
        "reconcile-artifacts",
        "build-pattern-input",
    ]:
        assert cmd in names


@patch("meritranker_data_ingestion.services.pipeline_runner.export_review_items_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.diagnose_question_coverage_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.audit_final_package_from_directory")
@patch("meritranker_data_ingestion.services.pipeline_runner.build_final_package_from_directory")
@patch("meritranker_data_ingestion.services.pipeline_runner.map_answers_solutions_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.parse_question_candidates_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.inspect_raw_markdown_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.classify_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.MarkerAdapter")
def test_run_pipeline_success_stage_order(
    mock_marker_cls: MagicMock,
    mock_classify: MagicMock,
    mock_inspect: MagicMock,
    mock_parse: MagicMock,
    mock_map: MagicMock,
    mock_build: MagicMock,
    mock_audit: MagicMock,
    mock_diagnose: MagicMock,
    mock_export: MagicMock,
    output_dir: Path,
) -> None:
    mocks = _mock_successes(output_dir)
    mock_marker_cls.return_value.extract.return_value = mocks["manifest"]
    mock_classify.return_value = mocks["classify"]
    mock_inspect.return_value = {"total_raw_lines": 100, "image_reference_count": 5}
    mock_parse.return_value = mocks["parse"]
    mock_map.return_value = mocks["map"]
    mock_build.return_value = (mocks["package"], mocks["validation"])
    mock_audit.return_value = mocks["audit"]
    mock_diagnose.return_value = mocks["coverage"]
    mock_export.return_value = mocks["review"]

    result = run_pipeline(
        PipelineOptions(
            input_pdf=Path("exam.pdf"),
            output_dir=output_dir,
            expected_count=100,
        ),
    )

    stage_names = [s.stage for s in result.stages]
    assert stage_names == EXPECTED_STAGES
    assert all(
        s.status in {PipelineStageStatus.SUCCEEDED, PipelineStageStatus.SKIPPED}
        for s in result.stages
    )
    assert result.status == PipelineRunStatus.SUCCEEDED

    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["expected_question_count"] == 100
    mock_diagnose.assert_called_once()
    assert mock_diagnose.call_args.kwargs["expected_question_count"] == 100


@patch("meritranker_data_ingestion.services.pipeline_runner.classify_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.MarkerAdapter")
def test_run_pipeline_stops_on_prepare_failure(
    mock_marker_cls: MagicMock,
    mock_classify: MagicMock,
    output_dir: Path,
) -> None:
    from meritranker_data_ingestion.schemas.extraction import ExtractionPackageManifest

    mock_marker_cls.return_value.extract.return_value = ExtractionPackageManifest(
        input_pdf_path=Path("exam.pdf"),
        source_file_name="exam.pdf",
        output_dir=output_dir,
        parser_engine="marker",
        status=ExtractionStatus.FAILED,
        errors=["marker not found"],
    )

    result = run_pipeline(
        PipelineOptions(
            input_pdf=Path("exam.pdf"),
            output_dir=output_dir,
        ),
    )

    assert result.status == PipelineRunStatus.FAILED
    assert len(result.stages) == 1
    assert result.stages[0].stage == "prepare"
    mock_classify.assert_not_called()


@patch("meritranker_data_ingestion.services.pipeline_runner.export_review_items_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.diagnose_question_coverage_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.audit_final_package_from_directory")
@patch("meritranker_data_ingestion.services.pipeline_runner.build_final_package_from_directory")
@patch("meritranker_data_ingestion.services.pipeline_runner.map_answers_solutions_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.parse_question_candidates_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.inspect_raw_markdown_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.classify_package")
@patch("meritranker_data_ingestion.services.pipeline_runner.MarkerAdapter")
def test_skip_audit(
    mock_marker_cls: MagicMock,
    mock_classify: MagicMock,
    mock_inspect: MagicMock,
    mock_parse: MagicMock,
    mock_map: MagicMock,
    mock_build: MagicMock,
    mock_audit: MagicMock,
    mock_diagnose: MagicMock,
    mock_export: MagicMock,
    output_dir: Path,
) -> None:
    mocks = _mock_successes(output_dir)
    mock_marker_cls.return_value.extract.return_value = mocks["manifest"]
    mock_classify.return_value = mocks["classify"]
    mock_inspect.return_value = {"total_raw_lines": 10, "image_reference_count": 0}
    mock_parse.return_value = mocks["parse"]
    mock_map.return_value = mocks["map"]
    mock_build.return_value = (mocks["package"], mocks["validation"])
    mock_diagnose.return_value = mocks["coverage"]
    mock_export.return_value = mocks["review"]

    result = run_pipeline(
        PipelineOptions(
            input_pdf=Path("exam.pdf"),
            output_dir=output_dir,
            expected_count=100,
            skip_audit=True,
        ),
    )

    audit_stage = next(s for s in result.stages if s.stage == "audit-final-package")
    assert audit_stage.status == PipelineStageStatus.SKIPPED
    mock_audit.assert_not_called()


def test_summary_does_not_contain_full_raw_text() -> None:
    long_text = "X" * 500
    item = _final_item()
    item.raw_text = long_text
    item.question_text_raw = long_text

    from meritranker_data_ingestion.schemas.pipeline import PipelineRunResult, PipelineStageResult

    result = PipelineRunResult(
        status=PipelineRunStatus.SUCCEEDED,
        package_dir="/tmp/pkg",
        output_dir="/tmp/out",
        stages=[PipelineStageResult(stage="prepare", status=PipelineStageStatus.SUCCEEDED)],
        summary={"total_questions": 1},
    )
    dumped = json.dumps(build_pipeline_summary(result))
    assert long_text not in dumped


def test_existing_package_without_force_raises(output_dir: Path) -> None:
    package_dir = output_dir / "extraction_package"
    package_dir.mkdir(parents=True)

    with pytest.raises(PipelineError, match="already exists"):
        run_pipeline(
            PipelineOptions(
                input_pdf=Path("exam.pdf"),
                output_dir=output_dir,
            ),
        )


@patch("meritranker_data_ingestion.cli.run_pipeline")
def test_cli_run_pipeline_exit_code(mock_run: MagicMock, tmp_path: Path, capsys) -> None:
    from meritranker_data_ingestion.schemas.pipeline import PipelineRunResult

    mock_run.return_value = PipelineRunResult(
        status=PipelineRunStatus.SUCCEEDED,
        package_dir=str(tmp_path / "extraction_package"),
        output_dir=str(tmp_path),
        summary={"total_questions": 1, "validated_count": 1},
    )

    code = main([
        "run-pipeline",
        "--input", "exam.pdf",
        "--output", str(tmp_path),
        "--expected-count", "100",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "total_questions" in out
    assert "X" * 200 not in out
