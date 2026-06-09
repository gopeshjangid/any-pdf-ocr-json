"""Simplified per-question status resolution (Part 14O)."""

from __future__ import annotations

from dataclasses import dataclass

from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionItemMetadata,
    FinalQuestionQualityStatus,
)
from meritranker_data_ingestion.services.deterministic_option_parser import count_usable_options
from meritranker_data_ingestion.services.final_item_acceptance_gate import strip_stale_window_issues
from meritranker_data_ingestion.services.public_visual_serializer import visual_has_syntax
from meritranker_data_ingestion.services.issue_severity_resolver import (
    has_blocking_extraction_issues,
    is_hallucination_issue,
    is_quarantine_issue,
)
from meritranker_data_ingestion.services.review_issue_normalizer import normalize_review_issues
from meritranker_data_ingestion.services.visual_detection import detect_visual_dependency

CORRUPTION_MARKERS = (
    "hallucinated",
    "noise_in_question_text",
    "cross_window",
    "table_extraction_corrupt",
    "weak_source_grounding",
)

SOLVED_ANSWER_SOURCE_MODES = frozenset({
    "separate_solution_section",
    "inline_answer",
    "answer_key_table",
})


@dataclass(frozen=True)
class StatusCounts:
    ready_count: int
    review_count: int
    visual_required_count: int
    blocked_count: int
    review_items_count: int


# Backward-compatible alias for enhancer/export builder
ReadinessCounts = StatusCounts


def answers_expected_from_profile(answer_source_mode: str) -> bool:
    return answer_source_mode in SOLVED_ANSWER_SOURCE_MODES


def apply_readiness_metadata(
    item: FinalQuestionItem,
    *,
    answers_expected: bool = False,
) -> FinalQuestionItem:
    metadata = resolve_item_readiness(item, answers_expected=answers_expected)
    quality = (
        FinalQuestionQualityStatus.BLOCKED
        if metadata.status == "blocked"
        else _map_status_to_legacy_quality(metadata, item)
    )
    return item.model_copy(
        update={
            "metadata": metadata,
            "quality_status": quality,
        },
    )


def resolve_item_readiness(
    item: FinalQuestionItem,
    *,
    answers_expected: bool = False,
) -> FinalQuestionItemMetadata:
    if _is_blocked_item(item):
        blocked_issues = list(item.issues)
        if (
            "question_missing_from_extraction" not in blocked_issues
            and not (item.question_text_raw or "").strip()
        ):
            blocked_issues.append("question_missing_from_extraction")
        return FinalQuestionItemMetadata(
            status="blocked",
            review_issues=normalize_review_issues(
                blocked_issues,
                question_text=item.question_text_raw or "",
            ),
        )

    issues = strip_stale_window_issues(list(item.issues))
    review_issues: list[str] = []
    usable = count_usable_options(item.options)
    visual_needed, _ = detect_visual_dependency(item)
    syntax_ready = any(visual_has_syntax(v) for v in item.visuals)
    corrupted = _has_corruption(issues)
    answer_available = bool(item.correct_answer_key and item.correct_answer_text)
    solution_available = bool(item.solution_text_raw and item.solution_text_raw.strip())

    if usable < 4:
        review_issues.append("incomplete_options")
    if corrupted:
        review_issues.append("question_text_uncertain")
    if "answer_key_not_in_options" in issues:
        review_issues.append("answer_key_not_in_options")
    if "correct_answer_text_unavailable" in issues:
        review_issues.append("answer_key_not_in_options")

    if answers_expected and not answer_available:
        review_issues.append("expected_answer_missing")
    if answers_expected and not solution_available:
        if any(
            marker in issue
            for issue in issues
            for marker in ("missing_solution", "solution_unavailable", "solution_missing")
        ):
            review_issues.append("expected_solution_missing")

    if item.chosen_option_canonical_key and item.correct_answer_key == item.chosen_option_canonical_key:
        review_issues.append("chosen_option_not_correct_answer_source")

    normalized = normalize_review_issues(
        review_issues + [i for i in issues if i not in review_issues],
        question_text=item.question_text_raw or "",
    )

    if visual_needed and not syntax_ready:
        return FinalQuestionItemMetadata(
            status="visual_required",
            review_issues=_dedupe(normalized + ["visual_syntax_missing"]),
        )

    if usable < 4:
        return FinalQuestionItemMetadata(
            status="review",
            review_issues=_dedupe(normalized + ["incomplete_options"]),
        )
    if corrupted or has_blocking_extraction_issues(normalized):
        return FinalQuestionItemMetadata(status="review", review_issues=normalized)

    if not item.question_text_raw.strip():
        return FinalQuestionItemMetadata(
            status="blocked",
            review_issues=_dedupe(normalized + ["question_text_uncertain"]),
        )

    return FinalQuestionItemMetadata(status="ready", review_issues=normalized)


def compute_readiness_counts(items: list[FinalQuestionItem]) -> StatusCounts:
    return compute_status_counts(items)


def compute_status_counts(items: list[FinalQuestionItem]) -> StatusCounts:
    ready = sum(1 for item in items if item.metadata.status == "ready")
    review = sum(1 for item in items if item.metadata.status == "review")
    visual = sum(1 for item in items if item.metadata.status == "visual_required")
    blocked = sum(1 for item in items if item.metadata.status == "blocked")
    not_ready = len(items) - ready
    return StatusCounts(
        ready_count=ready,
        review_count=review,
        visual_required_count=visual,
        blocked_count=blocked,
        review_items_count=not_ready,
    )


def _map_status_to_legacy_quality(
    metadata: FinalQuestionItemMetadata,
    item: FinalQuestionItem,
) -> FinalQuestionQualityStatus:
    if metadata.status == "visual_required":
        return FinalQuestionQualityStatus.VISUAL_REQUIRED
    if metadata.status == "blocked":
        return FinalQuestionQualityStatus.BLOCKED
    if metadata.status == "ready" and not item.correct_answer_key:
        return FinalQuestionQualityStatus.ANSWER_UNAVAILABLE
    if metadata.status == "ready" and item.correct_answer_key and item.correct_answer_text:
        return FinalQuestionQualityStatus.ACCEPTED_SAFE
    return FinalQuestionQualityStatus.REVIEW_REQUIRED


def _is_blocked_item(item: FinalQuestionItem) -> bool:
    if "question_missing_from_extraction" in item.issues:
        return True
    if any(is_quarantine_issue(issue) for issue in item.issues):
        return True
    if any(is_hallucination_issue(issue) for issue in item.issues):
        return item.quality_status == FinalQuestionQualityStatus.BLOCKED
    if item.quality_status == FinalQuestionQualityStatus.BLOCKED:
        return not (item.question_text_raw or "").strip()
    return any(issue.startswith("bad_item_classes:") for issue in item.issues)


def _has_corruption(issues: list[str]) -> bool:
    return any(any(marker in issue for marker in CORRUPTION_MARKERS) for issue in issues)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
