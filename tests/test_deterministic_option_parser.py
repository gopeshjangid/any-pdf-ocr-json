"""Tests for Part 14L deterministic option parser and strict final gate."""

from __future__ import annotations

import json
from pathlib import Path

from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine
from meritranker_data_ingestion.schemas.evidence_answer_solution_map import (
    AnswerSolutionMapEntry,
)
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalAnswerSource,
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
)
from meritranker_data_ingestion.schemas.question_window import QuestionWindow
from meritranker_data_ingestion.services.deterministic_option_parser import (
    parse_options_from_window_lines,
)
from meritranker_data_ingestion.services.final_item_acceptance_gate import (
    apply_final_item_acceptance_gate,
    compute_final_item_gate_metrics,
)
from meritranker_data_ingestion.services.window_final_question_builder import (
    _build_item_from_window,
)


def _line(line_id: str, text: str) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
    )


def test_same_line_paren_options() -> None:
    lines = [_line("o1", "(a) 20,28 (b) 15,21")]
    result = parse_options_from_window_lines(lines)
    by_key = {opt.canonical_key: opt.text_raw for opt in result.options}
    assert by_key == {"A": "20,28", "B": "15,21"}


def test_two_line_paren_options() -> None:
    lines = [
        _line("o1", "  - (a) 20,28 (b) 15,21"),
        _line("o2", "- - (c) 25,40 (d) 10,14"),
    ]
    result = parse_options_from_window_lines(lines)
    by_key = {opt.canonical_key: opt.text_raw for opt in result.options}
    assert by_key == {
        "A": "20,28",
        "B": "15,21",
        "C": "25,40",
        "D": "10,14",
    }


def test_one_option_per_line() -> None:
    lines = [
        _line("a", "(a) 68 years"),
        _line("b", "(b) 88 years"),
        _line("c", "(c) 78 years"),
        _line("d", "(d) Cannot be determined"),
    ]
    result = parse_options_from_window_lines(lines)
    assert len(result.options) == 4
    assert result.options[0].text_raw == "68 years"
    assert result.options[-1].text_raw == "Cannot be determined"


def test_numeric_options_normalized_to_abcd() -> None:
    lines = [_line("n1", "1. Alpha 2. Beta 3. Gamma 4. Delta")]
    result = parse_options_from_window_lines(lines)
    by_key = {opt.canonical_key: opt.text_raw for opt in result.options}
    assert by_key == {"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"}


def test_question_text_excludes_option_text() -> None:
    lines = [
        _line(
            "q1",
            "- **1.** The ages of A and B are in the ratio 5:7. Five years ago, their ages were in the",
        ),
        _line("o1", "  - (a) 20,28 (b) 15,21"),
        _line("o2", "- - (c) 25,40 (d) 10,14"),
    ]
    result = parse_options_from_window_lines(
        lines,
        anchor_line_ids={"q1"},
        option_candidate_line_ids={"o1", "o2"},
    )
    joined = " ".join(result.question_text_parts)
    assert "20,28" not in joined
    assert "ratio 5:7" in joined


def test_correct_answer_text_filled_from_parsed_option() -> None:
    options = [
        FinalQuestionOption(key="c", key_raw="c", text_raw="Gamma", canonical_key="C"),
    ]
    item = FinalQuestionItem(
        final_question_id="fq_1",
        global_order=1,
        question_number=1,
        question_text_raw="Pick one",
        options=options,
        correct_answer_key="C",
        correct_answer_text=None,
        quality_status=FinalQuestionQualityStatus.REVIEW_REQUIRED,
        issues=["correct_answer_text_unavailable"],
    )
    gated = apply_final_item_acceptance_gate(
        item.model_copy(
            update={
                "options": options
                + [
                    FinalQuestionOption(key="a", key_raw="a", text_raw="A", canonical_key="A"),
                    FinalQuestionOption(key="b", key_raw="b", text_raw="B", canonical_key="B"),
                    FinalQuestionOption(key="d", key_raw="d", text_raw="D", canonical_key="D"),
                ],
            },
        ),
    )
    assert gated.correct_answer_text == "Gamma"


