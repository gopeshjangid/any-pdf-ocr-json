"""Tests for Part 10 candidate structural audit and parser hardening."""

from __future__ import annotations

from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.classification import LineType, MarkdownLineRecord
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionAnswer,
    FinalQuestionAsset,
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionPackage,
    FinalQuestionSolution,
    FinalQuestionSourceTrace,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    AnswerCandidate,
    MappingStatus,
    QuestionAnswerSolutionMapping,
    SolutionCandidate,
)
from meritranker_data_ingestion.schemas.ingestion_eligibility import AnswerMode, EligibilityStatus
from meritranker_data_ingestion.schemas.question_candidates import CandidateReviewStatus
from meritranker_data_ingestion.services.candidate_structure_auditor import build_structure_audit
from meritranker_data_ingestion.services.ingestion_eligibility_builder import build_ingestion_eligibility
from meritranker_data_ingestion.services.option_marker_splitter import split_option_text
from meritranker_data_ingestion.services.question_candidate_parser import _build_candidate


def _line(
    num: int,
    raw: str,
    line_type: LineType,
    *,
    label: str | None = None,
) -> MarkdownLineRecord:
    return MarkdownLineRecord(
        line_number=num,
        raw_text=raw,
        normalized_preview=raw,
        line_type=line_type,
        detected_label=label,
        confidence=0.9,
    )


def _trace() -> FinalQuestionSourceTrace:
    return FinalQuestionSourceTrace(start_line=1, end_line=5, line_numbers=[1, 2, 3, 4, 5])


def _final_option(key: str, text: str = "", linked: list[str] | None = None) -> FinalQuestionOption:
    return FinalQuestionOption(
        key=key,
        key_raw=f"({key.lower()})",
        text_raw=text,
        linked_asset_paths=linked or [],
        source_trace=_trace(),
        confidence=0.9,
    )


def _final_item(
    qnum: int,
    *,
    question_text: str,
    issues: list[str] | None = None,
    options: list[FinalQuestionOption] | None = None,
    assets: list[FinalQuestionAsset] | None = None,
    status: ValidationStatus = ValidationStatus.VALIDATED,
) -> FinalQuestionItem:
    return FinalQuestionItem(
        question_id=f"q_{qnum:04d}",
        question_number=qnum,
        question_number_raw=f"Q{qnum}",
        question_text_raw=question_text,
        raw_text=question_text,
        options=options or [],
        answer=FinalQuestionAnswer(available=True, key="A", confidence=0.9),
        solution=FinalQuestionSolution(available=True, text_raw="Sol", confidence=0.9),
        assets=assets or [],
        source_trace=_trace(),
        validation_status=status,
        confidence=0.9,
        issues=issues or [],
    )


Q29_LINES = [
    _line(1, "**Q29.** If POSTER is coded as 592314 and DARK is coded as 8647, then how will STROKE be coded as?", LineType.QUESTION_ANCHOR, label="Q29"),
    _line(2, "(a) 234917", LineType.OPTION_CANDIDATE, label="A"),
    _line(3, "(b) 234971", LineType.OPTION_CANDIDATE, label="B"),
    _line(4, "(c) 493287 (d) 329417", LineType.OPTION_CANDIDATE, label="C"),
]

Q19_LINES = [
    _line(1, "**Q19.** A father was twelve times as old as his son twenty years ago.", LineType.QUESTION_ANCHOR, label="Q19"),
    _line(2, "(a) 22 and 44 years", LineType.OPTION_CANDIDATE, label="A"),
    _line(3, "(b) 33 and 66 years", LineType.OPTION_CANDIDATE, label="B"),
    _line(4, "(c) 27 and 54 years", LineType.OPTION_CANDIDATE, label="C"),
    _line(5, "(d) 15 and 30 years", LineType.OPTION_CANDIDATE, label="D"),
    _line(6, "![](noise.jpeg)", LineType.IMAGE_REFERENCE),
]

Q31_LINES = [
    _line(1, "**Q31.** How many squares are there in the following figure?", LineType.QUESTION_ANCHOR, label="Q31"),
    _line(2, "![](figure.jpeg)", LineType.IMAGE_REFERENCE),
    _line(3, "(a) 10", LineType.OPTION_CANDIDATE, label="A"),
    _line(4, "(b) 12", LineType.OPTION_CANDIDATE, label="B"),
    _line(5, "(c) 14", LineType.OPTION_CANDIDATE, label="C"),
    _line(6, "(d) 16", LineType.OPTION_CANDIDATE, label="D"),
]

Q41_LINES = [
    _line(1, "**Q41.** Select the correct dice positions.", LineType.QUESTION_ANCHOR, label="Q41"),
    _line(2, "![](dice1.jpeg)", LineType.IMAGE_REFERENCE),
    _line(3, "![](dice2.jpeg)", LineType.IMAGE_REFERENCE),
    _line(4, "(a) opt1", LineType.OPTION_CANDIDATE, label="A"),
    _line(5, "(b) opt2", LineType.OPTION_CANDIDATE, label="B"),
    _line(6, "(c) opt3", LineType.OPTION_CANDIDATE, label="C"),
    _line(7, "(d) opt4", LineType.OPTION_CANDIDATE, label="D"),
]

