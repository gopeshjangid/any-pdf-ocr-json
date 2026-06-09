"""Tests for visual asset binding and solution anchor splitting."""

from __future__ import annotations

from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.classification import LineType, MarkdownLineRecord
from meritranker_data_ingestion.schemas.question_candidates import CandidateReviewStatus
from meritranker_data_ingestion.services.answer_solution_mapper import (
    _collect_solution_blocks,
    map_answers_solutions,
)
from meritranker_data_ingestion.services.question_candidate_parser import (
    _build_candidate,
    parse_question_candidates,
)
from meritranker_data_ingestion.services.review_exporter import build_review_export
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionItem,
    FinalQuestionPackage,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.question_candidates import QuestionCandidate


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


Q67_LINES = [
    _line(1, "**Q67.** Select the figure that will come next in the following figure series.", LineType.QUESTION_ANCHOR, label="Q67"),
    _line(2, "![](_page_9_Picture_17.jpeg)", LineType.IMAGE_REFERENCE),
    _line(3, "(a)", LineType.OPTION_CANDIDATE, label="A"),
    _line(4, "![](_page_9_Picture_19.jpeg)", LineType.IMAGE_REFERENCE),
    _line(5, "(b)", LineType.OPTION_CANDIDATE, label="B"),
    _line(6, "![](_page_9_Picture_21.jpeg)", LineType.IMAGE_REFERENCE),
    _line(7, "(c)", LineType.OPTION_CANDIDATE, label="C"),
    _line(8, "![](_page_9_Picture_23.jpeg)", LineType.IMAGE_REFERENCE),
    _line(9, "(d)", LineType.OPTION_CANDIDATE, label="D"),
    _line(10, "![](_page_9_Picture_25.jpeg)", LineType.IMAGE_REFERENCE),
]

Q68_LINES = [
    _line(1, "**Q68.** Select the figure that will come next in the following figure series.", LineType.QUESTION_ANCHOR, label="Q68"),
    _line(2, "![](_page_9_Picture_27.jpeg)", LineType.IMAGE_REFERENCE),
    _line(3, "![](_page_9_Picture_29.jpeg)", LineType.IMAGE_REFERENCE),
    _line(4, "(b)", LineType.OPTION_CANDIDATE, label="B"),
    _line(5, "![](_page_9_Picture_31.jpeg)", LineType.IMAGE_REFERENCE),
    _line(6, "(c)", LineType.OPTION_CANDIDATE, label="C"),
    _line(7, "![](_page_9_Picture_33.jpeg)", LineType.IMAGE_REFERENCE),
    _line(8, "![](_page_9_Picture_35.jpeg)", LineType.IMAGE_REFERENCE),
]


def test_q67_visual_option_binding(tmp_path: Path) -> None:
    assets_dir = tmp_path / "marker" / "assets"
    assets_dir.mkdir(parents=True)
    for name in (
        "_page_9_Picture_17.jpeg",
        "_page_9_Picture_19.jpeg",
        "_page_9_Picture_21.jpeg",
        "_page_9_Picture_23.jpeg",
        "_page_9_Picture_25.jpeg",
    ):
        (assets_dir / name).write_bytes(b"img")

    candidate = _build_candidate(67, Q67_LINES, False, package_dir=tmp_path)
    assert candidate.question_number == 67
    assert len(candidate.options) == 4
    assert all(opt.linked_asset_paths for opt in candidate.options)
    assert candidate.options[0].linked_asset_paths == ["_page_9_Picture_19.jpeg"]
    assert "empty_option_text" not in candidate.options[0].issues
    assert candidate.review_status == CandidateReviewStatus.CANDIDATE_NEEDS_REVIEW
    assert "visual_question_requires_review" in candidate.issues
    assert candidate.review_status != CandidateReviewStatus.CANDIDATE_INCOMPLETE

    question_assets = [a for a in candidate.assets if a.role.value == "question_image"]
    assert len(question_assets) == 1
    assert question_assets[0].asset_path == "_page_9_Picture_17.jpeg"


def test_q68_missing_option_labels(tmp_path: Path) -> None:
    assets_dir = tmp_path / "marker" / "assets"
    assets_dir.mkdir(parents=True)
    for i in range(27, 36, 2):
        (assets_dir / f"_page_9_Picture_{i}.jpeg").write_bytes(b"img")

    candidate = _build_candidate(68, Q68_LINES, False, package_dir=tmp_path)
    assert "missing_option_labels_for_visual_question" in candidate.issues
    assert candidate.review_status == CandidateReviewStatus.CANDIDATE_INCOMPLETE
    option_keys = {opt.key for opt in candidate.options}
    assert option_keys == {"B", "C"}


def test_question_image_before_first_option() -> None:
    candidate = _build_candidate(1, Q67_LINES, False)
    roles = {a.asset_path: a.role.value for a in candidate.assets}
    assert roles["_page_9_Picture_17.jpeg"] == "question_image"


def test_option_image_role_and_link() -> None:
    candidate = _build_candidate(1, Q67_LINES, False)
    option_assets = [a for a in candidate.assets if a.role.value == "option_image"]
    assert len(option_assets) == 4
    assert option_assets[0].option_key == "A"


