"""Part 14U answer-key-only PYQ stabilization tests."""

from __future__ import annotations

from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionItemMetadata,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
)
from meritranker_data_ingestion.services.answer_key_zone_detector import (
    is_answer_key_anchor_line,
    is_answer_key_contaminated_text,
    is_answer_key_line,
    parse_mathsf_option_line,
    parse_pipe_table_option_line,
)
from meritranker_data_ingestion.services.deterministic_option_parser import (
    _parse_options_from_line_text,
    _dummy_line,
    parse_options_from_window_lines,
)
from meritranker_data_ingestion.services.expected_count_canonicalizer import _candidate_score
from meritranker_data_ingestion.services.question_window_builder import _is_question_anchor_line


def _line(text: str, line_id: str = "l1") -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
    )


def test_answer_key_table_line_detection() -> None:
    row = "| 34.B                  | 35.D | 36.D | 37.B | 38.D | 39.D | 40.B | 41.D |"
    assert is_answer_key_line(row)
    assert is_answer_key_anchor_line(row)
    assert not _is_question_anchor_line(row)


def test_real_question_anchor_not_answer_key() -> None:
    text = "34. Name the Bhakti Saint from South India who was initially a Jaina"
    assert not is_answer_key_line(text)
    assert not is_answer_key_anchor_line(text)
    assert _is_question_anchor_line(text)


def test_bare_answer_key_line_rejected() -> None:
    assert is_answer_key_line("96.C")
    assert is_answer_key_anchor_line("34.B")


def test_pipe_table_option_parsing() -> None:
    parsed = parse_pipe_table_option_line("| A    caught                                                                 |")
    assert parsed is not None
    assert parsed[0] == "A"
    assert parsed[2] == "caught"


def test_mathsf_option_parsing() -> None:
    parsed = parse_mathsf_option_line(r"$$\mathsf{c} \ \ \div, +, \div, +, =$$")
    assert parsed is not None
    assert parsed[0] == "C"


def test_label_formula_merge_and_parse() -> None:
    lines = [
        _line("A ", "l1"),
        _line(r"$$\div, -, +, \times, =$$", "l2"),
        _line("B ", "l3"),
        _line(r"$$+, -, \div, -, =$$", "l4"),
    ]
    from meritranker_data_ingestion.services.option_recovery import _merge_option_continuations

    merged = _merge_option_continuations(lines)
    result = parse_options_from_window_lines(merged)
    assert len(result.options) >= 2
    keys = {opt.canonical_key for opt in result.options}
    assert "A" in keys
    assert "B" in keys


def test_canonicalizer_deprioritizes_answer_key_text() -> None:
    good = FinalQuestionItem(
        final_question_id="good",
        global_order=34,
        question_number=34,
        question_text_raw="34. Name the Bhakti Saint from South India",
        options=[
            FinalQuestionOption(key="a", key_raw="(a)", text_raw="Basavanna"),
            FinalQuestionOption(key="b", key_raw="(b)", text_raw="Eknath"),
            FinalQuestionOption(key="c", key_raw="(c)", text_raw="Tallapaka"),
            FinalQuestionOption(key="d", key_raw="(d)", text_raw="Karaikkal"),
        ],
        quality_status=FinalQuestionQualityStatus.REVIEW_REQUIRED,
        metadata=FinalQuestionItemMetadata(status="review", review_issues=[]),
    )
    bad = good.model_copy(
        update={
            "final_question_id": "bad",
            "question_text_raw": "| 34.B | 35.D | 36.D |",
            "options": [],
        },
    )
    assert _candidate_score(good) > _candidate_score(bad)
    assert is_answer_key_contaminated_text(bad.question_text_raw)


def test_continued_question_anchor_after_solution_heading() -> None:
    from meritranker_data_ingestion.services.document_section_splitter import DocumentSectionSplit
    from meritranker_data_ingestion.services.question_window_builder import (
        _is_continued_question_anchor,
        _select_question_anchors,
    )

    assert _is_continued_question_anchor(
        "34. Name the Bhakti Saint from South India who was initially a Jaina",
    )
    assert _is_continued_question_anchor(
        "| 76. Select the most appropriate option that can substitute the underlined segment",
    )
    assert not _is_continued_question_anchor("34.B")
    split = DocumentSectionSplit(
        lines=[],
        question_lines=[],
        solution_lines=[],
        boundaries=[],
        solution_section_detected=True,
        solution_heading_index=10,
        solution_section_confidence=0.9,
        inline_solution_style=False,
    )
    anchors = [
        (5, "5. Earlier question in paper", 5),
        (15, "34. Continued question after answer-key block", 34),
        (16, "34.B", 34),
    ]
    selected = _select_question_anchors(anchors, split)
    assert any(a[2] == 34 for a in selected)
    assert not any(a[1] == "34.B" for a in selected)


def test_bullet_bold_option_still_parses() -> None:
    opts = _parse_options_from_line_text("- **A** EROFNGI", _dummy_line("- **A** EROFNGI"))
    assert len(opts) == 1
    assert opts[0].canonical_key == "A"