Q67_LINES = [
    _line(1, "**Q67.** Select the figure that will come next in the following figure series.", LineType.QUESTION_ANCHOR, label="Q67"),
    _line(2, "![](q.jpeg)", LineType.IMAGE_REFERENCE),
    _line(3, "(a)", LineType.OPTION_CANDIDATE, label="A"),
    _line(4, "![](a.jpeg)", LineType.IMAGE_REFERENCE),
    _line(5, "(b)", LineType.OPTION_CANDIDATE, label="B"),
    _line(6, "![](b.jpeg)", LineType.IMAGE_REFERENCE),
    _line(7, "(c)", LineType.OPTION_CANDIDATE, label="C"),
    _line(8, "![](c.jpeg)", LineType.IMAGE_REFERENCE),
    _line(9, "(d)", LineType.OPTION_CANDIDATE, label="D"),
    _line(10, "![](d.jpeg)", LineType.IMAGE_REFERENCE),
]

Q52_LINES = [
    _line(1, "**Q52.** Select the correct mirror image.", LineType.QUESTION_ANCHOR, label="Q52"),
    _line(2, "![](q.jpeg)", LineType.IMAGE_REFERENCE),
    _line(3, "![](opt1.jpeg)", LineType.IMAGE_REFERENCE),
    _line(4, "![](opt2.jpeg)", LineType.IMAGE_REFERENCE),
]


def test_same_line_option_split() -> None:
    segments, split = split_option_text("493287 (d) 329417", "C")
    assert split is True
    assert len(segments) == 2
    assert segments[0] == ("C", "(c)", "493287")
    assert segments[1] == ("D", "(d)", "329417")


def test_no_split_in_normal_prose() -> None:
    segments, split = split_option_text("option (d) is correct in prose", None)
    assert split is False
    assert segments == []


def test_q29_same_line_option_split_candidate() -> None:
    candidate = _build_candidate(29, Q29_LINES, False)
    assert len(candidate.options) == 4
    assert candidate.options[2].text_raw == "493287"
    assert candidate.options[3].text_raw == "329417"
    assert "same_line_option_split_applied" in candidate.issues
    assert candidate.review_status == CandidateReviewStatus.CANDIDATE_VALID


def test_q19_trailing_image_not_bound(tmp_path: Path) -> None:
    assets_dir = tmp_path / "marker" / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "noise.jpeg").write_bytes(b"x")

    candidate = _build_candidate(19, Q19_LINES, False, package_dir=tmp_path)
    option_d = candidate.options[3]
    assert option_d.linked_asset_paths == []
    noise = [a for a in candidate.assets if a.asset_path == "noise.jpeg"][0]
    assert noise.role.value == "noise_candidate"
    assert "possible_noise_asset_after_options" in candidate.issues
    assert candidate.review_status == CandidateReviewStatus.CANDIDATE_VALID


def test_q31_visual_text_options_valid_but_diagram_issue(tmp_path: Path) -> None:
    assets_dir = tmp_path / "marker" / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "figure.jpeg").write_bytes(b"x")

    candidate = _build_candidate(31, Q31_LINES, False, package_dir=tmp_path)
    assert candidate.review_status == CandidateReviewStatus.CANDIDATE_VALID
    assert "visual_question_requires_diagram_syntax" in candidate.issues


def test_q41_multi_question_images(tmp_path: Path) -> None:
    assets_dir = tmp_path / "marker" / "assets"
    assets_dir.mkdir(parents=True)
    for name in ("dice1.jpeg", "dice2.jpeg"):
        (assets_dir / name).write_bytes(b"x")

    candidate = _build_candidate(41, Q41_LINES, False, package_dir=tmp_path)
    roles = {a.asset_path: a.role.value for a in candidate.assets}
    assert roles["dice1.jpeg"] == "question_image"
    assert roles["dice2.jpeg"] == "question_support_image"
    assert "unlabeled_option_images" not in candidate.issues
    assert candidate.review_status == CandidateReviewStatus.CANDIDATE_VALID


def test_q67_visual_option_binding_preserved(tmp_path: Path) -> None:
    assets_dir = tmp_path / "marker" / "assets"
    assets_dir.mkdir(parents=True)
    for name in ("q.jpeg", "a.jpeg", "b.jpeg", "c.jpeg", "d.jpeg"):
        (assets_dir / name).write_bytes(b"x")

    candidate = _build_candidate(67, Q67_LINES, False, package_dir=tmp_path)
    assert all(opt.linked_asset_paths for opt in candidate.options)
    assert candidate.review_status == CandidateReviewStatus.CANDIDATE_NEEDS_REVIEW
    assert "visual_question_requires_review" in candidate.issues


