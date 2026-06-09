"""Detect major document sections in merged evidence (question vs solution)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine

RE_SOLUTION_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:\*\*)?\s*"
    r"(solutions?|answers?(?:\s*key)?|answer\s*key|answer\s*sheet|"
    r"explanations?|detailed\s+solutions?|answers?\s+with\s+explanations?)\s*:?\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)
RE_COMPOUND_SOLUTION_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:\*\*)?\s*answers?\s+with\s+explanations?\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)
RE_ANSWER_KEY_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:\*\*)?\s*answer\s*key\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)
RE_FALSE_POSITIVE_HEADING = re.compile(
    r"\b(?:directions?|instructions?|questions?\s+paper|practice\s+questions?|"
    r"quantitative\s+aptitude|general\s+awareness|english\s+language|"
    r"reasoning|syllabus|index|contents?|topic|chapter)\b",
    re.IGNORECASE,
)
RE_SOLUTION_START = re.compile(
    r"^(?:\|?\s*)?(?:\*\*)?\s*(?:Q\.?\s*)?(\d{1,3})\s*[\.\)]\s*"
    r"(?:\*\*)?\s*"
    r"(?:Ans(?:wer)?[\.\:]?\s*)?"
    r"(?:\(\s*\*?([A-Ea-e])\s*\*?\s*\)|"
    r"([A-Ea-e])\s*[\)\.:]|"
    r"Answer\s*:\s*([A-Ea-e])\b)",
    re.IGNORECASE,
)
RE_NOISE_SECTION = re.compile(
    r"(?:free\s+mock|download\s+pdf|subscribe\s+now|previous\s+papers?)",
    re.IGNORECASE,
)


class DocumentSectionType(str, Enum):
    QUESTION = "question_section"
    SOLUTION = "solution_section"
    ANSWER_KEY = "answer_key_section"
    EXPLANATION = "explanation_section"
    NOISE = "advertisement/footer/noise"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DocumentSectionBoundary:
    section_type: DocumentSectionType
    start_index: int
    end_index: int
    heading_line_id: str | None
    heading_text: str | None


@dataclass(frozen=True)
class DocumentSectionSplit:
    lines: list[EvidenceLine]
    question_lines: list[EvidenceLine]
    solution_lines: list[EvidenceLine]
    boundaries: list[DocumentSectionBoundary]
    solution_section_detected: bool
    solution_heading_index: int | None
    solution_section_confidence: float
    inline_solution_style: bool


def split_document_sections(lines: list[EvidenceLine]) -> DocumentSectionSplit:
    """Split evidence lines into question and solution/explanation sections."""
    solution_idx = _find_solution_heading_index(lines)
    if solution_idx is not None:
        question_lines = lines[:solution_idx]
        solution_lines = lines[solution_idx + 1 :]
        heading = lines[solution_idx]
        section_type = (
            DocumentSectionType.ANSWER_KEY
            if RE_ANSWER_KEY_HEADING.match(heading.text_raw.strip())
            else DocumentSectionType.EXPLANATION
            if "explanation" in heading.text_raw.lower()
            else DocumentSectionType.SOLUTION
        )
        confidence = _heading_confidence(lines, solution_idx)
        boundaries = [
            DocumentSectionBoundary(
                section_type=DocumentSectionType.QUESTION,
                start_index=0,
                end_index=solution_idx,
                heading_line_id=None,
                heading_text=None,
            ),
            DocumentSectionBoundary(
                section_type=section_type,
                start_index=solution_idx,
                end_index=len(lines),
                heading_line_id=heading.line_id,
                heading_text=heading.text_raw.strip(),
            ),
        ]
        return DocumentSectionSplit(
            lines=lines,
            question_lines=question_lines,
            solution_lines=solution_lines,
            boundaries=boundaries,
            solution_section_detected=True,
            solution_heading_index=solution_idx,
            solution_section_confidence=confidence,
            inline_solution_style=False,
        )

    return DocumentSectionSplit(
        lines=lines,
        question_lines=lines,
        solution_lines=[],
        boundaries=[
            DocumentSectionBoundary(
                section_type=DocumentSectionType.QUESTION,
                start_index=0,
                end_index=len(lines),
                heading_line_id=None,
                heading_text=None,
            ),
        ],
        solution_section_detected=False,
        solution_heading_index=None,
        solution_section_confidence=0.0,
        inline_solution_style=True,
    )


def is_solution_style_anchor(text: str) -> bool:
    """True when a numbered line carries an answer label (solution block start)."""
    return RE_SOLUTION_START.match(_strip_solution_anchor_prefix(text)) is not None


def _strip_solution_anchor_prefix(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"^[-•*]\s+", "", stripped)
    stripped = re.sub(r"^\*{1,2}", "", stripped)
    stripped = re.sub(
        r"^(?:Q\.?\s*)?(\d{1,3})\s*[\.\)]\s*\*{0,2}\s*",
        r"\1. ",
        stripped,
        flags=re.IGNORECASE,
    )
    return stripped.strip()


def line_index_for_id(lines: list[EvidenceLine], line_id: str) -> int | None:
    for idx, line in enumerate(lines):
        if line.line_id == line_id:
            return idx
    return None


def _find_solution_heading_index(lines: list[EvidenceLine]) -> int | None:
    candidates: list[tuple[int, float]] = []
    for idx, line in enumerate(lines):
        text = line.text_raw.strip()
        if not text:
            continue
        if RE_NOISE_SECTION.search(text):
            continue
        if RE_FALSE_POSITIVE_HEADING.search(text) and not _is_strong_solution_heading(text):
            continue
        if (
            RE_SOLUTION_HEADING.match(text)
            or RE_ANSWER_KEY_HEADING.match(text)
            or RE_COMPOUND_SOLUTION_HEADING.match(text)
        ):
            confidence = _heading_confidence(lines, idx)
            if confidence >= 0.5:
                candidates.append((idx, confidence))
            continue
        normalized = re.sub(r"[*#_\s]+", "", text).lower()
        if normalized in {
            "explanations",
            "explanation",
            "solutions",
            "solution",
            "answers",
            "answerkey",
            "answerswithexplanations",
            "answerwithexplanations",
            "detailedsolution",
            "detailedsolutions",
        }:
            confidence = _heading_confidence(lines, idx)
            if confidence >= 0.5:
                candidates.append((idx, confidence))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[1], item[0]))[0]


def _is_strong_solution_heading(text: str) -> bool:
    normalized = re.sub(r"[*#_\s]+", "", text).lower()
    return normalized in {
        "explanations",
        "explanation",
        "solutions",
        "solution",
        "answers",
        "answerkey",
        "answerswithexplanations",
        "answerwithexplanations",
        "detailedsolution",
        "detailedsolutions",
        "answerskey",
    }


def _heading_confidence(lines: list[EvidenceLine], heading_idx: int) -> float:
    """Confirm heading by nearby solution-format lines."""
    score = 0.5
    for line in lines[heading_idx + 1 : heading_idx + 8]:
        if not line.text_raw.strip():
            continue
        if is_solution_style_anchor(line.text_raw):
            score = 0.95
            break
    return score
