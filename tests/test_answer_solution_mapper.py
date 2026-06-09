"""Tests for deterministic answer/solution mapper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.classification import LineType, MarkdownLineRecord
from meritranker_data_ingestion.schemas.answer_solution_mapping import MappingStatus
from meritranker_data_ingestion.schemas.question_candidates import (
    CandidateReviewStatus,
    QuestionCandidate,
    QuestionSourceTrace,
)
from meritranker_data_ingestion.services.answer_solution_mapper import (
    MappingError,
    map_answers_solutions,
    map_answers_solutions_package,
)


def _line(
    n: int,
    raw: str,
    line_type: LineType,
    *,
    label: str | None = None,
    page: int | None = None,
) -> MarkdownLineRecord:
    return MarkdownLineRecord(
        line_number=n,
        raw_text=raw,
        normalized_preview=raw.strip(),
        page_number=page,
        line_type=line_type,
        detected_label=label,
        confidence=0.9,
    )


def _candidate(
    qid: str,
    qnum: int,
    *,
    duplicate: bool = False,
) -> QuestionCandidate:
    return QuestionCandidate(
        question_id=qid,
        question_number=qnum,
        question_number_raw=f"Q{qnum}",
        raw_text=f"Q{qnum}. Question text",
        question_text_raw=f"Q{qnum}. Question text",
        source_trace=QuestionSourceTrace(
            start_line=qnum,
            end_line=qnum,
            line_numbers=[qnum],
        ),
        confidence=0.9,
        review_status=CandidateReviewStatus.CANDIDATE_DUPLICATE if duplicate else CandidateReviewStatus.CANDIDATE_VALID,
    )


def _write_package(
    package: Path,
    lines: list[MarkdownLineRecord],
    candidates: list[QuestionCandidate],
) -> None:
    classified = package / "classified"
    questions = package / "questions"
    classified.mkdir(parents=True)
    questions.mkdir(parents=True)
    (classified / "lines.json").write_text(
        json.dumps([ln.model_dump(mode="json") for ln in lines]),
        encoding="utf-8",
    )
    (classified / "blocks.json").write_text("[]", encoding="utf-8")
    (questions / "question-candidates.json").write_text(
        json.dumps([c.model_dump(mode="json") for c in candidates]),
        encoding="utf-8",
    )


def test_s1_ans_paren_mapping() -> None:
    lines = [
        _line(1, "Q1. Question", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "## Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(3, "S1. Ans.(d) Full explanation here.", LineType.SOLUTION_ANCHOR, label="S1"),
    ]
    candidates = [_candidate("q_0001", 1)]
    result = map_answers_solutions(lines, [], candidates, Path("/tmp/pkg"))

    m = result.mappings[0]
    assert m.mapping_status == MappingStatus.MAPPED
    assert m.answer is not None
    assert m.answer.answer_key == "D"
    assert m.solution is not None
    assert "Full explanation" in m.solution.raw_text


def test_s1_ans_no_paren_variant() -> None:
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(3, "S1. Ans(d) text", LineType.SOLUTION_ANCHOR, label="S1"),
    ]
    result = map_answers_solutions(lines, [], [_candidate("q_0001", 1)], Path("/tmp"))
    assert result.mappings[0].answer.answer_key == "D"


def test_s1_answer_colon_variant() -> None:
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "## Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(3, "S1. Answer: D", LineType.SOLUTION_ANCHOR, label="S1"),
        _line(4, "Explanation line", LineType.TEXT),
    ]
    result = map_answers_solutions(lines, [], [_candidate("q_0001", 1)], Path("/tmp"))
    m = result.mappings[0]
    assert m.answer.answer_key == "D"
    assert "Explanation line" in m.solution.raw_text


def test_answer_key_section() -> None:
    lines = [
        _line(1, "Q1. Q1", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Q2. Q2", LineType.QUESTION_ANCHOR, label="Q2"),
        _line(3, "Answer Key", LineType.SOLUTION_SECTION_HEADING),
        _line(4, "1. B", LineType.TEXT),
        _line(5, "2. C", LineType.TEXT),
    ]
    candidates = [_candidate("q_0001", 1), _candidate("q_0002", 2)]
    result = map_answers_solutions(lines, [], candidates, Path("/tmp"))
    assert result.mappings[0].answer.answer_key == "B"
    assert result.mappings[1].answer.answer_key == "C"


def test_solution_boundary_until_next_s() -> None:
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Q2. Q", LineType.QUESTION_ANCHOR, label="Q2"),
        _line(3, "## Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(4, "S1. Ans.(a) Sol one", LineType.SOLUTION_ANCHOR, label="S1"),
        _line(5, "extra detail", LineType.TEXT),
        _line(6, "S2. Ans.(b) Sol two", LineType.SOLUTION_ANCHOR, label="S2"),
    ]
    result = map_answers_solutions(
        lines,
        [],
        [_candidate("q_0001", 1), _candidate("q_0002", 2)],
        Path("/tmp"),
    )
    assert "extra detail" in result.mappings[0].solution.raw_text
    assert "Sol two" in result.mappings[1].solution.raw_text
    assert "extra detail" not in result.mappings[1].solution.raw_text


def test_solution_at_eof() -> None:
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(3, "S1. Ans.(c) Last solution", LineType.SOLUTION_ANCHOR, label="S1"),
    ]
    result = map_answers_solutions(lines, [], [_candidate("q_0001", 1)], Path("/tmp"))
    assert result.mappings[0].solution.end_line == 3


def test_solution_with_image_reference() -> None:
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(3, "S1. Ans.(a) See", LineType.SOLUTION_ANCHOR, label="S1"),
        _line(4, "![](img/sol.png)", LineType.IMAGE_REFERENCE),
    ]
    result = map_answers_solutions(lines, [], [_candidate("q_0001", 1)], Path("/tmp"))
    sol = result.mappings[0].solution
    assert "![](img/sol.png)" in sol.raw_text
    assert "img/sol.png" in sol.image_references


def test_no_solution_section_not_available() -> None:
    lines = [_line(1, "Q1. Only question", LineType.QUESTION_ANCHOR, label="Q1")]
    result = map_answers_solutions(lines, [], [_candidate("q_0001", 1)], Path("/tmp"))
    assert result.mappings[0].mapping_status == MappingStatus.NOT_AVAILABLE
    assert result.not_available_count == 1
    assert any("no_solution_section" in w for w in result.warnings)


def test_raw_solution_text_preserved() -> None:
    raw = "S1. Ans.(d)  spaced  text  "
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(3, raw, LineType.SOLUTION_ANCHOR, label="S1"),
    ]
    result = map_answers_solutions(lines, [], [_candidate("q_0001", 1)], Path("/tmp"))
    assert result.mappings[0].solution.raw_text == raw


def test_duplicate_conflicting_answers() -> None:
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(3, "S1. Ans.(a) one", LineType.SOLUTION_ANCHOR, label="S1"),
        _line(4, "1. B", LineType.TEXT),
        _line(5, "2. C", LineType.TEXT),
    ]
    # Add second conflicting answer for q1 via Q1 Answer line
    lines.append(_line(6, "Q1 Answer: D", LineType.ANSWER_MARKER))
    result = map_answers_solutions(lines, [], [_candidate("q_0001", 1)], Path("/tmp"))
    m = result.mappings[0]
    assert m.mapping_status == MappingStatus.DUPLICATE_CONFLICT


def test_duplicate_question_candidate() -> None:
    lines = [
        _line(1, "Q1. First", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Q1. Dup", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(3, "Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(4, "S1. Ans.(a) sol", LineType.SOLUTION_ANCHOR, label="S1"),
    ]
    candidates = [_candidate("q_0001", 1), _candidate("q_0002", 1, duplicate=True)]
    result = map_answers_solutions(lines, [], candidates, Path("/tmp"))
    assert result.mappings[1].mapping_status == MappingStatus.DUPLICATE_CONFLICT


def test_missing_solution_number_warning() -> None:
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Q3. Q", LineType.QUESTION_ANCHOR, label="Q3"),
        _line(3, "Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(4, "S1. Ans.(a) sol1", LineType.SOLUTION_ANCHOR, label="S1"),
        _line(5, "S3. Ans.(c) sol3", LineType.SOLUTION_ANCHOR, label="S3"),
    ]
    result = map_answers_solutions(
        lines,
        [],
        [_candidate("q_0001", 1), _candidate("q_0002", 3)],
        Path("/tmp"),
    )
    assert any("missing_solution_numbers" in w for w in result.warnings)


def test_package_writes_outputs(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    lines = [
        _line(1, "Q1. Q", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "Solutions", LineType.SOLUTION_SECTION_HEADING),
        _line(3, "S1. Ans.(b) sol", LineType.SOLUTION_ANCHOR, label="S1"),
    ]
    _write_package(package, lines, [_candidate("q_0001", 1)])

    result = map_answers_solutions_package(package)

    assert (package / "mappings" / "answer-solution-map.json").exists()
    assert (package / "mappings" / "answer-solution-report.json").exists()
    assert (package / "mappings" / "question-candidates-with-mappings.json").exists()
    assert result.mapped_count == 1


def test_package_missing_candidates(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    classified = package / "classified"
    classified.mkdir(parents=True)
    (classified / "lines.json").write_text("[]", encoding="utf-8")
    (classified / "blocks.json").write_text("[]", encoding="utf-8")

    with pytest.raises(MappingError, match="Missing question candidates"):
        map_answers_solutions_package(package)


def test_package_missing_classified(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    package.mkdir()

    with pytest.raises(MappingError, match="Missing classified"):
        map_answers_solutions_package(package)
