"""Tests for deterministic question candidate parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.classification import LineType, MarkdownLineRecord
from meritranker_data_ingestion.schemas.question_candidates import (
    CandidateReviewStatus,
    ParseStatus,
)
from meritranker_data_ingestion.services.question_candidate_parser import (
    QuestionParseError,
    parse_question_candidates,
    parse_question_candidates_package,
)


def _line(
    number: int,
    raw: str,
    line_type: LineType,
    *,
    label: str | None = None,
    page: int | None = None,
    confidence: float = 0.9,
) -> MarkdownLineRecord:
    return MarkdownLineRecord(
        line_number=number,
        raw_text=raw,
        normalized_preview=raw.strip(),
        page_number=page,
        line_type=line_type,
        detected_label=label,
        confidence=confidence,
    )


def _write_classified(package: Path, lines: list[MarkdownLineRecord]) -> None:
    classified = package / "classified"
    classified.mkdir(parents=True)
    payload = [ln.model_dump(mode="json") for ln in lines]
    (classified / "lines.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (classified / "blocks.json").write_text("[]", encoding="utf-8")


def test_single_question_four_options() -> None:
    lines = [
        _line(1, "Q1. What is 2+2?", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "(a) 3", LineType.OPTION_CANDIDATE, label="A"),
        _line(3, "(b) 4", LineType.OPTION_CANDIDATE, label="B"),
        _line(4, "(c) 5", LineType.OPTION_CANDIDATE, label="C"),
        _line(5, "(d) 6", LineType.OPTION_CANDIDATE, label="D"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    assert result.total_candidates == 1
    c = result.candidates[0]
    assert c.question_number == 1
    assert len(c.options) == 4
    assert c.options[0].key == "A"
    assert c.options[0].key_raw == "(a)"
    assert c.review_status == CandidateReviewStatus.CANDIDATE_VALID


def test_multi_line_question_body() -> None:
    lines = [
        _line(1, "Q2. Consider the following:", LineType.QUESTION_ANCHOR, label="Q2"),
        _line(2, "A particle moves with velocity v.", LineType.TEXT),
        _line(3, "(a) 1 m/s", LineType.OPTION_CANDIDATE, label="A"),
        _line(4, "(b) 2 m/s", LineType.OPTION_CANDIDATE, label="B"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    c = result.candidates[0]
    assert "particle moves" in c.question_text_raw
    assert "particle moves" in c.raw_text
    assert c.options[0].text_raw == "1 m/s"


def test_question_with_image_reference() -> None:
    lines = [
        _line(1, "Q3. See diagram.", LineType.QUESTION_ANCHOR, label="Q3"),
        _line(2, "![](figures/3.1)", LineType.IMAGE_REFERENCE),
        _line(3, "(a) yes", LineType.OPTION_CANDIDATE, label="A"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    c = result.candidates[0]
    assert len(c.assets) == 1
    assert c.assets[0].asset_path == "figures/3.1"
    assert result.candidates_with_images == 1


def test_question_with_empty_option() -> None:
    lines = [
        _line(1, "Q4. Pick one.", LineType.QUESTION_ANCHOR, label="Q4"),
        _line(2, "(a)", LineType.OPTION_CANDIDATE, label="A"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    c = result.candidates[0]
    assert c.options[0].issues == ["empty_option_text"]
    assert c.review_status == CandidateReviewStatus.CANDIDATE_NEEDS_REVIEW


def test_duplicate_question_number() -> None:
    lines = [
        _line(1, "Q1. First", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Q1. Duplicate", LineType.QUESTION_ANCHOR, label="Q1"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    assert result.total_candidates == 2
    assert 1 in result.duplicate_question_numbers
    assert result.candidates[1].review_status == CandidateReviewStatus.CANDIDATE_DUPLICATE


def test_missing_question_number_sequence() -> None:
    lines = [
        _line(1, "Q1. One", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Q3. Three", LineType.QUESTION_ANCHOR, label="Q3"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    assert 2 in result.missing_question_numbers
    assert any("missing_question_numbers" in w for w in result.warnings)


def test_stops_before_solution_section() -> None:
    lines = [
        _line(1, "Q1. Question", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "(a) opt", LineType.OPTION_CANDIDATE, label="A"),
        _line(3, "## Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(4, "S1. Ans.(a)", LineType.SOLUTION_ANCHOR, label="S1"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    assert result.total_candidates == 1
    assert "Solutions" not in result.candidates[0].raw_text
    assert "S1" not in result.candidates[0].raw_text


def test_no_solution_section() -> None:
    lines = [
        _line(1, "Q1. Only questions", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Q2. No solutions", LineType.QUESTION_ANCHOR, label="Q2"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    assert result.total_candidates == 2


def test_noise_inside_candidate() -> None:
    lines = [
        _line(1, "Q1. Question", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Visit www.spam.com", LineType.PAGE_FOOTER_MARKER),
        _line(3, "(a) opt", LineType.OPTION_CANDIDATE, label="A"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    c = result.candidates[0]
    assert "www.spam.com" in c.raw_text
    assert "www.spam.com" not in c.question_text_raw
    assert any("noise_inside" in i for i in c.issues)
    assert result.candidates_with_noise == 1


def test_missing_options_keeps_candidate() -> None:
    lines = [
        _line(1, "Q5. No options here.", LineType.QUESTION_ANCHOR, label="Q5"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    c = result.candidates[0]
    assert c.review_status == CandidateReviewStatus.CANDIDATE_INCOMPLETE
    assert "missing_options" in c.issues
    assert result.candidates_with_no_options == 1


def test_raw_text_exact_preservation() -> None:
    raw_lines = [
        "Q1.  Spacing preserved.  ",
        "  continuation line  ",
        "(a) answer",
    ]
    lines = [
        _line(1, raw_lines[0], LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, raw_lines[1], LineType.TEXT),
        _line(3, raw_lines[2], LineType.OPTION_CANDIDATE, label="A"),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    assert result.candidates[0].raw_text == "\n".join(raw_lines)


def test_option_key_normalization_preserves_key_raw() -> None:
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "A. Option text", LineType.OPTION_CANDIDATE, label="A", confidence=0.7),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    opt = result.candidates[0].options[0]
    assert opt.key == "A"
    assert opt.key_raw == "A."


def test_package_writes_outputs(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    lines = [
        _line(1, "Q1. Test", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "(a) one", LineType.OPTION_CANDIDATE, label="A"),
    ]
    _write_classified(package, lines)

    result = parse_question_candidates_package(package)

    assert result.status == ParseStatus.SUCCEEDED
    assert (package / "questions" / "question-candidates.json").exists()
    assert (package / "questions" / "question-candidate-report.json").exists()


def test_package_missing_classified_files(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    package.mkdir()

    with pytest.raises(QuestionParseError, match="Missing classified lines"):
        parse_question_candidates_package(package)


def test_source_trace_fields() -> None:
    lines = [
        _line(10, "Q1. Traced", LineType.QUESTION_ANCHOR, label="Q1", page=2),
        _line(11, "body", LineType.TEXT, page=2),
        _line(12, "(a) x", LineType.OPTION_CANDIDATE, label="A", page=3),
    ]
    result = parse_question_candidates(lines, [], Path("/tmp/pkg"))
    trace = result.candidates[0].source_trace
    assert trace.start_line == 10
    assert trace.end_line == 12
    assert trace.page_start == 2
    assert trace.page_end == 3
    assert trace.line_numbers == [10, 11, 12]
