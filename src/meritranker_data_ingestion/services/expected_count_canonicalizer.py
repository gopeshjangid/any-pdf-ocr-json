"""Canonicalize final export to expected_count public question slots (Part 14R)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionItemMetadata,
    FinalQuestionQualityStatus,
)
from meritranker_data_ingestion.services.answer_key_zone_detector import (
    is_answer_key_contaminated_text,
)
from meritranker_data_ingestion.services.deterministic_option_parser import count_usable_options
from meritranker_data_ingestion.services.final_readiness_resolver import CORRUPTION_MARKERS

EXTRA_CANDIDATES_JSON_NAME = "extra-question-candidates.json"
DUPLICATE_CANDIDATES_JSON_NAME = "duplicate-question-candidates.json"
OVER_DETECTION_RATIO = 1.5


@dataclass(frozen=True)
class CanonicalizationReport:
    expected_count: int
    raw_candidate_count: int
    public_question_count: int
    extra_candidate_count: int
    duplicate_candidate_count: int
    missing_placeholder_count: int
    missing_question_numbers: list[int]
    duplicate_question_numbers: list[int]
    over_detection: bool


def canonicalize_for_expected_count(
    items: list[FinalQuestionItem],
    *,
    expected_count: int,
    source_file_name: str = "unknown.pdf",
) -> tuple[list[FinalQuestionItem], CanonicalizationReport]:
    """Reduce raw candidates to exactly expected_count canonical public slots."""
    if expected_count <= 0:
        return items, _report_without_expected(items, expected_count)

    by_number: dict[int, list[FinalQuestionItem]] = {}
    extra_candidates: list[FinalQuestionItem] = []

    for item in items:
        qnum = item.question_number
        if qnum is None or qnum < 1 or qnum > expected_count:
            extra_candidates.append(item)
            continue
        by_number.setdefault(qnum, []).append(item)

    duplicate_candidates: list[FinalQuestionItem] = []
    duplicate_numbers: list[int] = []
    public_items: list[FinalQuestionItem] = []
    missing_numbers: list[int] = []
    placeholders = 0

    over_detection = len(items) > int(expected_count * OVER_DETECTION_RATIO)

    for qnum in range(1, expected_count + 1):
        candidates = by_number.get(qnum, [])
        if not candidates:
            placeholder = _missing_placeholder(
                qnum,
                source_file_name=source_file_name,
                over_detection=over_detection,
            )
            public_items.append(placeholder)
            missing_numbers.append(qnum)
            placeholders += 1
            continue

        if len(candidates) > 1:
            duplicate_numbers.append(qnum)

        ranked = sorted(candidates, key=_candidate_score, reverse=True)
        best = ranked[0].model_copy(
            update={
                "question_number": qnum,
                "global_order": qnum,
                "source_question_number_raw": str(qnum),
            },
        )
        public_items.append(best)
        duplicate_candidates.extend(ranked[1:])

    report = CanonicalizationReport(
        expected_count=expected_count,
        raw_candidate_count=len(items),
        public_question_count=len(public_items),
        extra_candidate_count=len(extra_candidates),
        duplicate_candidate_count=len(duplicate_candidates),
        missing_placeholder_count=placeholders,
        missing_question_numbers=missing_numbers,
        duplicate_question_numbers=sorted(duplicate_numbers),
        over_detection=over_detection,
    )
    return public_items, report


def write_canonicalization_diagnostics(
    output_dir: Path,
    *,
    extra_candidates: list[FinalQuestionItem],
    duplicate_candidates: list[FinalQuestionItem],
    report: CanonicalizationReport,
) -> None:
    """Persist non-public candidate diagnostics under final-questions/."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_candidate_file(
        output_dir / EXTRA_CANDIDATES_JSON_NAME,
        extra_candidates,
        report,
        kind="extra",
    )
    _write_candidate_file(
        output_dir / DUPLICATE_CANDIDATES_JSON_NAME,
        duplicate_candidates,
        report,
        kind="duplicate",
    )


def split_canonicalization_candidates(
    items: list[FinalQuestionItem],
    *,
    expected_count: int,
) -> tuple[list[FinalQuestionItem], list[FinalQuestionItem], list[FinalQuestionItem]]:
    """Return (public_slots_preview, extra, duplicate_losers) without building placeholders."""
    by_number: dict[int, list[FinalQuestionItem]] = {}
    extra: list[FinalQuestionItem] = []
    for item in items:
        qnum = item.question_number
        if qnum is None or qnum < 1 or qnum > expected_count:
            extra.append(item)
            continue
        by_number.setdefault(qnum, []).append(item)

    duplicate: list[FinalQuestionItem] = []
    public: list[FinalQuestionItem] = []
    for qnum in range(1, expected_count + 1):
        candidates = by_number.get(qnum, [])
        if not candidates:
            continue
        ranked = sorted(candidates, key=_candidate_score, reverse=True)
        public.append(ranked[0])
        duplicate.extend(ranked[1:])
    return public, extra, duplicate


def _candidate_score(item: FinalQuestionItem) -> tuple[int, int, int, int, int]:
    text = (item.question_text_raw or "").strip()
    if is_answer_key_contaminated_text(text):
        return (-3, -1, -1, -1, -999)
    text_clean = 1 if text and not _has_corruption(item.issues) else 0
    usable = count_usable_options(item.options)
    has_four = 1 if usable >= 4 else 0
    has_answer = 1 if item.correct_answer_key and item.correct_answer_text else 0
    has_solution = 1 if item.solution_text_raw and item.solution_text_raw.strip() else 0
    issue_penalty = -len(item.metadata.review_issues or item.issues)
    return (text_clean, has_four, has_answer, has_solution, issue_penalty)


def _has_corruption(issues: list[str]) -> bool:
    return any(any(marker in issue for marker in CORRUPTION_MARKERS) for issue in issues)


def _missing_placeholder(
    qnum: int,
    *,
    source_file_name: str,
    over_detection: bool = False,
) -> FinalQuestionItem:
    review_issues = ["question_missing_from_extraction"]
    if over_detection:
        review_issues.append("unsupported_layout_detected")
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
            review_issues=review_issues,
        ),
    )


def _write_candidate_file(
    path: Path,
    candidates: list[FinalQuestionItem],
    report: CanonicalizationReport,
    *,
    kind: str,
) -> None:
    payload = {
        "kind": kind,
        "count": len(candidates),
        "expected_count": report.expected_count,
        "raw_candidate_count": report.raw_candidate_count,
        "candidates": [
            {
                "final_question_id": item.final_question_id,
                "question_number": item.question_number,
                "global_order": item.global_order,
                "question_text_preview": (item.question_text_raw or "")[:120],
                "option_count": count_usable_options(item.options),
                "status": item.metadata.status,
                "review_issues": list(item.metadata.review_issues),
            }
            for item in candidates
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _report_without_expected(
    items: list[FinalQuestionItem],
    expected_count: int,
) -> CanonicalizationReport:
    return CanonicalizationReport(
        expected_count=expected_count,
        raw_candidate_count=len(items),
        public_question_count=len(items),
        extra_candidate_count=0,
        duplicate_candidate_count=0,
        missing_placeholder_count=0,
        missing_question_numbers=[],
        duplicate_question_numbers=[],
        over_detection=False,
    )
