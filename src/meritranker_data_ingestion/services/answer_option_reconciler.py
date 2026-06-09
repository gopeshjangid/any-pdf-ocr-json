"""Reconcile answer keys with recovered options after window supplement (Part 14Q)."""

from __future__ import annotations

from meritranker_data_ingestion.schemas.evidence_answer_solution_map import AnswerSolutionMapEntry
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalAnswerSource,
    FinalQuestionItem,
    FinalQuestionOption,
)
from meritranker_data_ingestion.schemas.question_window import QuestionWindow, QuestionWindowsPackage
from meritranker_data_ingestion.services.semantic_key_normalizer import normalize_answer_key

_STALE_ANSWER_ISSUES = frozenset({
    "answer_key_not_in_options",
    "correct_answer_text_unavailable",
    "missing_answer_answer_key_only_mode",
    "answer_unavailable",
})


def index_question_windows(windows_pkg: QuestionWindowsPackage | None) -> dict[int, QuestionWindow]:
    """Pick the best question-section window when duplicate numbers exist."""
    if windows_pkg is None:
        return {}
    best: dict[int, QuestionWindow] = {}
    for window in windows_pkg.windows:
        qn = window.parsed_question_number
        if qn is None:
            continue
        current = best.get(qn)
        if current is None or _window_score(window) < _window_score(current):
            best[qn] = window
    return best


def reconcile_item_answers(
    item: FinalQuestionItem,
    *,
    map_entry: AnswerSolutionMapEntry | None,
) -> FinalQuestionItem:
    """Refresh answer text after options change; drop stale answer join issues."""
    issues = [issue for issue in item.issues if issue not in _STALE_ANSWER_ISSUES]
    answer_key = item.correct_answer_key
    answer_text = item.correct_answer_text
    answer_source = item.answer_source
    solution_text = item.solution_text_raw
    solution_source = item.solution_source

    if map_entry is not None:
        if map_entry.solution_text and map_entry.solution_text.strip():
            solution_text = map_entry.solution_text.strip()
            solution_source = "solution_section"
        if map_entry.answer_label:
            answer_key = map_entry.answer_label.strip().upper()[:1] or map_entry.answer_label
            answer_text = _option_text_for_key(item.options, answer_key)
            if answer_text:
                answer_source = FinalAnswerSource.SEPARATE_SOLUTION_SECTION
            else:
                issues.append("answer_key_not_in_options")

    if answer_key and not answer_text:
        answer_text = _option_text_for_key(item.options, answer_key)
        if not answer_text:
            issues.append("answer_key_not_in_options")

    return item.model_copy(
        update={
            "correct_answer_key": answer_key,
            "correct_answer_text": answer_text,
            "answer_source": answer_source,
            "solution_text_raw": solution_text,
            "solution_source": solution_source,
            "issues": _dedupe(issues),
        },
    )


def _window_score(window: QuestionWindow) -> tuple[int, int, int]:
    order_match = 0 if window.global_order == (window.parsed_question_number or -1) else 1
    return (order_match, -len(window.line_ids), window.global_order)


def _option_text_for_key(
    options: list[FinalQuestionOption],
    answer_key: str,
) -> str | None:
    target, _ = normalize_answer_key(answer_key)
    if not target:
        return None
    for opt in options:
        canon, _ = normalize_answer_key(opt.canonical_key or opt.key or opt.key_raw)
        if canon == target and opt.text_raw.strip():
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
