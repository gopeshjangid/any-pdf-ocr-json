"""OCR-specific role hint detection (Part 14A)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.ocr_evidence import OcrRoleHint
from meritranker_data_ingestion.services.evidence_role_hints import detect_role_hints
from meritranker_data_ingestion.services.line_text_classifier import (
    RE_OPTION_DOT,
    _match_question_anchor,
    classification_target,
)

RE_CHOSEN_OPTION = re.compile(
    r"Chosen\s+Option\s*:\s*([A-Da-d1-4\-]+)?",
    re.IGNORECASE,
)
RE_CHOSEN_OPTION_INSTRUCTION = re.compile(
    r"chosen\s+option\s+(?:on\s+the\s+(?:right|left)|indicates|means|shows|represents)",
    re.IGNORECASE,
)
RE_ADDA_INSTRUCTION = re.compile(
    r"(?:options?\s+shown\s+in\s+green|tick\s+icon\s+are\s+correct|correct\s+answer\s+will\s+carry)",
    re.IGNORECASE,
)
RE_STATUS = re.compile(r"Status\s*:\s*(Answered|Not\s+Answered)", re.IGNORECASE)
RE_NUMERIC_OPTION = re.compile(r"^(\d+)\s*[\.\)]\s+(.+)$")
RE_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def detect_ocr_role_hints(text: str) -> list[OcrRoleHint]:
    hints: list[OcrRoleHint] = []
    stripped = text.strip()
    if not stripped:
        return hints

    if RE_CHOSEN_OPTION.search(stripped):
        hints.append(OcrRoleHint.CHOSEN_OPTION_CANDIDATE)
    if RE_STATUS.search(stripped):
        hints.append(OcrRoleHint.STATUS_CANDIDATE)

    target = classification_target(stripped)
    if _match_question_anchor(target) or _match_question_anchor(stripped):
        hints.append(OcrRoleHint.QUESTION_ANCHOR_CANDIDATE)
    if RE_OPTION_DOT.match(target) or RE_NUMERIC_OPTION.match(stripped):
        hints.append(OcrRoleHint.OPTION_LABEL_CANDIDATE)

    marker_hints = detect_role_hints(stripped)
    if any(h.value == "answer_key_candidate" for h in marker_hints):
        hints.append(OcrRoleHint.ANSWER_KEY_CANDIDATE)
    if any(h.value == "solution_heading_candidate" for h in marker_hints):
        hints.append(OcrRoleHint.SOLUTION_CANDIDATE)

    if "tick" in stripped.lower() or "✓" in stripped:
        hints.append(OcrRoleHint.VISUAL_TICK_CANDIDATE)

    return _dedupe(hints)


def detect_language_profile(text_samples: list[str]) -> str:
    has_devanagari = any(RE_DEVANAGARI.search(t) for t in text_samples if t)
    has_latin = any(re.search(r"[A-Za-z]", t) for t in text_samples if t)
    if has_devanagari and has_latin:
        return "mixed"
    if has_devanagari:
        return "hindi"
    if has_latin:
        return "english"
    return "unknown"


def is_chosen_option_instruction_line(text: str) -> bool:
    """True for Adda/screenshot direction lines, not per-question response-sheet metadata."""
    stripped = text.strip()
    if not stripped:
        return False
    if RE_CHOSEN_OPTION_INSTRUCTION.search(stripped):
        return True
    if RE_ADDA_INSTRUCTION.search(stripped):
        return True
    lowered = stripped.lower()
    if "chosen option" in lowered and ":" not in stripped:
        if any(
            phrase in lowered
            for phrase in (
                "on the right",
                "on the left",
                "indicates the option",
                "selected by the candidate",
            )
        ):
            return True
    return False


def is_chosen_option_metadata_line(text: str) -> bool:
    """True for per-question Chosen Option: A / 2 rows (response-sheet metadata)."""
    stripped = text.strip()
    if not stripped or is_chosen_option_instruction_line(stripped):
        return False
    if RE_CHOSEN_OPTION.search(stripped):
        return True
    lowered = stripped.lower()
    return "chosen option" in lowered and ":" in stripped


def has_chosen_option_metadata(lines: list) -> bool:
    """Return True when document has response-sheet chosen-option rows, not just instructions."""
    return any(is_chosen_option_metadata_line(getattr(ln, "text_raw", str(ln))) for ln in lines)


def parse_chosen_option_key(text: str) -> str | None:
    """Return raw chosen option key (numeric `1` or letter `A`)."""
    match = RE_CHOSEN_OPTION.search(text)
    if not match:
        return None
    raw = (match.group(1) or "").strip()
    if not raw or raw in {"--", "-", "NA", "N/A"}:
        return None
    if raw.isdigit():
        return raw
    return raw.upper() if len(raw) == 1 else None


def parse_chosen_option_canonical_key(text: str) -> str | None:
    raw = parse_chosen_option_key(text)
    if not raw:
        return None
    if raw.isdigit():
        mapping = {"1": "A", "2": "B", "3": "C", "4": "D"}
        return mapping.get(raw)
    return raw if len(raw) == 1 else None


def _dedupe(items: list[OcrRoleHint]) -> list[OcrRoleHint]:
    seen: set[OcrRoleHint] = set()
    out: list[OcrRoleHint] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