def test_single_choice_lt4_options_not_accepted_safe() -> None:
    item = FinalQuestionItem(
        final_question_id="fq_1",
        global_order=1,
        question_number=1,
        question_text_raw="Pick one",
        options=[
            FinalQuestionOption(key="a", key_raw="a", text_raw="Only", canonical_key="A"),
        ],
        quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
        issues=["options_found:1"],
    )
    gated = apply_final_item_acceptance_gate(item)
    assert gated.quality_status == FinalQuestionQualityStatus.REVIEW_REQUIRED
    assert "incomplete_options" in gated.issues
    assert not any(issue.startswith("options_found:") for issue in gated.issues)


def test_accepted_safe_with_incomplete_options_fails_quality_metric() -> None:
    raw = FinalQuestionItem(
        final_question_id="fq_bad",
        global_order=1,
        question_number=1,
        question_text_raw="Q",
        options=[
            FinalQuestionOption(key="a", key_raw="a", text_raw="A", canonical_key="A"),
        ],
        quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
    )
    before = compute_final_item_gate_metrics([raw])
    assert before.accepted_safe_with_incomplete_options_count == 1
    gated = apply_final_item_acceptance_gate(raw)
    after = compute_final_item_gate_metrics([gated])
    assert after.accepted_safe_with_incomplete_options_count == 0


def test_quant_pdf_q1_fixture_produces_four_options() -> None:
    window = QuestionWindow(
        window_id="qw_0001",
        parsed_question_number=1,
        global_order=1,
        start_line_id="q1",
        line_ids=["q1", "o1", "o2"],
        question_anchor_line_ids=["q1"],
        option_candidate_line_ids=["o1", "o2"],
        issues=["options_found:0"],
    )
    line_by_id = {
        "q1": _line(
            "q1",
            "- **1.** The ages of A and B are in the ratio 5:7. Five years ago, their ages were in the",
        ),
        "o1": _line("o1", "  - (a) 20,28 (b) 15,21"),
        "o2": _line("o2", "- - (c) 25,40 (d) 10,14"),
    }
    item, _, _ = _build_item_from_window(
        window,
        line_by_id,
        AnswerSolutionMapEntry(
            question_number=1,
            answer_label="C",
            solution_text="solution",
            line_ids=["s1"],
        ),
    )
    by_key = {opt.canonical_key: opt.text_raw for opt in item.options}
    assert by_key == {
        "A": "20,28",
        "B": "15,21",
        "C": "25,40",
        "D": "10,14",
    }
    assert "20,28" not in item.question_text_raw
    assert not any(issue.startswith("options_found:") for issue in item.issues)


def test_quant_q2_years_options() -> None:
    lines = [
        _line("q2", "**2.** Four years ago, the ratio of ages of A and B was 3 : 5."),
        _line("o1", "(a) 32 years (b) 24 years"),
        _line("o2", "(c) 26 years (d) 22 years"),
    ]
    result = parse_options_from_window_lines(
        lines,
        anchor_line_ids={"q2"},
        option_candidate_line_ids={"o1", "o2"},
    )
    by_key = {opt.canonical_key: opt.text_raw for opt in result.options}
    assert by_key == {
        "A": "32 years",
        "B": "24 years",
        "C": "26 years",
        "D": "22 years",
    }


def test_replay_finalization_on_quant_package() -> None:
    pkg = Path(
        "batch_outputs/100-quantitative-aptitude-question_with_solution/extraction_package",
    )
    if not pkg.exists():
        return
    from meritranker_data_ingestion.services.finalization_replay import replay_finalization

    result = replay_finalization(pkg, expected_count=100, refresh_share_log=False)
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.total_questions_detected == 100
    assert report["accepted_safe_with_incomplete_options_count"] == 0
    assert report["questions_with_4_options_count"] >= 90
