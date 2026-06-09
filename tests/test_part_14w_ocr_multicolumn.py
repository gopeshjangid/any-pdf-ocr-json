"""Part 14W OCR/screenshot + multi-column solved PDF stabilization tests."""

from __future__ import annotations

from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine
from meritranker_data_ingestion.services.document_section_splitter import (
    is_solution_style_anchor,
    split_document_sections,
)
from meritranker_data_ingestion.services.deterministic_option_parser import (
    _is_noise,
    parse_options_from_window_lines,
)
from meritranker_data_ingestion.services.ocr_role_hints import (
    is_chosen_option_instruction_line,
    is_chosen_option_metadata_line,
)
from meritranker_data_ingestion.services.question_window_builder import (
    _count_window_options,
    _is_question_anchor_line,
)
from meritranker_data_ingestion.services.semantic_embedded_option_parser import (
    extract_options_from_line,
)
from meritranker_data_ingestion.services.unsupported_layout_detector import detect_unsupported_layout


def _line(text: str, line_id: str = "l1") -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
    )


def test_adda_chosen_option_instruction_not_metadata() -> None:
    instruction = (
        "- **2. Chosen option on the right of the question indicates "
        "the option selected by the candidate.**"
    )
    assert is_chosen_option_instruction_line(instruction)
    assert not is_chosen_option_metadata_line(instruction)


def test_response_sheet_chosen_option_metadata() -> None:
    row = "Chosen Option: 2"
    assert is_chosen_option_metadata_line(row)
    assert not is_chosen_option_instruction_line(row)


def test_adda_instructions_do_not_trigger_unsupported_layout() -> None:
    instruction = (
        "- **2. Chosen option on the right of the question indicates "
        "the option selected by the candidate.**"
    )
    lines = [
        _line("- **1. Options shown in green color with a tick icon are correct.**", "l0"),
        _line(instruction, "l1"),
        _line("**Q.1 A sample question text here?**", "l2"),
        _line("**Ans** A. first option", "l3"),
        _line("B. second option", "l4"),
        _line("C. third option", "l5"),
        _line("D. fourth option", "l6"),
    ]
    from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage

    evidence = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="test.pdf",
        primary_extractor="marker",
        lines=lines,
        extraction_status="succeeded",
    )
    result = detect_unsupported_layout(evidence, answer_source_mode="inline_answer")
    assert not result.unsupported_layout_detected


def test_answers_with_explanations_heading_splits_sections() -> None:
    lines = [
        _line("- **1.** Question one?", "l1"),
        _line("- (a) opt1", "l2"),
        _line("- (b) opt2", "l3"),
        _line("- (c) opt3", "l4"),
        _line("- (d) opt4", "l5"),
        _line("## **Answers with Explanations**", "l6"),
        _line("- **4.** (b) explanation text for question four", "l7"),
    ]
    split = split_document_sections(lines)
    assert split.solution_section_detected
    assert len(split.question_lines) == 5
    assert "Explanations" in (split.boundaries[-1].heading_text or "")


def test_paren_options_parsed_from_window() -> None:
    lines = [
        _line("- **4.** As atomic number increases?", "l1"),
        _line("- (a) option A text", "l2"),
        _line("- (b) option B text", "l3"),
        _line("- (c) option C text", "l4"),
        _line("- (d) option D text", "l5"),
    ]
    result = parse_options_from_window_lines(lines, anchor_line_ids={"l1"})
    assert len(result.options) == 4
    keys = {opt.canonical_key for opt in result.options}
    assert keys == {"A", "B", "C", "D"}


def test_count_window_options_letter_labels() -> None:
    lines = [
        _line("- (a) 92", "l1"),
        _line("- (b) 90", "l2"),
        _line("- (c) 88", "l3"),
        _line("- (d) 86", "l4"),
    ]
    assert _count_window_options(lines) == 4


def test_ad_watermark_excluded_as_noise() -> None:
    assert _is_noise("**ALL EXAMS, ONE SUBSCRIPTION**")
    assert _is_noise("ATTEMPT FREE MOCK NOW")


def test_inline_ans_bold_not_noise() -> None:
    assert not _is_noise("**Ans** A. 30 days")


def test_explanation_anchor_detected_with_bullet_bold() -> None:
    text = "- **4.** (b) As we move from Lithium to Fluorine"
    assert is_solution_style_anchor(text)


def test_instruction_line_not_question_anchor() -> None:
    instruction = (
        "- **2. Chosen option on the right of the question indicates "
        "the option selected by the candidate.**"
    )
    assert not _is_question_anchor_line(instruction)
    assert _is_question_anchor_line("**Q.2 What should come in place of '?' in the series?**")


def test_tick_prefixed_table_option_parsed() -> None:
    opts = extract_options_from_line("| ✓ B. Cytoskeleton |", _line("| ✓ B. Cytoskeleton |"))
    assert opts
    assert opts[0][0] == "B"
    assert "Cytoskeleton" in opts[0][2]
