"""Tests for final question package builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    AnswerCandidate,
    MappingStatus,
    QuestionAnswerSolutionMapping,
    SolutionCandidate,
)
from meritranker_data_ingestion.schemas.final_question_package import ValidationStatus
from meritranker_data_ingestion.schemas.question_candidates import (
    AssetRole,
    CandidateReviewStatus,
    QuestionAssetReference,
    QuestionCandidate,
    QuestionOptionCandidate,
    QuestionSourceTrace,
)
from meritranker_data_ingestion.services.final_question_package_builder import (
    FinalizeError,
    build_final_package,
    build_final_package_from_directory,
)


def _candidate(
    qid: str = "q_0001",
    qnum: int = 1,
    *,
    raw: str = "Q1. What is 2+2?",
    options: list[QuestionOptionCandidate] | None = None,
    assets: list[QuestionAssetReference] | None = None,
    review: CandidateReviewStatus = CandidateReviewStatus.CANDIDATE_VALID,
    issues: list[str] | None = None,
) -> QuestionCandidate:
    if options is None:
        options = [
            QuestionOptionCandidate(
                key="A", key_raw="(a)", text_raw="3", start_line=2, end_line=2, confidence=0.9,
            ),
            QuestionOptionCandidate(
                key="B", key_raw="(b)", text_raw="4", start_line=3, end_line=3, confidence=0.9,
            ),
        ]
    return QuestionCandidate(
        question_id=qid,
        question_number=qnum,
        question_number_raw=f"Q{qnum}",
        raw_text=raw,
        question_text_raw=raw.split("\n")[0] if "\n" in raw else raw,
        options=options,
        assets=assets or [],
        source_trace=QuestionSourceTrace(start_line=1, end_line=3, line_numbers=[1, 2, 3]),
        confidence=0.9,
        review_status=review,
        issues=issues or [],
    )


def _mapping(
    qid: str = "q_0001",
    qnum: int = 1,
    *,
    answer_key: str | None = "B",
    solution_text: str | None = None,
    status: MappingStatus = MappingStatus.MAPPED,
    issues: list[str] | None = None,
) -> QuestionAnswerSolutionMapping:
    answer = None
    if answer_key:
        answer = AnswerCandidate(
            question_number=qnum,
            answer_key=answer_key,
            answer_key_raw=answer_key.lower(),
            source_line=10,
            source_text_raw=f"S{qnum}. Ans.({answer_key.lower()})",
            confidence=0.95,
        )
    solution = None
    if solution_text:
        solution = SolutionCandidate(
            question_number=qnum,
            raw_text=solution_text,
            start_line=10,
            end_line=11,
            line_numbers=[10, 11],
            confidence=0.9,
        )
    return QuestionAnswerSolutionMapping(
        question_id=qid,
        question_number=qnum,
        answer_available=answer is not None,
        answer=answer,
        solution_available=solution is not None,
        solution=solution,
        mapping_status=status,
        confidence=0.9,
        issues=issues or [],
    )


def test_question_only_finalization() -> None:
    candidates = [_candidate()]
    mappings = [_mapping(answer_key=None, status=MappingStatus.NOT_AVAILABLE)]
    mappings[0] = QuestionAnswerSolutionMapping(
        question_id="q_0001",
        question_number=1,
        mapping_status=MappingStatus.NOT_AVAILABLE,
        confidence=0.9,
    )
    package, report = build_final_package(candidates, mappings)
    assert package.items[0].validation_status == ValidationStatus.QUESTION_ONLY_VALIDATED
    assert not package.items[0].answer.available
    assert report.question_only_validated_count == 1


def test_mapped_answer_finalization() -> None:
    package, report = build_final_package(
        [_candidate()],
        [_mapping(answer_key="B")],
    )
    assert package.items[0].validation_status == ValidationStatus.VALIDATED
    assert package.items[0].answer.key == "B"
    assert report.validated_count == 1


def test_mapped_answer_and_solution() -> None:
    package, _ = build_final_package(
        [_candidate()],
        [_mapping(answer_key="B", solution_text="S1. Ans.(b) Explanation")],
    )
    assert package.items[0].solution.available
    assert "Explanation" in package.items[0].solution.text_raw
    assert package.solved_count == 1


def test_raw_text_exact_preservation() -> None:
    raw = "Q1.  spaced  text\n(a) 3\n(b) 4"
    package, _ = build_final_package(
        [_candidate(raw=raw)],
        [_mapping()],
    )
    assert package.items[0].raw_text == raw


def test_option_text_exact_preservation() -> None:
    package, _ = build_final_package([_candidate()], [_mapping()])
    assert package.items[0].options[0].text_raw == "3"
    assert package.items[0].options[1].key_raw == "(b)"


def test_answer_key_mismatch_needs_review() -> None:
    package, report = build_final_package(
        [_candidate()],
        [_mapping(answer_key="D")],
    )
    assert package.items[0].validation_status == ValidationStatus.NEEDS_REVIEW
    assert "answer_option_mismatch" in package.items[0].issues
    assert report.answer_option_mismatch_count == 1


def test_answer_key_matches_option() -> None:
    package, _ = build_final_package([_candidate()], [_mapping(answer_key="A")])
    assert package.items[0].validation_status == ValidationStatus.VALIDATED


def test_visual_asset_preservation() -> None:
    assets = [
        QuestionAssetReference(
            raw_markdown="![](fig/1.png)",
            asset_path="fig/1.png",
            role=AssetRole.QUESTION_IMAGE,
            line_number=2,
            confidence=0.9,
        ),
    ]
    mappings_na = [
        QuestionAnswerSolutionMapping(
            question_id="q_0001",
            question_number=1,
            mapping_status=MappingStatus.NOT_AVAILABLE,
            confidence=0.9,
        ),
    ]
    package, report = build_final_package([_candidate(assets=assets)], mappings_na)
    assert package.items[0].assets[0].raw_markdown == "![](fig/1.png)"
    assert report.visual_question_count == 1


def test_incomplete_candidate_preservation() -> None:
    package, report = build_final_package(
        [_candidate(options=[], review=CandidateReviewStatus.CANDIDATE_INCOMPLETE)],
        [
            QuestionAnswerSolutionMapping(
                question_id="q_0001",
                question_number=1,
                mapping_status=MappingStatus.NOT_AVAILABLE,
                confidence=0.9,
            ),
        ],
    )
    assert len(package.items) == 1
    assert package.items[0].validation_status == ValidationStatus.INCOMPLETE
    assert report.incomplete_count == 1


def test_duplicate_conflict_propagation() -> None:
    package, report = build_final_package(
        [_candidate(review=CandidateReviewStatus.CANDIDATE_DUPLICATE)],
        [_mapping(status=MappingStatus.DUPLICATE_CONFLICT, issues=["conflicting_answer_keys"])],
    )
    assert package.items[0].validation_status == ValidationStatus.DUPLICATE_CONFLICT
    assert report.duplicate_conflict_count == 1


def test_final_item_count_equals_candidates() -> None:
    candidates = [
        _candidate("q_0001", 1),
        _candidate("q_0002", 2, raw="Q2. Second"),
    ]
    mappings = [
        _mapping("q_0001", 1),
        QuestionAnswerSolutionMapping(
            question_id="q_0002",
            question_number=2,
            mapping_status=MappingStatus.NOT_AVAILABLE,
            confidence=0.9,
        ),
    ]
    package, report = build_final_package(candidates, mappings)
    assert len(package.items) == 2
    assert report.total_questions == 2


def test_validation_report_counts() -> None:
    package, report = build_final_package(
        [_candidate(), _candidate("q_0002", 2, options=[], review=CandidateReviewStatus.CANDIDATE_INCOMPLETE)],
        [
            _mapping(),
            QuestionAnswerSolutionMapping(
                question_id="q_0002",
                question_number=2,
                mapping_status=MappingStatus.NOT_AVAILABLE,
                confidence=0.9,
            ),
        ],
        missing_question_numbers=[3],
        duplicate_question_numbers=[],
    )
    assert report.missing_question_numbers == [3]
    assert report.validated_count + report.incomplete_count >= 1


def _write_full_package(
    package: Path,
    candidates: list[QuestionCandidate],
    mappings: list[QuestionAnswerSolutionMapping],
    *,
    with_mapping: bool = True,
) -> None:
    questions = package / "questions"
    mappings_dir = package / "mappings"
    questions.mkdir(parents=True)
    if with_mapping:
        mappings_dir.mkdir(parents=True)
    (questions / "question-candidates.json").write_text(
        json.dumps([c.model_dump(mode="json") for c in candidates]),
        encoding="utf-8",
    )
    if with_mapping:
        (mappings_dir / "answer-solution-map.json").write_text(
            json.dumps([m.model_dump(mode="json") for m in mappings]),
            encoding="utf-8",
        )


def test_package_directory_success(tmp_path: Path) -> None:
    pkg = tmp_path / "extraction_package"
    _write_full_package(pkg, [_candidate()], [_mapping()])
    package, report = build_final_package_from_directory(pkg)
    assert (pkg / "final" / "questions.json").exists()
    assert (pkg / "final" / "validation-report.json").exists()
    assert package.total_questions == 1


def test_package_missing_candidates(tmp_path: Path) -> None:
    pkg = tmp_path / "extraction_package"
    pkg.mkdir()
    with pytest.raises(FinalizeError, match="Missing question candidates"):
        build_final_package_from_directory(pkg)


def test_package_missing_mapping(tmp_path: Path) -> None:
    pkg = tmp_path / "extraction_package"
    _write_full_package(pkg, [_candidate()], [_mapping()], with_mapping=False)
    with pytest.raises(FinalizeError, match="Missing answer/solution map"):
        build_final_package_from_directory(pkg)
