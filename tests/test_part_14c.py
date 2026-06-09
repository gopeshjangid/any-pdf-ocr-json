"""Tests for Part 14C OCR split + local question windows."""

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
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.question_window import QuestionWindowsPackage, QuestionWindow
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingPackage,
    SemanticBindingStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
)
from meritranker_data_ingestion.services.azure_ocr_adapter import (
    AzurePageOcrAttempt,
    AzurePageOcrStatus,
)
from meritranker_data_ingestion.services.extraction_capability_router import profile_extraction_capability
from meritranker_data_ingestion.services.ocr_evidence_builder import OcrEvidenceError, build_ocr_evidence_package
from meritranker_data_ingestion.services.ocr_input_sizer import is_invalid_content_length_error
from meritranker_data_ingestion.services.ocr_runtime_preflight import resolve_ocr_used
from meritranker_data_ingestion.services.question_window_builder import (
    build_question_windows,
    window_line_ids_for_question,
)
from meritranker_data_ingestion.services.semantic_numeric_option_repair import apply_numeric_option_repair
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    SemanticPipelineError,
    SemanticPipelineOptions,
    run_semantic_pipeline,
)
from meritranker_data_ingestion.services.semantic_source_span_resolver import resolve_source_spans
from meritranker_data_ingestion.services.unsupported_layout_detector import detect_unsupported_layout
from meritranker_data_ingestion.services.option_key_normalizer import parse_multiple_numeric_options as parse_multi


def test_invalid_content_length_detection() -> None:
    assert is_invalid_content_length_error(Exception("InvalidContentLength"))
    assert not is_invalid_content_length_error(Exception("timeout"))


def test_explicit_azure_ocr_failure_raises_without_fallback(tmp_path: Path) -> None:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    _write_min_package(pkg)
    with patch(
        "meritranker_data_ingestion.services.ocr_evidence_builder.render_pdf_pages",
        return_value=([], []),
    ), patch(
        "meritranker_data_ingestion.services.ocr_evidence_builder.select_ocr_adapters",
    ) as mock_adapters:
        adapter = MagicMock()
        adapter.engine_name = "azure_ocr"
        adapter.extract_with_status.return_value = MagicMock(
            package=MagicMock(lines=[], ocr_engines_used=[], warnings=[], errors=["fail"]),
            page_statuses=[],
            ocr_failed=True,
            ocr_failed_reason="azure_ocr_produced_zero_lines",
            pages_attempted=1,
            pages_succeeded=0,
            pages_failed=1,
        )
        mock_adapters.return_value = [adapter]
        with pytest.raises(OcrEvidenceError, match="allow-ocr-fallback"):
            build_ocr_evidence_package(pkg, engine="azure", allow_fallback=False)
        evidence_path = pkg / OCR_DIR / OCR_EVIDENCE_JSON_NAME
        assert evidence_path.exists()
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence["status"] == "failed"
        assert "azure_ocr_produced_zero_lines" in evidence["errors"]


def test_allow_ocr_fallback_continues_with_warning(tmp_path: Path) -> None:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    _write_min_package(pkg)
    with patch(
        "meritranker_data_ingestion.services.ocr_evidence_builder.render_pdf_pages",
        return_value=([], []),
    ), patch(
        "meritranker_data_ingestion.services.ocr_evidence_builder.select_ocr_adapters",
    ) as mock_adapters:
        adapter = MagicMock()
        adapter.engine_name = "azure_ocr"
        adapter.extract_with_status.return_value = MagicMock(
            package=MagicMock(
                lines=[],
                ocr_engines_used=[],
                warnings=[],
                errors=[],
                model_dump=lambda mode="json": {},
            ),
            page_statuses=[],
            ocr_failed=True,
            ocr_failed_reason="azure_ocr_produced_zero_lines",
            pages_attempted=1,
            pages_succeeded=0,
            pages_failed=1,
        )
        mock_adapters.return_value = [adapter]
        result = build_ocr_evidence_package(pkg, engine="azure", allow_fallback=True)
    assert result.ocr_fallback_used is True
    assert "explicit_ocr_failed_marker_fallback_allowed" in result.package.warnings


def test_ocr_used_false_when_zero_lines() -> None:
    assert resolve_ocr_used(ocr_line_count=0, ocr_engines_used=[]) is False


