"""Tests for Part 14O simplified final JSON contract."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalAnswerSource,
    FinalQuestionItem,
    FinalQuestionItemMetadata,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
    FinalQuestionVisual,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.services.final_completeness_verifier import verify_and_fill_completeness
from meritranker_data_ingestion.services.final_questions_public_serializer import (
    serialize_public_package,
    write_public_questions_json,
)
from meritranker_data_ingestion.services.final_readiness_resolver import (
    apply_readiness_metadata,
    resolve_item_readiness,
)
from meritranker_data_ingestion.services.final_review_analyzer import build_final_review_analyzer
from meritranker_data_ingestion.services.share_log_builder import ShareLogContext, build_share_log
from meritranker_data_ingestion.services.visual_detection import apply_visual_metadata


def _four_options() -> list[FinalQuestionOption]:
    return [
        FinalQuestionOption(key="a", key_raw="a", text_raw="A", canonical_key="A"),
        FinalQuestionOption(key="b", key_raw="b", text_raw="B", canonical_key="B"),
        FinalQuestionOption(key="c", key_raw="c", text_raw="C", canonical_key="C"),
        FinalQuestionOption(key="d", key_raw="d", text_raw="D", canonical_key="D"),
    ]


def _item(**kwargs) -> FinalQuestionItem:
    defaults = {
        "final_question_id": "fq_1",
        "global_order": 1,
        "question_number": 1,
        "question_text_raw": "Pick the correct option.",
        "options": _four_options(),
    }
    defaults.update(kwargs)
    return FinalQuestionItem(**defaults)


def test_public_json_top_level_keys_only() -> None:
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=1,
        items=[apply_readiness_metadata(_item())],
    )
    payload = serialize_public_package(package)
    assert set(payload.keys()) == {"fileMeta", "questions"}


def test_public_question_matches_pattern_input_keys() -> None:
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=1,
        items=[
            apply_readiness_metadata(
                _item(
                    correct_answer_key="A",
                    correct_answer_text="A",
                    solution_text_raw="Because A.",
                    solution_source="pdf",
                ),
            ),
        ],
    )
    question = serialize_public_package(package)["questions"][0]
    expected_keys = {
        "externalId",
        "questionText",
        "questionType",
        "options",
        "correctAnswer",
        "solutionText",
        "solutionSource",
        "visuals",
        "metadata",
    }
    assert set(question.keys()) == expected_keys
    assert set(question["metadata"].keys()) == {
        "exams",
        "years",
        "section",
        "sourcePaper",
        "questionNumber",
        "status",
        "reviewIssues",
    }


def test_metadata_uses_only_status_and_review_issues() -> None:
    metadata = resolve_item_readiness(_item())
    dumped = metadata.model_dump()
    assert set(dumped.keys()) == {"status", "review_issues"}


def test_missing_answer_does_not_block_ready_without_solved_pdf() -> None:
    item = _item(
        quality_status=FinalQuestionQualityStatus.ANSWER_UNAVAILABLE,
        answer_source=FinalAnswerSource.UNAVAILABLE,
    )
    metadata = resolve_item_readiness(item, answers_expected=False)
    assert metadata.status == "ready"
    assert "expected_answer_missing" not in metadata.review_issues


def test_missing_answer_in_solved_pdf_stays_ready_with_issue() -> None:
    item = _item(
        quality_status=FinalQuestionQualityStatus.ANSWER_UNAVAILABLE,
        answer_source=FinalAnswerSource.SEPARATE_SOLUTION_SECTION,
    )
    metadata = resolve_item_readiness(item, answers_expected=True)
    assert metadata.status == "ready"
    assert "expected_answer_missing" in metadata.review_issues


def test_missing_options_sets_review_status() -> None:
    item = _item(
        options=[FinalQuestionOption(key="a", key_raw="a", text_raw="Only", canonical_key="A")],
        issues=["incomplete_options"],
    )
    metadata = resolve_item_readiness(item)
    assert metadata.status == "review"
    assert "incomplete_options" in metadata.review_issues


def test_visual_required_without_render_spec() -> None:
    item = _item(
        question_text_raw="Study the following graph and answer.",
        options=[],
    )
    updated = apply_readiness_metadata(apply_visual_metadata(item))
    assert updated.metadata.status == "visual_required"


def test_missing_question_placeholder_blocked() -> None:
    filled, report = verify_and_fill_completeness([_item(question_number=1)], expected_count=2)
    assert report.placeholders_added == 1
    blocked = next(i for i in filled if i.question_number == 2)
    assert blocked.metadata.status == "blocked"
    assert "question_missing_from_extraction" in blocked.metadata.review_issues


def test_public_json_excludes_old_readiness_booleans(tmp_path: Path) -> None:
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=1,
        items=[apply_readiness_metadata(_item())],
    )
    out = tmp_path / "paper.questions.json"
    write_public_questions_json(package, out)
    raw = out.read_text(encoding="utf-8")
    forbidden = (
        "questionBankReady",
        "patternIngestionReady",
        "manualReviewRequired",
        "qualityStatus",
        "ingestionAction",
        "source_trace",
        "sourceTrace",
        "extractionStatus",
        "renderTarget",
        "renderSpec",
        "sourcePage",
    )
    for token in forbidden:
        assert token not in raw


def test_share_log_uses_simplified_metrics(tmp_path: Path) -> None:
    out = tmp_path / "paper"
    out.mkdir()
    pkg = out / "extraction_package"
    fq_dir = pkg / "final-questions"
    fq_dir.mkdir(parents=True)
    (fq_dir / "final-questions.json").write_text(
        FinalQuestionsPackage(
            source_file_name="paper.pdf",
            total_questions_detected=2,
            ready_count=1,
            review_count=1,
            items=[
                apply_readiness_metadata(_item(question_number=1)),
                apply_readiness_metadata(
                    _item(
                        question_number=2,
                        final_question_id="fq_2",
                        global_order=2,
                        options=[],
                        issues=["incomplete_options"],
                    ),
                ),
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )
    (fq_dir / "final-questions-report.json").write_text(
        json.dumps(
            {
                "expected_count": 2,
                "ready_count": 1,
                "review_count": 1,
                "incomplete_options_count": 1,
                "accepted_safe_with_incomplete_options_count": 0,
            },
        ),
        encoding="utf-8",
    )
    ctx = ShareLogContext(
        pdf_file_name="paper.pdf",
        input_path=tmp_path / "paper.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
        questions_json_path=fq_dir / "final-questions.json",
    )
    result = build_share_log(ctx)
    assert "ready_count" in result.metrics
    assert "question_bank_ready_count" not in result.metrics
    assert result.metrics["ready_count"] == 1


def test_review_analyzer_uses_simplified_status(tmp_path: Path) -> None:
    package = FinalQuestionsPackage(
        source_file_name="t.pdf",
        total_questions_detected=1,
        items=[
            apply_readiness_metadata(
                _item(
                    options=[],
                    question_text_raw="In the given figure, find angle.",
                ),
            ),
        ],
    )
    result = build_final_review_analyzer(tmp_path / "extraction_package", package=package)
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    item = data["items"][0]
    assert item["status"] in {"review", "visual_required", "blocked"}
    assert "isQuestionBankUsable" not in item
    assert "isPatternIngestionReady" not in item
