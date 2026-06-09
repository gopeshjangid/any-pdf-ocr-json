"""Tests for Part 14P public JSON contract stabilization and audit."""

from __future__ import annotations

import json
from pathlib import Path

from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
    FinalQuestionVisual,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.services.final_questions_public_serializer import (
    serialize_public_package,
    write_public_questions_json,
)
from meritranker_data_ingestion.services.final_readiness_resolver import apply_readiness_metadata
from meritranker_data_ingestion.services.public_questions_audit import audit_public_questions_json
from meritranker_data_ingestion.services.visual_detection import apply_visual_metadata

ALLOWED_VISUAL_KEYS = {
    "visualId",
    "type",
    "role",
    "linkedOptionLabel",
    "description",
    "syntax",
    "issues",
}


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


def test_public_visual_has_only_allowed_fields() -> None:
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=1,
        items=[apply_readiness_metadata(apply_visual_metadata(_item(
            question_text_raw="In the given figure, find x.",
            options=[],
        )))],
    )
    visual = serialize_public_package(package)["questions"][0]["visuals"][0]
    assert set(visual.keys()) == ALLOWED_VISUAL_KEYS
    assert "extractionStatus" not in visual
    assert "renderSpec" not in visual
    assert visual["syntax"] is None


def test_visual_required_has_syntax_missing_issue() -> None:
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=1,
        items=[apply_readiness_metadata(apply_visual_metadata(_item(
            question_text_raw="Study the following graph and answer.",
            options=[],
        )))],
    )
    meta = serialize_public_package(package)["questions"][0]["metadata"]
    assert meta["status"] == "visual_required"
    assert "visual_syntax_missing" in meta["reviewIssues"]


def test_audit_helper_passes_valid_public_json(tmp_path: Path) -> None:
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=1,
        items=[apply_readiness_metadata(_item(
            correct_answer_key="A",
            correct_answer_text="A",
            solution_text_raw="work",
        ))],
    )
    out = tmp_path / "paper.questions.json"
    write_public_questions_json(package, out)
    result = audit_public_questions_json(out)
    assert result.passed, result.errors


def test_audit_rejects_legacy_fields() -> None:
    payload = {
        "fileMeta": {
            "sourceName": "x.pdf",
            "sourceType": "pdf_extraction",
            "exam": None,
            "year": None,
            "set": None,
            "shift": None,
            "language": "en",
            "createdBy": "ai_extraction",
            "notes": "",
        },
        "questions": [{
            "externalId": "Q001",
            "questionText": "Q",
            "questionType": "single_choice",
            "options": [],
            "correctAnswer": {"label": None, "text": None},
            "solutionText": None,
            "solutionSource": "unavailable",
            "visuals": [],
            "metadata": {
                "exams": [],
                "years": [],
                "section": None,
                "sourcePaper": None,
                "questionNumber": 1,
                "status": "review",
                "reviewIssues": [],
                "questionBankReady": True,
            },
        }],
    }
    result = audit_public_questions_json(payload)
    assert not result.passed
    assert any("legacy_field" in e or "unexpected_metadata" in e for e in result.errors)


def test_audit_ready_requires_four_options() -> None:
    payload = {
        "fileMeta": {
            "sourceName": "x.pdf",
            "sourceType": "pdf_extraction",
            "exam": None,
            "year": None,
            "set": None,
            "shift": None,
            "language": "en",
            "createdBy": "ai_extraction",
            "notes": "",
        },
        "questions": [{
            "externalId": "Q001",
            "questionText": "Q",
            "questionType": "single_choice",
            "options": [{"label": "A", "text": "only"}],
            "correctAnswer": {"label": None, "text": None},
            "solutionText": None,
            "solutionSource": "unavailable",
            "visuals": [],
            "metadata": {
                "exams": [],
                "years": [],
                "section": None,
                "sourcePaper": None,
                "questionNumber": 1,
                "status": "ready",
                "reviewIssues": [],
            },
        }],
    }
    result = audit_public_questions_json(payload)
    assert not result.passed
    assert any("ready_with_incomplete_options" in e for e in result.errors)


def test_audit_quant_package_if_present() -> None:
    path = Path(
        "batch_outputs/100-quantitative-aptitude-question_with_solution/"
        "100-quantitative-aptitude-question_with_solution.questions.json",
    )
    if not path.exists():
        return
    result = audit_public_questions_json(path)
    assert result.passed, result.errors


def test_blocked_empty_text_gets_missing_issue_for_audit() -> None:
    item = _item(
        question_text_raw="",
        quality_status=FinalQuestionQualityStatus.BLOCKED,
        issues=["hallucination_suspected"],
    )
    resolved = apply_readiness_metadata(item)
    assert resolved.metadata.status == "blocked"
    assert "question_missing_from_extraction" in resolved.metadata.review_issues
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=1,
        items=[resolved],
    )
    payload = serialize_public_package(package)
    result = audit_public_questions_json(payload)
    assert result.passed, result.errors


def test_visual_syntax_from_render_ready_spec() -> None:
    item = _item(
        visuals=[
            FinalQuestionVisual(
                visual_id="Q001_V1",
                type="geometry",
                extraction_status="render_ready",
                render_spec={
                    "format": "merit_visual_v1",
                    "kind": "geometry",
                    "canvas": {"width": 800, "height": 500},
                    "objects": [{"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 10}],
                    "constraints": [],
                },
            ),
        ],
    )
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=1,
        items=[apply_readiness_metadata(item)],
    )
    visual = serialize_public_package(package)["questions"][0]["visuals"][0]
    assert visual["syntax"] is not None
    assert visual["syntax"]["format"] == "merit_visual_v1"
