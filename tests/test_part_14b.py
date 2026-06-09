"""Tests for Part 14B OCR preflight + numeric options + question-only export."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    EXTRACTION_PACKAGE_DIR,
    OCR_DIR,
    OCR_EVIDENCE_JSON_NAME,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BOUND_QUESTIONS_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.ocr_evidence import OcrEvidencePackage
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
    SemanticBindingPackage,
)
from meritranker_data_ingestion.services.extraction_capability_router import (
    profile_extraction_capability,
)
from meritranker_data_ingestion.services.final_questions_export_builder import (
    build_final_questions_export,
)
from meritranker_data_ingestion.services.ocr_runtime_preflight import (
    resolve_ocr_used,
    run_ocr_runtime_preflight,
)
from meritranker_data_ingestion.services.option_key_normalizer import (
    parse_multiple_numeric_options,
    parse_numeric_option_line,
)
from meritranker_data_ingestion.services.response_sheet_option_parser import (
    parse_response_sheet_options_from_text,
)
from meritranker_data_ingestion.services.semantic_bad_item_guard import classify_bad_item
from meritranker_data_ingestion.services.semantic_final_acceptance_gate import (
    FinalGateStatus,
    evaluate_final_acceptance_gate,
)
from meritranker_data_ingestion.services.semantic_numeric_option_repair import (
    apply_numeric_option_repair,
)
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    SemanticPipelineError,
    SemanticPipelineOptions,
    run_semantic_pipeline,
)


def test_azure_ocr_missing_dependency_fails_preflight() -> None:
    with patch(
        "meritranker_data_ingestion.services.ocr_runtime_preflight._azure_dependency_available",
        return_value=False,
    ):
        result = run_ocr_runtime_preflight(engine="azure")
    assert result.strict_failure is True
    assert "azure_dependency_missing" in (result.ocr_failed_reason or "")


def test_auto_ocr_missing_falls_back_with_warning() -> None:
    with patch(
        "meritranker_data_ingestion.services.ocr_runtime_preflight._azure_dependency_available",
        return_value=False,
    ), patch(
        "meritranker_data_ingestion.services.ocr_runtime_preflight._paddle_dependency_available",
        return_value=False,
    ):
        result = run_ocr_runtime_preflight(engine="auto")
    assert result.strict_failure is False
    assert "ocr_unavailable_marker_only_fallback" in result.warnings


def test_ocr_used_false_when_line_count_zero() -> None:
    assert resolve_ocr_used(ocr_line_count=0, ocr_engines_used=[]) is False
    assert resolve_ocr_used(ocr_line_count=3, ocr_engines_used=["azure_ocr"]) is True


def test_numeric_option_normalizes_to_key_and_canonical() -> None:
    parsed = parse_numeric_option_line("1. कनाटक")
    assert parsed is not None
    assert parsed.key == "1"
    assert parsed.key_raw == "1."
    assert parsed.canonical_key == "A"
    assert parsed.option_index == 1
    assert parsed.text_raw == "कनाटक"


def test_multiple_numeric_options_in_table_line() -> None:
    text = "1. कनाटक 2. ओडशा 3. केरल 4. पंजाब"
    options = parse_multiple_numeric_options(text)
    assert len(options) == 4
    assert options[0].key == "1"
    assert options[3].canonical_key == "D"


def test_response_sheet_table_options_across_rows() -> None:
    line1 = "| Ans | 1. कनाटक<br>2. ओडशा<br>3. केरल |"
    line2 = "|     | 4. पंजाब |"
    opts1 = parse_response_sheet_options_from_text(line1)
    opts2 = parse_response_sheet_options_from_text(line2)
    keys = {opt.key for opt in opts1 + opts2}
    assert keys == {"1", "2", "3", "4"}


def test_question_only_source_backed_becomes_answer_unavailable() -> None:
    item = _question_with_numeric_options()
    item.answer = SemanticBoundAnswer(available=False, source_spans=[])
    item.binding_status = SemanticBindingItemStatus.REJECTED
    item.issues = ["missing_answer_answer_key_only_mode"]
    gate = evaluate_final_acceptance_gate(
        item,
        answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY,
    )
    assert gate.status == FinalGateStatus.ANSWER_UNAVAILABLE_EXPORT


def test_chosen_option_stored_separately_in_final_export(tmp_path: Path) -> None:
    pkg = _write_package_with_chosen(tmp_path)
    result = build_final_questions_export(pkg, answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY)
    item = result.package.items[0]
    assert item.chosen_option_key == "2"
    assert item.chosen_option_canonical_key == "B"
    assert item.correct_answer_key is None


def test_chosen_option_never_becomes_correct_answer(tmp_path: Path) -> None:
    pkg = _write_package_with_chosen(tmp_path)
    result = build_final_questions_export(pkg, answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY)
    assert result.package.chosen_option_as_correct_answer_count == 0


def test_final_report_includes_numeric_option_counts(tmp_path: Path) -> None:
    pkg = _write_repaired_package(tmp_path, [_question_with_numeric_options()])
    result = build_final_questions_export(pkg, answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY)
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["numeric_option_questions_count"] >= 1
    assert "ocr_used" in report
    assert report["ocr_used"] is False


def test_question_only_hallucination_answer_only_not_quarantined() -> None:
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="Q1",
        raw_text="Q1",
        options=[],
        answer=SemanticBoundAnswer(available=True, key="A", source_spans=[]),
        binding_status=SemanticBindingItemStatus.REJECTED,
        issues=["hallucinated_answer_text"],
    )
    classes = classify_bad_item(
        item,
        expected_count=100,
        answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY,
    )
    assert "hallucinated_question_text" not in classes


def test_pipeline_azure_preflight_fails_before_bind(tmp_path: Path) -> None:
    pdf = tmp_path / "exam.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    output = tmp_path / "output_aug"
    with patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.ExtractorOrchestrator",
    ) as mock_orch, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.normalize_evidence_package",
    ) as mock_norm, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.run_ocr_runtime_preflight",
    ) as mock_preflight, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.bind_semantically_package",
    ) as mock_bind:
        mock_orch.return_value.prepare.return_value = MagicMock(succeeded=True, package_manifest=MagicMock(source_file_name="exam.pdf"), extractor_manifest=MagicMock(source_file_name="exam.pdf"))
        mock_norm.return_value = MagicMock(package=MagicMock(extraction_status=MagicMock(value="succeeded")))
        mock_preflight.return_value = MagicMock(
            requested_engine="azure",
            effective_engine=None,
            ocr_available=False,
            ocr_failed_reason="azure_dependency_missing",
            warnings=[],
            strict_failure=True,
            azure_dependency_ok=False,
            paddle_dependency_ok=False,
            azure_config_ok=False,
        )
        with pytest.raises(SemanticPipelineError, match="OCR preflight failed"):
            run_semantic_pipeline(
                SemanticPipelineOptions(
                    input_pdf=pdf,
                    output_dir=output,
                    ocr_engine="azure",
                ),
            )
        mock_bind.assert_not_called()


def _question_with_numeric_options() -> SemanticBoundQuestion:
    span = [SourceSpan(extractor="marker", line_id="l1")]
    return SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** Question?",
        raw_text="**1.** Question?",
        options=[
            SemanticBoundOption(key="1", key_raw="1.", text_raw="कनाटक", source_spans=span),
            SemanticBoundOption(key="2", key_raw="2.", text_raw="ओडशा", source_spans=span),
            SemanticBoundOption(key="3", key_raw="3.", text_raw="केरल", source_spans=span),
            SemanticBoundOption(key="4", key_raw="4.", text_raw="पंजाब", source_spans=span),
        ],
        answer=SemanticBoundAnswer(available=False, source_spans=[]),
        source_spans=span,
        binding_status=SemanticBindingItemStatus.ACCEPTED,
    )


def _write_repaired_package(
    tmp_path: Path,
    items: list[SemanticBoundQuestion],
) -> Path:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    ev = pkg / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="test.pdf",
        primary_extractor="marker",
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=[],
    ).model_dump_json()
    (ev / DOCUMENT_EVIDENCE_JSON_NAME).write_text(
        DocumentEvidencePackage(
            package_version="1.0",
            source_file_name="test.pdf",
            primary_extractor="marker",
            extractors_used=["marker"],
            extraction_status=EvidenceExtractionStatus.SUCCEEDED,
            lines=[],
        ).model_dump_json(),
        encoding="utf-8",
    )
    (pkg / OCR_DIR).mkdir(parents=True, exist_ok=True)
    (pkg / OCR_DIR / OCR_EVIDENCE_JSON_NAME).write_text(
        OcrEvidencePackage(source_file_name="test.pdf").model_dump_json(),
        encoding="utf-8",
    )
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True, exist_ok=True)
    binding = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="test.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY,
        status=SemanticBindingStatus.SUCCEEDED,
        input_evidence_hash="hash",
        items=items,
    )
    (sem / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME).write_text(
        binding.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return pkg


def _write_package_with_chosen(tmp_path: Path) -> Path:
    pkg = _write_repaired_package(tmp_path, [_question_with_numeric_options()])
    evidence = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="test.pdf",
        primary_extractor="marker",
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=[
            EvidenceLine(
                line_id="l1",
                text_raw="**1.** Question?",
                normalized_preview="1. Question?",
                source_extractor="marker",
            ),
            EvidenceLine(
                line_id="l2",
                text_raw="Chosen Option : 2",
                normalized_preview="Chosen Option : 2",
                source_extractor="marker",
            ),
        ],
    )
    (pkg / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME).write_text(
        evidence.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return pkg


def test_numeric_option_repair_splits_combined_text() -> None:
    evidence = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="test.pdf",
        primary_extractor="marker",
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=[],
    )
    package = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="test.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY,
        status=SemanticBindingStatus.SUCCEEDED,
        input_evidence_hash="hash",
        items=[
            SemanticBoundQuestion(
                semantic_question_id="sq_0001",
                question_number=1,
                question_text_raw="Q",
                raw_text="Q",
                options=[
                    SemanticBoundOption(
                        key="",
                        key_raw="",
                        text_raw="1. कनाटक",
                        source_spans=[SourceSpan(extractor="marker", line_id="l1")],
                    ),
                ],
                answer=SemanticBoundAnswer(available=False, source_spans=[]),
                source_spans=[SourceSpan(extractor="marker", line_id="l1")],
                binding_status=SemanticBindingItemStatus.ACCEPTED,
            ),
        ],
    )
    stats = apply_numeric_option_repair(package, evidence)
    assert stats.options_split_count == 1
    assert package.items[0].options[0].key == "1"
    assert package.items[0].options[0].text_raw == "कनाटक"


def test_numeric_option_repair_expands_multi_option_blob() -> None:
    evidence = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="test.pdf",
        primary_extractor="marker",
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=[],
    )
    package = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="test.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY,
        status=SemanticBindingStatus.SUCCEEDED,
        input_evidence_hash="hash",
        items=[
            SemanticBoundQuestion(
                semantic_question_id="sq_0001",
                question_number=1,
                question_text_raw="Q",
                raw_text="Q",
                options=[
                    SemanticBoundOption(
                        key="",
                        key_raw="",
                        text_raw="1. कनाटक\n2. ओडशा\n3. केरल",
                        source_spans=[SourceSpan(extractor="marker", line_id="l1")],
                    ),
                    SemanticBoundOption(
                        key="",
                        key_raw="",
                        text_raw="4. पंजाब",
                        source_spans=[SourceSpan(extractor="marker", line_id="l2")],
                    ),
                ],
                answer=SemanticBoundAnswer(available=False, source_spans=[]),
                source_spans=[SourceSpan(extractor="marker", line_id="l1")],
                binding_status=SemanticBindingItemStatus.ACCEPTED,
            ),
        ],
    )
    stats = apply_numeric_option_repair(package, evidence)
    assert stats.options_split_count == 4
    keys = [opt.key for opt in package.items[0].options]
    assert keys == ["1", "2", "3", "4"]


def test_profile_ocr_used_false_without_lines(tmp_path: Path) -> None:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    ev = pkg / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / DOCUMENT_EVIDENCE_JSON_NAME).write_text(
        DocumentEvidencePackage(
            package_version="1.0",
            source_file_name="test.pdf",
            primary_extractor="marker",
            extractors_used=["marker"],
            extraction_status=EvidenceExtractionStatus.SUCCEEDED,
            lines=[],
        ).model_dump_json(),
        encoding="utf-8",
    )
    (pkg / OCR_DIR).mkdir(parents=True, exist_ok=True)
    (pkg / OCR_DIR / OCR_EVIDENCE_JSON_NAME).write_text(
        OcrEvidencePackage(source_file_name="test.pdf", ocr_engines_used=[]).model_dump_json(),
        encoding="utf-8",
    )
    result = profile_extraction_capability(pkg, ocr_line_count=0, ocr_engines_used=[])
    assert result.profile.ocr_used is False
