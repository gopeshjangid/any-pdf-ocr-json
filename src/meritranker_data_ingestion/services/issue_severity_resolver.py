"""Blocking vs non-blocking review issue classification (Part 14S)."""

from __future__ import annotations

BLOCKING_EXTRACTION_ISSUES = frozenset({
    "question_missing_from_extraction",
    "question_text_uncertain",
    "incomplete_options",
    "visual_syntax_missing",
    "formula_ocr_uncertain",
    "table_ocr_uncertain",
    "hallucination_suspected",
    "cross_window_option_leakage",
    "corrupt_question_text",
    "chosen_option_not_correct_answer_source",
})

NON_BLOCKING_ANSWER_ISSUES = frozenset({
    "expected_answer_missing",
    "expected_solution_missing",
    "answer_key_not_in_options",
    "correct_answer_text_unavailable",
    "solution_unavailable",
})

HALLUCINATION_ISSUE_MARKERS = (
    "hallucinated",
    "hallucination_suspected",
)

QUARANTINE_ISSUES = frozenset({
    "quarantined",
    "quarantined_or_excluded",
    "rejected_bad_item",
})


def is_blocking_extraction_issue(issue: str) -> bool:
    """Return True when issue should force review/blocked/visual_required."""
    lower = (issue or "").strip().lower()
    if not lower:
        return False
    if lower in BLOCKING_EXTRACTION_ISSUES:
        return True
    if lower in NON_BLOCKING_ANSWER_ISSUES:
        return False
    if any(marker in lower for marker in HALLUCINATION_ISSUE_MARKERS):
        return True
    if "cross_window" in lower:
        return True
    if lower.startswith("bad_item_classes:"):
        return True
    if "corrupt" in lower and "question" in lower:
        return True
    return False


def has_blocking_extraction_issues(issues: list[str]) -> bool:
    return any(is_blocking_extraction_issue(issue) for issue in issues)


def is_hallucination_issue(issue: str) -> bool:
    lower = (issue or "").lower()
    return any(marker in lower for marker in HALLUCINATION_ISSUE_MARKERS)


def is_quarantine_issue(issue: str) -> bool:
    lower = (issue or "").strip().lower()
    return lower in QUARANTINE_ISSUES or lower.startswith("bad_item_classes:")
