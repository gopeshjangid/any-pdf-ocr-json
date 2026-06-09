"""Tests for Part 14R expected-count canonicalization and status semantics."""

from __future__ import annotations

import json
from pathlib import Path

from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.services.expected_count_canonicalizer import (
    canonicalize_for_expected_count,
)
from meritranker_data_ingestion.services.final_questions_public_serializer import (
    serialize_public_package,
    write_public_questions_json,
)
from meritranker_data_ingestion.services.final_readiness_resolver import (
    apply_readiness_metadata,
    resolve_item_readiness,
)
from meritranker_data_ingestion.services.public_questions_audit import audit_public_questions_json


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


def test_canonicalize_253_to_100_public_slots() -> None:
    raw = [
        _item(
            final_question_id=f"fq_{idx}",
            global_order=idx,
            question_number=(idx % 100) + 1 if idx % 100 else 100,
            question_text_raw=f"Question {idx}",
        )
        for idx in range(1, 254)
    ]
    public, report = canonicalize_for_expected_count(raw, expected_count=100)
    assert len(public) == 100
    assert report.raw_candidate_count == 253
    assert report.public_question_count == 100
    assert report.duplicate_candidate_count > 0


def test_duplicate_candidates_pick_best_by_completeness() -> None:
    weak = apply_readiness_metadata(
        _item(
            final_question_id="fq_weak",
            question_number=1,
            question_text_raw="",
            options=[],
        ),
    )
    strong = apply_readiness_metadata(
        _item(
            final_question_id="fq_strong",
            question_number=1,
            question_text_raw="Complete question text.",
            options=_four_options(),
            correct_answer_key="A",
            correct_answer_text="A",
        ),
    )
    public, report = canonicalize_for_expected_count(
        [weak, strong],
        expected_count=2,
    )
    assert report.duplicate_candidate_count == 1
    assert public[0].final_question_id == "fq_strong"
    assert public[0].question_text_raw == "Complete question text."


def test_missing_slot_creates_blocked_placeholder() -> None:
    public, report = canonicalize_for_expected_count(
        [_item(question_number=1)],
        expected_count=3,
    )
    assert report.missing_placeholder_count == 2
    assert public[1].metadata.status == "blocked"
    assert "question_missing_from_extraction" in public[1].metadata.review_issues


def test_extra_candidates_not_in_public_json(tmp_path: Path) -> None:
    items = [
        apply_readiness_metadata(_item(question_number=1)),
        apply_readiness_metadata(
            _item(
                final_question_id="fq_extra",
                question_number=150,
                question_text_raw="Extra",
            ),
        ),
    ]
    public, _ = canonicalize_for_expected_count(items, expected_count=1)
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=len(public),
        items=public,
    )
    out = tmp_path / "paper.questions.json"
    write_public_questions_json(package, out)
    data = json.loads(out.read_text())
    assert len(data["questions"]) == 1
    assert data["questions"][0]["externalId"] == "Q001"


def test_answer_missing_does_not_downgrade_ready_status() -> None:
    item = _item(
        correct_answer_key=None,
        correct_answer_text=None,
    )
    meta = resolve_item_readiness(item, answers_expected=True)
    assert meta.status == "ready"
    assert "expected_answer_missing" in meta.review_issues


def test_solution_missing_does_not_downgrade_ready_status() -> None:
    item = _item(
        correct_answer_key="A",
        correct_answer_text="A",
        solution_text_raw=None,
        issues=["solution_missing"],
    )
    meta = resolve_item_readiness(item, answers_expected=True)
    assert meta.status == "ready"
    assert "expected_solution_missing" in meta.review_issues


def test_incomplete_options_stays_review() -> None:
    item = _item(options=[])
    meta = resolve_item_readiness(item, answers_expected=True)
    assert meta.status == "review"
    assert "incomplete_options" in meta.review_issues


def test_audit_fails_when_length_not_expected_count() -> None:
    payload = serialize_public_package(
        FinalQuestionsPackage(
            source_file_name="paper.pdf",
            total_questions_detected=2,
            items=[
                apply_readiness_metadata(_item(question_number=1)),
                apply_readiness_metadata(_item(final_question_id="fq_2", question_number=2)),
            ],
        ),
    )
    result = audit_public_questions_json(payload, expected_count=3)
    assert not result.passed
    assert any("question_count_mismatch" in e for e in result.errors)


def test_audit_passes_canonical_100_slots(tmp_path: Path) -> None:
    public, _ = canonicalize_for_expected_count(
        [
            apply_readiness_metadata(
                _item(
                    final_question_id=f"fq_{n}",
                    global_order=n,
                    question_number=n,
                    correct_answer_key="A",
                    correct_answer_text="A",
                ),
            )
            for n in range(1, 101)
        ],
        expected_count=100,
    )
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=100,
        items=public,
    )
    out = tmp_path / "paper.questions.json"
    write_public_questions_json(package, out)
    result = audit_public_questions_json(out, expected_count=100)
    assert result.passed, result.errors
