"""Strict acceptance gate for unified final-question items (Part 14L)."""

from __future__ import annotations

from dataclasses import dataclass

from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionQualityStatus,
)
from meritranker_data_ingestion.services.deterministic_option_parser import count_usable_options
from meritranker_data_ingestion.services.semantic_key_normalizer import normalize_answer_key

STALE_WINDOW_ISSUE_PREFIXES = ("options_found:",)


@dataclass(frozen=True)
class FinalItemGateMetrics:
    questions_with_4_options_count: int
    questions_with_incomplete_options_count: int
    accepted_safe_with_incomplete_options_count: int


def apply_final_item_acceptance_gate(item: FinalQuestionItem) -> FinalQuestionItem:
    """Enforce single-choice MCQ rules on exported final items."""
    issues = strip_stale_window_issues(list(item.issues))
    quality = item.quality_status
    usable = count_usable_options(item.options)

    if usable < 4:
        if "incomplete_options" not in issues:
            issues.append("incomplete_options")
        if quality == FinalQuestionQualityStatus.ACCEPTED_SAFE:
            quality = FinalQuestionQualityStatus.REVIEW_REQUIRED

    answer_text = (item.correct_answer_text or "").strip()
    if item.correct_answer_key:
        matched = _option_text_for_key(item.options, item.correct_answer_key)
        if matched:
            if not answer_text or matched != answer_text:
                item = item.model_copy(update={"correct_answer_text": matched})
                answer_text = matched
        elif not answer_text:
            if "correct_answer_text_unavailable" not in issues:
                issues.append("correct_answer_text_unavailable")
            if quality == FinalQuestionQualityStatus.ACCEPTED_SAFE:
                quality = FinalQuestionQualityStatus.REVIEW_REQUIRED
        else:
            if "answer_key_not_in_options" not in issues:
                issues.append("answer_key_not_in_options")
            if quality == FinalQuestionQualityStatus.ACCEPTED_SAFE:
                quality = FinalQuestionQualityStatus.REVIEW_REQUIRED

    return item.model_copy(
        update={
            "quality_status": quality,
            "issues": _dedupe(issues),
        },
    )


def apply_gate_to_items(items: list[FinalQuestionItem]) -> list[FinalQuestionItem]:
    return [apply_final_item_acceptance_gate(item) for item in items]


def compute_final_item_gate_metrics(items: list[FinalQuestionItem]) -> FinalItemGateMetrics:
    with_4 = 0
    incomplete = 0
    accepted_safe_incomplete = 0
    for item in items:
        usable = count_usable_options(item.options)
        if usable >= 4:
            with_4 += 1
        else:
            incomplete += 1
        if (
            item.quality_status == FinalQuestionQualityStatus.ACCEPTED_SAFE
            and usable < 4
        ):
            accepted_safe_incomplete += 1
    return FinalItemGateMetrics(
        questions_with_4_options_count=with_4,
        questions_with_incomplete_options_count=incomplete,
        accepted_safe_with_incomplete_options_count=accepted_safe_incomplete,
    )


def strip_stale_window_issues(issues: list[str]) -> list[str]:
    return [
        issue
        for issue in issues
        if not any(issue.startswith(prefix) for prefix in STALE_WINDOW_ISSUE_PREFIXES)
    ]


def _option_text_for_key(options: list, answer_key: str) -> str | None:
    for opt in options:
        canon, _ = normalize_answer_key(
            getattr(opt, "canonical_key", None) or getattr(opt, "key", None) or getattr(opt, "key_raw", None),
        )
        if canon == answer_key and (getattr(opt, "text_raw", None) or "").strip():
            return opt.text_raw.strip()
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
