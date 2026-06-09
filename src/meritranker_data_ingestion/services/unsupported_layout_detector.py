"""Detect unsupported repeated-numbering / response-sheet layouts (Part 14C)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage
from meritranker_data_ingestion.services.ocr_role_hints import (
    has_chosen_option_metadata,
    is_chosen_option_instruction_line,
    is_chosen_option_metadata_line,
)

RE_Q_ANCHOR = re.compile(
    r"(?:^|\||\s)(?:Q\.?\s*)(\d{1,3})(?:\s*[\.\)]|\s+)",
    re.IGNORECASE,
)
RE_RESPONSE_SHEET = re.compile(
    r"(?:question\s*id|chosen\s*option|status\s*:|\bans\s*\d)",
    re.IGNORECASE,
)
RE_NOISE = re.compile(
    r"(?:free\s+mock|download\s+pdf|www\.|subscribe\s+now)",
    re.IGNORECASE,
)

MIN_REPEAT_DISTANCE_LINES = 15


@dataclass(frozen=True)
class UnsupportedLayoutResult:
    unsupported_layout_detected: bool
    unsupported_reason: str | None
    recommended_pipeline_action: str | None
    recommended_next_pipeline: str | None
    repeated_question_numbers: list[int]
    response_sheet_markers_detected: bool
    chosen_option_detected: bool


def detect_unsupported_layout(
    evidence: DocumentEvidencePackage,
    *,
    answer_source_mode: str = "unavailable",
) -> UnsupportedLayoutResult:
    """Detect response-sheet / repeated numbering layouts unsafe for global binding."""
    anchors: list[tuple[int, int, str]] = []
    response_sheet_hits = 0
    chosen_detected = False

    for idx, line in enumerate(evidence.lines):
        text = line.text_raw
        lowered = text.lower()
        if RE_RESPONSE_SHEET.search(text) and not is_chosen_option_instruction_line(text):
            response_sheet_hits += 1
        if is_chosen_option_metadata_line(text):
            chosen_detected = True
        if "question id" in lowered:
            response_sheet_hits += 1
        match = RE_Q_ANCHOR.search(text)
        if match:
            try:
                qnum = int(match.group(1))
                anchors.append((idx, qnum, line.line_id))
            except ValueError:
                continue

    by_number: dict[int, list[int]] = {}
    for idx, qnum, _ in anchors:
        by_number.setdefault(qnum, []).append(idx)

    repeated: list[int] = []
    for qnum, indices in sorted(by_number.items()):
        if len(indices) < 2:
            continue
        for i in range(1, len(indices)):
            if indices[i] - indices[i - 1] >= MIN_REPEAT_DISTANCE_LINES:
                repeated.append(qnum)
                break

    repeated = sorted(set(repeated))
    response_sheet = response_sheet_hits >= 3 or (
        chosen_detected and response_sheet_hits >= 1
    )
    # Repeated numbering alone (e.g. bilingual PYQ sections) is handled by canonicalization.
    has_metadata = has_chosen_option_metadata(evidence.lines)
    unsupported = has_metadata or (
        response_sheet
        and answer_source_mode in {"chosen_option_metadata_only", "unavailable"}
        and has_metadata
    )

    if unsupported:
        return UnsupportedLayoutResult(
            unsupported_layout_detected=True,
            unsupported_reason="repeated_numbering_or_response_sheet_layout",
            recommended_pipeline_action="stop_unsupported_layout",
            recommended_next_pipeline="response_sheet_extraction_pipeline",
            repeated_question_numbers=repeated,
            response_sheet_markers_detected=response_sheet,
            chosen_option_detected=chosen_detected,
        )

    return UnsupportedLayoutResult(
        unsupported_layout_detected=False,
        unsupported_reason=None,
        recommended_pipeline_action=None,
        recommended_next_pipeline=None,
        repeated_question_numbers=repeated,
        response_sheet_markers_detected=response_sheet,
        chosen_option_detected=chosen_detected,
    )
