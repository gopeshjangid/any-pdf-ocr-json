"""Detect answer-key table zones and contaminated lines (Part 14U)."""

from __future__ import annotations

import re

RE_NUMERIC_KEY_PAIR = re.compile(
    r"(?<![\d/])(\d{1,3})\s*\.\s*([A-Da-d])\b",
)
RE_BARE_ANSWER_KEY = re.compile(
    r"^\d{1,3}\s*\.\s*[A-Da-d]\s*\.?$",
    re.IGNORECASE,
)
RE_ANSWER_KEY_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:\*\*)?\s*(?:answer\s*key|answers?\s*key)\s*(?:\*\*)?\s*:?\s*$",
    re.IGNORECASE,
)
RE_PIPE_TABLE_OPTION = re.compile(
    r"^(?:\|+\s*)+([A-Da-d])\s{2,}(.+?)\s*\|?\s*$",
)
RE_MATHSF_OPTION_LABEL = re.compile(
    r"\\mathsf\{([A-Da-d])\}",
    re.IGNORECASE,
)


def count_answer_key_pairs(text: str) -> int:
    """Return compact question-number/answer-label pairs on one line."""
    return len(list(RE_NUMERIC_KEY_PAIR.finditer(text.strip())))


def is_answer_key_line(text: str) -> bool:
    """True when a line is an answer-key row/cell, not question content."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if RE_BARE_ANSWER_KEY.fullmatch(stripped):
        return True
    pairs = count_answer_key_pairs(stripped)
    if pairs >= 2:
        return True
    if pairs == 1 and len(stripped) < 48 and "|" in stripped:
        return True
    if pairs == 1 and len(stripped) < 16:
        return True
    return False


def is_answer_key_anchor_line(text: str) -> bool:
    """True when a numbered anchor is an answer-key artifact, not a question."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if is_answer_key_line(stripped):
        return True
    match = re.match(
        r"^(?:\|+\s*)?(?:[-•*]\s*)?(?:\*\*)?(?:Q\.?\s*)?(\d{1,3})\s*[\.\)]\s*(.*)$",
        stripped,
        re.IGNORECASE,
    )
    if not match:
        return False
    remainder = (match.group(2) or "").strip()
    if not remainder:
        return False
    if re.fullmatch(r"[A-D]\s*(?:\|.*)?", remainder, re.IGNORECASE):
        return True
    if count_answer_key_pairs(stripped) >= 2:
        return True
    if "|" in stripped and count_answer_key_pairs(stripped) >= 1 and len(remainder) < 12:
        return True
    return False


def is_answer_key_contaminated_text(text: str) -> bool:
    """True when question text looks like answer-key table residue."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if is_answer_key_line(stripped):
        return True
    if stripped.startswith("|") and count_answer_key_pairs(stripped) >= 1:
        pairs = count_answer_key_pairs(stripped)
        if pairs >= 1 and len(stripped) < 120 and not re.search(
            r"\b(select|which|what|who|how|find|identify|arrange|evaluate)\b",
            stripped,
            re.IGNORECASE,
        ):
            return True
    return False


def find_answer_key_zone_start(lines: list) -> int | None:
    """Return first line index of a compact answer-key table block."""
    best: int | None = None
    for idx, line in enumerate(lines):
        text = getattr(line, "text_raw", line) if not isinstance(line, str) else line
        if isinstance(line, dict):
            text = line.get("text_raw", "")
        text = (text or "").strip()
        if not text:
            continue
        if RE_ANSWER_KEY_HEADING.match(text):
            return idx
        if count_answer_key_pairs(text) >= 4:
            best = idx if best is None else min(best, idx)
    return best


def parse_pipe_table_option_line(text: str) -> tuple[str, str, str] | None:
    """Parse `| A    option text |` table option rows."""
    stripped = (text or "").strip()
    if not stripped.startswith("|"):
        return None
    match = RE_PIPE_TABLE_OPTION.match(stripped)
    if not match:
        return None
    raw_key = match.group(1)
    opt_text = match.group(2).strip()
    if not opt_text or opt_text.startswith("-"):
        return None
    return raw_key.upper(), raw_key, opt_text


def parse_mathsf_option_line(text: str) -> tuple[str, str, str] | None:
    """Parse `$$\\mathsf{c} ...$$` / `$$\\mathsf{D} ...$$` option lines."""
    stripped = (text or "").strip()
    match = RE_MATHSF_OPTION_LABEL.search(stripped)
    if not match:
        return None
    raw_key = match.group(1)
    return raw_key.upper(), raw_key, stripped
