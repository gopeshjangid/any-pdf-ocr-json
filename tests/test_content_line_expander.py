"""Tests for table-row content-line expansion and related classification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.classification import (
    ContentSourceKind,
    LineType,
)
from meritranker_data_ingestion.services.content_line_expander import (
    expand_content_lines,
    split_br_segments,
    split_table_cells,
)
from meritranker_data_ingestion.services.line_text_classifier import classify_text
from meritranker_data_ingestion.services.markdown_classifier import (
    ZERO_ANCHOR_ERROR,
    classify_line,
    classify_markdown,
)
from meritranker_data_ingestion.services.question_candidate_parser import (
    ZERO_CANDIDATE_ERROR,
    parse_question_candidates,
)
from meritranker_data_ingestion.schemas.classification import MarkdownBlockRecord
from meritranker_data_ingestion.services.extraction_package import (
    copy_marker_assets,
    discover_marker_image_files,
)

TABLE_ROW = (
    "| Q1.<br>Select the set in which the numbers are related<br>"
    "in the same way as are the numbers of the following<br>"
    "set.<br>(3, 24, 4)<br>(a) (6, 35, 11)<br>(b) (2, 30, 8)<br>"
    "(c) (12, 84, 4)<br>(d) (4, 72, 9)                               | "
    "Q6.<br>Select the number-pair in which the two<br>"
    "numbers are related in the same way as are the two<br>"
    "numbers of the following number pair.<br>36 : 84<br>"
    "(a) 27 : 63<br>(b) 21 : 51<br>(c) 57 : 135<br>(d) 45 : 95                               |"
)


def test_split_table_cells_ignores_separator() -> None:
    cells = split_table_cells("| A | B |")
    assert len(cells) == 2
    assert cells[0] == "A"


def test_split_br_segments() -> None:
    segments = split_br_segments("Q1.<br>Question?<br>(a) A")
    assert segments == ["Q1.", "Question?", "(a) A"]


def test_table_row_expansion_question_anchors() -> None:
    raw_line = classify_line(5, TABLE_ROW, None)
    assert raw_line.line_type == LineType.TABLE_ROW

    content_lines = expand_content_lines([raw_line])
    anchors = [cl for cl in content_lines if cl.line_type == LineType.QUESTION_ANCHOR]
    assert len(anchors) == 2
    labels = {cl.detected_label for cl in anchors}
    assert labels == {"Q1", "Q6"}
    assert all(cl.source_kind == ContentSourceKind.TABLE_CELL_SEGMENT for cl in anchors)


def test_option_detection_from_br_segments() -> None:
    raw_line = classify_line(1, TABLE_ROW, None)
    content_lines = expand_content_lines([raw_line])
    options = [cl for cl in content_lines if cl.line_type == LineType.OPTION_CANDIDATE]
    assert len(options) >= 8
    assert options[0].raw_text.startswith("(a)")


def test_raw_table_row_preserved_in_lines_json(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    marker = package / "marker"
    marker.mkdir(parents=True)
    (marker / "raw.md").write_text(TABLE_ROW + "\n", encoding="utf-8")

    result = classify_markdown(TABLE_ROW + "\n", package)
    assert result.lines[0].line_type == LineType.TABLE_ROW
    assert result.lines[0].raw_text == TABLE_ROW


def test_candidate_parsing_from_content_lines() -> None:
    raw_line = classify_line(1, TABLE_ROW, None)
    content_lines = expand_content_lines([raw_line])
    lines = [
        __import__(
            "meritranker_data_ingestion.services.content_line_expander",
            fromlist=["content_line_to_markdown_line"],
        ).content_line_to_markdown_line(cl)
        for cl in content_lines
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"), raw_line_count=1)
    assert result.total_candidates == 2


def test_numeric_anchor_conservative_answer_key() -> None:
    result = classify_text("1. B", allow_table_row=False, allow_page_markers=False)
    assert result.line_type != LineType.QUESTION_ANCHOR


def test_zero_anchor_warning_on_large_markdown() -> None:
    lines = ["some text\n"] * 150
    result = classify_markdown("".join(lines), Path("/tmp/pkg"))
    assert ZERO_ANCHOR_ERROR in result.errors
    assert result.content_question_anchor_count == 0


def test_zero_candidate_gate() -> None:
    lines = ["text\n"] * 150
    from meritranker_data_ingestion.services.markdown_classifier import classify_lines

    records = classify_lines("".join(lines))
    result = parse_question_candidates(records, [], Path("/tmp/pkg"), raw_line_count=len(records))
    assert result.status.value == "failed"
    assert ZERO_CANDIDATE_ERROR in result.errors[0]


def test_nested_marker_image_discovery(tmp_path: Path) -> None:
    work = tmp_path / "work" / "original"
    work.mkdir(parents=True)
    img = work / "_page_0_Picture_0.jpeg"
    img.write_bytes(b"\xff\xd8\xff")

    found = discover_marker_image_files(tmp_path / "work")
    assert len(found) == 1

    dest = tmp_path / "assets"
    copy_marker_assets(tmp_path / "work", dest, tmp_path)
    assert (dest / "_page_0_Picture_0.jpeg").exists()
    assert (dest / "original" / "_page_0_Picture_0.jpeg").exists()


def test_list_bold_question_anchor_detection() -> None:
    record = classify_line(1, "- **Q11.** Rs 1,875 is divided among A, B and C", None)
    assert record.line_type == LineType.QUESTION_ANCHOR
    assert record.detected_label == "Q11"
    assert record.raw_text.startswith("- **Q11.**")


def test_list_option_detection() -> None:
    record = classify_line(2, "- (a) Rs 500", None)
    assert record.line_type == LineType.OPTION_CANDIDATE
    assert record.detected_label == "A"


def test_inspect_command(tmp_path: Path) -> None:
    from meritranker_data_ingestion.cli import main

    package = tmp_path / "extraction_package"
    marker = package / "marker"
    marker.mkdir(parents=True)
    (marker / "raw.md").write_text(TABLE_ROW + "\n", encoding="utf-8")

    exit_code = main(["inspect-raw-markdown", "--package", str(package)])
    assert exit_code == 0
    assert (package / "diagnostics" / "raw-markdown-inspection.json").exists()
