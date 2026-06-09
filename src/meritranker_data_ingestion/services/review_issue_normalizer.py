"""Normalize review issue strings for public export (Part 14P)."""

from __future__ import annotations

import re

ALLOWED_REVIEW_ISSUES = frozenset({
    "incomplete_options",
    "question_text_uncertain",
    "formula_ocr_uncertain",
    "table_ocr_uncertain",
    "visual_syntax_missing",
    "answer_key_not_in_options",
    "answer_solution_conflict",
    "expected_answer_missing",
    "expected_solution_missing",
    "question_missing_from_extraction",
    "chosen_option_not_correct_answer_source",
    "cross_window_option_leakage",
    "hallucination_suspected",
})

ISSUE_ALIASES: dict[str, str] = {
    "source_text_uncertain": "question_text_uncertain",
    "noise_in_question_text": "question_text_uncertain",
    "weak_source_grounding": "question_text_uncertain",
    "table_extraction_corrupt": "table_ocr_uncertain",
    "correct_answer_text_unavailable": "answer_key_not_in_options",
    "visual_required": "visual_syntax_missing",
    "original_visual_required": "visual_syntax_missing",
    "answer_unavailable": "expected_answer_missing",
    "missing_answer_answer_key_only_mode": "expected_answer_missing",
    "answer_key_not_in_options": "answer_key_not_in_options",
    "incomplete_options": "incomplete_options",
    "expected_answer_missing": "expected_answer_missing",
    "expected_solution_missing": "expected_solution_missing",
    "question_missing_from_extraction": "question_missing_from_extraction",
    "chosen_option_not_correct_answer_source": "chosen_option_not_correct_answer_source",
    "question_text_missing": "question_text_uncertain",
    "hallucinated": "hallucination_suspected",
    "cross_window": "cross_window_option_leakage",
    "cross_window_option_span_reuse": "cross_window_option_leakage",
}

RE_FORMULA = re.compile(r"\\frac|\\left|\\right|\\sqrt", re.I)


def normalize_review_issues(
    issues: list[str],
    *,
    question_text: str = "",
) -> list[str]:
    """Map internal/raw issues to short normalized public issue codes."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in issues:
        code = _normalize_one(raw, question_text=question_text)
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _normalize_one(raw: str, *, question_text: str) -> str | None:
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    lower = text.lower()
    if lower in ALLOWED_REVIEW_ISSUES:
        return lower
    if lower in ISSUE_ALIASES:
        return ISSUE_ALIASES[lower]
    if lower.startswith("bad_item_classes:"):
        return "question_text_uncertain"
    if "hallucinat" in lower:
        return "hallucination_suspected"
    if "cross_window" in lower:
        return "cross_window_option_leakage"
    if "incomplete_options" in lower or "option_incomplete" in lower:
        return "incomplete_options"
    if "answer_key_not_in_options" in lower:
        return "answer_key_not_in_options"
    if "formula" in lower or (RE_FORMULA.search(question_text) and "option" in lower):
        return "formula_ocr_uncertain"
    if "table" in lower and ("chart" in lower or "graph" in lower or "corrupt" in lower):
        return "table_ocr_uncertain"
    if len(text) > 64 or " " in text and ":" not in text:
        return None
    slug = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")
    if slug in ALLOWED_REVIEW_ISSUES:
        return slug
    if slug in ISSUE_ALIASES:
        return ISSUE_ALIASES[slug]
    return None
