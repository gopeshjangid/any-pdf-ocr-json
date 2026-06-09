"""Tests for deterministic markdown line/block classifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.classification import (
    BlockType,
    ClassificationStatus,
    LineType,
)
from meritranker_data_ingestion.services.markdown_classifier import (
    ClassificationError,
    classify_line,
    classify_lines,
    classify_package,
    group_blocks,
)


SAMPLE_MARKDOWN = """\
<!-- PageNumber: 1 -->
# Sample Exam Paper

Subject: Physics

Q1. Select the correct option.

(a) 12

(b) 24

![](figures/3.1)

| Col A | Col B |
| --- | --- |
| 1 | 2 |

$$
E = mc^2
$$

Some body text with (a) inside equation context.

<!-- PageBreak -->
<!-- PageNumber: 2 -->

Q100. Another question stem.

A. 12

## Solutions

S1. Ans.(d) Explanation here.

Visit us at www.example.com for more.

"""


def test_question_anchor_detection() -> None:
    record = classify_line(1, "Q1. Select the correct option.", None)
    assert record.line_type == LineType.QUESTION_ANCHOR
    assert record.detected_label == "Q1"
    assert record.raw_text == "Q1. Select the correct option."


def test_question_anchor_high_number() -> None:
    record = classify_line(1, "Q100. Another question stem.", None)
    assert record.line_type == LineType.QUESTION_ANCHOR
    assert record.detected_label == "Q100"


def test_option_paren_detection() -> None:
    record = classify_line(1, "(a) 12", None)
    assert record.line_type == LineType.OPTION_CANDIDATE
    assert record.detected_label == "A"


def test_option_dot_lower_confidence() -> None:
    record = classify_line(1, "A. 12", None)
    assert record.line_type == LineType.OPTION_CANDIDATE
    assert record.confidence == 0.7


def test_option_dot_equation_stays_text() -> None:
    record = classify_line(1, "A. x^2 + y_1 = 0", None)
    assert record.line_type == LineType.TEXT
    assert "equation_like" in record.issues[0]


def test_solution_heading_detection() -> None:
    record = classify_line(1, "## Solutions", None)
    assert record.line_type == LineType.SOLUTION_SECTION_HEADING


def test_solution_anchor_and_answer_marker() -> None:
    record = classify_line(1, "S1. Ans.(d) Explanation here.", None)
    assert record.line_type == LineType.SOLUTION_ANCHOR
    assert record.detected_label == "S1"
    assert "contains_answer_marker" in record.issues


def test_image_reference_detection() -> None:
    record = classify_line(1, "![](figures/3.1)", None)
    assert record.line_type == LineType.IMAGE_REFERENCE


def test_page_marker_detection() -> None:
    record = classify_line(1, "<!-- PageNumber: 3 -->", None)
    assert record.line_type == LineType.PAGE_NUMBER_MARKER
    assert record.page_number == 3
    assert record.detected_label == "3"


def test_page_break_marker() -> None:
    record = classify_line(1, "<!-- PageBreak -->", None)
    assert record.line_type == LineType.PAGE_BREAK_MARKER


def test_footer_noise_detection() -> None:
    record = classify_line(1, "Visit us at www.example.com for more.", None)
    assert record.line_type == LineType.PAGE_FOOTER_MARKER


def test_table_row_detection() -> None:
    record = classify_line(1, "| Col A | Col B |", None)
    assert record.line_type == LineType.TABLE_ROW


def test_math_block_detection() -> None:
    record = classify_line(1, "$$", None)
    assert record.line_type == LineType.MATH_BLOCK


def test_blank_line_detection() -> None:
    record = classify_line(1, "   ", None)
    assert record.line_type == LineType.BLANK


def test_metadata_candidate_detection() -> None:
    record = classify_line(1, "Subject: Physics", None)
    assert record.line_type == LineType.METADATA_CANDIDATE


def test_raw_text_preserved_exactly() -> None:
    raw = "  Q1. Spacing preserved.  "
    record = classify_line(1, raw, None)
    assert record.raw_text == raw
    assert record.normalized_preview == raw.strip()


def test_classify_lines_preserves_every_line() -> None:
    lines = classify_lines(SAMPLE_MARKDOWN)
    expected_line_count = len(SAMPLE_MARKDOWN.splitlines())
    assert len(lines) == expected_line_count
    for idx, record in enumerate(lines, start=1):
        assert record.line_number == idx
        assert record.raw_text == SAMPLE_MARKDOWN.splitlines()[idx - 1]


def test_page_number_inheritance() -> None:
    md = "<!-- PageNumber: 5 -->\nPlain line\n"
    records = classify_lines(md)
    assert records[1].page_number == 5


def test_block_grouping_table_and_options() -> None:
    lines = classify_lines("Q1. Question\n(a) one\n(b) two\n| a | b |\n| 1 | 2 |\n")
    blocks = group_blocks(lines)
    types = [b.block_type for b in blocks]
    assert BlockType.QUESTION_CANDIDATE_BLOCK in types
    assert BlockType.OPTION_CANDIDATE_BLOCK in types
    assert BlockType.TABLE_BLOCK in types


def test_question_blocks_are_single_line() -> None:
    lines = classify_lines("Q1. First\nBody text\nQ2. Second\n")
    blocks = group_blocks(lines)
    question_blocks = [b for b in blocks if b.block_type == BlockType.QUESTION_CANDIDATE_BLOCK]
    assert len(question_blocks) == 2
    assert question_blocks[0].start_line == question_blocks[0].end_line


def test_classify_package_writes_outputs(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    marker_dir = package / "marker"
    marker_dir.mkdir(parents=True)
    (marker_dir / "raw.md").write_text(SAMPLE_MARKDOWN, encoding="utf-8")

    result = classify_package(package)

    assert result.status == ClassificationStatus.SUCCEEDED
    assert (package / "classified" / "lines.json").exists()
    assert (package / "classified" / "blocks.json").exists()
    assert (package / "classified" / "classification-report.json").exists()
    assert result.question_anchor_count == 2
    assert result.option_candidate_count >= 2
    assert result.image_reference_count == 1


def test_classify_package_missing_raw_md(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    package.mkdir()

    with pytest.raises(ClassificationError, match="Missing markdown"):
        classify_package(package)

    report = package / "classified" / "classification-report.json"
    assert report.exists()
    assert '"failed"' in report.read_text(encoding="utf-8")
