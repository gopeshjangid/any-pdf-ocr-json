"""Sanity checks for question/solution section split and window counts."""

from __future__ import annotations

from dataclasses import dataclass

from meritranker_data_ingestion.schemas.evidence_answer_solution_map import AnswerSolutionMapPackage
from meritranker_data_ingestion.schemas.question_window import QuestionWindowsPackage
from meritranker_data_ingestion.schemas.solution_window import SolutionWindowsPackage

QUESTION_WINDOW_MIN_RATIO = 0.70
SOLUTION_WINDOW_MAX_RATIO = 1.30
ANSWER_MAP_MAX_RATIO = 1.30


@dataclass(frozen=True)
class SectionSplitSanityResult:
    passed: bool
    failure_reason: str | None
    question_window_build_status: str
    section_split_status: str
    solution_window_detection_status: str
    answer_solution_map_status: str
    section_split_fallback_used: bool = False


def evaluate_section_split_sanity(
    *,
    expected_count: int | None,
    question_windows: QuestionWindowsPackage,
    solution_windows: SolutionWindowsPackage,
    answer_map: AnswerSolutionMapPackage,
) -> SectionSplitSanityResult:
    """Validate window counts before semantic binding proceeds."""
    qw_count = question_windows.total_windows
    sol_count = solution_windows.total_windows
    map_count = answer_map.total_mapped
    fallback_used = "section_split_fallback_used" in question_windows.warnings

    qw_status = "ok"
    split_status = "ok"
    sol_status = "ok"
    map_status = "ok" if answer_map.map_usable else "over_detected"

    failure_reason: str | None = None

    if expected_count and expected_count > 0:
        min_qw = int(expected_count * QUESTION_WINDOW_MIN_RATIO)
        max_sol = int(expected_count * SOLUTION_WINDOW_MAX_RATIO)
        max_map = int(expected_count * ANSWER_MAP_MAX_RATIO)

        if qw_count < min_qw:
            qw_status = "failed"
            split_status = "failed"
            failure_reason = failure_reason or "question_window_build_failed"

        if sol_count > max_sol:
            sol_status = "over_detected"
            failure_reason = failure_reason or "solution_over_detection"

        if map_count > max_map or not answer_map.map_usable:
            map_status = "over_detected"
            if "answer_solution_map_over_detected" in answer_map.warnings:
                failure_reason = failure_reason or "answer_solution_map_over_detected"

        if (
            qw_count < min_qw
            and sol_count > max_sol
            and split_status == "failed"
        ):
            failure_reason = "question_solution_split_failed"

    if question_windows.question_solution_section_mixed:
        split_status = "mixed"

    passed = failure_reason is None and qw_count > 0

    if qw_count == 0:
        qw_status = "failed"
        failure_reason = failure_reason or "question_window_build_failed"
        passed = False

    return SectionSplitSanityResult(
        passed=passed,
        failure_reason=failure_reason,
        question_window_build_status=qw_status,
        section_split_status=split_status,
        solution_window_detection_status=sol_status,
        answer_solution_map_status=map_status,
        section_split_fallback_used=fallback_used,
    )
