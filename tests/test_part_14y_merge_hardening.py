"""Part 14Y merge gate hardening tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.services.public_questions_audit import audit_public_questions_json
from meritranker_data_ingestion.services.review_merge_service import (
    REASON_NON_READY_REMAINING,
    REASON_VALIDATION_FAILED,
    merge_reviewed_questions,
    merge_reviewed_questions_folder,
)


def _sample_question(
    qnum: int,
    status: str,
    *,
    options: int = 4,
    visual: bool = False,
    correct_label: str | None = None,
    correct_text: str | None = None,
    legacy_field: bool = False,
) -> dict:
    opts = [
        {"label": label, "text": f"opt {label}"}
        for label in ("A", "B", "C", "D")[:options]
    ]
    visuals = [{"syntax": None, "type": "image"}] if visual else []
    question = {
        "externalId": f"Q{qnum:03d}",
        "questionText": f"Question {qnum}",
        "questionType": "single_choice",
        "options": opts,
        "correctAnswer": {"label": correct_label, "text": correct_text},
        "solutionText": None,
        "solutionSource": "unavailable",
        "visuals": visuals,
        "metadata": {
            "exams": [],
            "years": [],
            "section": None,
            "sourcePaper": "test.pdf",
            "questionNumber": qnum,
            "status": status,
            "reviewIssues": [] if status == "ready" else ["incomplete_options"],
        },
    }
    if legacy_field:
        question["questionBankReady"] = True
    return question


def _write_pair(
    folder: Path,
    stem: str,
    questions: list[dict],
    review: list[dict] | None = None,
) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    questions_path = folder / f"{stem}.questions.json"
    review_path = folder / f"{stem}.review.json"
    questions_path.write_text(
        json.dumps({"fileMeta": {"sourceName": "test.pdf"}, "questions": questions}, indent=2),
        encoding="utf-8",
    )
    review_path.write_text(
        json.dumps(
            {"fileMeta": {"sourceName": "test.pdf"}, "questions": review or []},
            indent=2,
        ),
        encoding="utf-8",
    )
    return questions_path, review_path


def test_merge_does_not_copy_when_non_ready_remain(tmp_path: Path) -> None:
    stem = "sample_pdf"
    folder = tmp_path / stem
    questions = [_sample_question(1, "ready"), _sample_question(2, "review")]
    questions_path, review_path = _write_pair(folder, stem, questions, review=[])

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )

    assert result.validation_passed
    assert not result.passed
    assert not result.copied_to_ready_dir
    assert result.reason_if_not_copied == REASON_NON_READY_REMAINING
    assert result.final_questions_path is not None
    assert result.final_questions_path.exists()
    assert result.ready_copy_path is None
    assert not (tmp_path / "ready" / f"{stem}.final.questions.json").exists()


def test_allow_partial_copies_when_non_ready_remain(tmp_path: Path) -> None:
    stem = "sample_pdf"
    folder = tmp_path / stem
    questions = [_sample_question(1, "ready"), _sample_question(2, "review")]
    questions_path, review_path = _write_pair(folder, stem, questions, review=[])

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
        allow_partial=True,
    )

    assert result.passed
    assert result.copied_to_ready_dir
    assert result.ready_copy_path is not None
    assert result.ready_copy_path.exists()


def test_merge_report_includes_required_fields(tmp_path: Path) -> None:
    stem = "sample_pdf"
    folder = tmp_path / stem
    questions = [_sample_question(1, "ready"), _sample_question(2, "visual_required")]
    questions_path, review_path = _write_pair(folder, stem, questions, review=[])

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )

    report = json.loads(result.report_json_path.read_text(encoding="utf-8"))
    for key in (
        "pdf_stem",
        "questions_json",
        "review_json",
        "final_questions_json",
        "ready_dir_output_path",
        "total_questions",
        "ready_count",
        "review_count",
        "visual_required_count",
        "blocked_count",
        "copied_to_ready_dir",
        "reason_if_not_copied",
        "validation_status",
        "validation_errors",
        "patched_question_count",
        "unchanged_question_count",
    ):
        assert key in report
    assert "Merge Report" in result.report_md_path.read_text(encoding="utf-8")
    assert "visual_required" in result.report_md_path.read_text(encoding="utf-8")


def test_merge_copies_when_all_ready(tmp_path: Path) -> None:
    stem = "sample_pdf"
    folder = tmp_path / stem
    questions = [_sample_question(1, "ready"), _sample_question(2, "ready")]
    review = [_sample_question(2, "ready")]
    questions_path, review_path = _write_pair(folder, stem, questions, review)

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )

    assert result.passed
    assert result.copied_to_ready_dir
    assert (tmp_path / "ready" / f"{stem}.final.questions.json").exists()


def test_merge_fails_ready_incomplete_options(tmp_path: Path) -> None:
    stem = "sample_pdf"
    folder = tmp_path / stem
    questions = [_sample_question(1, "ready"), _sample_question(2, "review", options=2)]
    review = [_sample_question(2, "ready", options=2)]
    questions_path, review_path = _write_pair(folder, stem, questions, review)

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )

    assert not result.validation_passed
    assert not result.copied_to_ready_dir
    assert result.reason_if_not_copied == REASON_VALIDATION_FAILED


def test_merge_fails_ready_visual_missing_syntax(tmp_path: Path) -> None:
    stem = "sample_pdf"
    folder = tmp_path / stem
    questions = [_sample_question(1, "ready"), _sample_question(2, "review")]
    review = [_sample_question(2, "ready", visual=True)]
    questions_path, review_path = _write_pair(folder, stem, questions, review)

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )

    assert not result.validation_passed
    assert not result.copied_to_ready_dir


def test_merge_fails_correct_answer_label_not_in_options(tmp_path: Path) -> None:
    stem = "sample_pdf"
    folder = tmp_path / stem
    questions = [_sample_question(1, "ready"), _sample_question(2, "review")]
    review = [_sample_question(2, "ready", correct_label="Z")]
    questions_path, review_path = _write_pair(folder, stem, questions, review)

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )

    assert not result.validation_passed
    assert any("correctAnswer_label_not_in_options" in err for err in result.errors)


def test_merge_fails_correct_answer_text_mismatch(tmp_path: Path) -> None:
    stem = "sample_pdf"
    folder = tmp_path / stem
    questions = [_sample_question(1, "ready"), _sample_question(2, "review")]
    bad = _sample_question(2, "ready", correct_label="A", correct_text="wrong text")
    questions_path, review_path = _write_pair(folder, stem, questions, [bad])

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )

    assert not result.validation_passed
    assert any("correctAnswer_text_mismatch" in err for err in result.errors)


def test_merge_fails_legacy_public_fields(tmp_path: Path) -> None:
    stem = "sample_pdf"
    folder = tmp_path / stem
    questions = [_sample_question(1, "ready"), _sample_question(2, "review")]
    review = [_sample_question(2, "ready", legacy_field=True)]
    questions_path, review_path = _write_pair(folder, stem, questions, review)

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )

    assert not result.validation_passed
    assert any("legacy_field" in err for err in result.errors)


def test_batch_merge_continues_and_reports_failures(tmp_path: Path) -> None:
    batch = tmp_path / "batch_outputs"
    ready = tmp_path / "ready"

    good = batch / "good_pdf"
    _write_pair(
        good,
        "good_pdf",
        [_sample_question(1, "ready"), _sample_question(2, "ready")],
        [_sample_question(2, "ready")],
    )

    bad = batch / "bad_pdf"
    _write_pair(
        bad,
        "bad_pdf",
        [_sample_question(1, "ready"), _sample_question(2, "review")],
        [],
    )

    results = merge_reviewed_questions_folder(batch, ready_dir=ready, expected_count=2)
    assert len(results) == 2
    assert sum(1 for r in results if r.passed) == 1
    assert sum(1 for r in results if not r.passed) == 1


def test_audit_correct_answer_checks() -> None:
    payload = {
        "fileMeta": {"sourceName": "t.pdf"},
        "questions": [
            _sample_question(1, "ready", correct_label="Z"),
        ],
    }
    audit = audit_public_questions_json(payload, expected_count=1)
    assert not audit.passed
