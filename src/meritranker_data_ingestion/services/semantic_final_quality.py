"""Quality propagation and count reconciliation for semantic final export."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBoundQuestion,
    SemanticBindingPackage,
)


@dataclass
class CountReconciliation:
    expected_count: int | None
    actual_semantic_count: int
    count_match: bool
    overflow_count: int = 0
    extra_item_ids: list[str] = field(default_factory=list)
    extra_question_numbers: list[int] = field(default_factory=list)
    non_numeric_question_ids: list[str] = field(default_factory=list)
    out_of_range_question_numbers: list[int] = field(default_factory=list)


@dataclass
class FinalExportQualityResult:
    quality_status_from_semantic_evaluation: str
    final_export_quality_status: str
    source_quality_status: str
    ready_for_full_paper_ingestion: bool
    ready_for_partial_accepted_ingestion: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def compute_count_reconciliation(
    package: SemanticBindingPackage,
    *,
    expected_count: int | None,
    evaluation: dict | None = None,
) -> CountReconciliation:
    """Reconcile expected question count against semantic binding items."""
    items = package.items
    actual = len(items)
    count_match = expected_count is None or actual == expected_count
    overflow_count = max(0, actual - expected_count) if expected_count is not None else 0

    extra_item_ids: list[str] = []
    extra_question_numbers: list[int] = []
    non_numeric_question_ids: list[str] = []
    out_of_range_question_numbers: list[int] = []

    for item in items:
        reason = classify_extra_item(item, expected_count=expected_count)
        if reason is None:
            continue
        extra_item_ids.append(item.semantic_question_id)
        if reason == "non_numeric_question_number":
            non_numeric_question_ids.append(item.semantic_question_id)
        elif reason == "out_of_range_question_number" and item.question_number is not None:
            out_of_range_question_numbers.append(item.question_number)
        if item.question_number is not None and item.question_number not in extra_question_numbers:
            if reason in ("out_of_range_question_number", "extra_question_candidate"):
                extra_question_numbers.append(item.question_number)

    if evaluation:
        for qnum in evaluation.get("duplicate_question_numbers") or []:
            if qnum not in extra_question_numbers:
                extra_question_numbers.append(qnum)

    return CountReconciliation(
        expected_count=expected_count,
        actual_semantic_count=actual,
        count_match=count_match,
        overflow_count=overflow_count,
        extra_item_ids=extra_item_ids,
        extra_question_numbers=sorted(set(extra_question_numbers)),
        non_numeric_question_ids=non_numeric_question_ids,
        out_of_range_question_numbers=sorted(set(out_of_range_question_numbers)),
    )


def classify_extra_item(
    item: SemanticBoundQuestion,
    *,
    expected_count: int | None,
) -> str | None:
    """Return exclusion reason for overflow/extra items, or None if in-range."""
    if item.question_number is None:
        return "non_numeric_question_number"

    if expected_count is not None:
        if item.question_number < 1 or item.question_number > expected_count:
            return "out_of_range_question_number"

    semantic_id_match = re.match(r"sq_(\d+)$", item.semantic_question_id)
    if semantic_id_match and expected_count is not None:
        semantic_index = int(semantic_id_match.group(1))
        if semantic_index > expected_count:
            return "extra_question_candidate"

    return None


def is_extra_item(item: SemanticBoundQuestion, *, expected_count: int | None) -> bool:
    return classify_extra_item(item, expected_count=expected_count) is not None


def derive_final_export_quality(
    evaluation: dict,
    reconciliation: CountReconciliation,
    *,
    accepted_exported_count: int,
    excluded_count: int,
    accepted_safe_exported_count: int | None = None,
) -> FinalExportQualityResult:
    """Propagate semantic evaluation quality into final export report."""
    semantic_quality = str(evaluation.get("quality_status") or "warning")
    hallucination = int(evaluation.get("hallucination_suspected_count") or 0)
    source_span_missing = int(evaluation.get("source_span_missing_count") or 0)
    answer_key_not_in_options = int(evaluation.get("answer_key_not_in_options_count") or 0)

    warnings: list[str] = []
    errors: list[str] = []
    status = "passed"

    if hallucination > 0:
        status = "failed"
        if "semantic_quality_failed" not in errors:
            errors.append("semantic_quality_failed")
        errors.append(f"hallucination_suspected_count={hallucination}")

    if reconciliation.expected_count is not None and not reconciliation.count_match:
        status = "failed"
        if "semantic_quality_failed" not in errors:
            errors.append("semantic_quality_failed")
        errors.append(
            "expected_count_mismatch: "
            f"expected={reconciliation.expected_count} "
            f"actual={reconciliation.actual_semantic_count}",
        )

    if semantic_quality == "failed" and status != "failed":
        status = "failed"
        if "semantic_quality_failed" not in errors:
            errors.append("semantic_quality_failed")

    if source_span_missing > 0:
        warnings.append(f"source_span_missing_count={source_span_missing}")
        if status == "passed":
            status = "warning"

    if answer_key_not_in_options > 0:
        warnings.append(f"answer_key_not_in_options_count={answer_key_not_in_options}")
        if status == "passed":
            status = "warning"

    if semantic_quality == "warning" and status == "passed":
        status = "warning"

    if excluded_count > 0 and status == "passed":
        status = "partial"

    source_quality_status = semantic_quality if semantic_quality == "failed" else semantic_quality

    ready_for_full = (
        status == "passed"
        and reconciliation.count_match
        and excluded_count == 0
        and hallucination == 0
    )
    safe_exported = (
        accepted_safe_exported_count
        if accepted_safe_exported_count is not None
        else accepted_exported_count
    )
    ready_for_partial = safe_exported > 0 and safe_exported == accepted_exported_count

    return FinalExportQualityResult(
        quality_status_from_semantic_evaluation=semantic_quality,
        final_export_quality_status=status,
        source_quality_status=source_quality_status,
        ready_for_full_paper_ingestion=ready_for_full,
        ready_for_partial_accepted_ingestion=ready_for_partial,
        warnings=warnings,
        errors=errors,
    )
