"""Tests for review item export (Part 8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionAnswer,
    FinalQuestionItem,
    FinalQuestionPackage,
    FinalQuestionSolution,
    FinalQuestionSourceTrace,
    ValidationStatus,
)
from meritranker_data_ingestion.services.review_exporter import (
    build_review_export,
    export_review_items_package,
    render_review_markdown,
)


def _trace() -> FinalQuestionSourceTrace:
    return FinalQuestionSourceTrace(start_line=1, end_line=3, line_numbers=[1, 2, 3])


def _item(
    qid: str,
    qnum: int,
    *,
    status: ValidationStatus,
    issues: list[str] | None = None,
    raw_text: str = "Short preview text.",
) -> FinalQuestionItem:
    from meritranker_data_ingestion.schemas.final_question_package import FinalQuestionOption

    return FinalQuestionItem(
        question_id=qid,
        question_number=qnum,
        question_number_raw=f"Q{qnum}",
        question_text_raw=raw_text,
        raw_text=raw_text,
        options=[
            FinalQuestionOption(
                key="A", key_raw="(a)", text_raw="opt",
                source_trace=_trace(), confidence=0.9,
            ),
        ],
        answer=FinalQuestionAnswer(available=False),
        solution=FinalQuestionSolution(available=False),
        assets=[],
        source_trace=_trace(),
        validation_status=status,
        confidence=0.8,
        issues=issues or [],
    )


def _package(*items: FinalQuestionItem) -> FinalQuestionPackage:
    return FinalQuestionPackage(
        source_file_name="exam.pdf",
        parser_engine="marker",
        total_questions=len(items),
        valid_questions=1,
        items=list(items),
    )


def test_includes_needs_review_item() -> None:
    report = build_review_export(
        _package(
            _item("q_0001", 1, status=ValidationStatus.VALIDATED),
            _item("q_0002", 2, status=ValidationStatus.NEEDS_REVIEW),
        ),
    )
    ids = {item.question_id for item in report.items}
    assert "q_0002" in ids
    assert "q_0001" not in ids


def test_includes_incomplete_item() -> None:
    report = build_review_export(
        _package(_item("q_0003", 3, status=ValidationStatus.INCOMPLETE)),
    )
    assert len(report.items) == 1
    assert report.items[0].review_reason == "incomplete"


def test_excludes_validated_by_default() -> None:
    report = build_review_export(
        _package(_item("q_0004", 4, status=ValidationStatus.VALIDATED)),
    )
    assert report.review_item_count == 0


def test_includes_validated_when_flag_set() -> None:
    report = build_review_export(
        _package(_item("q_0005", 5, status=ValidationStatus.VALIDATED)),
        include_validated=True,
    )
    assert report.review_item_count == 1
    assert report.include_validated is True


def test_markdown_generated() -> None:
    report = build_review_export(
        _package(_item("q_0006", 6, status=ValidationStatus.NEEDS_REVIEW)),
    )
    md = render_review_markdown(report)
    assert "# Review Items Export" in md
    assert "not modified" in md
    assert "needs_review" in md


def test_export_writes_artifacts(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    final_dir = package_dir / "final"
    final_dir.mkdir(parents=True)
    package = _package(
        _item("q_0007", 7, status=ValidationStatus.NEEDS_REVIEW),
        _item("q_0008", 8, status=ValidationStatus.VALIDATED),
    )
    (final_dir / "questions.json").write_text(
        package.model_dump_json(indent=2),
        encoding="utf-8",
    )

    report = export_review_items_package(package_dir)
    assert report.review_item_count == 1

    review_json = package_dir / "review" / "review-items.json"
    review_md = package_dir / "review" / "review-items.md"
    assert review_json.is_file()
    assert review_md.is_file()

    data = json.loads(review_json.read_text(encoding="utf-8"))
    assert data["review_item_count"] == 1
    assert len(data["items"]) == 1

    original = json.loads((final_dir / "questions.json").read_text(encoding="utf-8"))
    assert original["total_questions"] == 2
