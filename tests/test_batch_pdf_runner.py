"""Tests for batch PDF folder runner and share logs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import EXTRACTION_PACKAGE_DIR, FINAL_QUESTIONS_DIR, FINAL_QUESTIONS_JSON_NAME
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionItemMetadata,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.services.batch_pdf_runner import (
    BATCH_RUN_LOG_JSONL_NAME,
    BATCH_SUMMARY_MD_NAME,
    BatchPdfRunnerOptions,
    find_pdfs,
    run_pdf_folder,
)
from meritranker_data_ingestion.services.pdf_stem import safe_pdf_stem
from meritranker_data_ingestion.services.pipeline_stage_tracker import PipelineStageEvent
from meritranker_data_ingestion.services.semantic_pipeline_runner import SemanticPipelineResult
from meritranker_data_ingestion.services.share_log_builder import ShareLogContext, build_share_log


def test_safe_pdf_stem_normalizes_name() -> None:
    assert safe_pdf_stem("SSC-CGL 2021 Aug.pdf") == "ssc-cgl_2021_aug"


def test_find_pdfs_from_folder(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "b.PDF").write_bytes(b"%PDF")
    (tmp_path / "note.txt").write_text("x")
    found = find_pdfs(tmp_path)
    assert len(found) == 2


def _four_options() -> list[FinalQuestionOption]:
    return [
        FinalQuestionOption(key=str(i), key_raw=f"{i}.", text_raw=f"Opt {i}")
        for i in range(1, 5)
    ]


def _pipeline_result(
    tmp_path: Path,
    *,
    output_dir: Path | None = None,
    with_final: bool = True,
) -> SemanticPipelineResult:
    root = output_dir or (tmp_path / "out")
    pkg = root / EXTRACTION_PACKAGE_DIR
    if with_final:
        fq_dir = pkg / FINAL_QUESTIONS_DIR
        fq_dir.mkdir(parents=True, exist_ok=True)
        fq_pkg = FinalQuestionsPackage(
            source_file_name="paper1.pdf",
            total_questions_detected=2,
            accepted_safe_count=1,
            review_required_count=1,
            items=[
                FinalQuestionItem(
                    final_question_id="fq_0001",
                    global_order=1,
                    question_number=1,
                    question_text_raw="Q1 text",
                    options=_four_options(),
                    source_trace={"question_line_ids": ["l1"], "provenance": ["marker"]},
                    quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
                    issues=[],
                ),
                FinalQuestionItem(
                    final_question_id="fq_0002",
                    global_order=2,
                    question_number=2,
                    question_text_raw="Q2 text",
                    options=[],
                    quality_status=FinalQuestionQualityStatus.REVIEW_REQUIRED,
                    issues=["missing_options"],
                ),
            ],
        )
        (fq_dir / FINAL_QUESTIONS_JSON_NAME).write_text(fq_pkg.model_dump_json(), encoding="utf-8")
    return SemanticPipelineResult(
        source_file_name="paper1.pdf",
        output_root=root,
        package_dir=pkg,
        expected_count=2,
        semantic_item_count=2,
        count_match=True,
        accepted_count=1,
        review_required_count=1,
        rejected_count=0,
        questions_with_4_options_count=0,
        answer_available_count=0,
        source_span_missing_count=0,
        answer_key_not_in_options_count=0,
        hallucination_suspected_count=0,
        semantic_quality_status="warning",
        final_export_quality_status=None,
        exported_count=None,
        excluded_count=None,
        bad_item_count=0,
        quarantined_item_count=0,
        accepted_safe_count=1,
        unsafe_previously_accepted_count=0,
        ready_for_full_paper_ingestion=None,
        ready_for_partial_accepted_ingestion=None,
        quality_status="warning",
        total_questions_detected=2,
        final_questions_accepted_safe_count=1,
        ocr_line_count=10,
        merged_evidence_line_count=20,
        effective_answer_mode="question-only",
        artifact_paths={},
        stage_events=[
            PipelineStageEvent(
                stage="prepare_marker",
                status="succeeded",
                duration_ms=100,
                key_result="paper1.pdf",
            ),
        ],
    )


@patch("meritranker_data_ingestion.services.batch_pdf_runner.run_semantic_pipeline")
def test_batch_creates_per_pdf_outputs(mock_run: MagicMock, tmp_path: Path) -> None:
    input_dir = tmp_path / "input_pdfs"
    output_dir = tmp_path / "batch_outputs"
    input_dir.mkdir()
    (input_dir / "paper1.pdf").write_bytes(b"%PDF")
    (input_dir / "clean_text.pdf").write_bytes(b"%PDF")

    def side_effect(options):
        result = _pipeline_result(tmp_path)
        result.output_root = options.output_dir
        result.package_dir = options.output_dir / EXTRACTION_PACKAGE_DIR
        fq_dir = result.package_dir / FINAL_QUESTIONS_DIR
        fq_dir.mkdir(parents=True, exist_ok=True)
        pkg = FinalQuestionsPackage(
            source_file_name=f"{options.output_dir.name}.pdf",
            total_questions_detected=1,
            accepted_safe_count=1,
            items=[
                FinalQuestionItem(
                    final_question_id="fq_0001",
                    global_order=1,
                    question_number=1,
                    question_text_raw="Q",
                    options=_four_options(),
                    source_trace={"question_line_ids": ["l1"], "provenance": ["marker"]},
                    quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
                ),
            ],
        )
        (fq_dir / FINAL_QUESTIONS_JSON_NAME).write_text(pkg.model_dump_json(), encoding="utf-8")
        return result

    mock_run.side_effect = side_effect

    result = run_pdf_folder(
        BatchPdfRunnerOptions(
            input_dir=input_dir,
            output_dir=output_dir,
            continue_on_error=True,
            clean_output=True,
        ),
    )

    assert result.summary_path.exists()
    assert result.log_path.exists()
    assert (output_dir / "paper1" / "paper1.questions.json").exists()
    assert (output_dir / "paper1" / "paper1.share-log.md").exists()
    assert (output_dir / "clean_text" / "clean_text.questions.json").exists()
    assert BATCH_SUMMARY_MD_NAME in result.summary_path.name


@patch("meritranker_data_ingestion.services.batch_pdf_runner.run_semantic_pipeline")
def test_continue_on_error_processes_second_pdf(mock_run: MagicMock, tmp_path: Path) -> None:
    input_dir = tmp_path / "input_pdfs"
    output_dir = tmp_path / "batch_outputs"
    input_dir.mkdir()
    (input_dir / "bad.pdf").write_bytes(b"%PDF")
    (input_dir / "good.pdf").write_bytes(b"%PDF")

    def side_effect(options):
        if options.input_pdf.name == "bad.pdf":
            from meritranker_data_ingestion.services.semantic_pipeline_runner import SemanticPipelineError

            raise SemanticPipelineError("prepare failed: marker missing")
        return _pipeline_result(tmp_path, output_dir=options.output_dir)

    mock_run.side_effect = side_effect
    result = run_pdf_folder(
        BatchPdfRunnerOptions(
            input_dir=input_dir,
            output_dir=output_dir,
            continue_on_error=True,
            clean_output=True,
        ),
    )
    assert len(result.items) == 2
    assert (output_dir / "bad" / "bad.share-log.md").exists()
    assert result.items[0].run_status == "failed"
    assert result.items[1].questions_json_path is not None


@patch("meritranker_data_ingestion.services.batch_pdf_runner.run_semantic_pipeline")
def test_no_overwrite_without_clean_output(mock_run: MagicMock, tmp_path: Path) -> None:
    input_dir = tmp_path / "input_pdfs"
    output_dir = tmp_path / "batch_outputs"
    input_dir.mkdir()
    (input_dir / "paper1.pdf").write_bytes(b"%PDF")
    existing = output_dir / "paper1"
    existing.mkdir(parents=True)
    (existing / "paper1.questions.json").write_text('{"old": true}', encoding="utf-8")

    result = run_pdf_folder(
        BatchPdfRunnerOptions(
            input_dir=input_dir,
            output_dir=output_dir,
            continue_on_error=True,
            clean_output=False,
        ),
    )
    mock_run.assert_not_called()
    assert result.items[0].error_message == "skipped_existing_output"
    assert json.loads((existing / "paper1.questions.json").read_text()) == {"old": True}


def test_share_log_includes_stage_timeline_and_metrics(tmp_path: Path) -> None:
    out = tmp_path / "paper1"
    out.mkdir()
    questions = out / "paper1.questions.json"
    pkg = FinalQuestionsPackage(
        source_file_name="paper1.pdf",
        total_questions_detected=1,
        accepted_safe_count=1,
        items=[
            FinalQuestionItem(
                final_question_id="fq_0001",
                global_order=1,
                question_number=1,
                question_text_raw="Sample",
                options=_four_options(),
                source_trace={"question_line_ids": ["l1"], "provenance": ["marker"]},
                quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
            ),
        ],
    )
    questions.write_text(pkg.model_dump_json(), encoding="utf-8")

    ctx = ShareLogContext(
        pdf_file_name="paper1.pdf",
        input_path=tmp_path / "paper1.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=12.5,
        pipeline_result=_pipeline_result(tmp_path),
        questions_json_path=questions,
        stage_events=[
            PipelineStageEvent(
                stage="ocr_extraction",
                status="succeeded",
                duration_ms=50,
                key_result="lines=10",
            ),
        ],
    )
    result = build_share_log(ctx)
    text = result.share_log_path.read_text(encoding="utf-8")
    assert "## 3. Stage Timeline" in text
    assert "ocr_extraction" in text
    assert "## 4. Key Metrics" in text
    assert "ready_count:" in text
    assert result.quality_verdict in {"full_ready", "partial_ready"}


def test_unsafe_accepted_item_marks_failed_verdict(tmp_path: Path) -> None:
    out = tmp_path / "bad"
    out.mkdir()
    questions = out / "bad.questions.json"
    pkg = FinalQuestionsPackage(
        source_file_name="bad.pdf",
        total_questions_detected=1,
        accepted_safe_count=1,
        items=[
            FinalQuestionItem(
                final_question_id="fq_0001",
                global_order=1,
                question_number=1,
                question_text_raw="",
                quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
                metadata=FinalQuestionItemMetadata(status="ready", review_issues=[]),
            ),
        ],
    )
    questions.write_text(pkg.model_dump_json(), encoding="utf-8")

    ctx = ShareLogContext(
        pdf_file_name="bad.pdf",
        input_path=tmp_path / "bad.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
        questions_json_path=questions,
    )
    result = build_share_log(ctx)
    assert result.quality_verdict == "failed"
    assert result.main_failure_reason in {
        "ready_quality_violation",
        "ready_with_incomplete_options",
    }


@patch("meritranker_data_ingestion.services.batch_pdf_runner.run_semantic_pipeline")
def test_batch_summary_uses_simplified_columns(mock_run: MagicMock, tmp_path: Path) -> None:
    input_dir = tmp_path / "input_pdfs"
    output_dir = tmp_path / "batch_outputs"
    input_dir.mkdir()
    (input_dir / "paper1.pdf").write_bytes(b"%PDF")
    mock_run.return_value = _pipeline_result(tmp_path)

    run_pdf_folder(
        BatchPdfRunnerOptions(
            input_dir=input_dir,
            output_dir=output_dir,
            continue_on_error=True,
        ),
    )
    summary = (output_dir / "batch-summary.md").read_text(encoding="utf-8")
    assert "layout_type_detected" in summary
    assert "public_json_audit" in summary
    assert "qb_ready" not in summary
    assert "pattern_ready" not in summary


@patch("meritranker_data_ingestion.services.batch_pdf_runner.run_semantic_pipeline")
def test_batch_jsonl_written(mock_run: MagicMock, tmp_path: Path) -> None:
    input_dir = tmp_path / "input_pdfs"
    output_dir = tmp_path / "batch_outputs"
    input_dir.mkdir()
    (input_dir / "paper1.pdf").write_bytes(b"%PDF")
    mock_run.return_value = _pipeline_result(tmp_path)

    run_pdf_folder(
        BatchPdfRunnerOptions(
            input_dir=input_dir,
            output_dir=output_dir,
            clean_output=True,
        ),
    )
    log_lines = (output_dir / BATCH_RUN_LOG_JSONL_NAME).read_text(encoding="utf-8").strip().splitlines()
    assert log_lines
    event = json.loads(log_lines[0])
    assert event["pdf_name"] == "paper1.pdf"
    assert "stage" in event
