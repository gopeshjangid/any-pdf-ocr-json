"""Tests for answer-option reconciliation after window supplement (Part 14Q)."""

from __future__ import annotations

from meritranker_data_ingestion.schemas.evidence_answer_solution_map import AnswerSolutionMapEntry
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionOption,
)
from meritranker_data_ingestion.schemas.question_window import QuestionWindow, QuestionWindowsPackage
from meritranker_data_ingestion.services.answer_option_reconciler import (
    index_question_windows,
    reconcile_item_answers,
)
from meritranker_data_ingestion.services.final_readiness_resolver import resolve_item_readiness
from meritranker_data_ingestion.services.option_recovery import consolidate_options


def _item(**kwargs) -> FinalQuestionItem:
    defaults = {
        "final_question_id": "fq_1",
        "global_order": 1,
        "question_number": 6,
        "question_text_raw": "Pick one.",
        "options": [
            FinalQuestionOption(key="a", key_raw="a", text_raw="feer", canonical_key="A"),
            FinalQuestionOption(key="b", key_raw="b", text_raw="fear", canonical_key="B"),
            FinalQuestionOption(key="c", key_raw="c", text_raw="fere", canonical_key="C"),
            FinalQuestionOption(key="d", key_raw="d", text_raw="fair", canonical_key="D"),
        ],
        "correct_answer_key": "D",
        "issues": ["answer_key_not_in_options", "missing_answer_answer_key_only_mode"],
    }
    defaults.update(kwargs)
    return FinalQuestionItem(**defaults)


def test_reconcile_fills_answer_text_from_options() -> None:
    item = reconcile_item_answers(_item(), map_entry=None)
    assert item.correct_answer_text == "fair"
    assert "answer_key_not_in_options" not in item.issues


def test_reconcile_from_solution_map() -> None:
    item = _item(correct_answer_key=None, correct_answer_text=None)
    entry = AnswerSolutionMapEntry(
        question_number=6,
        answer_label="D",
        solution_text="Because fair is correct.",
        line_ids=["s1"],
    )
    item = reconcile_item_answers(item, map_entry=entry)
    assert item.correct_answer_key == "D"
    assert item.correct_answer_text == "fair"
    assert item.solution_text_raw == "Because fair is correct."


def test_ready_after_reconcile_when_answer_matches() -> None:
    item = reconcile_item_answers(_item(), map_entry=None)
    metadata = resolve_item_readiness(item, answers_expected=True)
    assert metadata.status == "ready"
    assert "expected_answer_missing" not in metadata.review_issues
    assert "answer_key_not_in_options" not in metadata.review_issues


def test_consolidate_options_dedupes_semantic_and_window_labels() -> None:
    options = [
        FinalQuestionOption(key="?", key_raw="?", text_raw="- (a) 37.7", canonical_key="?"),
        FinalQuestionOption(key="a", key_raw="a", text_raw="37.7", canonical_key="A"),
        FinalQuestionOption(key="?", key_raw="?", text_raw="- (c) 33.3", canonical_key="?"),
        FinalQuestionOption(key="c", key_raw="c", text_raw="33.3", canonical_key="C"),
    ]
    merged = consolidate_options(options)
    assert len(merged) == 2
    assert {opt.canonical_key for opt in merged} == {"A", "C"}


def test_index_prefers_question_section_window() -> None:
    pkg = QuestionWindowsPackage(
        source_file_name="t.pdf",
        total_windows=2,
        windows=[
            QuestionWindow(
                window_id="qw_0001",
                parsed_question_number=1,
                global_order=1,
                line_ids=["a", "b", "c", "d"],
            ),
            QuestionWindow(
                window_id="qw_0067",
                parsed_question_number=1,
                global_order=67,
                line_ids=["x"],
            ),
        ],
    )
    picked = index_question_windows(pkg)[1]
    assert picked.window_id == "qw_0001"
