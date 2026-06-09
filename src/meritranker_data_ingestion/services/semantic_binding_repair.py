"""Deterministic post-LLM repair for semantic binding artifacts (Part 13F)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_EVALUATION_JSON_NAME,
    SEMANTIC_BINDING_EVALUATION_MD_NAME,
    SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME,
    SEMANTIC_BINDING_REPAIR_REPORT_NAME,
    SEMANTIC_BINDING_REPAIR_SUMMARY_MD_NAME,
    SEMANTIC_BINDING_VALIDATION_NAME,
    SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingPackage,
    SemanticBindingQualityThresholds,
    SemanticBindingValidationReport,
)
from meritranker_data_ingestion.services.evidence_resolver import load_document_evidence
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.semantic_numeric_option_repair import (
    apply_numeric_option_repair,
)
from meritranker_data_ingestion.services.semantic_binding_evaluation import (
    build_semantic_binding_evaluation,
    render_evaluation_md,
)
from meritranker_data_ingestion.services.semantic_binding_validator import validate_semantic_items
from meritranker_data_ingestion.services.semantic_key_normalizer import apply_key_normalization
from meritranker_data_ingestion.services.semantic_final_acceptance_gate import (
    sanitize_duplicate_warnings,
)
from meritranker_data_ingestion.services.semantic_binding_evaluation import (
    _duplicate_question_numbers,
)
from meritranker_data_ingestion.services.question_window_builder import load_question_windows
from meritranker_data_ingestion.services.semantic_source_span_resolver import (
    SourceSpanResolverStats,
    resolve_source_spans,
)


class SemanticBindingRepairError(Exception):
    """Raised when semantic binding repair cannot proceed."""


class SemanticBindingRepairReport(BaseModel):
    total_items: int = 0
    option_keys_normalized_count: int = 0
    answer_keys_normalized_count: int = 0
    question_spans_resolved_count: int = 0
    option_spans_resolved_count: int = 0
    answer_spans_resolved_count: int = 0
    solution_spans_resolved_count: int = 0
    unresolved_question_spans_count: int = 0
    unresolved_option_spans_count: int = 0
    unresolved_answer_spans_count: int = 0
    options_filled_from_evidence_count: int = 0
    answer_key_not_in_options_before: int = 0
    answer_key_not_in_options_after: int = 0
    source_span_missing_before: int = 0
    source_span_missing_after: int = 0
    accepted_before: int = 0
    accepted_after: int = 0
    review_required_before: int = 0
    review_required_after: int = 0
    rejected_before: int = 0
    rejected_after: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    repaired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SemanticBindingRepairResult:
    package: SemanticBindingPackage
    validation: SemanticBindingValidationReport
    repair_report: SemanticBindingRepairReport
    repaired_path: Path
    repair_report_path: Path
    validation_path: Path
    evaluation_path: Path
    summary_path: Path


def repair_semantic_binding_package(
    package_dir: Path,
    *,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    expected_count: int | None = None,
    overwrite_semantic_binding: bool = False,
    thresholds: SemanticBindingQualityThresholds | None = None,
) -> SemanticBindingRepairResult:
    """Normalize keys, resolve source spans, re-validate — no LLM calls."""
    resolved = resolve_path(package_dir)
    out_dir = resolved / SEMANTIC_BINDING_DIR
    source_path = out_dir / SEMANTIC_BOUND_QUESTIONS_NAME
    if not source_path.exists():
        raise SemanticBindingRepairError(
            f"Missing semantic binding output at {source_path}. Run bind-semantically first.",
        )

    try:
        evidence, _ = load_document_evidence(resolved)
    except FileNotFoundError as exc:
        raise SemanticBindingRepairError(f"{exc}. Run normalize-evidence first.") from exc
    package = SemanticBindingPackage.model_validate_json(source_path.read_text(encoding="utf-8"))

    validation_before = validate_semantic_items(
        package.items,
        package.metadata_candidates,
        evidence,
        answer_mode=answer_mode,
        expected_count=expected_count,
    )

    windows_pkg = load_question_windows(resolved)
    option_norm, answer_norm = apply_key_normalization(package)
    span_stats = resolve_source_spans(
        package,
        evidence,
        answer_mode=answer_mode,
        windows_pkg=windows_pkg,
    )
    numeric_stats = apply_numeric_option_repair(
        package,
        evidence,
        windows_pkg=windows_pkg,
    )
    apply_key_normalization(package)

    validation_after = validate_semantic_items(
        package.items,
        package.metadata_candidates,
        evidence,
        answer_mode=answer_mode,
        expected_count=expected_count,
    )
    for item in package.items:
        if item.quarantine_status == "quarantined" or item.excluded_from_export:
            item.quarantine_status = "quarantined"
            item.excluded_from_export = True
            item.binding_status = SemanticBindingItemStatus.REJECTED
    package.validation_summary = validation_after

    repair_report = SemanticBindingRepairReport(
        total_items=len(package.items),
        option_keys_normalized_count=option_norm,
        answer_keys_normalized_count=answer_norm,
        question_spans_resolved_count=span_stats.question_spans_resolved_count,
        option_spans_resolved_count=span_stats.option_spans_resolved_count,
        answer_spans_resolved_count=span_stats.answer_spans_resolved_count,
        solution_spans_resolved_count=span_stats.solution_spans_resolved_count,
        unresolved_question_spans_count=span_stats.unresolved_question_spans_count,
        unresolved_option_spans_count=span_stats.unresolved_option_spans_count,
        unresolved_answer_spans_count=span_stats.unresolved_answer_spans_count,
        options_filled_from_evidence_count=(
            span_stats.options_filled_from_evidence_count
            + numeric_stats.response_sheet_bound_count
            + numeric_stats.options_split_count
        ),
        answer_key_not_in_options_before=validation_before.answer_key_not_in_options_count,
        answer_key_not_in_options_after=validation_after.answer_key_not_in_options_count,
        source_span_missing_before=validation_before.source_span_missing_count,
        source_span_missing_after=validation_after.source_span_missing_count,
        accepted_before=validation_before.accepted_count,
        accepted_after=validation_after.accepted_count,
        review_required_before=validation_before.review_required_count,
        review_required_after=validation_after.review_required_count,
        rejected_before=validation_before.rejected_count,
        rejected_after=validation_after.rejected_count,
        warnings=list(package.warnings) + span_stats.warnings,
        errors=list(package.errors),
    )

    package.warnings = sanitize_duplicate_warnings(
        package.warnings,
        duplicate_question_numbers=_duplicate_question_numbers(package.items),
        duplicate_count=validation_after.duplicate_question_number_count,
    )

    evaluation = build_semantic_binding_evaluation(
        package,
        validation_after,
        expected_count=expected_count,
        answer_mode=answer_mode,
        package_dir=resolved,
        thresholds=thresholds,
    )

    repaired_path = out_dir / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME
    repair_report_path = out_dir / SEMANTIC_BINDING_REPAIR_REPORT_NAME
    validation_path = out_dir / SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME
    evaluation_path = out_dir / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME
    summary_path = out_dir / SEMANTIC_BINDING_REPAIR_SUMMARY_MD_NAME

    repaired_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    repair_report_path.write_text(repair_report.model_dump_json(indent=2), encoding="utf-8")
    validation_path.write_text(validation_after.model_dump_json(indent=2), encoding="utf-8")
    evaluation_path.write_text(evaluation.model_dump_json(indent=2), encoding="utf-8")
    summary_path.write_text(_render_repair_summary_md(repair_report, evaluation), encoding="utf-8")

    if overwrite_semantic_binding:
        source_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / SEMANTIC_BINDING_VALIDATION_NAME).write_text(
            validation_after.model_dump_json(indent=2),
            encoding="utf-8",
        )
        (out_dir / SEMANTIC_BINDING_EVALUATION_JSON_NAME).write_text(
            evaluation.model_dump_json(indent=2),
            encoding="utf-8",
        )
        (out_dir / SEMANTIC_BINDING_EVALUATION_MD_NAME).write_text(
            render_evaluation_md(evaluation),
            encoding="utf-8",
        )

    return SemanticBindingRepairResult(
        package=package,
        validation=validation_after,
        repair_report=repair_report,
        repaired_path=repaired_path,
        repair_report_path=repair_report_path,
        validation_path=validation_path,
        evaluation_path=evaluation_path,
        summary_path=summary_path,
    )


def _render_repair_summary_md(repair_report: SemanticBindingRepairReport, evaluation) -> str:
    lines = [
        "# Semantic Binding Repair Summary",
        "",
        f"- total_items: {repair_report.total_items}",
        f"- option_keys_normalized: {repair_report.option_keys_normalized_count}",
        f"- answer_keys_normalized: {repair_report.answer_keys_normalized_count}",
        f"- options_filled_from_evidence: {repair_report.options_filled_from_evidence_count}",
        "",
        "## Source spans",
        f"- question_spans_resolved: {repair_report.question_spans_resolved_count}",
        f"- option_spans_resolved: {repair_report.option_spans_resolved_count}",
        f"- answer_spans_resolved: {repair_report.answer_spans_resolved_count}",
        f"- solution_spans_resolved: {repair_report.solution_spans_resolved_count}",
        f"- unresolved_question_spans: {repair_report.unresolved_question_spans_count}",
        f"- unresolved_option_spans: {repair_report.unresolved_option_spans_count}",
        f"- unresolved_answer_spans: {repair_report.unresolved_answer_spans_count}",
        "",
        "## Before / after",
        f"- source_span_missing: {repair_report.source_span_missing_before} → {repair_report.source_span_missing_after}",
        f"- answer_key_not_in_options: {repair_report.answer_key_not_in_options_before} → {repair_report.answer_key_not_in_options_after}",
        f"- accepted: {repair_report.accepted_before} → {repair_report.accepted_after}",
        f"- review_required: {repair_report.review_required_before} → {repair_report.review_required_after}",
        f"- rejected: {repair_report.rejected_before} → {repair_report.rejected_after}",
        "",
        f"## Evaluation quality_status: {evaluation.quality_status}",
    ]
    if repair_report.warnings:
        lines.extend(["", "## Warnings", *[f"- {w}" for w in repair_report.warnings]])
    return "\n".join(lines) + "\n"
