"""Tests for Part 13J strict final acceptance gate."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    EXTRACTION_PACKAGE_DIR,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME,
    SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME,
    SEMANTIC_FINAL_DIR,
    SEMANTIC_FINAL_GATE_REPORT_NAME,
    SEMANTIC_FINAL_QUESTIONS_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import SourceSpan
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
    SemanticBindingPackage,
    SemanticBindingValidationReport,
)
from meritranker_data_ingestion.schemas.semantic_final_export import SemanticFinalExportMode
from meritranker_data_ingestion.services.semantic_binding_evaluation import (
    build_semantic_binding_evaluation,
)
from meritranker_data_ingestion.services.semantic_final_acceptance_gate import (
    FinalGateStatus,
    evaluate_final_acceptance_gate,
    sanitize_duplicate_warnings,
)
from meritranker_data_ingestion.services.semantic_final_export_builder import build_semantic_final_export
from meritranker_data_ingestion.services.semantic_review_exporter import export_semantic_review_items


def _span(line_id: str = "l1") -> list[SourceSpan]:
    return [SourceSpan(extractor="marker", line_id=line_id)]


def _good_mcq(
    qnum: int,
    *,
    answer_key: str = "B",
    status: SemanticBindingItemStatus = SemanticBindingItemStatus.ACCEPTED,
) -> SemanticBoundQuestion:
    opts = [
        SemanticBoundOption(
            key=k,
            key_raw=k,
            text_raw=f"option {k}",
            source_spans=_span(f"lo_{k}"),
        )
        for k in ("A", "B", "C", "D")
    ]
    return SemanticBoundQuestion(
        semantic_question_id=f"sq_{qnum:04d}",
        question_number=qnum,
        question_text_raw=f"**{qnum}.** What is the answer?",
        raw_text=f"**{qnum}.** What is the answer?",
        options=opts,
        answer=SemanticBoundAnswer(
            available=True,
            key=answer_key,
            key_raw=answer_key,
            source_spans=_span("la"),
        ),
        source_spans=_span("lq"),
        binding_status=status,
        issues=[],
    )


def _write_pkg(tmp_path: Path, items: list[SemanticBoundQuestion]) -> Path:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True, exist_ok=True)
    package = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="exam.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        input_evidence_hash="abc",
        status=SemanticBindingStatus.SUCCEEDED,
        items=items,
    )
    (sem / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME).write_text(
        package.model_dump_json(indent=2),
        encoding="utf-8",
    )
    eval_payload = {
        "expected_count": len(items),
        "semantic_item_count": len(items),
        "accepted_count": sum(1 for i in items if i.binding_status == SemanticBindingItemStatus.ACCEPTED),
        "review_required_count": 0,
        "rejected_count": 0,
        "hallucination_suspected_count": 0,
        "quality_status": "passed",
    }
    (sem / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME).write_text(
        json.dumps(eval_payload),
        encoding="utf-8",
    )
    return pkg


def test_empty_options_not_exported(tmp_path: Path) -> None:
    bad = _good_mcq(1)
    bad.options = [
        SemanticBoundOption(key="", key_raw="", text_raw="", source_spans=[]),
    ] * 4
    pkg = _write_pkg(tmp_path, [bad])
    result = build_semantic_final_export(pkg)
    assert result.package.exported_count == 0
    assert result.package.unsafe_previously_accepted_count == 1


def test_visual_question_routes_review_visual(tmp_path: Path) -> None:
    visual = SemanticBoundQuestion(
        semantic_question_id="sq_0012",
        question_number=12,
        question_text_raw="**12.** Which answer figure is the mirror image?",
        raw_text="**12.** Which answer figure is the mirror image?",
        options=[],
        answer=SemanticBoundAnswer(available=True, key="B", key_raw="B", source_spans=_span()),
        source_spans=_span(),
        binding_status=SemanticBindingItemStatus.ACCEPTED,
        issues=[],
    )
    gate = evaluate_final_acceptance_gate(visual, expected_count=100)
    assert gate.status == FinalGateStatus.REVIEW_VISUAL_REQUIRED


def test_fewer_than_four_options_routes_corrupt() -> None:
    item = _good_mcq(3)
    item.options = item.options[:2]
    gate = evaluate_final_acceptance_gate(item, expected_count=100)
    assert gate.status == FinalGateStatus.REVIEW_EVIDENCE_CORRUPT
    assert "incomplete_text_options" in gate.reasons


def test_correct_answer_text_unavailable_blocks_safe() -> None:
    item = _good_mcq(4, answer_key="B")
    item.options[1].text_raw = ""
    gate = evaluate_final_acceptance_gate(item, expected_count=100)
    assert gate.status != FinalGateStatus.ACCEPTED_SAFE
    assert "correct_answer_text_unavailable" in gate.gate_issues or "empty_option_key_or_text" in gate.reasons


def test_answer_key_not_in_options_blocks_safe() -> None:
    item = _good_mcq(5, answer_key="E")
    gate = evaluate_final_acceptance_gate(item, expected_count=100)
    assert gate.status == FinalGateStatus.REVIEW_EVIDENCE_CORRUPT


def test_quarantined_blocks_export(tmp_path: Path) -> None:
    item = _good_mcq(6)
    item.quarantine_status = "quarantined"
    item.excluded_from_export = True
    item.bad_item_classes = ["hallucinated_question_text"]
    pkg = _write_pkg(tmp_path, [item])
    result = build_semantic_final_export(pkg)
    assert result.package.exported_count == 0


def test_good_mcq_exports_accepted_safe(tmp_path: Path) -> None:
    pkg = _write_pkg(tmp_path, [_good_mcq(1), _good_mcq(2)])
    result = build_semantic_final_export(pkg)
    assert result.package.exported_count == 2
    assert result.package.accepted_safe_count == 2
    exported = json.loads((pkg / SEMANTIC_FINAL_DIR / SEMANTIC_FINAL_QUESTIONS_NAME).read_text())
    for item in exported["items"]:
        assert item["final_gate_status"] == "accepted_safe"
        assert item["correct_answer_text"] is not None
        assert all(opt["key"] and opt["text_raw"] for opt in item["options"])


def test_review_items_include_final_gate_status(tmp_path: Path) -> None:
    visual = SemanticBoundQuestion(
        semantic_question_id="sq_0024",
        question_number=24,
        question_text_raw="**24.** Select the figure from answer figures.",
        raw_text="**24.** Select the figure from answer figures.",
        options=[],
        answer=SemanticBoundAnswer(available=True, key="B", key_raw="B", source_spans=_span()),
        source_spans=_span(),
        binding_status=SemanticBindingItemStatus.ACCEPTED,
        issues=[],
    )
    pkg = _write_pkg(tmp_path, [_good_mcq(1), visual])
    result = export_semantic_review_items(pkg, expected_count=100)
    assert result.report.total_review_items == 1
    assert result.report.items[0].final_gate_status == "review_visual_required"


def test_final_gate_report_reconciles(tmp_path: Path) -> None:
    pkg = _write_pkg(tmp_path, [_good_mcq(1), _good_mcq(2)])
    result = build_semantic_final_export(pkg)
    gate = json.loads((pkg / SEMANTIC_FINAL_DIR / SEMANTIC_FINAL_GATE_REPORT_NAME).read_text())
    assert gate["accepted_safe_count"] == 2
    assert gate["exported_count"] == result.package.exported_count


def test_no_duplicate_warnings_when_none() -> None:
    validation = SemanticBindingValidationReport(duplicate_question_number_count=0)
    package = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="x.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        input_evidence_hash="a",
        status=SemanticBindingStatus.SUCCEEDED,
        items=[_good_mcq(1)],
        warnings=["duplicate_question_number:1", "duplicate_question_number:2"],
    )
    cleaned = sanitize_duplicate_warnings(
        package.warnings,
        duplicate_question_numbers=[],
        duplicate_count=0,
    )
    assert cleaned == []
    evaluation = build_semantic_binding_evaluation(
        package,
        validation,
        expected_count=1,
    )
    assert not any(w.startswith("duplicate_question_number:") for w in evaluation.warnings)


def test_duplicate_warning_when_real_duplicate() -> None:
    warnings = sanitize_duplicate_warnings(
        [],
        duplicate_question_numbers=[5],
        duplicate_count=1,
    )
    assert warnings == []
    package_warnings = ["duplicate_question_number:5"]
    kept = sanitize_duplicate_warnings(package_warnings, duplicate_question_numbers=[5], duplicate_count=1)
    assert kept == ["duplicate_question_number:5"]
