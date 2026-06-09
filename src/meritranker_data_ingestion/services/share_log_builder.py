"""Compact share-log markdown for batch PDF runs."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.config import (
    EVIDENCE_ANSWER_SOLUTION_MAP_JSON_NAME,
    EXTRACTION_PACKAGE_DIR,
    FINAL_QUESTIONS_DIR,
    FINAL_QUESTIONS_JSON_NAME,
    FINAL_QUESTIONS_REPORT_NAME,
    OCR_DIR,
    OCR_ENGINE_LOGS_DIR,
    OCR_EVIDENCE_JSON_NAME,
    QUESTION_WINDOWS_JSON_NAME,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME,
    SOLUTION_WINDOWS_JSON_NAME,
    get_marker_command_base,
)
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionQualityStatus,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.services.deterministic_option_parser import count_usable_options
from meritranker_data_ingestion.services.layout_type_classifier import classify_layout_type
from meritranker_data_ingestion.services.public_questions_audit import audit_public_questions_json
from meritranker_data_ingestion.services.ocr_input_sizer import pymupdf_available
from meritranker_data_ingestion.services.ocr_runtime_preflight import _azure_dependency_available
from meritranker_data_ingestion.services.pipeline_stage_tracker import PipelineStageEvent
from meritranker_data_ingestion.services.semantic_pipeline_runner import SemanticPipelineResult


@dataclass
class ShareLogContext:
    pdf_file_name: str
    input_path: Path
    output_folder: Path
    started_at: datetime
    duration_seconds: float
    pipeline_error: str | None = None
    pipeline_result: SemanticPipelineResult | None = None
    questions_json_path: Path | None = None
    stage_events: list[PipelineStageEvent] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None


@dataclass
class ShareLogBuildResult:
    share_log_path: Path
    run_status: str
    quality_verdict: str
    main_failure_reason: str | None
    recommended_next_action: str | None
    metrics: dict[str, Any]


def build_share_log(ctx: ShareLogContext) -> ShareLogBuildResult:
    """Build <stem>.share-log.md from pipeline context and artifacts."""
    package_dir = ctx.output_folder / EXTRACTION_PACKAGE_DIR
    fq = _load_internal_final_questions(ctx.output_folder) or _load_final_questions(
        ctx.questions_json_path,
    )
    metrics = _collect_metrics(ctx, fq, package_dir)
    quality_verdict, main_reason, next_action = _quality_verdict(
        metrics,
        fq,
        ctx.pipeline_error,
        package_dir=package_dir,
    )
    run_status = _run_status(ctx.pipeline_error, fq, quality_verdict)
    share_path = ctx.output_folder / f"{ctx.output_folder.name}.share-log.md"
    share_path.write_text(
        _render_markdown(
            ctx,
            metrics,
            run_status,
            quality_verdict,
            main_reason,
            next_action,
            fq_package=fq,
        ),
        encoding="utf-8",
    )
    return ShareLogBuildResult(
        share_log_path=share_path,
        run_status=run_status,
        quality_verdict=quality_verdict,
        main_failure_reason=main_reason,
        recommended_next_action=next_action,
        metrics=metrics,
    )


def _load_internal_final_questions(output_folder: Path) -> FinalQuestionsPackage | None:
    return _load_final_questions(internal_final_questions_path(output_folder))


def _load_final_questions(path: Path | None) -> FinalQuestionsPackage | None:
    if path is None or not path.exists():
        return None
    try:
        return FinalQuestionsPackage.model_validate_json(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _collect_metrics(
    ctx: ShareLogContext,
    fq: FinalQuestionsPackage | None,
    package_dir: Path,
) -> dict[str, Any]:
    pr = ctx.pipeline_result
    evaluation: dict[str, Any] = {}
    eval_path = package_dir / SEMANTIC_BINDING_DIR / "semantic-binding-evaluation.repaired.json"
    if eval_path.exists():
        try:
            evaluation = json.loads(eval_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            evaluation = {}

    cross_window = 0
    unsafe_accepted = 0
    accepted_safe_incomplete = 0
    questions_with_4 = 0
    questions_incomplete = 0
    if fq:
        for item in fq.items:
            usable = count_usable_options(item.options)
            if usable >= 4:
                questions_with_4 += 1
            else:
                questions_incomplete += 1
            if item.metadata.status == "ready" and usable < 4:
                accepted_safe_incomplete += 1
            if item.metadata.status == "ready":
                if not item.question_text_raw.strip():
                    unsafe_accepted += 1
                if usable < 4:
                    unsafe_accepted += 1
                if not item.source_trace.question_line_ids and not item.source_trace.provenance:
                    unsafe_accepted += 1
            for issue in item.issues:
                if "cross_window" in issue:
                    cross_window += 1
                if "hallucinated" in issue and item.metadata.status == "ready":
                    unsafe_accepted += 1

    ev_dir = package_dir / "evidence"
    qw_data = _load_json(ev_dir / QUESTION_WINDOWS_JSON_NAME)
    sol_data = _load_json(ev_dir / SOLUTION_WINDOWS_JSON_NAME)
    map_data = _load_json(ev_dir / EVIDENCE_ANSWER_SOLUTION_MAP_JSON_NAME)
    fq_report = _load_json(package_dir / FINAL_QUESTIONS_DIR / FINAL_QUESTIONS_REPORT_NAME)

    qw_count = int(qw_data.get("total_windows", 0)) if qw_data else 0
    sol_count = int(sol_data.get("total_windows", 0)) if sol_data else 0
    map_count = int(map_data.get("total_mapped", 0)) if map_data else 0
    section_mixed = bool(qw_data.get("question_solution_section_mixed")) if qw_data else False
    qw_build_status = qw_data.get("question_window_build_status", "unknown") if qw_data else "missing"
    split_status = qw_data.get("section_split_status", "unknown") if qw_data else "missing"
    split_fallback = bool(qw_data.get("section_split_fallback_used")) if qw_data else False
    sol_detection_status = (
        sol_data.get("solution_window_detection_status", "unknown") if sol_data else "missing"
    )
    map_status = map_data.get("answer_solution_map_status", "unknown") if map_data else "missing"

    answer_available = sum(
        1
        for item in (fq.items if fq else [])
        if item.correct_answer_key and item.correct_answer_text
    )

    metrics: dict[str, Any] = {
        "expected_count": (
            pr.expected_count if pr else fq_report.get("expected_count") if fq_report else evaluation.get("expected_count")
        ),
        "total_questions_detected": int(
            fq_report.get("public_question_count", fq.total_questions_detected if fq else 0),
        ) if fq_report else (fq.total_questions_detected if fq else pr.total_questions_detected if pr else 0),
        "public_question_count": int(fq_report.get("public_question_count", fq.total_questions_detected if fq else 0)) if fq_report else (fq.total_questions_detected if fq else 0),
        "raw_candidate_count": int(fq_report.get("raw_candidate_count", 0)) if fq_report else 0,
        "extra_candidate_count": int(fq_report.get("extra_candidate_count", 0)) if fq_report else 0,
        "duplicate_candidate_count": int(fq_report.get("duplicate_candidate_count", 0)) if fq_report else 0,
        "missing_placeholder_count": int(
            fq_report.get("missing_question_placeholders_added", fq_report.get("missing_question_count", 0)),
        ) if fq_report else 0,
        "semantic_item_count": pr.semantic_item_count if pr else evaluation.get("semantic_item_count", 0),
        "count_match": pr.count_match if pr else None,
        "ready_count": int(fq_report.get("ready_count", fq.ready_count if fq else 0)) if fq_report else (fq.ready_count if fq else 0),
        "review_count": int(fq_report.get("review_count", fq.review_count if fq else 0)) if fq_report else (fq.review_count if fq else 0),
        "visual_required_count": fq.visual_required_count if fq else 0,
        "blocked_count": fq.blocked_count if fq else 0,
        "answer_available_count": int(
            fq_report.get("answer_available_count", answer_available),
        ) if fq_report else answer_available,
        "solution_available_count": int(
            fq_report.get("solution_available_count", 0),
        ) if fq_report else sum(
            1
            for item in (fq.items if fq else [])
            if item.solution_text_raw and item.solution_text_raw.strip()
        ),
        "missing_question_count": int(
            fq_report.get("missing_question_count", fq_report.get("missing_question_placeholders_added", 0)),
        ) if fq_report else 0,
        "ocr_line_count": fq.ocr_line_count if fq else (pr.ocr_line_count if pr else 0),
        "merged_evidence_line_count": pr.merged_evidence_line_count if pr else 0,
        "question_window_count": qw_count,
        "solution_window_count": sol_count,
        "answer_solution_map_count": map_count,
        "question_solution_section_mixed": section_mixed,
        "question_window_build_status": qw_build_status,
        "section_split_status": split_status,
        "section_split_fallback_used": split_fallback,
        "solution_window_detection_status": sol_detection_status,
        "answer_solution_map_status": map_status,
        "answers_mapped_from_solution_count": int(
            fq_report.get("answers_mapped_from_solution_count", 0),
        ) if fq_report else 0,
        "solutions_mapped_count": int(fq_report.get("solutions_mapped_count", 0)) if fq_report else 0,
        "deterministic_window_export_used": bool(
            fq_report.get("deterministic_window_export_used"),
        ) if fq_report else False,
        "deterministic_window_questions_built": int(
            fq_report.get("deterministic_window_questions_built", 0),
        ) if fq_report else 0,
        "semantic_underbound_window_fallback_used": bool(
            fq_report.get("semantic_underbound_window_fallback_used"),
        ) if fq_report else False,
        "semantic_returned_item_count": (
            pr.semantic_item_count if pr else evaluation.get("semantic_item_count", 0)
        ),
        "semantic_provider_called": _semantic_diag_metric(
            package_dir,
            evaluation,
            "provider_called",
        ),
        "questions_with_4_options_count": int(
            fq_report.get("questions_with_4_options_count", questions_with_4),
        ) if fq_report else questions_with_4,
        "questions_with_incomplete_options_count": int(
            fq_report.get("questions_with_incomplete_options_count", questions_incomplete),
        ) if fq_report else questions_incomplete,
        "accepted_safe_with_incomplete_options_count": int(
            fq_report.get(
                "accepted_safe_with_incomplete_options_count",
                accepted_safe_incomplete,
            ),
        ) if fq_report else accepted_safe_incomplete,
        "chosen_option_detected_count": fq.chosen_option_detected_count if fq else 0,
        "chosen_option_as_correct_answer_count": fq.chosen_option_as_correct_answer_count if fq else 0,
        "hallucination_suspected_count": pr.hallucination_suspected_count if pr else 0,
        "cross_window_option_span_reuse_count": cross_window,
        "source_span_missing_count": pr.source_span_missing_count if pr else 0,
        "bad_item_count": pr.bad_item_count if pr else 0,
        "quarantined_item_count": pr.quarantined_item_count if pr else 0,
        "unsafe_accepted_safe_count": unsafe_accepted,
        "review_items_count": int(
            fq_report.get("review_items_count", fq.review_items_count if fq else 0)
            if fq_report
            else (fq.review_items_count if fq else 0),
        ),
        "incomplete_options_count": int(
            fq_report.get("incomplete_options_count", questions_incomplete),
        ) if fq_report else questions_incomplete,
        "answer_solution_join_gap_count": int(
            fq_report.get("answer_solution_join_gap_count", 0),
        ) if fq_report else 0,
        "missing_question_placeholders_added": int(
            fq_report.get("missing_question_placeholders_added", 0),
        ) if fq_report else 0,
        "missing_question_numbers": fq_report.get("missing_question_numbers", []) if fq_report else [],
        "duplicate_question_numbers": fq_report.get("duplicate_question_numbers", []) if fq_report else [],
        "ocr_requested_engine": fq.ocr_requested_engine if fq else None,
        "ocr_effective_engine": fq.ocr_effective_engine if fq else None,
        "ocr_used": fq.ocr_used if fq else False,
        "marker_available": shutil.which(shlex_first_token(get_marker_command_base())) is not None,
        "marker_command": get_marker_command_base(),
        "azure_di_available": _azure_dependency_available(),
        "pymupdf_available": pymupdf_available(),
        "llm_provider": ctx.provider,
        "llm_model": ctx.model,
    }
    metrics.update(_enrichment_metrics(metrics, package_dir, ctx.questions_json_path))
    return metrics


def shlex_first_token(command: str) -> str:
    import shlex

    parts = shlex.split(command)
    return parts[0] if parts else command


def _enrichment_metrics(
    metrics: dict[str, Any],
    package_dir: Path,
    questions_json_path: Path | None,
) -> dict[str, Any]:
    expected = int(metrics.get("expected_count") or 0)
    ready = int(metrics.get("ready_count") or 0)
    ready_pct = round((ready / expected) * 100, 1) if expected else 0.0
    layout = classify_layout_type(metrics, package_dir=package_dir)
    audit = "missing"
    if questions_json_path and questions_json_path.exists():
        audit_result = audit_public_questions_json(
            questions_json_path,
            expected_count=expected or None,
        )
        audit = "PASS" if audit_result.passed else "FAIL"
    enriched = {
        "layout_type_detected": layout,
        "ready_percentage": ready_pct,
        "public_json_audit": audit,
    }
    profile_path = package_dir / "diagnostics" / "pdf-extractor-profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            if isinstance(profile, dict):
                enriched.update(
                    {
                        "extractor_strategy_requested": profile.get("extractor_strategy_requested"),
                        "extractor_strategy_effective": profile.get("extractor_strategy_effective"),
                        "pdf_profile_summary": {
                            "text_density_score": profile.get("text_density_score"),
                            "image_area_ratio": profile.get("image_area_ratio"),
                            "scanned_or_screenshot_score": profile.get("scanned_or_screenshot_score"),
                            "layout_hint": profile.get("layout_hint"),
                        },
                        "sampled_pages": profile.get("sampled_pages"),
                        "ignored_profile_pages_count": profile.get("ignored_profile_pages_count"),
                        "marker_used": profile.get("marker_used"),
                        "azure_used": profile.get("azure_used"),
                        "dual_used": profile.get("dual_used"),
                        "fallback_used": profile.get("fallback_used", profile.get("fallback_allowed")),
                        "fallback_reason": profile.get("fallback_reason"),
                        "marker_quality_summary": profile.get("marker_quality_summary"),
                    },
                )
        except (json.JSONDecodeError, OSError):
            pass
    marker_quality_path = package_dir / "diagnostics" / "marker-quality-summary.json"
    if marker_quality_path.exists() and "marker_quality_summary" not in enriched:
        try:
            marker_summary = json.loads(marker_quality_path.read_text(encoding="utf-8"))
            if isinstance(marker_summary, dict):
                enriched["marker_quality_summary"] = marker_summary
        except (json.JSONDecodeError, OSError):
            pass
    return enriched


def _unsupported_layout_main_issue(package_dir: Path) -> str | None:
    from meritranker_data_ingestion.services.layout_type_classifier import _load_layout_artifacts

    artifact = _load_layout_artifacts(package_dir)
    if not artifact.get("unsupported_layout_detected"):
        return None
    if artifact.get("response_sheet_markers_detected") or artifact.get("chosen_option_detected"):
        return "response_sheet_layout"
    return "unsupported_layout"


def _quality_verdict(
    metrics: dict[str, Any],
    fq: FinalQuestionsPackage | None,
    pipeline_error: str | None,
    *,
    package_dir: Path | None = None,
) -> tuple[str, str | None, str | None]:
    if pipeline_error:
        reason = _classify_failure(pipeline_error, metrics, fq)
        return "failed", reason, _recommend_action(reason, metrics)

    empty_reason = _empty_export_failure(metrics, fq)
    if empty_reason:
        return "failed", empty_reason, _recommend_action(empty_reason, metrics)

    if fq is None:
        return "failed", "final_questions_missing", "Inspect pipeline failure artifact and rerun."

    if metrics["chosen_option_as_correct_answer_count"] > 0:
        return "failed", "chosen_option_as_correct_answer", "Remove chosen-option-as-answer mapping."
    if metrics["cross_window_option_span_reuse_count"] > 0 and metrics.get("ready_count", 0) > 0:
        accepted_cross = sum(
            1
            for item in fq.items
            if item.metadata.status == "ready"
            and any("cross_window" in i for i in item.issues)
        )
        if accepted_cross:
            return "failed", "cross_window_option_leakage", "Inspect question windows and option spans."

    unsupported_issue = _unsupported_layout_main_issue(package_dir) if package_dir else None
    expected = metrics.get("expected_count") or 0
    raw_candidates = metrics.get("raw_candidate_count", 0) or metrics.get("semantic_returned_item_count", 0)
    detected = metrics.get("total_questions_detected", 0) or 0
    if unsupported_issue and expected and detected == expected:
        return (
            "failed",
            unsupported_issue,
            "Unsupported or response-sheet layout; canonicalized to expected_count with blocked placeholders.",
        )
    if expected and raw_candidates > int(expected * 1.5):
        over_issue = "response_sheet_layout" if metrics.get("chosen_option_detected_count", 0) > 0 else "question_window_over_detection"
        return (
            "failed",
            over_issue,
            "Raw candidate count exceeds expected slots; inspect extra-question-candidates.json.",
        )

    if metrics.get("missing_question_placeholders_added", 0) > 0 or metrics.get("missing_question_count", 0) > 0:
        if expected and raw_candidates > int(expected * 1.5):
            return (
                "failed",
                "unsupported_layout",
                "Unsupported layout with missing placeholders; canonicalized to expected_count.",
            )
        return (
            "failed",
            "question_missing",
            "Missing question numbers were filled with blocked placeholders; inspect extraction.",
        )

    if metrics.get("accepted_safe_with_incomplete_options_count", 0) > 0:
        return (
            "failed",
            "ready_with_incomplete_options",
            "Repair ready items missing complete option sets.",
        )

    if metrics["unsafe_accepted_safe_count"] > 0:
        return "failed", "ready_quality_violation", "Review ready items with empty text/options."

    accepted_hallucination = sum(
        1
        for item in fq.items
        if item.metadata.status == "ready"
        and any("hallucinated" in i for i in item.issues)
    )
    if accepted_hallucination:
        return "failed", "semantic_hallucination", "Quarantine hallucinated items before acceptance."

    poor_reason = _poor_quality_reason(metrics, fq)
    if poor_reason:
        return "partial_ready", poor_reason, _recommend_action(poor_reason, metrics)

    partial_reason = _partial_completion_reason(metrics)
    if partial_reason:
        return "partial_ready", partial_reason, _recommend_action(partial_reason, metrics)

    expected = metrics.get("expected_count")
    detected = metrics.get("total_questions_detected", 0) or 0
    ready = metrics.get("ready_count", 0) or 0
    if expected and detected == expected and ready == detected:
        return "full_ready", None, "No action required."
    if ready > 0:
        return "partial_ready", "review_items_remaining", "Inspect final-review-items.json for remaining work."

    return "partial_ready", "review_items_remaining", "Inspect final-review-items.json for remaining work."


def _semantic_diag_metric(
    package_dir: Path,
    evaluation: dict[str, Any],
    key: str,
) -> object:
    binding_path = package_dir / SEMANTIC_BINDING_DIR / "semantic-bound-questions.json"
    if binding_path.exists():
        try:
            data = json.loads(binding_path.read_text(encoding="utf-8"))
            for warning in data.get("warnings", []):
                if warning.startswith(f"{key}:"):
                    raw = warning.split(":", 1)[1]
                    if raw in {"True", "False"}:
                        return raw == "True"
                    return raw
        except (json.JSONDecodeError, OSError):
            pass
    return evaluation.get(key)


def _empty_export_failure(
    metrics: dict[str, Any],
    fq: FinalQuestionsPackage | None,
) -> str | None:
    detected = metrics.get("total_questions_detected", 0) or 0
    if detected != 0:
        return None
    expected = metrics.get("expected_count")
    if expected and expected > 0:
        return "final_questions_empty"
    if metrics.get("question_window_build_status") == "failed":
        return "question_window_build_failed"
    if metrics.get("solution_window_detection_status") == "over_detected":
        return "solution_over_detection"
    if metrics.get("answer_solution_map_status") == "over_detected":
        return "answer_solution_map_over_detected"
    if metrics.get("question_window_count", 0) == 0:
        return "question_window_build_failed"
    return "final_questions_empty"


def _poor_quality_reason(metrics: dict[str, Any], fq: FinalQuestionsPackage | None) -> str | None:
    if metrics.get("question_window_build_status") == "failed":
        return "question_window_build_failed"
    if metrics.get("solution_window_detection_status") == "over_detected":
        return "solution_over_detection"
    if metrics.get("answer_solution_map_status") == "over_detected":
        return "answer_solution_map_over_detected"
    if metrics.get("question_solution_section_mixed"):
        return "question_solution_section_mixed"
    expected = metrics.get("expected_count") or (fq.total_questions_detected if fq else None)
    if expected and metrics.get("question_window_count", 0) > int(expected * 1.3):
        return "question_solution_section_mixed"
    detected = metrics.get("total_questions_detected", 0) or 0
    if detected and metrics.get("answer_solution_map_count", 0) < max(10, int(detected * 0.5)):
        if metrics.get("solution_window_count", 0) >= 10:
            return "answer_solution_mapping_failed"
    if detected and metrics.get("answer_available_count", 0) < max(5, int(detected * 0.2)):
        if metrics.get("solution_window_count", 0) >= 10:
            return "low_answer_available_count"
    if detected and metrics.get("ready_count", 0) == 0:
        expected = metrics.get("expected_count")
        if expected and detected == expected and metrics.get("blocked_count", 0) == 0:
            return None
        return "low_ready_count"
    return None


def _partial_completion_reason(metrics: dict[str, Any]) -> str | None:
    expected = metrics.get("expected_count")
    detected = metrics.get("total_questions_detected", 0) or 0
    if not (
        expected
        and detected == expected
        and metrics.get("blocked_count", 0) == 0
        and metrics.get("accepted_safe_with_incomplete_options_count", 0) == 0
    ):
        return None
    if metrics.get("incomplete_options_count", 0) > 0:
        return "incomplete_options_remaining"
    if metrics.get("visual_required_count", 0) > 0:
        return "visual_items_remaining"
    if metrics.get("review_count", 0) > 0 or metrics.get("review_items_count", 0) > 0:
        return "review_items_remaining"
    return None


def _section_split_failure_from_error(error: str) -> str | None:
    lower = error.lower()
    for reason in (
        "question_window_build_failed",
        "question_solution_split_failed",
        "solution_over_detection",
        "answer_solution_map_over_detected",
    ):
        if reason in lower:
            return reason
    if "section split sanity failed" in lower:
        if "question_windows=" in lower and "solution_windows=" in lower:
            return "question_solution_split_failed"
        return "question_window_build_failed"
    return None


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _run_status(
    pipeline_error: str | None,
    fq: FinalQuestionsPackage | None,
    quality_verdict: str,
) -> str:
    if pipeline_error and fq is None:
        return "failed"
    if quality_verdict == "failed":
        return "failed"
    if quality_verdict in {"partial_ready", "PARTIAL"}:
        return "partial_ready"
    if quality_verdict in {"full_ready", "PASSED"}:
        return "full_ready"
    return quality_verdict.lower()


def _classify_failure(
    error: str,
    metrics: dict[str, Any],
    fq: FinalQuestionsPackage | None,
) -> str:
    section_reason = _section_split_failure_from_error(error)
    if section_reason:
        return section_reason
    lower = error.lower()
    if "prepare failed" in lower or "marker" in lower:
        return "marker_missing"
    if "ocr preflight" in lower or "dependency_missing" in lower:
        return "ocr_failed"
    if "ocr" in lower and ("zero" in lower or "produced_zero" in lower):
        return "ocr_zero_lines"
    if "unsupportedcontent" in lower:
        return "azure_ocr_unsupported_content"
    if "unsupported layout" in lower:
        return "unsupported_layout"
    if "bind-semantically" in lower or "llm" in lower:
        return "llm_failed"
    if fq is None:
        return "final_questions_missing"
    return "pipeline_failed"


def _recommend_action(reason: str | None, metrics: dict[str, Any]) -> str | None:
    actions = {
        "marker_missing": "Install Marker CLI and set MERITRANKER_MARKER_COMMAND if needed.",
        "ocr_failed": "Install OCR dependencies: uv sync --extra azure --extra ocr",
        "ocr_zero_lines": "Run OCR with --ocr-engine azure or inspect azure-page-ocr-status.json.",
        "azure_ocr_unsupported_content": "Fix Azure OCR rendered-image fallback; verify PyMuPDF.",
        "unsupported_layout": "Use --allow-unsupported-layout for experimental question-only mode.",
        "llm_failed": "Check LLM provider credentials and model deployment.",
        "final_questions_missing": "Inspect extraction_package/pipeline-failure.json and rerun.",
        "chosen_option_as_correct_answer": "Chosen Option must never be used as correct answer.",
        "cross_window_option_leakage": "Inspect question-windows.json and window-scoped binding.",
        "semantic_hallucination": "Review semantic binding chunks and remaining issues.",
        "accepted_safe_quality_violation": "Repair accepted_safe items with missing text/options/spans.",
        "accepted_safe_with_incomplete_options": "Ensure accepted_safe requires 4 usable options per MCQ.",
        "answer_solution_mapping_failed": "Inspect solution-windows.json and answer-solution-map.json.",
        "question_solution_section_mixed": "Verify Explanations/Solutions heading detection in section splitter.",
        "question_window_build_failed": "Inspect question-windows.json and section split fallback warnings.",
        "question_solution_split_failed": "Review solution heading detection and question anchor formats.",
        "solution_over_detection": "Inspect solution-windows.json for false numbered anchors.",
        "answer_solution_map_over_detected": "Answer-solution map was capped; verify solution section parsing.",
        "final_questions_empty": "Pipeline produced zero questions; inspect window build and binding stages.",
        "low_answer_available_count": "Check answer-solution map joins in final questions export.",
        "low_accepted_safe_count": "Review final questions JSON quality statuses and source spans.",
        "review_items_remaining": "Inspect final-review-items.json and resolve hold_for_review items.",
        "question_window_over_detection": "Inspect extra-question-candidates.json and question-windows.json.",
        "response_sheet_layout": "Response-sheet layout detected; review canonicalized blocked placeholders.",
        "unsupported_layout": "Repeated numbering or unsupported layout; review canonicalized blocked placeholders.",
        "visual_items_remaining": "Attach or render required visuals for figure/graph questions.",
        "answer_unavailable_items_remaining": "Answers missing but question bank may still be usable.",
        "incomplete_options_remaining": "Recover or manually complete options for remaining MCQs.",
    }
    if reason and reason in actions:
        return actions[reason]
    if not metrics.get("pymupdf_available"):
        return "Install PyMuPDF: uv sync --extra ocr"
    return "Inspect share-log stage timeline and extraction_package artifacts."


def _problem_samples(fq: FinalQuestionsPackage | None, limit: int = 10) -> list[dict[str, str]]:
    if fq is None:
        return []
    ranked: list[tuple[int, dict[str, str]]] = []
    for item in fq.items:
        usable = count_usable_options(item.options)
        score = _problem_sample_priority(item, usable)
        if score <= 0:
            continue
        preview = (item.question_text_raw or "").replace("\n", " ")[:80]
        review_issues = list(item.metadata.review_issues) if item.metadata else []
        ranked.append(
            (
                score,
                {
                    "question_id": item.final_question_id,
                    "question_number": str(item.question_number or ""),
                    "status": item.metadata.status if item.metadata else item.quality_status.value,
                    "issues": ", ".join(review_issues[:5]) or ", ".join(item.issues[:5]) or "none",
                    "short_question_preview": preview or "(empty)",
                    "recommended_action": _item_action(item),
                },
            ),
        )
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [sample for _, sample in ranked[:limit]]


def _fallback_problem_samples(
    fq: FinalQuestionsPackage | None,
    limit: int = 10,
) -> list[dict[str, str]]:
    if fq is None:
        return []
    out: list[dict[str, str]] = []
    for item in fq.items:
        status = item.metadata.status if item.metadata else ""
        if status not in {"blocked", "review", "visual_required"}:
            continue
        review_issues = list(item.metadata.review_issues) if item.metadata else []
        out.append(
            {
                "question_id": item.final_question_id,
                "question_number": str(item.question_number or ""),
                "status": status,
                "issues": ", ".join(review_issues[:5]) or "none",
                "short_question_preview": (item.question_text_raw or "")[:80] or "(empty)",
                "recommended_action": _item_action(item),
            },
        )
        if len(out) >= limit:
            break
    return out


def _problem_sample_priority(item, usable: int) -> int:
    status = item.metadata.status if item.metadata else ""
    review_issues = set(item.metadata.review_issues if item.metadata else [])

    if status == "blocked" and "hallucination_suspected" in review_issues:
        return 950
    if status == "blocked" and "question_missing_from_extraction" in review_issues:
        return 1000
    if status == "visual_required":
        return 900
    if status == "review" and "incomplete_options" in review_issues:
        return 800
    if status == "review" and "question_text_uncertain" in review_issues:
        return 750
    if status == "ready" and "answer_key_not_in_options" in review_issues:
        return 600
    if status == "ready" and "expected_answer_missing" in review_issues:
        return 500
    if status == "ready" and "expected_solution_missing" in review_issues:
        return 450
    if status == "blocked":
        return 400
    if status == "review":
        return 300
    if status == "ready" and usable < 4:
        return 200
    if review_issues:
        return 100
    return 0


def _item_action(item) -> str:
    status = item.metadata.status if item.metadata else item.quality_status.value
    if status == "visual_required":
        return "Review visual assets and option images."
    if status == "blocked" and "question_missing_from_extraction" in (item.metadata.review_issues if item.metadata else []):
        return "Question missing from extraction; inspect windows or placeholders."
    if status == "ready" and "expected_answer_missing" in (item.metadata.review_issues if item.metadata else []):
        return "Question bank ready; obtain answer for pattern ingestion."
    if status == "ready" and "expected_solution_missing" in (item.metadata.review_issues if item.metadata else []):
        return "Question bank ready; obtain solution for pattern ingestion."
    if any("cross_window" in i for i in item.issues):
        return "Inspect question window scope."
    if any("hallucinated" in i for i in item.issues):
        return "Verify source spans against evidence."
    return "Review item in final questions JSON."


def _render_markdown(
    ctx: ShareLogContext,
    metrics: dict[str, Any],
    run_status: str,
    quality_verdict: str,
    main_reason: str | None,
    next_action: str | None,
    *,
    fq_package: FinalQuestionsPackage | None = None,
) -> str:
    lines = [
        f"# Share Log — {ctx.pdf_file_name}",
        "",
        "## 1. PDF Info",
        "",
        f"- pdf_file_name: {ctx.pdf_file_name}",
        f"- input_path: {ctx.input_path}",
        f"- output_folder: {ctx.output_folder}",
        f"- run_status: {run_status}",
        f"- started_at: {ctx.started_at.isoformat()}",
        f"- duration: {ctx.duration_seconds:.1f}s",
        "",
        "## 2. Tool/Environment Status",
        "",
        f"- marker_available: {metrics['marker_available']}",
        f"- marker_command: {metrics['marker_command']}",
        f"- ocr_requested_engine: {metrics.get('ocr_requested_engine') or 'unknown'}",
        f"- ocr_effective_engine: {metrics.get('ocr_effective_engine') or 'none'}",
        f"- ocr_used: {metrics.get('ocr_used')}",
        f"- ocr_line_count: {metrics.get('ocr_line_count', 0)}",
        f"- azure_di_available: {metrics['azure_di_available']}",
        f"- pymupdf_available: {metrics['pymupdf_available']}",
        f"- llm_provider: {ctx.provider or 'unknown'}",
        f"- llm_model: {ctx.model or 'unknown'}",
        "",
        "## 3. Stage Timeline",
        "",
        "| stage | status | duration | key result | warning/error |",
        "| --- | --- | --- | --- | --- |",
    ]
    events = ctx.stage_events or (ctx.pipeline_result.stage_events if ctx.pipeline_result else [])
    if not events and ctx.pipeline_error:
        lines.append(f"| pipeline | failed | — | — | {ctx.pipeline_error[:120]} |")
    for event in events:
        warn = (event.warning_or_error or "").replace("|", "/").replace("\n", " ")[:120]
        kr = (event.key_result or "").replace("|", "/")
        lines.append(
            f"| {event.stage} | {event.status} | {event.duration_ms}ms | {kr} | {warn} |",
        )

    lines.extend(
        [
            "",
            "## 4. Key Metrics",
            "",
        ],
    )
    metric_keys = [
        "extractor_strategy_requested",
        "extractor_strategy_effective",
        "pdf_profile_summary",
        "sampled_pages",
        "ignored_profile_pages_count",
        "marker_used",
        "azure_used",
        "dual_used",
        "fallback_used",
        "fallback_reason",
        "marker_quality_summary",
        "layout_type_detected",
        "expected_count",
        "total_questions_detected",
        "ready_count",
        "ready_percentage",
        "public_json_audit",
        "review_count",
        "visual_required_count",
        "blocked_count",
        "answer_available_count",
        "solution_available_count",
        "incomplete_options_count",
        "missing_question_count",
        "review_items_count",
        "answer_solution_join_gap_count",
        "accepted_safe_with_incomplete_options_count",
        "chosen_option_detected_count",
        "chosen_option_as_correct_answer_count",
        "hallucination_suspected_count",
        "cross_window_option_span_reuse_count",
        "question_window_count",
        "solution_window_count",
        "answer_solution_map_count",
        "semantic_returned_item_count",
        "ocr_line_count",
    ]
    for key in metric_keys:
        lines.append(f"- {key}: {metrics.get(key)}")

    lines.extend(["", "### Canonicalization (internal)", ""])
    for key in (
        "raw_candidate_count",
        "extra_candidate_count",
        "duplicate_candidate_count",
        "missing_placeholder_count",
        "public_question_count",
    ):
        if key in metrics:
            lines.append(f"- {key}: {metrics.get(key)}")

    lines.extend(
        [
            "",
            "## 5. Quality Verdict",
            "",
            f"**{quality_verdict}**",
            "",
            "## 6. Main Failure Reason",
            "",
            main_reason or ctx.pipeline_error or "none",
            "",
            "## 7. Recommended Next Action",
            "",
            next_action or "none",
            "",
            "## 8. Important Paths",
            "",
        ],
    )
    pkg = ctx.output_folder / EXTRACTION_PACKAGE_DIR
    paths = {
        "questions_json": str(ctx.questions_json_path) if ctx.questions_json_path else "missing",
        "ocr_evidence": str(pkg / OCR_DIR / OCR_EVIDENCE_JSON_NAME),
        "azure_page_status": str(pkg / OCR_DIR / OCR_ENGINE_LOGS_DIR / "azure-page-ocr-status.json"),
        "question_windows": str(pkg / "evidence" / QUESTION_WINDOWS_JSON_NAME),
        "solution_windows": str(pkg / "evidence" / SOLUTION_WINDOWS_JSON_NAME),
        "answer_solution_map": str(pkg / "evidence" / EVIDENCE_ANSWER_SOLUTION_MAP_JSON_NAME),
        "final_questions_report": str(pkg / FINAL_QUESTIONS_DIR / FINAL_QUESTIONS_REPORT_NAME),
        "semantic_evaluation": str(pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME),
        "final_gate_report": str(pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME),
    }
    for key, value in paths.items():
        exists = "exists" if Path(value).exists() else "missing"
        lines.append(f"- {key}: {value} ({exists})")

    fq = fq_package or _load_internal_final_questions(ctx.output_folder)
    lines.extend(["", "## 9. Problem Samples", ""])
    needs_samples = any(
        (metrics.get(key) or 0) > 0
        for key in (
            "review_count",
            "visual_required_count",
            "blocked_count",
            "review_items_count",
            "incomplete_options_count",
            "missing_question_count",
            "extra_candidate_count",
        )
    )
    samples = _problem_samples(fq)
    if not samples and needs_samples:
        samples = _problem_samples(fq, limit=10) or _fallback_problem_samples(fq)
    if not samples:
        lines.append("_No problematic items sampled._")
    else:
        for sample in samples:
            lines.append(
                f"- **{sample['question_id']}** Q{sample['question_number']} `{sample['status']}`",
            )
            lines.append(f"  - issues: {sample['issues']}")
            lines.append(f"  - preview: {sample['short_question_preview']}")
            lines.append(f"  - action: {sample['recommended_action']}")

    return "\n".join(lines) + "\n"


def default_questions_export_path(output_folder: Path, stem: str) -> Path:
    return output_folder / f"{stem}.questions.json"


def internal_final_questions_path(output_folder: Path) -> Path:
    return output_folder / EXTRACTION_PACKAGE_DIR / FINAL_QUESTIONS_DIR / FINAL_QUESTIONS_JSON_NAME
