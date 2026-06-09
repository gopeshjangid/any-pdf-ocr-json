"""Tests for Part 13H semantic final export and review patch workflow."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    EXTRACTION_PACKAGE_DIR,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME,
    SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME,
    SEMANTIC_FINAL_DIR,
    SEMANTIC_FINAL_PATCH_APPLIED_NAME,
    SEMANTIC_FINAL_PATCH_TEMPLATE_NAME,
    SEMANTIC_FINAL_QUESTIONS_NAME,
    SEMANTIC_FINAL_REPORT_NAME,
    SEMANTIC_FINAL_REVIEW_ITEMS_JSON_NAME,
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
)
from meritranker_data_ingestion.schemas.semantic_final_export import (
    PatchAction,
    SemanticFinalExportMode,
    SemanticPatchItemInput,
    SemanticPatchOptionInput,
    SemanticReviewPatchFile,
)
from meritranker_data_ingestion.services.semantic_final_export_builder import (
    build_semantic_final_export,
    convert_bound_to_final,
)
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    SemanticPipelineOptions,
    run_semantic_pipeline,
)
from meritranker_data_ingestion.services.semantic_review_exporter import export_semantic_review_items
from meritranker_data_ingestion.services.semantic_review_patch_applier import (
    SemanticReviewPatchError,
    apply_semantic_review_patch,
)
from meritranker_data_ingestion.services.semantic_remaining_issue_diagnostician import (
    diagnose_semantic_remaining_issues,
)


def _bound(
    qnum: int,
    status: SemanticBindingItemStatus,
    *,
    options: list[SemanticBoundOption] | None = None,
    answer_key: str = "A",
    gate_safe: bool = True,
) -> SemanticBoundQuestion:
    span = [SourceSpan(extractor="marker", line_id="l1")]
    opts = options or [
        SemanticBoundOption(key="A", key_raw="A", text_raw="one", source_spans=span),
        SemanticBoundOption(key="B", key_raw="B", text_raw="two", source_spans=span),
        SemanticBoundOption(key="C", key_raw="C", text_raw="three", source_spans=span),
        SemanticBoundOption(key="D", key_raw="D", text_raw="four", source_spans=span),
    ]
    if not gate_safe:
        opts = [SemanticBoundOption(key="", key_raw="", text_raw="", source_spans=[])]
    answer = SemanticBoundAnswer(
        available=True,
        key=answer_key,
        key_raw=answer_key,
        source_spans=span if gate_safe else [],
    )
    return SemanticBoundQuestion(
        semantic_question_id=f"sq_{qnum:04d}",
        question_number=qnum,
        question_text_raw=f"**{qnum}.** Question {qnum}",
        raw_text=f"**{qnum}.** Question {qnum}",
        options=opts,
        answer=answer,
        source_spans=span if gate_safe else [],
        binding_status=status,
        issues=[],
    )


def _write_evaluation(
    pkg: Path,
    *,
    expected_count: int | None = 100,
    semantic_item_count: int | None = None,
    accepted_count: int = 0,
    review_required_count: int = 0,
    rejected_count: int = 0,
    hallucination_suspected_count: int = 0,
    source_span_missing_count: int = 0,
    answer_key_not_in_options_count: int = 0,
    quality_status: str = "passed",
) -> None:
    sem = pkg / SEMANTIC_BINDING_DIR
    count = semantic_item_count if semantic_item_count is not None else (
        accepted_count + review_required_count + rejected_count
    )
    payload = {
        "expected_count": expected_count,
        "semantic_item_count": count,
        "accepted_count": accepted_count,
        "review_required_count": review_required_count,
        "rejected_count": rejected_count,
        "hallucination_suspected_count": hallucination_suspected_count,
        "source_span_missing_count": source_span_missing_count,
        "answer_key_not_in_options_count": answer_key_not_in_options_count,
        "missing_question_numbers": [],
        "duplicate_question_numbers": [],
        "quality_status": quality_status,
    }
    (sem / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME).write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def _write_package(
    tmp_path: Path,
    items: list[SemanticBoundQuestion],
    *,
    evaluation: dict | None = None,
) -> Path:
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
    if evaluation is None:
        accepted = sum(1 for i in items if i.binding_status == SemanticBindingItemStatus.ACCEPTED)
        review = sum(1 for i in items if i.binding_status == SemanticBindingItemStatus.REVIEW_REQUIRED)
        rejected = sum(1 for i in items if i.binding_status == SemanticBindingItemStatus.REJECTED)
        _write_evaluation(
            pkg,
            expected_count=len(items),
            semantic_item_count=len(items),
            accepted_count=accepted,
            review_required_count=review,
            rejected_count=rejected,
        )
    else:
        (sem / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME).write_text(
            json.dumps(evaluation, indent=2),
            encoding="utf-8",
        )
    return pkg


def test_accepted_only_export_excludes_review_rejected(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path,
        [
            _bound(1, SemanticBindingItemStatus.ACCEPTED),
            _bound(2, SemanticBindingItemStatus.REVIEW_REQUIRED, gate_safe=False),
            _bound(3, SemanticBindingItemStatus.REJECTED, gate_safe=False),
        ],
    )
    result = build_semantic_final_export(pkg, export_mode=SemanticFinalExportMode.ACCEPTED_ONLY)
    assert result.package.exported_count == 1
    assert result.package.accepted_exported_count == 1
    assert result.package.excluded_count == 2
    assert all(item.question_number == 1 for item in result.package.items)


def test_review_items_and_patch_template_generated(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path,
        [
            _bound(1, SemanticBindingItemStatus.ACCEPTED),
            _bound(2, SemanticBindingItemStatus.REVIEW_REQUIRED, gate_safe=False),
        ],
    )
    result = export_semantic_review_items(pkg, generate_patch_template=True)
    assert result.report.total_review_items == 1
    assert result.json_path.exists()
    assert result.md_path.exists()
    assert result.patch_template_path is not None
    assert result.patch_template_path.exists()
    template = json.loads(result.patch_template_path.read_text(encoding="utf-8"))
    assert template["patch_items"][0]["action"] == "hold_for_review"
    assert template["patch_items"][0]["confirm_no_guessing"] is False


def test_patch_rejected_without_confirm_or_notes(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, [_bound(2, SemanticBindingItemStatus.REVIEW_REQUIRED)])
    patch_path = pkg / SEMANTIC_FINAL_DIR / "review-patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch = SemanticReviewPatchFile(
        source_file_name="exam.pdf",
        patch_items=[
            SemanticPatchItemInput(
                patch_id="q_0002",
                question_number=2,
                action=PatchAction.ACCEPT_WITH_MANUAL_PATCH,
                reviewer_notes="",
                confirm_no_guessing=False,
                options=[
                    SemanticPatchOptionInput(key="A", text_raw="fixed"),
                ],
                correct_answer_key="A",
            ),
        ],
    )
    patch_path.write_text(patch.model_dump_json(indent=2), encoding="utf-8")
    result = apply_semantic_review_patch(pkg, patch_path)
    assert result.report.rejected_patch_count == 1
    assert result.report.applied_count == 0


def test_valid_manual_patch_applied_with_provenance(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, [_bound(2, SemanticBindingItemStatus.REVIEW_REQUIRED)])
    patch_path = pkg / SEMANTIC_FINAL_DIR / "review-patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch = SemanticReviewPatchFile(
        source_file_name="exam.pdf",
        reviewer="qa",
        patch_items=[
            SemanticPatchItemInput(
                patch_id="q_0002",
                question_number=2,
                action=PatchAction.ACCEPT_WITH_MANUAL_PATCH,
                reviewer_notes="Verified against PDF page 5.",
                manual_source_reference="PDF p5",
                confirm_no_guessing=True,
                question_text_raw="**2.** Fixed question",
                options=[
                    SemanticPatchOptionInput(key="A", text_raw="alpha"),
                    SemanticPatchOptionInput(key="B", text_raw="beta"),
                    SemanticPatchOptionInput(key="C", text_raw="gamma"),
                    SemanticPatchOptionInput(key="D", text_raw="delta"),
                ],
                correct_answer_key="C",
            ),
        ],
    )
    patch_path.write_text(patch.model_dump_json(indent=2), encoding="utf-8")
    result = apply_semantic_review_patch(pkg, patch_path)
    assert result.report.applied_count == 1
    applied = json.loads((pkg / SEMANTIC_FINAL_DIR / SEMANTIC_FINAL_PATCH_APPLIED_NAME).read_text())
    item = applied["items"][0]["final_item"]
    assert "manual_patch" in item["provenance"]
    assert item["answer_source"] == "manual_review"
    assert item["correct_answer_text"] == "gamma"


def test_patch_cannot_modify_accepted_without_flag(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, [_bound(1, SemanticBindingItemStatus.ACCEPTED)])
    patch_path = pkg / SEMANTIC_FINAL_DIR / "review-patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch = SemanticReviewPatchFile(
        patch_items=[
            SemanticPatchItemInput(
                patch_id="q_0001",
                question_number=1,
                action=PatchAction.ACCEPT_WITH_MANUAL_PATCH,
                reviewer_notes="note",
                confirm_no_guessing=True,
                options=[SemanticPatchOptionInput(key="A", text_raw="x")],
                correct_answer_key="A",
            ),
        ],
    )
    patch_path.write_text(patch.model_dump_json(indent=2), encoding="utf-8")
    result = apply_semantic_review_patch(pkg, patch_path)
    assert result.report.rejected_patch_count == 1


def test_correct_answer_text_from_option(tmp_path: Path) -> None:
    item = convert_bound_to_final(_bound(1, SemanticBindingItemStatus.ACCEPTED, answer_key="B"))
    assert item.correct_answer_key == "B"
    assert item.correct_answer_text == "two"


def test_correct_answer_text_unavailable_flagged() -> None:
    item = convert_bound_to_final(
        _bound(
            1,
            SemanticBindingItemStatus.ACCEPTED,
            options=[SemanticBoundOption(key="A", key_raw="A", text_raw="", source_spans=[])],
            answer_key="A",
        ),
    )
    assert item.correct_answer_text is None
    assert "correct_answer_text_unavailable" in item.issues


def test_final_export_counts_reconcile(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path,
        [_bound(i, SemanticBindingItemStatus.ACCEPTED) for i in range(1, 6)]
        + [_bound(6, SemanticBindingItemStatus.REVIEW_REQUIRED, gate_safe=False)],
    )
    result = build_semantic_final_export(pkg)
    pkg_result = result.package
    assert pkg_result.exported_count == pkg_result.accepted_exported_count
    assert pkg_result.total_semantic_items == 6
    assert pkg_result.exported_count == 5
    assert pkg_result.excluded_count == 1


def test_outputs_under_selected_output_folder(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, [_bound(1, SemanticBindingItemStatus.ACCEPTED)])
    result = build_semantic_final_export(pkg)
    assert str(result.questions_path).startswith(str(tmp_path / EXTRACTION_PACKAGE_DIR))
    assert SEMANTIC_FINAL_DIR in str(result.questions_path)


def test_no_provider_during_final_export(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, [_bound(1, SemanticBindingItemStatus.ACCEPTED)])
    with patch(
        "meritranker_data_ingestion.services.semantic_final_export_builder.resolve_llm_provider",
        create=True,
    ) as mock_provider:
        build_semantic_final_export(pkg)
        export_semantic_review_items(pkg)
        mock_provider.assert_not_called()


def test_patch_outside_package_rejected(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, [_bound(2, SemanticBindingItemStatus.REVIEW_REQUIRED)])
    outside = tmp_path / "outside-patch.json"
    outside.write_text('{"patch_items": []}', encoding="utf-8")
    with pytest.raises(SemanticReviewPatchError):
        apply_semantic_review_patch(pkg, outside)


def test_hallucination_makes_final_report_failed(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path,
        [_bound(1, SemanticBindingItemStatus.ACCEPTED)],
        evaluation={
            "expected_count": 1,
            "semantic_item_count": 1,
            "accepted_count": 1,
            "review_required_count": 0,
            "rejected_count": 0,
            "hallucination_suspected_count": 1,
            "source_span_missing_count": 0,
            "answer_key_not_in_options_count": 0,
            "quality_status": "failed",
        },
    )
    result = build_semantic_final_export(pkg)
    assert result.package.final_export_quality_status == "failed"
    assert result.package.quality_status == "failed"
    report = json.loads((pkg / SEMANTIC_FINAL_DIR / SEMANTIC_FINAL_REPORT_NAME).read_text())
    assert report["quality_status"] == "failed"
    assert "hallucination_suspected_count=1" in report["errors"]


def test_count_mismatch_makes_final_report_failed(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path,
        [_bound(i, SemanticBindingItemStatus.ACCEPTED) for i in range(1, 4)],
        evaluation={
            "expected_count": 2,
            "semantic_item_count": 3,
            "accepted_count": 3,
            "review_required_count": 0,
            "rejected_count": 0,
            "hallucination_suspected_count": 0,
            "source_span_missing_count": 0,
            "answer_key_not_in_options_count": 0,
            "quality_status": "passed",
        },
    )
    result = build_semantic_final_export(pkg)
    assert result.package.count_match is False
    assert result.package.final_export_quality_status == "failed"
    assert result.package.ready_for_full_paper_ingestion is False


def test_accepted_only_still_exports_but_marks_unsafe(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path,
        [
            _bound(1, SemanticBindingItemStatus.ACCEPTED),
            _bound(2, SemanticBindingItemStatus.REJECTED),
        ],
        evaluation={
            "expected_count": 2,
            "semantic_item_count": 2,
            "accepted_count": 1,
            "review_required_count": 0,
            "rejected_count": 1,
            "hallucination_suspected_count": 1,
            "source_span_missing_count": 0,
            "answer_key_not_in_options_count": 0,
            "quality_status": "failed",
        },
    )
    result = build_semantic_final_export(pkg, export_mode=SemanticFinalExportMode.ACCEPTED_ONLY)
    assert result.package.exported_count == 1
    assert result.package.accepted_safe_count == 1
    assert result.package.final_export_quality_status == "failed"
    assert result.package.ready_for_full_paper_ingestion is False
    assert result.package.ready_for_partial_accepted_ingestion is True


def test_non_numeric_extra_item_excluded_from_export(tmp_path: Path) -> None:
    extra = SemanticBoundQuestion(
        semantic_question_id="sq_0101",
        question_number=None,
        question_text_raw="orphan",
        raw_text="orphan",
        options=[],
        answer=SemanticBoundAnswer(available=False, key=None, key_raw=None, source_spans=[]),
        binding_status=SemanticBindingItemStatus.ACCEPTED,
        issues=[],
    )
    pkg = _write_package(
        tmp_path,
        [_bound(1, SemanticBindingItemStatus.ACCEPTED), extra],
        evaluation={
            "expected_count": 1,
            "semantic_item_count": 2,
            "accepted_count": 2,
            "review_required_count": 0,
            "rejected_count": 0,
            "hallucination_suspected_count": 0,
            "source_span_missing_count": 0,
            "answer_key_not_in_options_count": 0,
            "quality_status": "failed",
        },
    )
    result = build_semantic_final_export(pkg)
    assert result.package.exported_count == 1
    assert "sq_0101" in result.package.non_numeric_question_ids
    assert result.package.extra_excluded_count == 1


def test_remaining_issues_json_has_items_key(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path,
        [
            _bound(1, SemanticBindingItemStatus.ACCEPTED),
            _bound(2, SemanticBindingItemStatus.REVIEW_REQUIRED),
        ],
    )
    # minimal evidence for diagnostician
    evidence_dir = pkg / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.joinpath("document-evidence.json").write_text(
        json.dumps(
            {
                "package_version": "1.0",
                "source_file_name": "exam.pdf",
                "primary_extractor": "marker",
                "extractors_available": ["marker"],
                "extractors_used": ["marker"],
                "extraction_status": "succeeded",
                "lines": [
                    {
                        "line_id": "l1",
                        "text_raw": "**2.** Question two",
                        "normalized_preview": "**2.** Question two",
                        "source_extractor": "marker",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    result = diagnose_semantic_remaining_issues(pkg, use_repaired=True)
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert "items" in payload
    assert isinstance(payload["items"], list)
    assert payload["non_accepted_count"] == len(payload["items"])
    assert payload["items"][0]["patch_id"]
    assert payload["items"][0]["failure_class"]


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
def test_pipeline_summary_includes_quality_fields(
    mock_orchestrator_cls,
    mock_normalize,
    mock_preflight,
    mock_ocr,
    mock_merge,
    mock_qw,
    mock_sol,
    mock_map,
    mock_profile,
    mock_bind,
    mock_guard,
    mock_repair,
    mock_diagnose,
    mock_evaluate,
    tmp_path: Path,
) -> None:
    from meritranker_data_ingestion.services.semantic_bad_item_guard import (
        SemanticBadItemGuardResult,
        SemanticBadItemsReport,
    )
    from meritranker_data_ingestion.schemas.extraction import ExtractionPackageManifest, ExtractionStatus
    from meritranker_data_ingestion.schemas.extractor import ExtractorManifest, ExtractorRunStatus, ExtractorType
    from meritranker_data_ingestion.services.semantic_binding_repair import SemanticBindingRepairResult
    from meritranker_data_ingestion.schemas.semantic_binding import SemanticBindingValidationReport
    from meritranker_data_ingestion.services.semantic_binder import SemanticBindingResult

    output = tmp_path / "output"
    pkg = output / EXTRACTION_PACKAGE_DIR
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True)

    items = [_bound(1, SemanticBindingItemStatus.ACCEPTED)]
    bound_pkg = SemanticBindingPackage(
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
        bound_pkg.model_dump_json(indent=2),
        encoding="utf-8",
    )
    _write_evaluation(
        pkg,
        expected_count=2,
        semantic_item_count=1,
        accepted_count=1,
        quality_status="failed",
        hallucination_suspected_count=1,
    )

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
    mock_normalize.return_value = MagicMock(package=MagicMock(extraction_status=MagicMock(value="succeeded")))
    mock_preflight.return_value = MagicMock(
        requested_engine="auto",
        effective_engine=None,
        ocr_available=False,
        ocr_failed_reason=None,
        warnings=[],
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
    eval_path = sem / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME
    mock_guard.return_value = SemanticBadItemGuardResult(
        package=bound_pkg,
        report=SemanticBadItemsReport(),
        json_path=sem / "semantic-bad-items.json",
        md_path=sem / "semantic-bad-items.md",
        quarantined_count=0,
    )
    mock_repair.return_value = SemanticBindingRepairResult(
        package=bound_pkg,
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
        package=bound_pkg,
        output_path=sem / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
        validation_path=sem / "validation.repaired.json",
        evaluation_path=eval_path,
        report_path=sem / "report.json",
        from_cache=True,
    )

    result = run_semantic_pipeline(
        SemanticPipelineOptions(
            input_pdf=tmp_path / "exam.pdf",
            output_dir=output,
            expected_count=2,
            build_semantic_final_export_flag=True,
        ),
    )
    assert result.count_match is False
    assert result.final_export_quality_status == "failed"
    assert result.ready_for_full_paper_ingestion is False
    assert result.accepted_safe_count is not None