def test_linked_asset_missing_issue(tmp_path: Path) -> None:
    candidate = _build_candidate(1, Q67_LINES, False, package_dir=tmp_path)
    assert any("linked_asset_missing" in a.issues for a in candidate.assets)


def test_linked_asset_exists_no_missing_issue(tmp_path: Path) -> None:
    assets_dir = tmp_path / "marker" / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "_page_9_Picture_17.jpeg").write_bytes(b"img")
    (assets_dir / "_page_9_Picture_19.jpeg").write_bytes(b"img")

    lines = Q67_LINES[:4]
    candidate = _build_candidate(1, lines, False, package_dir=tmp_path)
    present = [a for a in candidate.assets if a.asset_path == "_page_9_Picture_17.jpeg"][0]
    assert "linked_asset_missing" not in present.issues


def test_s1_s2_same_line_split() -> None:
    lines = [
        _line(1, "**S1. Ans.(d) Sol.**", LineType.SOLUTION_ANCHOR, label="S1"),
        _line(2, "(3, 24, 4) → 3 × 4 × 2 = 24", LineType.TEXT),
        _line(
            3,
            "similarly, option (d) (4, 72, 9) → 4 × 9 × 2 = 72 **S2. Ans.(d) Sol.**",
            LineType.TEXT,
        ),
        _line(4, "C +3 → F +3 → I", LineType.TEXT),
        _line(5, "## **S3. Ans.(a)**", LineType.SOLUTION_ANCHOR, label="S3"),
    ]
    blocks, stats = _collect_solution_blocks(lines, solution_start=1)
    s1 = blocks[1][0]
    s2 = blocks[2][0]
    assert "S2" not in s1.raw_text
    assert s2.raw_text.startswith("**S2. Ans.(d) Sol.**") or "S2. Ans.(d)" in s2.raw_text
    assert stats.solution_segments_created_from_splits >= 1


def test_decorated_solution_anchors_split() -> None:
    lines = [
        _line(1, "**S1. Ans.(d) Sol.**", LineType.SOLUTION_ANCHOR, label="S1"),
        _line(2, "solution one", LineType.TEXT),
        _line(3, "## **S2. Ans.(c)**", LineType.SOLUTION_ANCHOR, label="S2"),
        _line(4, "solution two", LineType.TEXT),
    ]
    blocks, _ = _collect_solution_blocks(lines, solution_start=1)
    assert blocks[1][0].raw_text == "**S1. Ans.(d) Sol.**\nsolution one"
    assert blocks[2][0].raw_text.startswith("## **S2. Ans.(c)**")


def test_mapper_diagnostics_populated() -> None:
    lines = [
        _line(1, "Q1. Test", LineType.QUESTION_ANCHOR, label="Q1"),
        _line(2, "**S1. Ans.(d) Sol.**", LineType.SOLUTION_ANCHOR, label="S1"),
        _line(3, "part one **S2. Ans.(b) Sol.**", LineType.TEXT),
    ]
    candidates = parse_question_candidates(lines[:1], [], Path("/tmp/pkg")).candidates
    result = map_answers_solutions(lines, [], candidates, Path("/tmp/pkg"))
    assert result.multi_anchor_solution_lines_count >= 0
    assert result.solution_segments_created_from_splits >= 1
    assert result.solution_candidate_count >= 2


def test_review_export_visual_reason() -> None:
    from meritranker_data_ingestion.schemas.final_question_package import (
        FinalQuestionAnswer,
        FinalQuestionAsset,
        FinalQuestionOption,
        FinalQuestionSolution,
        FinalQuestionSourceTrace,
    )
    from meritranker_data_ingestion.schemas.question_candidates import AssetRole

    trace = FinalQuestionSourceTrace(start_line=1, end_line=5, line_numbers=[1, 2, 3, 4, 5])
    item = FinalQuestionItem(
        question_id="q_0067",
        question_number=67,
        question_number_raw="Q67",
        question_text_raw="Visual question",
        raw_text="raw",
        options=[
            FinalQuestionOption(
                key="A",
                key_raw="(a)",
                text_raw="",
                linked_asset_paths=["img_a.jpeg"],
                source_trace=trace,
                confidence=0.9,
            ),
        ],
        answer=FinalQuestionAnswer(available=True, key="A"),
        solution=FinalQuestionSolution(available=True, text_raw="sol"),
        assets=[
            FinalQuestionAsset(
                raw_markdown="![](img_a.jpeg)",
                asset_path="img_a.jpeg",
                role=AssetRole.OPTION_IMAGE,
                option_key="A",
                line_number=3,
                confidence=0.9,
            ),
        ],
        source_trace=trace,
        validation_status=ValidationStatus.NEEDS_REVIEW,
        confidence=0.8,
        issues=["visual_question_requires_review"],
    )
    report = build_review_export(FinalQuestionPackage(
        source_file_name="exam.pdf",
        parser_engine="marker",
        total_questions=1,
        valid_questions=0,
        items=[item],
    ))
    assert report.items[0].review_reason == "visual_question_requires_review"