def test_question_windows_built_from_anchors(tmp_path: Path) -> None:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    lines = [
        _line("l1", "Q.1 What is A?"),
        _line("l2", "1. Alpha"),
        _line("l3", "2. Beta"),
        _line("l4", "3. Gamma"),
        _line("l5", "4. Delta"),
        _line("l6", "Chosen Option : 2"),
        _line("l7", "Q.2 Next question?"),
        _line("l8", "1. One"),
    ]
    _write_evidence(pkg, lines)
    result = build_question_windows(pkg)
    assert result.package.total_windows >= 2
    assert result.package.windows[0].parsed_question_number == 1
    assert "l2" in result.package.windows[0].option_candidate_line_ids


def test_options_bind_only_within_local_window(tmp_path: Path) -> None:
    evidence = _evidence_pkg(
        [
            _line("q1", "Q.1 Question one"),
            _line("o1", "1. Alpha"),
            _line("o2", "2. Beta"),
            _line("o3", "3. Gamma"),
            _line("o4", "4. Delta"),
            _line("q5", "Q.5 Later question"),
            _line("bad", "1. Wrong reuse"),
        ],
    )
    windows = QuestionWindowsPackage(
        source_file_name="t.pdf",
        windows=[
            QuestionWindow(
                window_id="qw_0005",
                parsed_question_number=5,
                global_order=5,
                line_ids=["q5", "bad"],
                option_candidate_line_ids=["bad"],
            ),
        ],
    )
    package = _binding_pkg(
        SemanticBoundQuestion(
            semantic_question_id="sq_0005",
            question_number=5,
            question_text_raw="Q.5",
            raw_text="Q.5",
            window_id="qw_0005",
            options=[
                SemanticBoundOption(
                    key="1",
                    key_raw="1.",
                    text_raw="Wrong reuse",
                    source_spans=[SourceSpan(extractor="marker", line_id="o1")],
                ),
            ],
            answer=SemanticBoundAnswer(available=False),
            source_spans=[SourceSpan(extractor="marker", line_id="q5")],
            binding_status=SemanticBindingItemStatus.REVIEW_REQUIRED,
        ),
    )
    stats = resolve_source_spans(package, evidence, windows_pkg=windows)
    opt = package.items[0].options[0]
    assert "cross_window_option_span_reuse" in opt.issues or not opt.source_spans
    assert "cross_window_option_span_reuse" in str(stats.warnings)


def test_q5_cannot_reuse_q1_option_lines(tmp_path: Path) -> None:
    allowed = window_line_ids_for_question(
        QuestionWindowsPackage(
            source_file_name="t.pdf",
            windows=[
                QuestionWindow(
                    window_id="qw_0005",
                    parsed_question_number=5,
                    global_order=5,
                    line_ids=["q5", "o5"],
                ),
            ],
        ),
        question_number=5,
    )
    assert "o1" not in allowed
    assert "o5" in allowed


def test_response_sheet_markers_detected() -> None:
    evidence = _evidence_pkg(
        [
            _line("l1", "Question ID : 123"),
            _line("l2", "Status : Answered"),
            _line("l3", "Chosen Option : 2"),
            _line("l4", "Q.1 Sample"),
        ],
    )
    result = detect_unsupported_layout(evidence, answer_source_mode="chosen_option_metadata_only")
    assert result.response_sheet_markers_detected is True


def test_repeated_numbering_detected() -> None:
    lines = [_line(f"l{i}", f"Q.{1 if i % 20 == 0 else 2} text {i}") for i in range(50)]
    lines[0] = _line("l0", "Q.1 first")
    lines[25] = _line("l25", "Q.1 repeated far")
    evidence = _evidence_pkg(lines)
    result = detect_unsupported_layout(evidence, answer_source_mode="chosen_option_metadata_only")
    assert result.unsupported_layout_detected is False
    assert 1 in result.repeated_question_numbers


