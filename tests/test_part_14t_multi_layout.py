"""Part 14T multi-layout stabilization tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from meritranker_data_ingestion.config import EXTRACTION_PACKAGE_DIR, FINAL_QUESTIONS_DIR, FINAL_QUESTIONS_JSON_NAME
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionItemMetadata,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.services.batch_summary_builder import (
    PART_14T_TABLE_HEADER,
    collect_batch_pdf_metrics,
    refresh_batch_summary_from_outputs,
)
from meritranker_data_ingestion.services.layout_type_classifier import (
    LAYOUT_PYQ_SOLVED,
    LAYOUT_RESPONSE_SHEET,
    classify_layout_type,
)
from meritranker_data_ingestion.services.public_questions_audit import audit_public_questions_json
from meritranker_data_ingestion.services.share_log_builder import ShareLogContext, build_share_log
from meritranker_data_ingestion.services.final_questions_public_serializer import write_public_questions_json


def _write_min_package(
    output_folder: Path,
    *,
    ready: int = 1,
    review: int = 0,
    visual: int = 0,
    blocked: int = 0,
    expected: int = 2,
    unsupported: bool = False,
    response_sheet: bool = False,
) -> Path:
    pkg_dir = output_folder / EXTRACTION_PACKAGE_DIR
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "evidence").mkdir(exist_ok=True)
    (pkg_dir / FINAL_QUESTIONS_DIR).mkdir(exist_ok=True)
    if unsupported:
        profile = {
            "unsupported_layout_detected": True,
            "response_sheet_markers_detected": response_sheet,
            "chosen_option_detected": response_sheet,
        }
        (pkg_dir / "evidence" / "extraction-capability-profile.json").write_text(
            json.dumps(profile),
            encoding="utf-8",
        )
        (pkg_dir / "evidence" / "question-windows.json").write_text(
            json.dumps({"total_windows": 200, **profile}),
            encoding="utf-8",
        )
    report = {
        "expected_count": expected,
        "public_question_count": expected,
        "ready_count": ready,
        "review_count": review,
        "raw_candidate_count": 180 if unsupported else expected,
        "extra_candidate_count": 80 if unsupported else 0,
        "duplicate_candidate_count": 0,
        "missing_question_count": 0,
        "answer_available_count": ready,
        "solution_available_count": ready,
        "incomplete_options_count": review,
    }
    (pkg_dir / FINAL_QUESTIONS_DIR / "final-questions-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )
    items: list[FinalQuestionItem] = []
    statuses = (
        ["ready"] * ready
        + ["review"] * review
        + ["visual_required"] * visual
        + ["blocked"] * blocked
    )
    for idx, status in enumerate(statuses[:expected], start=1):
        usable_opts = 4 if status == "ready" else (2 if status == "review" else 0)
        options = [
            FinalQuestionOption(key=label, key_raw=f"({label.lower()})", text_raw=f"opt {label}")
            for label in "ABCD"[:usable_opts]
        ]
        q_status = FinalQuestionQualityStatus.ACCEPTED_SAFE
        if status == "blocked":
            q_status = FinalQuestionQualityStatus.BLOCKED
        elif status == "review":
            q_status = FinalQuestionQualityStatus.REVIEW_REQUIRED
        elif status == "visual_required":
            q_status = FinalQuestionQualityStatus.VISUAL_REQUIRED
        items.append(
            FinalQuestionItem(
                final_question_id=f"Q{idx:03d}",
                global_order=idx,
                question_number=idx,
                question_text_raw=f"Question {idx}",
                options=options,
                source_trace={"question_line_ids": [f"l{idx}"], "provenance": ["test"]},
                quality_status=q_status,
                metadata=FinalQuestionItemMetadata(
                    status=status,
                    review_issues=["incomplete_options"] if status == "review" else [],
                ),
            ),
        )
    while len(items) < expected:
        n = len(items) + 1
        items.append(
            FinalQuestionItem(
                final_question_id=f"Q{n:03d}",
                global_order=n,
                question_number=n,
                question_text_raw="",
                options=[],
                quality_status=FinalQuestionQualityStatus.BLOCKED,
                metadata=FinalQuestionItemMetadata(
                    status="blocked",
                    review_issues=["question_missing_from_extraction"],
                ),
            ),
        )
    fq = FinalQuestionsPackage(
        source_file_name=f"{output_folder.name}.pdf",
        items=items,
        total_questions_detected=expected,
        ready_count=ready,
        review_count=review,
        visual_required_count=visual,
        blocked_count=blocked,
    )
    (pkg_dir / FINAL_QUESTIONS_DIR / FINAL_QUESTIONS_JSON_NAME).write_text(
        fq.model_dump_json(),
        encoding="utf-8",
    )
    questions_path = output_folder / f"{output_folder.name}.questions.json"
    write_public_questions_json(fq, questions_path)
    return questions_path


def test_layout_classifier_response_sheet(tmp_path: Path) -> None:
    out = tmp_path / "ssc_ocr"
    out.mkdir()
    _write_min_package(out, ready=1, blocked=1, expected=2, unsupported=True, response_sheet=True)
    metrics = {
        "expected_count": 2,
        "raw_candidate_count": 180,
        "extra_candidate_count": 80,
        "question_window_count": 200,
        "chosen_option_detected_count": 1,
        "ready_count": 1,
        "blocked_count": 1,
        "total_questions_detected": 2,
        "ocr_used": True,
    }
    assert classify_layout_type(metrics, package_dir=out / EXTRACTION_PACKAGE_DIR) == LAYOUT_RESPONSE_SHEET


def test_layout_classifier_pyq_solved(tmp_path: Path) -> None:
    metrics = {
        "expected_count": 100,
        "raw_candidate_count": 101,
        "extra_candidate_count": 1,
        "question_window_count": 104,
        "solution_window_count": 91,
        "ready_count": 85,
        "total_questions_detected": 100,
    }
    assert classify_layout_type(metrics) == LAYOUT_PYQ_SOLVED


def test_batch_summary_contains_part_14t_columns(tmp_path: Path) -> None:
    batch_dir = tmp_path / "batch_outputs"
    batch_dir.mkdir()
    for name in ("pdf_a", "pdf_b"):
        folder = batch_dir / name
        folder.mkdir()
        _write_min_package(folder, ready=2, expected=2)
    path = refresh_batch_summary_from_outputs(batch_dir, expected_count=2)
    text = path.read_text(encoding="utf-8")
    assert "layout_type_detected" in text
    assert "ready_percentage" in text
    assert "public_json_audit" in text
    assert "raw_candidate_count" in text
    assert PART_14T_TABLE_HEADER in text
    assert text.count("PASS") >= 2


def test_public_json_audit_runs_for_batch_packages(tmp_path: Path) -> None:
    out = tmp_path / "paper"
    out.mkdir()
    questions = _write_min_package(out, ready=2, expected=2)
    row = collect_batch_pdf_metrics(out, expected_count=2)
    assert row.public_json_audit == "PASS"
    result = audit_public_questions_json(questions, expected_count=2)
    assert result.passed


def test_bullet_bold_option_line_parses_after_list_prefix_fix() -> None:
    from meritranker_data_ingestion.services.deterministic_option_parser import (
        _parse_options_from_line_text,
        _dummy_line,
    )

    opts = _parse_options_from_line_text("- **A** EROFNGI", _dummy_line("- **A** EROFNGI"))
    assert len(opts) == 1
    assert opts[0].canonical_key == "A"
    assert opts[0].text_raw == "EROFNGI"


def test_repeated_numbering_alone_not_unsupported() -> None:
    from meritranker_data_ingestion.schemas.document_evidence import (
        DocumentEvidencePackage,
        EvidenceExtractionStatus,
        EvidenceLine,
    )
    from meritranker_data_ingestion.services.unsupported_layout_detector import detect_unsupported_layout

    lines = [EvidenceLine(line_id=f"l{i}", text_raw=f"Q.{1 if i % 20 == 0 else 2} text {i}", normalized_preview="x", source_extractor="marker") for i in range(50)]
    lines[0] = lines[0].model_copy(update={"text_raw": "Q.1 first"})
    lines[25] = lines[25].model_copy(update={"text_raw": "Q.1 repeated far"})
    evidence = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="t.pdf",
        primary_extractor="marker",
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=lines,
    )
    result = detect_unsupported_layout(evidence, answer_source_mode="chosen_option_metadata_only")
    assert result.unsupported_layout_detected is False
    assert 1 in result.repeated_question_numbers


def test_ready_not_downgraded_by_answer_only_issues() -> None:
    from meritranker_data_ingestion.services.issue_severity_resolver import is_blocking_extraction_issue

    assert not is_blocking_extraction_issue("expected_answer_missing")
    assert not is_blocking_extraction_issue("expected_solution_missing")
    assert is_blocking_extraction_issue("incomplete_options")


def test_share_log_includes_layout_and_audit(tmp_path: Path) -> None:
    out = tmp_path / "paper"
    out.mkdir()
    questions = _write_min_package(out, ready=2, expected=2)
    ctx = ShareLogContext(
        pdf_file_name="paper.pdf",
        input_path=tmp_path / "paper.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
        questions_json_path=questions,
    )
    result = build_share_log(ctx)
    assert result.metrics["layout_type_detected"] in {LAYOUT_PYQ_SOLVED, "pyq_standard_mcq"}
    assert result.metrics["public_json_audit"] == "PASS"
    text = result.share_log_path.read_text(encoding="utf-8")
    assert "layout_type_detected" in text
    assert "public_json_audit" in text


def test_share_log_problem_samples_when_extra_candidates(tmp_path: Path) -> None:
    out = tmp_path / "ocr_sheet"
    out.mkdir()
    questions = _write_min_package(out, ready=1, blocked=1, expected=2, unsupported=True, response_sheet=True)
    ctx = ShareLogContext(
        pdf_file_name="ocr.pdf",
        input_path=tmp_path / "ocr.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
        questions_json_path=questions,
    )
    result = build_share_log(ctx)
    text = result.share_log_path.read_text(encoding="utf-8")
    assert "## 9. Problem Samples" in text
    assert "blocked" in text.lower() or "Q00" in text


def test_response_sheet_main_issue_over_over_detection(tmp_path: Path) -> None:
    out = tmp_path / "ocr_sheet"
    out.mkdir()
    questions = _write_min_package(out, ready=1, blocked=1, expected=2, unsupported=True, response_sheet=True)
    ctx = ShareLogContext(
        pdf_file_name="ocr.pdf",
        input_path=tmp_path / "ocr.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
        questions_json_path=questions,
    )
    result = build_share_log(ctx)
    assert result.main_failure_reason == "response_sheet_layout"
    assert result.metrics["layout_type_detected"] == LAYOUT_RESPONSE_SHEET


def test_replay_finalization_does_not_run_marker_or_llm() -> None:
    source = Path(
        "src/meritranker_data_ingestion/services/finalization_replay.py",
    ).read_text(encoding="utf-8")
    forbidden = (
        "run_semantic_pipeline",
        "run_marker",
        "run_ocr",
        "bind_semantically",
        "semantic_binder",
    )
    for token in forbidden:
        assert token not in source


def test_batch_runner_writes_part_14t_summary(tmp_path: Path) -> None:
    batch_dir = tmp_path / "batch_outputs"
    batch_dir.mkdir()
    for name in ("alpha", "beta"):
        folder = batch_dir / name
        folder.mkdir()
        _write_min_package(folder, ready=2, expected=2)
    path = refresh_batch_summary_from_outputs(batch_dir, expected_count=2, subtitle="batch runner metrics")
    summary = path.read_text(encoding="utf-8")
    assert "layout_type_detected" in summary
    assert "public_json_audit" in summary
    assert "alpha.pdf" in summary
