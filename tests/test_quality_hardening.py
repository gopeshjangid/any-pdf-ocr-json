"""Tests for real-sample quality hardening."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.classification import (
    ContentLineRecord,
    ContentSourceKind,
    LineType,
    MarkdownLineRecord,
)
from meritranker_data_ingestion.schemas.question_candidates import (
    CandidateReviewStatus,
    QuestionOptionCandidate,
    QuestionSourceTrace,
)
from meritranker_data_ingestion.services.answer_solution_mapper import (
    map_answers_solutions,
)
from meritranker_data_ingestion.services.question_candidate_parser import (
    _build_candidate,
    _compute_review_status,
    _is_provenance_issue,
    _real_issues,
    parse_question_candidates,
)
from meritranker_data_ingestion.schemas.question_candidates import QuestionCandidate

TABLE_Q1_LINES = [
    MarkdownLineRecord(
        line_number=5,
        raw_text="Q1.",
        normalized_preview="Q1.",
        line_type=LineType.QUESTION_ANCHOR,
        detected_label="Q1",
        confidence=0.95,
    ),
    MarkdownLineRecord(
        line_number=6,
        raw_text="Select the set in which the numbers are related",
        normalized_preview="Select the set in which the numbers are related",
        line_type=LineType.TEXT,
        confidence=0.9,
    ),
    MarkdownLineRecord(
        line_number=10,
        raw_text="(a) (6, 35, 11)",
        normalized_preview="(a) (6, 35, 11)",
        line_type=LineType.OPTION_CANDIDATE,
        detected_label="A",
        confidence=0.9,
    ),
    MarkdownLineRecord(
        line_number=11,
        raw_text="(b) (2, 30, 8)",
        normalized_preview="(b) (2, 30, 8)",
        line_type=LineType.OPTION_CANDIDATE,
        detected_label="B",
        confidence=0.9,
    ),
    MarkdownLineRecord(
        line_number=12,
        raw_text="(c) (12, 84, 4)",
        normalized_preview="(c) (12, 84, 4)",
        line_type=LineType.OPTION_CANDIDATE,
        detected_label="C",
        confidence=0.9,
    ),
    MarkdownLineRecord(
        line_number=13,
        raw_text="(d) (4, 72, 9)",
        normalized_preview="(d) (4, 72, 9)",
        line_type=LineType.OPTION_CANDIDATE,
        detected_label="D",
        confidence=0.9,
    ),
]

CONTENT_BY_NUM = {
    10: ContentLineRecord(
        content_line_number=10,
        raw_text="(a) (6, 35, 11)",
        normalized_preview="(a) (6, 35, 11)",
        line_type=LineType.OPTION_CANDIDATE,
        detected_label="A",
        confidence=0.9,
        source_kind=ContentSourceKind.TABLE_CELL_SEGMENT,
        parent_line_number=5,
        table_cell_index=0,
        table_segment_index=5,
    ),
}


def test_provenance_not_counted_as_issue() -> None:
    assert _is_provenance_issue("parent_line_5")
    assert _is_provenance_issue("table_cell_0")
    assert not _is_provenance_issue("empty_option_text")
    assert _real_issues(["parent_line_5", "noise_inside_candidate_line_3"]) == [
        "noise_inside_candidate_line_3",
    ]


def test_q1_like_candidate_valid() -> None:
    candidate = _build_candidate(1, TABLE_Q1_LINES, False, CONTENT_BY_NUM)
    assert candidate.review_status == CandidateReviewStatus.CANDIDATE_VALID
    assert candidate.issues == []
    assert all(opt.issues == [] for opt in candidate.options)
    assert candidate.options[0].source_trace is not None
    assert candidate.options[0].source_trace.parent_line_number == 5
    assert candidate.options[0].source_trace.table_cell_index == 0


def test_structured_option_provenance() -> None:
    candidate = _build_candidate(1, TABLE_Q1_LINES, False, CONTENT_BY_NUM)
    opt = candidate.options[0]
    assert opt.text_raw == "(6, 35, 11)"
    assert opt.source_trace.source_kind == "table_cell_segment"


def test_mapper_uses_content_lines_and_maps_solution() -> None:
    lines = [
        MarkdownLineRecord(
            line_number=1,
            raw_text="## **S1. Ans.(d) Sol.**",
            normalized_preview="S1. Ans.(d) Sol.",
            line_type=LineType.SOLUTION_ANCHOR,
            detected_label="S1",
            confidence=0.9,
        ),
        MarkdownLineRecord(
            line_number=2,
            raw_text="Explanation text here.",
            normalized_preview="Explanation text here.",
            line_type=LineType.TEXT,
            confidence=0.9,
        ),
    ]
    candidates = [
        QuestionCandidate(
            question_id="q_0001",
            question_number=1,
            question_number_raw="Q1",
            raw_text="Q1. Test",
            question_text_raw="Q1. Test",
            options=[
                QuestionOptionCandidate(
                    key="A", key_raw="(a)", text_raw="1", start_line=2, end_line=2, confidence=0.9,
                ),
                QuestionOptionCandidate(
                    key="B", key_raw="(b)", text_raw="2", start_line=3, end_line=3, confidence=0.9,
                ),
                QuestionOptionCandidate(
                    key="C", key_raw="(c)", text_raw="3", start_line=4, end_line=4, confidence=0.9,
                ),
                QuestionOptionCandidate(
                    key="D", key_raw="(d)", text_raw="4", start_line=5, end_line=5, confidence=0.9,
                ),
            ],
            source_trace=QuestionSourceTrace(start_line=1, end_line=5, line_numbers=[1, 2, 3, 4, 5]),
            confidence=0.9,
            review_status=CandidateReviewStatus.CANDIDATE_VALID,
        ),
    ]
    result = map_answers_solutions(
        lines,
        [],
        candidates,
        Path("/tmp/pkg"),
        content_lines_used=True,
        mapping_source="content_lines",
        line_source_path="/tmp/content-lines.json",
    )
    assert result.content_lines_used is True
    assert result.solution_anchor_count_seen_by_mapper == 1
    assert result.mapped_count == 1
    assert result.mappings[0].answer_available
    assert result.mappings[0].answer.answer_key == "D"


def test_mapper_warns_when_anchors_unmapped() -> None:
    lines = [
        MarkdownLineRecord(
            line_number=1,
            raw_text="## **S99. Ans.(a)**",
            normalized_preview="S99. Ans.(a)",
            line_type=LineType.SOLUTION_ANCHOR,
            detected_label="S99",
            confidence=0.9,
        ),
    ]
    candidates = [
        QuestionCandidate(
            question_id="q_0001",
            question_number=1,
            question_number_raw="Q1",
            raw_text="Q1.",
            question_text_raw="Q1.",
            source_trace=QuestionSourceTrace(start_line=1, end_line=1, line_numbers=[1]),
            confidence=0.9,
            review_status=CandidateReviewStatus.CANDIDATE_INCOMPLETE,
        ),
    ]
    result = map_answers_solutions(lines, [], candidates, Path("/tmp/pkg"))
    assert result.solution_anchor_count_seen_by_mapper == 1
    assert result.mapped_count == 0
    assert "solution_anchors_detected_but_no_mappings_created" in result.warnings


def test_status_distribution_reconciles() -> None:
    lines = TABLE_Q1_LINES + [
        MarkdownLineRecord(
            line_number=20,
            raw_text="Q2. Another?",
            normalized_preview="Q2. Another?",
            line_type=LineType.QUESTION_ANCHOR,
            detected_label="Q2",
            confidence=0.9,
        ),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"), raw_line_count=2)
    assert sum(result.status_distribution.values()) == result.total_candidates


def test_heading_question_anchor_classification() -> None:
    from meritranker_data_ingestion.services.line_text_classifier import classify_text

    result = classify_text("## **Q32.** How many triangles are there?")
    assert result.line_type == LineType.QUESTION_ANCHOR
    assert result.detected_label == "Q32"


def test_coverage_diagnose_command(tmp_path: Path) -> None:
    from meritranker_data_ingestion.cli import main

    package = tmp_path / "extraction_package"
    classified = package / "classified"
    classified.mkdir(parents=True)
    content = [
        {
            "content_line_number": 1,
            "raw_text": "Q1. Test",
            "normalized_preview": "Q1. Test",
            "line_type": "question_anchor",
            "detected_label": "Q1",
            "confidence": 0.9,
            "source_kind": "raw_line",
            "parent_line_number": 1,
            "issues": [],
        },
    ]
    (classified / "content-lines.json").write_text(json.dumps(content), encoding="utf-8")
    (classified / "lines.json").write_text("[]", encoding="utf-8")
    (classified / "blocks.json").write_text("[]", encoding="utf-8")

    exit_code = main([
        "diagnose-question-coverage",
        "--package", str(package),
        "--expected-count", "100",
    ])
    assert exit_code == 0
    assert (package / "diagnostics" / "question-coverage.json").exists()
