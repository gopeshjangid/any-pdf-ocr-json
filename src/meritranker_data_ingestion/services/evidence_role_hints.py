"""Deterministic role hints for normalized evidence lines (no semantic binding)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.document_evidence import MetadataCandidate, RoleHint, SourceSpan
from meritranker_data_ingestion.services.line_text_classifier import (
    RE_FOOTER_NOISE,
    RE_IMAGE_REF,
    RE_METADATA,
    RE_OPTION_CLOSE_PAREN,
    RE_OPTION_DOT,
    RE_OPTION_PAREN,
    RE_SOLUTION_HEADING,
    RE_TABLE_ROW,
    _match_question_anchor,
    classification_target,
    normalize_preview,
)

RE_BOLD_OPTION = re.compile(r"^[-*+]\s+\*\*([A-Da-d])\*\*\s+(.+)$")
RE_ANSWER_KEY_NUMERIC = re.compile(
    r"^(\d+)\s*[\.\)]\s*([A-Da-d])\s*\.?\s*$",
    re.IGNORECASE,
)
RE_ANSWER_KEY_INLINE = re.compile(
    r"^(\d+)\s*[\.\)]\s+([A-Da-d])\b",
    re.IGNORECASE,
)
RE_ANS_MARKER = re.compile(
    r"\bAns(?:wer)?\s*[\.\:]?\s*\(?\s*([A-Da-d])\s*\)?",
    re.IGNORECASE,
)
RE_SECTION_HEADING = re.compile(r"^#{1,6}\s+.+")
RE_INSTRUCTION = re.compile(
    r"^(?:Directions?|Instructions?|Read the following|Note\s*:)\b",
    re.IGNORECASE,
)
RE_ADVERTISEMENT = re.compile(
    r"(?:Free\s+Mock\s+Test|Previous\s+Papers?|Subscribe\s+Now|Buy\s+Now|"
    r"Download\s+PDF|Get\s+Free|Join\s+Telegram|Visit\s+our\s+website)",
    re.IGNORECASE,
)
RE_DOWNLOAD_LINK = re.compile(r"(?:www\.|http://|https://|\.pdf\b)", re.IGNORECASE)
RE_METADATA_EXAM = re.compile(
    r"(?:SSC\s*CGL|CGL|Tier\s*\d+|Shift\s*\d+|Exam\s*Date|"
    r"General\s+Awareness|Quantitative|Reasoning|English)",
    re.IGNORECASE,
)
RE_METADATA_KV = re.compile(
    r"^(?:Exam|Subject|Paper|Year|Set|Code|Shift|Tier|Section)\s*[:\-]\s*(.+)$",
    re.IGNORECASE,
)


def detect_role_hints(text_raw: str) -> list[RoleHint]:
    """Return deterministic role hints for a line (hints only, not bindings)."""
    hints: list[RoleHint] = []
    stripped = text_raw.strip()
    if not stripped:
        return hints

    target = classification_target(text_raw)

    if RE_IMAGE_REF.search(stripped) and stripped.startswith("!["):
        hints.append(RoleHint.IMAGE_REFERENCE_CANDIDATE)

    if RE_TABLE_ROW.match(stripped):
        hints.append(RoleHint.TABLE_CANDIDATE)

    if _match_question_anchor(target) or _match_question_anchor(stripped):
        hints.append(RoleHint.QUESTION_ANCHOR_CANDIDATE)

    if (
        RE_OPTION_PAREN.match(target)
        or RE_OPTION_CLOSE_PAREN.match(target)
        or RE_OPTION_DOT.match(target)
        or RE_BOLD_OPTION.match(stripped)
    ):
        hints.append(RoleHint.OPTION_LABEL_CANDIDATE)

    if (
        RE_ANSWER_KEY_NUMERIC.match(target)
        or RE_ANSWER_KEY_INLINE.match(target)
        or RE_ANS_MARKER.search(target)
    ):
        hints.append(RoleHint.ANSWER_KEY_CANDIDATE)

    if RE_SOLUTION_HEADING.match(target):
        hints.append(RoleHint.SOLUTION_HEADING_CANDIDATE)

    if RE_SECTION_HEADING.match(stripped):
        hints.append(RoleHint.SECTION_HEADING_CANDIDATE)

    if RE_INSTRUCTION.match(target):
        hints.append(RoleHint.INSTRUCTION_CANDIDATE)

    if RE_ADVERTISEMENT.search(stripped):
        hints.append(RoleHint.ADVERTISEMENT_CANDIDATE)

    if RE_DOWNLOAD_LINK.search(stripped):
        hints.append(RoleHint.DOWNLOAD_LINK_CANDIDATE)

    if RE_FOOTER_NOISE.search(stripped):
        hints.append(RoleHint.FOOTER_CANDIDATE)

    if RE_METADATA.match(target) or RE_METADATA_EXAM.search(stripped):
        hints.append(RoleHint.METADATA_CANDIDATE)

    return _dedupe_hints(hints)


def extract_metadata_candidates(
    line_id: str,
    text_raw: str,
    *,
    extractor: str,
    source_artifact_path: str | None,
    page_number: int | None,
    source_line_number: int | None,
    max_early_lines: int = 80,
    line_index: int,
) -> MetadataCandidate | None:
    """Extract a weak metadata candidate from early document lines."""
    if line_index >= max_early_lines:
        return None

    hints = detect_role_hints(text_raw)
    if RoleHint.METADATA_CANDIDATE not in hints:
        return None

    key_hint = "exam_metadata"
    value_raw = text_raw.strip()

    kv_match = RE_METADATA_KV.match(classification_target(text_raw))
    if kv_match:
        key_hint = "structured_metadata"
        value_raw = kv_match.group(0).strip()
    elif RE_METADATA_EXAM.search(text_raw):
        for token in ("SSC CGL", "Tier", "Shift", "Exam"):
            if token.lower() in text_raw.lower():
                key_hint = token.lower().replace(" ", "_")
                break

    return MetadataCandidate(
        key_hint=key_hint,
        value_raw=value_raw,
        source_trace=SourceSpan(
            extractor=extractor,
            page_number=page_number,
            line_id=line_id,
            source_artifact_path=source_artifact_path,
            raw_index=source_line_number,
        ),
        confidence=0.75 if kv_match else 0.65,
    )


def is_noise_line(hints: list[RoleHint]) -> bool:
    """Whether line is a noise candidate based on role hints."""
    noise_hints = {
        RoleHint.ADVERTISEMENT_CANDIDATE,
        RoleHint.DOWNLOAD_LINK_CANDIDATE,
        RoleHint.FOOTER_CANDIDATE,
    }
    return bool(noise_hints.intersection(hints))


def preview_text(text_raw: str) -> str:
    """Normalized preview for evidence lines."""
    return normalize_preview(text_raw)


def _dedupe_hints(hints: list[RoleHint]) -> list[RoleHint]:
    seen: set[RoleHint] = set()
    ordered: list[RoleHint] = []
    for hint in hints:
        if hint not in seen:
            seen.add(hint)
            ordered.append(hint)
    return ordered
