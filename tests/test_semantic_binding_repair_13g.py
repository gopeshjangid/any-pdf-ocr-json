"""Tests for Part 13G edge-case repair, diagnostics, and semantic pipeline runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    EXTRACTION_PACKAGE_DIR,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
    SemanticBindingPackage,
)
from meritranker_data_ingestion.services.output_path_guard import (
    OutputPathGuardError,
    validate_clean_output_path,
)
from meritranker_data_ingestion.services.semantic_embedded_option_parser import (
    extract_options_from_line,
)
from meritranker_data_ingestion.services.semantic_remaining_issue_diagnostician import (
    diagnose_semantic_remaining_issues,
)
from meritranker_data_ingestion.services.semantic_binding_repair import repair_semantic_binding_package
from meritranker_data_ingestion.services.semantic_binding_validator import validate_semantic_items
from meritranker_data_ingestion.services.semantic_key_normalizer import canonical_option_keys
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    SemanticPipelineOptions,
    run_semantic_pipeline,
)
from meritranker_data_ingestion.services.semantic_source_span_resolver import resolve_source_spans


def _line(line_id: str, text: str) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
        source_span=SourceSpan(extractor="marker", line_id=line_id),
        role_hints=[],
    )


def _evidence(lines: list[EvidenceLine]) -> DocumentEvidencePackage:
    return DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="exam.pdf",
        primary_extractor="marker",
        extractors_available=["marker"],
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=lines,
    )


def _package(items: list[SemanticBoundQuestion]) -> SemanticBindingPackage:
    return SemanticBindingPackage(
        package_version="1.0",
        source_file_name="exam.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        input_evidence_hash="abc",
        status=SemanticBindingStatus.SUCCEEDED,
        items=items,
    )


def _write_pkg(tmp_path: Path, package: SemanticBindingPackage, evidence: DocumentEvidencePackage) -> Path:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True, exist_ok=True)
    (sem / SEMANTIC_BOUND_QUESTIONS_NAME).write_text(
        package.model_dump_json(indent=2),
        encoding="utf-8",
    )
    ev = pkg / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / DOCUMENT_EVIDENCE_JSON_NAME).write_text(
        evidence.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return pkg


def test_pipe_table_row_multi_options() -> None:
    line = _line("t1", "| A | Bhutan | B | Nepal | C | India | D | Bangladesh |")
    opts = extract_options_from_line(line.text_raw, line)
    keys = [o[0] for o in opts]
    assert keys == ["A", "B", "C", "D"]


def test_same_line_paren_options() -> None:
    line = _line("t1", "(A) 12 (B) 16 (C) 13 (D) 14")
    opts = extract_options_from_line(line.text_raw, line)
    assert [o[0] for o in opts] == ["A", "B", "C", "D"]


def test_same_line_bold_bullet_options() -> None:
    line = _line("t1", "- **A** Bhutan - **B** Nepal - **C** India - **D** Bangladesh")
    opts = extract_options_from_line(line.text_raw, line)
    assert len(opts) >= 2
    assert opts[0][0] == "A"


def test_dash_plain_option_q58_style() -> None:
    line = _line("o1", "  - A The sum of two sides may be equal to the third side.")
    opts = extract_options_from_line(line.text_raw, line)
    assert opts and opts[0][0] == "A"


def test_neighbouring_line_option_repair_before_next_anchor() -> None:
    lines = [
        _line("q58", "- **58.** Select the correct statement about the properties of a triangle."),
        _line("oA", "  - A The sum of two sides may be equal to the third side."),
        _line("oB", "  - **B** The sum of two sides is always equal to the third side."),
        _line("oC", "  - C The sum of two sides is always greater than the third side."),
        _line("oD", "  - **D** The sum of two sides is always less than the third side."),
        _line("q59", "- **59.** Next question here."),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0058",
        question_number=58,
        question_text_raw="- **58.** Select the correct statement about the properties of a triangle.",
        raw_text="- **58.** Select the correct statement about the properties of a triangle.",
        options=[SemanticBoundOption(key="", key_raw="", text_raw="") for _ in range(4)],
        answer=SemanticBoundAnswer(available=True, key="C", key_raw="C", source_spans=[]),
    )
    package = _package([item])
    resolve_source_spans(package, evidence)
    keys = sorted(canonical_option_keys(package.items[0]))
    assert keys == ["A", "B", "C", "D"]
    assert "C" in keys


def test_no_cross_question_option_stealing() -> None:
    lines = [
        _line("q1", "**1.** First question"),
        _line("o1", "- **A** one"),
        _line("o2", "- **B** two"),
        _line("q2", "**2.** Second question"),
        _line("o3", "- **A** alpha"),
        _line("o4", "- **B** beta"),
        _line("o5", "- **C** gamma"),
        _line("o6", "- **D** delta"),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** First question",
        raw_text="**1.** First question",
        options=[SemanticBoundOption(key="", key_raw="", text_raw="") for _ in range(4)],
        answer=SemanticBoundAnswer(available=True, key="A", key_raw="A", source_spans=[]),
    )
    package = _package([item])
    resolve_source_spans(package, evidence)
    texts = [o.text_raw for o in package.items[0].options]
    assert "alpha" not in texts
    assert "one" in texts


def test_answer_key_mismatch_cleared_after_option_repair() -> None:
    lines = [
        _line("q1", "**1.** Pick one"),
        _line("o1", "- **A** one"),
        _line("o2", "- **B** two"),
        _line("o3", "- **C** three"),
        _line("o4", "- **D** four"),
        _line("ak", "1.C"),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** Pick one",
        raw_text="**1.** Pick one",
        options=[SemanticBoundOption(key="", key_raw="", text_raw="") for _ in range(4)],
        answer=SemanticBoundAnswer(
            available=True,
            key="C",
            key_raw="C",
            source_spans=[SourceSpan(extractor="marker", line_id="ak")],
        ),
    )
    package = _package([item])
    resolve_source_spans(package, evidence)
    report = validate_semantic_items(
        package.items,
        [],
        evidence,
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    )
    assert report.answer_key_not_in_options_count == 0


def test_incomplete_source_stays_non_accepted(tmp_path: Path) -> None:
    lines = [_line("q24", "- **24.** Which figure is the mirror image?")]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0024",
        question_number=24,
        question_text_raw="- **24.** Which figure is the mirror image?",
        raw_text="- **24.** Which figure is the mirror image?",
        options=[],
        answer=SemanticBoundAnswer(available=True, key="B", key_raw="B", source_spans=[]),
    )
    pkg = _write_pkg(tmp_path, _package([item]), evidence)
    repair_semantic_binding_package(pkg, expected_count=1)
    repaired = json.loads(
        (pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME).read_text(),
    )
    assert repaired["items"][0]["binding_status"] != "accepted"


def test_diagnose_remaining_issue_artifacts(tmp_path: Path) -> None:
    lines = [
        _line("q1", "**1.** Question"),
        _line("o1", "- **A** one"),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** Question",
        raw_text="**1.** Question",
        options=[SemanticBoundOption(key="A", key_raw="A", text_raw="one", source_spans=[])],
        answer=SemanticBoundAnswer(available=True, key="B", key_raw="B", source_spans=[]),
        binding_status="review_required",
        issues=["answer_key_not_in_options"],
    )
    pkg = _write_pkg(tmp_path, _package([item]), evidence)
    repair_semantic_binding_package(pkg)
    result = diagnose_semantic_remaining_issues(pkg, use_repaired=True)
    assert result.json_path.exists()
    assert result.md_path.exists()
    assert (pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME).exists()
    assert result.report.non_accepted_count >= 1


def test_clean_output_refuses_dangerous_paths(tmp_path: Path) -> None:
    with pytest.raises(OutputPathGuardError):
        validate_clean_output_path(Path("/"))
    with pytest.raises(OutputPathGuardError):
        validate_clean_output_path(Path.home())
    safe = validate_clean_output_path(tmp_path / "output")
    assert safe == (tmp_path / "output").resolve()


@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.evaluate_semantic_binding_package")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.diagnose_semantic_remaining_issues")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.repair_semantic_binding_package")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.apply_semantic_bad_item_guard")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.bind_semantically_package")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.profile_extraction_capability")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.build_evidence_answer_solution_map")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.build_solution_windows")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.build_question_windows")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.merge_evidence_package")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.build_ocr_evidence_package")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.run_ocr_runtime_preflight")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.normalize_evidence_package")
@patch("meritranker_data_ingestion.services.semantic_pipeline_runner.ExtractorOrchestrator")
def test_run_semantic_pipeline_stage_order(
    mock_orchestrator_cls: MagicMock,
    mock_normalize: MagicMock,
    mock_preflight: MagicMock,
    mock_ocr: MagicMock,
    mock_merge: MagicMock,
    mock_qw: MagicMock,
    mock_sol: MagicMock,
    mock_map: MagicMock,
    mock_profile: MagicMock,
    mock_bind: MagicMock,
    mock_guard: MagicMock,
    mock_repair: MagicMock,
    mock_diagnose: MagicMock,
    mock_evaluate: MagicMock,
    tmp_path: Path,
) -> None:
    from meritranker_data_ingestion.services.semantic_bad_item_guard import SemanticBadItemGuardResult
    from meritranker_data_ingestion.services.semantic_bad_item_guard import SemanticBadItemsReport
    from meritranker_data_ingestion.schemas.extraction import ExtractionPackageManifest, ExtractionStatus
    from meritranker_data_ingestion.schemas.extractor import ExtractorManifest, ExtractorRunStatus, ExtractorType
    from meritranker_data_ingestion.services.semantic_binding_repair import SemanticBindingRepairResult
    from meritranker_data_ingestion.schemas.semantic_binding import SemanticBindingValidationReport
    from meritranker_data_ingestion.services.semantic_binder import SemanticBindingResult

    output = tmp_path / "output"
    pkg = output / EXTRACTION_PACKAGE_DIR
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True)

    orchestrator = mock_orchestrator_cls.return_value
    orchestrator.prepare.return_value = MagicMock(
        succeeded=True,
        package_manifest=ExtractionPackageManifest(
            input_pdf_path=tmp_path / "exam.pdf",
            source_file_name="exam.pdf",
            output_dir=output,
            parser_engine="marker",
            status=ExtractionStatus.SUCCEEDED,
        ),
        extractor_manifest=ExtractorManifest(
            selected_extractor=ExtractorType.MARKER,
            source_file_name="exam.pdf",
            marker_status=ExtractorRunStatus.SUCCEEDED,
        ),
    )
    mock_normalize.return_value = MagicMock(
        package=MagicMock(
            extraction_status=MagicMock(value="succeeded"),
            lines=["line"] * 50,
        ),
    )
    mock_preflight.return_value = MagicMock(
        requested_engine="auto",
        effective_engine=None,
        ocr_available=False,
        ocr_failed_reason=None,
        warnings=["ocr_unavailable_marker_only_fallback"],
        strict_failure=False,
    )
    mock_ocr.return_value = MagicMock(
        package=MagicMock(lines=[]),
        json_path=pkg / "ocr" / "ocr-evidence.json",
    )
    mock_merge.return_value = MagicMock(
        package=MagicMock(lines=[]),
        summary={"merged_line_count": 0},
        merged_json_path=pkg / "evidence" / "merged-document-evidence.json",
        summary_json_path=pkg / "evidence" / "merged-evidence-summary.json",
    )
    mock_qw.return_value = MagicMock(
        json_path=pkg / "evidence" / "question-windows.json",
        package=MagicMock(
            unsupported_layout_detected=False,
            total_windows=2,
            windows_with_4_options=2,
            question_window_build_status="ok",
            question_solution_section_mixed=False,
            warnings=[],
        ),
    )
    mock_sol.return_value = MagicMock(
        json_path=pkg / "evidence" / "solution-windows.json",
        package=MagicMock(
            total_windows=2,
            solution_window_detection_status="ok",
            warnings=[],
        ),
    )
    mock_map.return_value = MagicMock(
        json_path=pkg / "evidence" / "answer-solution-map.json",
        package=MagicMock(
            total_mapped=2,
            map_usable=True,
            warnings=[],
        ),
    )
    from meritranker_data_ingestion.schemas.final_questions_export import ExtractionProfileSummary
    from meritranker_data_ingestion.services.extraction_capability_router import ExtractionCapabilityResult

    mock_profile.return_value = ExtractionCapabilityResult(
        profile=ExtractionProfileSummary(),
        recommended_answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        profile_path=pkg / "evidence" / "extraction-capability-profile.json",
        raw={},
    )

    eval_path = sem / "semantic-binding-evaluation.repaired.json"
    eval_path.write_text(
        json.dumps(
            {
                "semantic_item_count": 2,
                "accepted_count": 2,
                "review_required_count": 0,
                "rejected_count": 0,
                "questions_with_4_options_count": 2,
                "answer_available_count": 2,
                "source_span_missing_count": 0,
                "answer_key_not_in_options_count": 0,
                "hallucination_suspected_count": 0,
                "quality_status": "passed",
            },
        ),
        encoding="utf-8",
    )

    mock_guard.return_value = SemanticBadItemGuardResult(
        package=_package([]),
        report=SemanticBadItemsReport(),
        json_path=sem / "semantic-bad-items.json",
        md_path=sem / "semantic-bad-items.md",
        quarantined_count=0,
    )
    mock_repair.return_value = SemanticBindingRepairResult(
        package=_package([]),
        validation=SemanticBindingValidationReport(),
        repair_report=MagicMock(),
        repaired_path=sem / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
        repair_report_path=sem / "repair-report.json",
        validation_path=sem / "validation.repaired.json",
        evaluation_path=eval_path,
        summary_path=sem / "summary.md",
    )
    mock_diagnose.return_value = MagicMock(
        json_path=sem / SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME,
        md_path=sem / "remaining.md",
    )
    mock_evaluate.return_value = SemanticBindingResult(
        package=_package([]),
        output_path=sem / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
        validation_path=sem / "validation.repaired.json",
        report_path=sem / "report.json",
        evaluation_path=eval_path,
        from_cache=True,
    )

    run_semantic_pipeline(
        SemanticPipelineOptions(
            input_pdf=tmp_path / "exam.pdf",
            output_dir=output,
            expected_count=2,
            provider="mock",
        ),
    )

    orchestrator.prepare.assert_called_once()
    mock_normalize.assert_called_once()
    mock_ocr.assert_not_called()
    mock_merge.assert_called_once()
    mock_sol.assert_called_once()
    mock_map.assert_called_once()
    mock_profile.assert_called_once()
    mock_bind.assert_called_once()
    mock_guard.assert_called_once()
    mock_repair.assert_called_once()
    mock_diagnose.assert_called_once()
    mock_evaluate.assert_called_once()
    assert mock_evaluate.call_args.kwargs.get("use_repaired") is True


def test_run_semantic_pipeline_writes_under_selected_output(tmp_path: Path) -> None:
    output = tmp_path / "my_output"
    with patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.run_semantic_pipeline",
        wraps=run_semantic_pipeline,
    ):
        with patch(
            "meritranker_data_ingestion.services.semantic_pipeline_runner.ExtractorOrchestrator",
        ) as mock_orch:
            mock_orch.return_value.prepare.return_value = MagicMock(succeeded=False)
            with pytest.raises(Exception):
                run_semantic_pipeline(
                    SemanticPipelineOptions(
                        input_pdf=tmp_path / "x.pdf",
                        output_dir=output,
                    ),
                )
    assert output.resolve().parent == tmp_path.resolve()


def test_no_provider_during_diagnosis_repair(tmp_path: Path) -> None:
    lines = [_line("q1", "**1.** Q")]
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** Q",
        raw_text="**1.** Q",
        options=[],
        answer=SemanticBoundAnswer(available=False),
        binding_status="review_required",
        issues=["missing_options"],
    )
    pkg = _write_pkg(tmp_path, _package([item]), _evidence(lines))
    repair_semantic_binding_package(pkg)
    with patch(
        "meritranker_data_ingestion.services.semantic_remaining_issue_diagnostician.resolve_llm_provider",
        create=True,
    ) as mock_provider:
        diagnose_semantic_remaining_issues(pkg, use_repaired=True)
        mock_provider.assert_not_called()