def test_unsupported_layout_stops_pipeline_before_bind(tmp_path: Path) -> None:
    with patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.ExtractorOrchestrator",
    ) as mock_orch, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.normalize_evidence_package",
    ) as mock_norm, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.run_ocr_runtime_preflight",
    ) as mock_preflight, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.build_ocr_evidence_package",
    ), patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.merge_evidence_package",
    ) as mock_merge, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.build_question_windows",
    ) as mock_qw, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.build_solution_windows",
    ) as mock_sol, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.build_evidence_answer_solution_map",
    ) as mock_map, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.profile_extraction_capability",
    ) as mock_profile, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.bind_semantically_package",
    ) as mock_bind:
        mock_qw.return_value = MagicMock(
            json_path=tmp_path / "qw.json",
            package=MagicMock(
                unsupported_layout_detected=True,
                total_windows=10,
                question_window_build_status="ok",
                question_solution_section_mixed=False,
                warnings=[],
            ),
        )
        mock_sol.return_value = MagicMock(
            json_path=tmp_path / "sol.json",
            package=MagicMock(
                total_windows=10,
                solution_window_detection_status="ok",
                warnings=[],
            ),
        )
        mock_map.return_value = MagicMock(
            json_path=tmp_path / "map.json",
            package=MagicMock(
                total_mapped=10,
                map_usable=True,
                warnings=[],
            ),
        )
        mock_profile.return_value = MagicMock(
            profile_path=tmp_path / "profile.json",
            recommended_answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY,
        )
        mock_orch.return_value.prepare.return_value.succeeded = True
        mock_orch.return_value.prepare.return_value.package_manifest = None
        mock_orch.return_value.prepare.return_value.extractor_manifest = MagicMock(
            source_file_name="t.pdf",
        )
        mock_norm.return_value.package.extraction_status.value = "succeeded"
        mock_preflight.return_value = MagicMock(
            requested_engine="auto",
            effective_engine=None,
            strict_failure=False,
            ocr_available=False,
            ocr_failed_reason=None,
            warnings=[],
        )
        lines = [
            _line("l1", "Chosen Option : 1"),
            _line("l2", "Status : Answered"),
            _line("l3", "Question ID : 9"),
            _line("l4", "Q.1 first"),
            *[_line(f"l{i}", "padding") for i in range(5, 25)],
            _line("l25", "Q.1 repeated"),
        ]
        mock_merge.return_value.package = _evidence_pkg(lines)
        mock_merge.return_value.merged_json_path = tmp_path / "m.json"
        mock_merge.return_value.summary_json_path = tmp_path / "s.json"
        with pytest.raises(SemanticPipelineError, match="Unsupported layout"):
            run_semantic_pipeline(
                SemanticPipelineOptions(
                    input_pdf=tmp_path / "in.pdf",
                    output_dir=tmp_path / "out",
                    auto_profile=True,
                    allow_unsupported_layout=False,
                ),
            )
        mock_bind.assert_not_called()


def test_azure_page_status_written(tmp_path: Path) -> None:
    from meritranker_data_ingestion.services.azure_ocr_adapter import write_azure_page_ocr_status

    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    path = write_azure_page_ocr_status(
        pkg,
        [
            AzurePageOcrStatus(
                page_number=1,
                attempts=[
                    AzurePageOcrAttempt(
                        input_type="pdf_page",
                        status="failed",
                        error_code="InvalidContentLength",
                        original_size_bytes=1000,
                        final_size_bytes=900,
                    ),
                ],
                final_status="failed",
            ),
        ],
        ocr_failed=True,
        ocr_failed_reason="InvalidContentLength",
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pages"][0]["final_status"] == "failed"
    assert data["pages"][0]["attempts"][0]["error_code"] == "InvalidContentLength"
    assert data["ocr_failed"] is True


def _write_min_package(pkg: Path) -> None:
    (pkg / "source").mkdir(parents=True, exist_ok=True)
    (pkg / "source" / "original.pdf").write_bytes(b"%PDF-1.4")
    ev = pkg / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / DOCUMENT_EVIDENCE_JSON_NAME).write_text(
        DocumentEvidencePackage(
            package_version="1.0",
            source_file_name="t.pdf",
            primary_extractor="marker",
            extractors_used=["marker"],
            extraction_status=EvidenceExtractionStatus.SUCCEEDED,
            lines=[],
        ).model_dump_json(),
        encoding="utf-8",
    )


def _write_evidence(pkg: Path, lines: list[EvidenceLine]) -> None:
    ev = pkg / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / DOCUMENT_EVIDENCE_JSON_NAME).write_text(
        _evidence_pkg(lines).model_dump_json(),
        encoding="utf-8",
    )


def _line(line_id: str, text: str) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
    )


def _evidence_pkg(lines: list[EvidenceLine]) -> DocumentEvidencePackage:
    return DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="t.pdf",
        primary_extractor="marker",
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=lines,
    )


def _binding_pkg(*items: SemanticBoundQuestion) -> SemanticBindingPackage:
    return SemanticBindingPackage(
        package_version="1.0",
        source_file_name="t.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY,
        status=SemanticBindingStatus.SUCCEEDED,
        input_evidence_hash="hash",
        items=list(items),
    )
