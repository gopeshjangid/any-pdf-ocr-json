"""One-command semantic pipeline orchestration (Part 13G + 14A)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    EXTRACTION_PACKAGE_DIR,
    SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME,
    SEMANTIC_BINDING_DIR,
)
from meritranker_data_ingestion.schemas.document_evidence import PrimaryExtractorMode
from meritranker_data_ingestion.schemas.extractor import ExtractorType
from meritranker_data_ingestion.schemas.semantic_binding import SemanticBinderAnswerMode
from meritranker_data_ingestion.services.document_evidence_normalizer import (
    normalize_evidence_package,
)
from meritranker_data_ingestion.services.evidence_merger import merge_evidence_package
from meritranker_data_ingestion.services.extractor_orchestrator import (
    ExtractorOrchestrator,
    PrepareError,
)
from meritranker_data_ingestion.services.extraction_capability_router import (
    profile_extraction_capability,
    resolve_effective_answer_mode,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.final_questions_export_builder import (
    FinalQuestionsExportError,
    build_final_questions_export,
)
from meritranker_data_ingestion.services.ocr_adapter_base import OcrEngineMode
from meritranker_data_ingestion.services.ocr_evidence_builder import (
    OcrEvidenceError,
    build_ocr_evidence_package,
)
from meritranker_data_ingestion.services.evidence_answer_solution_mapper import (
    build_evidence_answer_solution_map,
)
from meritranker_data_ingestion.services.question_window_builder import build_question_windows
from meritranker_data_ingestion.services.section_split_sanity import evaluate_section_split_sanity
from meritranker_data_ingestion.services.solution_window_builder import build_solution_windows
from meritranker_data_ingestion.services.unsupported_layout_detector import detect_unsupported_layout
from meritranker_data_ingestion.services.unsupported_layout_report import write_unsupported_layout_report
from meritranker_data_ingestion.services.ocr_runtime_preflight import (
    resolve_ocr_used,
    run_ocr_runtime_preflight,
    write_ocr_preflight_failure_artifact,
)
from meritranker_data_ingestion.services.output_path_guard import (
    OutputPathGuardError,
    clean_output_directory,
)
from meritranker_data_ingestion.services.pipeline_stage_tracker import (
    PipelineStageEvent,
    PipelineStageRecorder,
)
from meritranker_data_ingestion.services.semantic_bad_item_guard import (
    SemanticBadItemGuardError,
    apply_semantic_bad_item_guard,
)
from meritranker_data_ingestion.services.semantic_binding_repair import (
    SemanticBindingRepairError,
    repair_semantic_binding_package,
)
from meritranker_data_ingestion.services.semantic_binder import (
    SemanticBindingError,
    bind_semantically_package,
    evaluate_semantic_binding_package,
)
from meritranker_data_ingestion.services.semantic_final_export_builder import (
    SemanticFinalExportError,
    build_semantic_final_export,
)
from meritranker_data_ingestion.services.semantic_remaining_issue_diagnostician import (
    SemanticRemainingIssuesError,
    diagnose_semantic_remaining_issues,
)
from meritranker_data_ingestion.services.semantic_review_exporter import (
    SemanticReviewExportError,
    export_semantic_review_items,
)
from meritranker_data_ingestion.schemas.semantic_final_export import SemanticFinalExportMode


class SemanticPipelineError(Exception):
    """Raised when semantic pipeline cannot start or complete."""


def resolve_ocr_engine_for_strategy(
    effective_strategy: str,
    requested_engine: str,
) -> str:
    """Map extractor strategy to OCR engine mode (Part 14X)."""
    from meritranker_data_ingestion.services.pdf_extractor_router import (
        STRATEGY_AZURE_PRIMARY,
        STRATEGY_DUAL_DEBUG,
        STRATEGY_MARKER_PRIMARY,
    )

    if effective_strategy == STRATEGY_MARKER_PRIMARY:
        return OcrEngineMode.NONE.value
    if effective_strategy in {STRATEGY_AZURE_PRIMARY, STRATEGY_DUAL_DEBUG}:
        if requested_engine != OcrEngineMode.NONE.value:
            return requested_engine
        return "auto"
    return requested_engine


@dataclass(frozen=True)
class SemanticPipelineOptions:
    input_pdf: Path
    output_dir: Path
    expected_count: int | None = None
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY
    extractor: str = "marker"
    provider: str | None = None
    model: str | None = None
    include_answer_key_evidence: bool = True
    timeout_seconds: int = 180
    force: bool = False
    clean_output: bool = False
    build_semantic_final_export_flag: bool = False
    semantic_final_export_mode: str = "accepted-only"
    generate_review_patch_template: bool = False
    ocr_engine: str = "auto"
    auto_profile: bool = False
    build_final_questions_export: bool = False
    allow_ocr_fallback: bool = False
    allow_unsupported_layout: bool = False
    extractor_strategy: str = "auto"
    allow_auto_fallback: bool = True
    enable_llm_window_repair: bool = False
    stage_recorder: PipelineStageRecorder | None = None


@dataclass
class SemanticPipelineResult:
    source_file_name: str
    output_root: Path
    package_dir: Path
    expected_count: int | None
    semantic_item_count: int
    count_match: bool | None
    accepted_count: int
    review_required_count: int
    rejected_count: int
    questions_with_4_options_count: int
    answer_available_count: int
    source_span_missing_count: int
    answer_key_not_in_options_count: int
    hallucination_suspected_count: int
    semantic_quality_status: str
    final_export_quality_status: str | None
    exported_count: int | None
    excluded_count: int | None
    bad_item_count: int
    quarantined_item_count: int
    accepted_safe_count: int | None
    unsafe_previously_accepted_count: int | None
    ready_for_full_paper_ingestion: bool | None
    ready_for_partial_accepted_ingestion: bool | None
    quality_status: str
    total_questions_detected: int | None
    final_questions_accepted_safe_count: int | None
    ocr_line_count: int | None
    merged_evidence_line_count: int | None
    effective_answer_mode: str | None
    artifact_paths: dict[str, str]
    stage_events: list[PipelineStageEvent]


def _stage_run(
    recorder: PipelineStageRecorder | None,
    stage: str,
    fn,
    *,
    key_result_fn=None,
    metrics_fn=None,
):
    if recorder is None:
        return fn()
    return recorder.run(stage, fn, key_result_fn=key_result_fn, metrics_fn=metrics_fn)


def run_semantic_pipeline(options: SemanticPipelineOptions) -> SemanticPipelineResult:
    """Run prepare → evidence → OCR → merge → profile → bind → repair → evaluate → export."""
    rec = options.stage_recorder
    output_root = resolve_path(options.output_dir)
    if options.clean_output:
        try:
            clean_output_directory(output_root)
        except OutputPathGuardError as exc:
            raise SemanticPipelineError(str(exc)) from exc

    package_dir = output_root / EXTRACTION_PACKAGE_DIR

    from meritranker_data_ingestion.services.marker_fallback_evaluator import (
        evaluate_marker_fallback,
        write_marker_quality_summary,
    )
    from meritranker_data_ingestion.services.pdf_extractor_router import (
        STRATEGY_AZURE_PRIMARY,
        STRATEGY_DUAL_DEBUG,
        STRATEGY_MARKER_PRIMARY,
        route_extractor_strategy,
        update_pdf_extractor_profile_fields,
        write_pdf_extractor_profile,
    )
    from meritranker_data_ingestion.services.ocr_document_evidence_builder import (
        OcrDocumentEvidenceError,
        build_document_evidence_from_ocr,
    )

    requested_strategy = options.extractor_strategy
    if requested_strategy == "auto" and options.ocr_engine == "azure":
        requested_strategy = STRATEGY_AZURE_PRIMARY
    elif requested_strategy == "auto" and options.ocr_engine == "paddle":
        requested_strategy = STRATEGY_AZURE_PRIMARY

    pdf_profile = route_extractor_strategy(
        options.input_pdf,
        strategy=requested_strategy,
        allow_auto_fallback=options.allow_auto_fallback,
    )
    effective_strategy = pdf_profile.extractor_strategy_effective
    fallback_used = False
    fallback_reason: str | None = None
    marker_quality_summary: dict | None = None
    marker_used = pdf_profile.marker_used
    azure_used = pdf_profile.azure_used
    dual_used = pdf_profile.dual_used

    def _prepare() -> object:
        orchestrator = ExtractorOrchestrator()
        try:
            if effective_strategy == STRATEGY_AZURE_PRIMARY:
                result = orchestrator.prepare_shell(
                    options.input_pdf,
                    output_root,
                    force=options.force or options.clean_output,
                )
            elif effective_strategy == STRATEGY_DUAL_DEBUG:
                result = orchestrator.prepare(
                    options.input_pdf,
                    output_root,
                    extractor=ExtractorType.BOTH,
                    force=options.force or options.clean_output,
                )
            else:
                result = orchestrator.prepare(
                    options.input_pdf,
                    output_root,
                    extractor=ExtractorType.MARKER,
                    force=options.force or options.clean_output,
                )
        except PrepareError as exc:
            raise SemanticPipelineError(f"prepare failed: {exc}") from exc
        if not result.succeeded and effective_strategy != STRATEGY_AZURE_PRIMARY:
            raise SemanticPipelineError("prepare stage did not succeed.")
        write_pdf_extractor_profile(package_dir, pdf_profile)
        return result

    prepare_result = _stage_run(
        rec,
        "prepare_marker",
        _prepare,
        key_result_fn=lambda r: (
            r.extractor_manifest.source_file_name
            if r.extractor_manifest is not None
            else "prepared"
        ),
    )

    normalize_result = None
    if effective_strategy != STRATEGY_AZURE_PRIMARY:

        def _normalize() -> object:
            result = normalize_evidence_package(
                package_dir,
                primary_extractor=PrimaryExtractorMode.MARKER,
            )
            if result.package.extraction_status.value == "failed":
                raise SemanticPipelineError("normalize-evidence failed.")
            return result

        normalize_result = _stage_run(
            rec,
            "normalize_evidence",
            _normalize,
            key_result_fn=lambda r: f"lines={len(r.package.lines)}",
        )
    elif rec is not None:
        rec.skip("normalize_evidence", reason="azure_primary_ocr_document_evidence")

    ocr_line_count: int | None = None
    ocr_engines_used: list[str] = []
    merged_line_count: int | None = None
    artifact_paths: dict[str, str] = {}

    def _ocr_preflight() -> object:
        result = run_ocr_runtime_preflight(engine=options.ocr_engine)
        artifact_paths["ocr_preflight_requested_engine"] = result.requested_engine
        if result.strict_failure:
            failure_path = write_ocr_preflight_failure_artifact(package_dir, result)
            artifact_paths["ocr_preflight_failure"] = str(failure_path)
            raise SemanticPipelineError(
                f"OCR preflight failed: {result.ocr_failed_reason}. "
                "Install with: uv sync --extra azure --extra ocr",
            )
        return result

    preflight = _stage_run(
        rec,
        "ocr_preflight",
        _ocr_preflight,
        key_result_fn=lambda r: f"engine={r.requested_engine}",
    )

    ocr_failed = False
    ocr_failed_reason: str | None = None
    ocr_fallback_used = False
    ocr_pages_attempted = 0
    ocr_pages_succeeded = 0
    ocr_pages_failed = 0

    ocr_engine_effective = resolve_ocr_engine_for_strategy(
        effective_strategy,
        options.ocr_engine,
    )

    if ocr_engine_effective != OcrEngineMode.NONE.value:

        def _ocr_extract() -> object:
            try:
                return build_ocr_evidence_package(
                    package_dir,
                    engine=ocr_engine_effective,
                    allow_fallback=options.allow_ocr_fallback,
                )
            except OcrEvidenceError as exc:
                _write_pipeline_failure_artifact(package_dir, "ocr-evidence", str(exc))
                raise SemanticPipelineError(str(exc)) from exc

        ocr_result = _stage_run(
            rec,
            "ocr_extraction",
            _ocr_extract,
            key_result_fn=lambda r: f"lines={len(r.package.lines)}",
            metrics_fn=lambda r: {
                "ocr_pages_attempted": r.ocr_pages_attempted,
                "ocr_pages_succeeded": r.ocr_pages_succeeded,
            },
        )
        ocr_line_count = len(ocr_result.package.lines)
        ocr_engines_used = list(ocr_result.package.ocr_engines_used)
        ocr_failed = ocr_result.ocr_failed
        ocr_failed_reason = ocr_result.ocr_failed_reason
        ocr_fallback_used = ocr_result.ocr_fallback_used
        ocr_pages_attempted = ocr_result.ocr_pages_attempted
        ocr_pages_succeeded = ocr_result.ocr_pages_succeeded
        ocr_pages_failed = ocr_result.ocr_pages_failed
        artifact_paths["ocr_evidence"] = str(ocr_result.json_path)
        if effective_strategy == STRATEGY_AZURE_PRIMARY:
            try:
                build_document_evidence_from_ocr(package_dir)
            except OcrDocumentEvidenceError as exc:
                raise SemanticPipelineError(str(exc)) from exc
    elif rec is not None:
        rec.skip("ocr_extraction", reason="ocr_engine=none")

    def _merge() -> object:
        return merge_evidence_package(package_dir)

    merge_result = _stage_run(
        rec,
        "merge_evidence",
        _merge,
        key_result_fn=lambda r: f"lines={len(r.package.lines)}",
    )
    merged_line_count = len(merge_result.package.lines)
    artifact_paths["merged_evidence"] = str(merge_result.merged_json_path)
    artifact_paths["merged_evidence_summary"] = str(merge_result.summary_json_path)

    from meritranker_data_ingestion.services.extraction_capability_router import _detect_answer_source

    from meritranker_data_ingestion.services.ocr_role_hints import has_chosen_option_metadata

    chosen_in_doc = has_chosen_option_metadata(merge_result.package.lines)
    layout_result = detect_unsupported_layout(
        merge_result.package,
        answer_source_mode=_detect_answer_source(merge_result.package, chosen_in_doc),
    )
    ul_json, _ul_md = write_unsupported_layout_report(package_dir, layout_result)
    artifact_paths["unsupported_layout_report"] = str(ul_json)

    def _question_windows() -> object:
        return build_question_windows(package_dir, expected_count=options.expected_count)

    qw_result = _stage_run(
        rec,
        "question_windows",
        _question_windows,
        key_result_fn=lambda r: f"windows={r.package.total_windows}",
    )
    artifact_paths["question_windows"] = str(qw_result.json_path)

    if (
        effective_strategy == STRATEGY_MARKER_PRIMARY
        and options.allow_auto_fallback
        and normalize_result is not None
        and ocr_line_count is None
    ):
        marker_lines = len(normalize_result.package.lines)
        qw_package = qw_result.package
        fallback_decision = evaluate_marker_fallback(
            marker_line_count=marker_lines,
            question_window_count=getattr(qw_package, "total_windows", 0),
            windows_with_4_options=getattr(qw_package, "windows_with_4_options", 0),
            expected_count=options.expected_count,
            scanned_or_screenshot_score=pdf_profile.scanned_or_screenshot_score,
            image_area_ratio=pdf_profile.image_area_ratio,
            profile_question_anchor_count=pdf_profile.question_anchor_count,
            profile_option_label_count=pdf_profile.option_label_count,
        )
        marker_quality_summary = fallback_decision.marker_quality_summary
        write_marker_quality_summary(package_dir, marker_quality_summary)
        if fallback_decision.should_fallback:
            fallback_used = True
            azure_used = True
            fallback_reason = fallback_decision.reason

            def _ocr_fallback() -> object:
                return build_ocr_evidence_package(
                    package_dir,
                    engine="auto",
                    allow_fallback=options.allow_ocr_fallback,
                )

            ocr_result = _stage_run(
                rec,
                "ocr_extraction",
                _ocr_fallback,
                key_result_fn=lambda r: f"lines={len(r.package.lines)}",
                metrics_fn=lambda r: {"fallback_reason": fallback_reason or ""},
            )
            ocr_line_count = len(ocr_result.package.lines)
            ocr_engines_used = list(ocr_result.package.ocr_engines_used)
            artifact_paths["ocr_evidence"] = str(ocr_result.json_path)

            merge_result = _stage_run(
                rec,
                "merge_evidence",
                _merge,
                key_result_fn=lambda r: f"lines={len(r.package.lines)}",
            )
            merged_line_count = len(merge_result.package.lines)
            artifact_paths["merged_evidence"] = str(merge_result.merged_json_path)
            artifact_paths["merged_evidence_summary"] = str(merge_result.summary_json_path)

            chosen_in_doc = has_chosen_option_metadata(merge_result.package.lines)
            layout_result = detect_unsupported_layout(
                merge_result.package,
                answer_source_mode=_detect_answer_source(merge_result.package, chosen_in_doc),
            )
            ul_json, _ul_md = write_unsupported_layout_report(package_dir, layout_result)
            artifact_paths["unsupported_layout_report"] = str(ul_json)

            qw_result = _stage_run(
                rec,
                "question_windows",
                _question_windows,
                key_result_fn=lambda r: f"windows={r.package.total_windows}",
            )
            artifact_paths["question_windows"] = str(qw_result.json_path)
        else:
            update_pdf_extractor_profile_fields(
                package_dir,
                marker_quality_summary=marker_quality_summary,
            )

    if fallback_used:
        update_pdf_extractor_profile_fields(
            package_dir,
            fallback_reason=fallback_reason,
            marker_quality_summary=marker_quality_summary,
            fallback_used=True,
            azure_used=True,
        )

    def _solution_windows() -> object:
        return build_solution_windows(package_dir, expected_count=options.expected_count)

    sol_result = _stage_run(
        rec,
        "solution_windows",
        _solution_windows,
        key_result_fn=lambda r: f"windows={r.package.total_windows}",
    )
    artifact_paths["solution_windows"] = str(sol_result.json_path)

    def _answer_solution_map() -> object:
        return build_evidence_answer_solution_map(
            package_dir,
            expected_count=options.expected_count,
        )

    map_result = _stage_run(
        rec,
        "answer_solution_map",
        _answer_solution_map,
        key_result_fn=lambda r: f"mapped={r.package.total_mapped}",
    )
    artifact_paths["answer_solution_map"] = str(map_result.json_path)

    sanity = evaluate_section_split_sanity(
        expected_count=options.expected_count,
        question_windows=qw_result.package,
        solution_windows=sol_result.package,
        answer_map=map_result.package,
    )
    artifact_paths["section_split_sanity"] = json.dumps(
        {
            "passed": sanity.passed,
            "failure_reason": sanity.failure_reason,
            "question_window_build_status": sanity.question_window_build_status,
            "section_split_status": sanity.section_split_status,
            "solution_window_detection_status": sanity.solution_window_detection_status,
            "answer_solution_map_status": sanity.answer_solution_map_status,
            "section_split_fallback_used": sanity.section_split_fallback_used,
        },
    )
    if not sanity.passed:
        reason = sanity.failure_reason or "question_solution_split_failed"
        _write_pipeline_failure_artifact(package_dir, "section_split_sanity", reason)
        if rec is not None:
            rec.skip("extraction_profile", reason=reason)
            rec.skip("semantic_binding", reason=reason)
            rec.skip("bad_item_guard", reason=reason)
            rec.skip("repair", reason=reason)
            rec.skip("final_gate", reason=reason)
            if options.build_final_questions_export:
                rec.skip("final_questions_export", reason=reason)
        raise SemanticPipelineError(
            f"Section split sanity failed: {reason}. "
            f"question_windows={qw_result.package.total_windows} "
            f"solution_windows={sol_result.package.total_windows}",
        )

    def _profile() -> object:
        return profile_extraction_capability(
            package_dir,
            ocr_preflight=preflight,
            ocr_line_count=ocr_line_count or 0,
            ocr_engines_used=ocr_engines_used,
            ocr_failed=ocr_failed,
            ocr_failed_reason=ocr_failed_reason,
            ocr_fallback_used=ocr_fallback_used,
            ocr_pages_attempted=ocr_pages_attempted,
            ocr_pages_succeeded=ocr_pages_succeeded,
            ocr_pages_failed=ocr_pages_failed,
            layout_result=layout_result,
        )

    profile_result = _stage_run(
        rec,
        "extraction_profile",
        _profile,
        key_result_fn=lambda r: f"mode={r.recommended_answer_mode.value}",
    )
    artifact_paths["extraction_capability_profile"] = str(profile_result.profile_path)

    effective_answer_mode = resolve_effective_answer_mode(
        options.answer_mode,
        profile_result if options.auto_profile or options.answer_mode == SemanticBinderAnswerMode.AUTO else None,
    )

    if (
        layout_result.unsupported_layout_detected
        and options.auto_profile
        and not options.allow_unsupported_layout
    ):
        write_unsupported_layout_report(package_dir, layout_result, stopped=True)
        raise SemanticPipelineError(
            "Unsupported layout detected (repeated numbering / response-sheet). "
            "Pass --allow-unsupported-layout to continue in experimental question-only mode.",
        )

    def _bind():
        try:
            return bind_semantically_package(
                package_dir,
                provider=options.provider,
                model=options.model,
                answer_mode=effective_answer_mode,
                expected_count=options.expected_count,
                force=options.force or options.clean_output,
                timeout_seconds=options.timeout_seconds,
                include_answer_key_evidence=options.include_answer_key_evidence,
            )
        except SemanticBindingError as exc:
            _write_pipeline_failure_artifact(package_dir, "bind-semantically", str(exc))
            raise SemanticPipelineError(f"bind-semantically failed: {exc}") from exc

    bind_result = _stage_run(
        rec,
        "semantic_binding",
        _bind,
        key_result_fn=lambda r: f"items={len(r.package.items)}",
    )
    if rec is not None and rec.events and bind_result is not None:
        last = rec.events[-1]
        if last.stage == "semantic_binding":
            returned = len(bind_result.package.items)
            diag = _binding_diagnostics_from_warnings(bind_result.package.warnings)
            last.metrics = diag
            if returned == 0:
                last.status = "warning"
                last.warning_or_error = diag.get("skipped_reason") or "returned_item_count:0"

    def _guard() -> object:
        try:
            return apply_semantic_bad_item_guard(
                package_dir,
                expected_count=options.expected_count,
                answer_mode=effective_answer_mode,
            )
        except SemanticBadItemGuardError as exc:
            raise SemanticPipelineError(f"bad-item-guard failed: {exc}") from exc

    guard_result = _stage_run(
        rec,
        "bad_item_guard",
        _guard,
        key_result_fn=lambda r: f"bad_items={r.report.bad_item_count}",
    )

    diagnose_result = None

    def _repair() -> object:
        nonlocal diagnose_result
        try:
            repair_result = repair_semantic_binding_package(
                package_dir,
                answer_mode=effective_answer_mode,
                expected_count=options.expected_count,
            )
            diagnose_result = diagnose_semantic_remaining_issues(
                package_dir,
                use_repaired=True,
            )
            return repair_result
        except SemanticBindingRepairError as exc:
            raise SemanticPipelineError(f"repair-semantic-binding failed: {exc}") from exc
        except SemanticRemainingIssuesError as exc:
            raise SemanticPipelineError(f"diagnose-semantic-issues failed: {exc}") from exc

    repair_result = _stage_run(rec, "repair", _repair, key_result_fn=lambda _: "repaired")

    def _evaluate() -> object:
        return evaluate_semantic_binding_package(
            package_dir,
            expected_count=options.expected_count,
            answer_mode=effective_answer_mode,
            use_repaired=True,
        )

    eval_result = _stage_run(
        rec,
        "final_gate",
        _evaluate,
        key_result_fn=lambda r: "evaluated",
    )
    evaluation = json.loads(eval_result.evaluation_path.read_text(encoding="utf-8"))

    artifact_paths.update({
        "package_dir": str(package_dir),
        "bad_items_json": str(guard_result.json_path),
        "bad_items_md": str(guard_result.md_path),
        "chunk_diagnostics": str(
            package_dir / SEMANTIC_BINDING_DIR / SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME,
        ),
        "semantic_bound_repaired": str(repair_result.repaired_path),
        "repair_report": str(repair_result.repair_report_path),
        "remaining_issues_json": str(diagnose_result.json_path),
        "remaining_issues_md": str(diagnose_result.md_path),
        "evaluation_repaired": str(eval_result.evaluation_path),
    })

    if options.build_semantic_final_export_flag or options.generate_review_patch_template:
        try:
            review_result = export_semantic_review_items(
                package_dir,
                generate_patch_template=options.generate_review_patch_template,
            )
            artifact_paths["review_items_json"] = str(review_result.json_path)
            artifact_paths["review_items_md"] = str(review_result.md_path)
            if review_result.patch_template_path:
                artifact_paths["patch_template"] = str(review_result.patch_template_path)
        except SemanticReviewExportError as exc:
            raise SemanticPipelineError(f"review export failed: {exc}") from exc

    final_export_quality_status: str | None = None
    exported_count: int | None = None
    excluded_count: int | None = None
    accepted_safe_count: int | None = None
    unsafe_previously_accepted_count: int | None = None
    ready_for_full: bool | None = None
    ready_for_partial: bool | None = None

    if options.build_semantic_final_export_flag:
        try:
            export_mode = SemanticFinalExportMode(options.semantic_final_export_mode)
            final_result = build_semantic_final_export(
                package_dir,
                export_mode=export_mode,
                answer_mode=effective_answer_mode,
            )
            artifact_paths["semantic_final_questions"] = str(final_result.questions_path)
            artifact_paths["semantic_final_report"] = str(final_result.report_path)
            artifact_paths["semantic_final_summary"] = str(final_result.summary_path)
            if final_result.gate_report_path:
                artifact_paths["final_gate_report"] = str(final_result.gate_report_path)
            final_pkg = final_result.package
            final_export_quality_status = final_pkg.final_export_quality_status
            exported_count = final_pkg.exported_count
            excluded_count = final_pkg.excluded_count
            accepted_safe_count = final_pkg.accepted_safe_count
            unsafe_previously_accepted_count = final_pkg.unsafe_previously_accepted_count
            ready_for_full = final_pkg.ready_for_full_paper_ingestion
            ready_for_partial = final_pkg.ready_for_partial_accepted_ingestion
        except SemanticFinalExportError as exc:
            raise SemanticPipelineError(f"semantic final export failed: {exc}") from exc

    total_questions_detected: int | None = None
    final_questions_accepted_safe: int | None = None

    if options.build_final_questions_export:

        def _final_export() -> object:
            try:
                return build_final_questions_export(
                    package_dir,
                    answer_mode=effective_answer_mode,
                    expected_count=options.expected_count,
                )
            except FinalQuestionsExportError as exc:
                raise SemanticPipelineError(f"final questions export failed: {exc}") from exc

        fq_result = _stage_run(
            rec,
            "final_questions_export",
            _final_export,
            key_result_fn=lambda r: f"detected={r.package.total_questions_detected}",
        )
        artifact_paths["final_questions"] = str(fq_result.json_path)
        artifact_paths["final_questions_report"] = str(fq_result.report_path)
        artifact_paths["final_questions_summary"] = str(fq_result.summary_path)
        total_questions_detected = fq_result.package.total_questions_detected
        final_questions_accepted_safe = fq_result.package.accepted_safe_count
    elif rec is not None:
        rec.skip("final_questions_export", reason="not_requested")

    expected_count = evaluation.get("expected_count")
    semantic_item_count = evaluation.get("semantic_item_count", 0)
    count_match = (
        expected_count is None or semantic_item_count == expected_count
    )

    return SemanticPipelineResult(
        source_file_name=(
            prepare_result.package_manifest.source_file_name
            if prepare_result.package_manifest is not None
            else prepare_result.extractor_manifest.source_file_name
        ),
        output_root=output_root,
        package_dir=package_dir,
        expected_count=expected_count,
        semantic_item_count=semantic_item_count,
        count_match=count_match,
        accepted_count=evaluation.get("accepted_count", 0),
        review_required_count=evaluation.get("review_required_count", 0),
        rejected_count=evaluation.get("rejected_count", 0),
        questions_with_4_options_count=evaluation.get("questions_with_4_options_count", 0),
        answer_available_count=evaluation.get("answer_available_count", 0),
        source_span_missing_count=evaluation.get("source_span_missing_count", 0),
        answer_key_not_in_options_count=evaluation.get("answer_key_not_in_options_count", 0),
        hallucination_suspected_count=evaluation.get("hallucination_suspected_count", 0),
        semantic_quality_status=evaluation.get("quality_status", "warning"),
        final_export_quality_status=final_export_quality_status,
        exported_count=exported_count,
        excluded_count=excluded_count,
        ready_for_full_paper_ingestion=ready_for_full,
        ready_for_partial_accepted_ingestion=ready_for_partial,
        bad_item_count=guard_result.report.bad_item_count,
        quarantined_item_count=guard_result.quarantined_count,
        accepted_safe_count=accepted_safe_count,
        unsafe_previously_accepted_count=unsafe_previously_accepted_count,
        quality_status=evaluation.get("quality_status", "warning"),
        total_questions_detected=total_questions_detected,
        final_questions_accepted_safe_count=final_questions_accepted_safe,
        ocr_line_count=ocr_line_count,
        merged_evidence_line_count=merged_line_count,
        effective_answer_mode=effective_answer_mode.value,
        artifact_paths=artifact_paths,
        stage_events=list(rec.events) if rec is not None else [],
    )


def _binding_diagnostics_from_warnings(warnings: list[str]) -> dict[str, object]:
    diag: dict[str, object] = {}
    for warning in warnings:
        if warning.startswith("planned_chunk_count:"):
            diag["planned_chunk_count"] = int(warning.split(":", 1)[1])
        elif warning.startswith("executed_chunk_count:"):
            diag["executed_chunk_count"] = warning.split(":", 1)[1]
        elif warning.startswith("returned_item_count:"):
            diag["returned_item_count"] = int(warning.split(":", 1)[1])
        elif warning.startswith("used_question_windows:"):
            diag["used_question_windows"] = warning.split(":", 1)[1] == "True"
        elif warning.startswith("provider_called:"):
            diag["provider_called"] = warning.split(":", 1)[1] == "True"
        elif warning.startswith("skipped_reason:"):
            diag["skipped_reason"] = warning.split(":", 1)[1]
    return diag


def _write_pipeline_failure_artifact(package_dir: Path, stage: str, message: str) -> None:
    path = package_dir / "pipeline-failure.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"stage": stage, "error": message}, indent=2),
        encoding="utf-8",
    )
