"""Deterministic answer-key candidate extraction from document evidence (Part 13D)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine, RoleHint
from meritranker_data_ingestion.schemas.semantic_binding import AnswerKeyEvidenceCandidate

# Compact answer-key patterns (multiple per line supported)
RE_NUMERIC_KEY = re.compile(
    r"(?<![\d/])(\d{1,3})\s*[\.\)\-]\s*([A-Da-d])\b",
)
RE_Q_KEY = re.compile(
    r"\bQ\s*(\d{1,3})\s+([A-Da-d])\b",
    re.IGNORECASE,
)
RE_ANS_MARKER = re.compile(
    r"\b(?:Ans(?:wer)?|Key)\s*[\.\:]?\s*\(?\s*(\d{1,3})\s*[\.\)\-]?\s*([A-Da-d])\s*\)?",
    re.IGNORECASE,
)
RE_ANSWER_KEY_HEADING = re.compile(
    r"^(?:Answer\s*Key|Answers?\s*(?:Key|Sheet)?)\s*:?\s*$",
    re.IGNORECASE,
)


def extract_answer_key_candidates(
    lines: list[EvidenceLine],
    *,
    max_question_number: int = 300,
) -> list[AnswerKeyEvidenceCandidate]:
    """
    Scan evidence lines for compact answer-key patterns.

    Does not mutate binder output — provides evidence for prompts and enrichment.
    """
    candidates: list[AnswerKeyEvidenceCandidate] = []
    seen: set[tuple[int, str]] = set()
    in_answer_section = False

    for line in lines:
        text = line.text_raw.strip()
        if not text:
            continue

        if RE_ANSWER_KEY_HEADING.match(text) or (
            RoleHint.SOLUTION_HEADING_CANDIDATE in line.role_hints
            and "answer" in text.lower()
        ):
            in_answer_section = True

        is_key_line = (
            in_answer_section
            or RoleHint.ANSWER_KEY_CANDIDATE in line.role_hints
            or _looks_like_answer_key_line(text)
        )
        if not is_key_line:
            continue

        for pattern in (RE_NUMERIC_KEY, RE_Q_KEY, RE_ANS_MARKER):
            for match in pattern.finditer(text):
                qnum = int(match.group(1))
                key = match.group(2).upper()
                if qnum < 1 or qnum > max_question_number:
                    continue
                dedupe_key = (qnum, key)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                issues: list[str] = []
                if len(key) != 1:
                    issues.append("unexpected_key_length")
                candidates.append(
                    AnswerKeyEvidenceCandidate(
                        question_number=qnum,
                        answer_key=key,
                        source_line_id=line.line_id,
                        source_text_raw=text,
                        confidence=0.9 if in_answer_section else 0.75,
                        issues=issues,
                    ),
                )

    return sorted(candidates, key=lambda c: c.question_number)


def _looks_like_answer_key_line(text: str) -> bool:
    """Heuristic: line has multiple compact numeric-key pairs."""
    matches = list(RE_NUMERIC_KEY.finditer(text))
    if len(matches) >= 2:
        return True
    if len(matches) == 1 and len(text) < 40:
        stripped = text.strip()
        return bool(re.fullmatch(r"\d{1,3}\s*[\.\)\-]\s*[A-Da-d]\.?", stripped, re.IGNORECASE))
    return False


def answer_key_candidates_to_prompt_json(
    candidates: list[AnswerKeyEvidenceCandidate],
) -> list[dict]:
    """Compact JSON-serializable list for LLM prompts."""
    return [
        {
            "question_number": c.question_number,
            "answer_key": c.answer_key,
            "source_line_id": c.source_line_id,
            "source_text_raw": c.source_text_raw,
            "confidence": c.confidence,
        }
        for c in candidates
    ]
