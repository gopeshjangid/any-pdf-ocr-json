"""Verify final export completeness and add missing-question placeholders (Part 14M+)."""

from __future__ import annotations

from dataclasses import dataclass

from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionItemMetadata,
    FinalQuestionQualityStatus,
)


@dataclass(frozen=True)
class CompletenessReport:
    expected_count: int | None
    total_questions_detected: int
    missing_question_numbers: list[int]
    duplicate_question_numbers: list[int]
    extra_question_numbers: list[int]
    placeholders_added: int


def verify_and_fill_completeness(
    items: list[FinalQuestionItem],
    *,
    expected_count: int | None,
    source_file_name: str = "unknown.pdf",
) -> tuple[list[FinalQuestionItem], CompletenessReport]:
    """Ensure expected question numbers are represented; never silently omit."""
    if not expected_count or expected_count <= 0:
        return items, _report_without_expected(items, expected_count)

    by_number: dict[int, list[FinalQuestionItem]] = {}
    for item in items:
        if item.question_number is None:
            continue
        by_number.setdefault(item.question_number, []).append(item)

    missing = [n for n in range(1, expected_count + 1) if n not in by_number]
    duplicate = sorted(n for n, group in by_number.items() if len(group) > 1)
    extra = sorted(n for n in by_number if n > expected_count)

    out = list(items)
    placeholders = 0
    for qnum in missing:
        out.append(_missing_placeholder(qnum, source_file_name=source_file_name))
        placeholders += 1

    out = _block_duplicate_and_unnumbered_items(out, expected_count=expected_count)

    out = sorted(
        out,
        key=lambda item: (
            item.question_number is None,
            item.question_number or 99999,
            item.global_order,
        ),
    )
    for idx, item in enumerate(out, start=1):
        out[idx - 1] = item.model_copy(update={"global_order": idx})

    return out, CompletenessReport(
        expected_count=expected_count,
        total_questions_detected=len(out),
        missing_question_numbers=missing,
        duplicate_question_numbers=duplicate,
        extra_question_numbers=extra,
        placeholders_added=placeholders,
    )


def _block_duplicate_and_unnumbered_items(
    items: list[FinalQuestionItem],
    *,
    expected_count: int,
) -> list[FinalQuestionItem]:
    seen_numbers: set[int] = set()
    updated: list[FinalQuestionItem] = []
    for item in items:
        qnum = item.question_number
        if qnum is None or qnum < 1 or qnum > expected_count:
            updated.append(
                item.model_copy(
                    update={
                        "quality_status": FinalQuestionQualityStatus.BLOCKED,
                        "metadata": FinalQuestionItemMetadata(
                            status="blocked",
                            review_issues=["question_missing_from_extraction"],
                        ),
                        "issues": _dedupe_issues(
                            list(item.issues) + ["extra_question_detected"],
                        ),
                    },
                ),
            )
            continue
        if qnum in seen_numbers:
            updated.append(
                item.model_copy(
                    update={
                        "quality_status": FinalQuestionQualityStatus.BLOCKED,
                        "metadata": FinalQuestionItemMetadata(
                            status="blocked",
                            review_issues=["question_missing_from_extraction"],
                        ),
                        "issues": _dedupe_issues(
                            list(item.issues) + ["duplicate_question_number"],
                        ),
                    },
                ),
            )
            continue
        seen_numbers.add(qnum)
        updated.append(item)
    return updated


def _dedupe_issues(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _missing_placeholder(qnum: int, *, source_file_name: str) -> FinalQuestionItem:
    return FinalQuestionItem(
        final_question_id=f"fq_missing_{qnum:04d}",
        global_order=qnum,
        source_question_number_raw=str(qnum),
        question_number=qnum,
        question_text_raw="",
        quality_status=FinalQuestionQualityStatus.BLOCKED,
        final_gate_status="blocked_missing_question",
        confidence=0.0,
        issues=["question_missing_from_extraction"],
        metadata=FinalQuestionItemMetadata(
            status="blocked",
            review_issues=["question_missing_from_extraction"],
        ),
    )


def _report_without_expected(
    items: list[FinalQuestionItem],
    expected_count: int | None,
) -> CompletenessReport:
    by_number: dict[int, int] = {}
    for item in items:
        if item.question_number is None:
            continue
        by_number[item.question_number] = by_number.get(item.question_number, 0) + 1
    return CompletenessReport(
        expected_count=expected_count,
        total_questions_detected=len(items),
        missing_question_numbers=[],
        duplicate_question_numbers=sorted(n for n, c in by_number.items() if c > 1),
        extra_question_numbers=[],
        placeholders_added=0,
    )