def test_q52_missing_labels_blocked(tmp_path: Path) -> None:
    assets_dir = tmp_path / "marker" / "assets"
    assets_dir.mkdir(parents=True)
    for name in ("q.jpeg", "opt1.jpeg", "opt2.jpeg"):
        (assets_dir / name).write_bytes(b"x")

    candidate = _build_candidate(52, Q52_LINES, False, package_dir=tmp_path)
    assert not candidate.options
    assert "source_backed_option_labels_missing" in candidate.issues
    assert candidate.review_status == CandidateReviewStatus.CANDIDATE_INCOMPLETE


def _mapping(item: FinalQuestionItem) -> QuestionAnswerSolutionMapping:
    return QuestionAnswerSolutionMapping(
        question_id=item.question_id,
        question_number=item.question_number,
        answer_available=item.answer.available,
        answer=AnswerCandidate(
            question_number=item.question_number or 1,
            answer_key=item.answer.key,
            answer_key_raw=item.answer.key_raw or "a",
            source_line=10,
            source_text_raw="Ans.(a)",
            confidence=0.9,
        )
        if item.answer.available
        else None,
        solution_available=item.solution.available,
        solution=SolutionCandidate(
            question_number=item.question_number or 1,
            raw_text="Solution",
            start_line=10,
            end_line=11,
            confidence=0.9,
        )
        if item.solution.available
        else None,
        mapping_status=MappingStatus.MAPPED,
        confidence=0.9,
        issues=[],
    )


def test_eligibility_visual_text_review_required() -> None:
    item = _final_item(
        31,
        question_text="How many squares are there in the following figure?",
        issues=["visual_question_requires_diagram_syntax"],
        options=[
            _final_option("A", "10"),
            _final_option("B", "12"),
            _final_option("C", "14"),
            _final_option("D", "16"),
        ],
        assets=[
            FinalQuestionAsset(
                raw_markdown="![](f.jpeg)",
                asset_path="f.jpeg",
                role="question_image",
                line_number=2,
                confidence=0.9,
            ),
        ],
    )
    report = build_ingestion_eligibility(
        _package(item),
        [_mapping(item)],
        package_dir=Path("/tmp/pkg"),
        answer_mode=AnswerMode.REQUIRED,
    )
    entry = report.items[0]
    assert entry.eligibility_status == EligibilityStatus.REVIEW_REQUIRED
    assert "visual_question_requires_diagram_syntax" in entry.review_reasons


def test_eligibility_non_visual_noise_still_eligible() -> None:
    item = _final_item(
        19,
        question_text="A father was twelve times as old as his son.",
        issues=["possible_noise_asset_after_options"],
        options=[
            _final_option("A", "22"),
            _final_option("B", "33"),
            _final_option("C", "27"),
            _final_option("D", "15"),
        ],
        assets=[
            FinalQuestionAsset(
                raw_markdown="![](n.jpeg)",
                asset_path="n.jpeg",
                role="noise_candidate",
                line_number=6,
                confidence=0.9,
                issues=["possible_noise_asset_after_options"],
            ),
        ],
    )
    report = build_ingestion_eligibility(
        _package(item),
        [_mapping(item)],
        package_dir=Path("/tmp/pkg"),
        answer_mode=AnswerMode.REQUIRED,
    )
    assert report.items[0].eligibility_status == EligibilityStatus.ELIGIBLE_FOR_INGESTION


def test_eligibility_visual_missing_labels_blocked() -> None:
    item = _final_item(
        52,
        question_text="Select the correct mirror image.",
        issues=["source_backed_option_labels_missing", "missing_options"],
        options=[],
        assets=[
            FinalQuestionAsset(
                raw_markdown="![](q.jpeg)",
                asset_path="q.jpeg",
                role="question_image",
                line_number=2,
                confidence=0.9,
            ),
        ],
        status=ValidationStatus.INCOMPLETE,
    )
    report = build_ingestion_eligibility(
        _package(item),
        [_mapping(item)],
        package_dir=Path("/tmp/pkg"),
        answer_mode=AnswerMode.REQUIRED,
    )
    assert report.items[0].eligibility_status == EligibilityStatus.BLOCKED


def test_structure_audit_counts(tmp_path: Path) -> None:
    assets_dir = tmp_path / "marker" / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "noise.jpeg").write_bytes(b"x")
    c29 = _build_candidate(29, Q29_LINES, False)
    c19 = _build_candidate(19, Q19_LINES, False, package_dir=tmp_path)
    audit = build_structure_audit([c29, c19])
    assert audit["total_candidates"] == 2
    assert audit["same_line_option_split_count"] == 1
    assert audit["noise_asset_candidate_count"] == 1


def _package(item: FinalQuestionItem) -> FinalQuestionPackage:
    return FinalQuestionPackage(
        source_file_name="exam.pdf",
        parser_engine="marker",
        total_questions=1,
        valid_questions=1,
        items=[item],
    )
