"""Answer-solution join gap diagnostics (Part 14M)."""

from __future__ import annotations

from dataclasses import dataclass

from meritranker_data_ingestion.schemas.evidence_answer_solution_map import (
    AnswerSolutionMapEntry,
    AnswerSolutionMapPackage,
)
from meritranker_data_ingestion.schemas.final_questions_export import FinalQuestionItem
from meritranker_data_ingestion.services.deterministic_option_parser import count_usable_options


@dataclass(frozen=True)
class AnswerSolutionJoinGap:
    question_number: int
    answer_label: str | None
    reason: str
    issues: list[str]


@dataclass(frozen=True)
class AnswerSolutionJoinDiagnostics:
    answer_solution_join_gap_count: int
    answer_solution_join_gap_items: list[AnswerSolutionJoinGap]


def diagnose_answer_solution_join_gaps(
    items: list[FinalQuestionItem],
    map_pkg: AnswerSolutionMapPackage | None,
) -> AnswerSolutionJoinDiagnostics:
    if map_pkg is None:
        return AnswerSolutionJoinDiagnostics(0, [])

    by_qnum = {
        item.question_number: item
        for item in items
        if item.question_number is not None
    }
    gaps: list[AnswerSolutionJoinGap] = []

    for entry in map_pkg.entries:
        if not entry.answer_label:
            continue
        item = by_qnum.get(entry.question_number)
        if item and item.correct_answer_key and item.correct_answer_text:
            continue
        gaps.append(
            AnswerSolutionJoinGap(
                question_number=entry.question_number,
                answer_label=entry.answer_label,
                reason=_classify_gap_reason(entry, item),
                issues=list(entry.issues) + (list(item.issues) if item else ["question_missing"]),
            ),
        )

    return AnswerSolutionJoinDiagnostics(
        answer_solution_join_gap_count=len(gaps),
        answer_solution_join_gap_items=gaps,
    )


def _classify_gap_reason(
    entry: AnswerSolutionMapEntry,
    item: FinalQuestionItem | None,
) -> str:
    if item is None:
        return "question_missing"
    if count_usable_options(item.options) < 4:
        return "option_incomplete"
    if entry.answer_label and "answer_key_not_in_options" in item.issues:
        return "answer_label_not_in_options"
    if entry.answer_label and "correct_answer_text_unavailable" in item.issues:
        return "correct_answer_text_unavailable"
    if not entry.answer_label:
        return "answer_label_parse_uncertain"
    if entry.question_number != item.question_number:
        return "solution_number_not_matched"
    return "answer_solution_join_gap"
