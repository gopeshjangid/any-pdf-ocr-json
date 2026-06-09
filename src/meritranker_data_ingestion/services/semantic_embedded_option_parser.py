"""Parse explicit A–E option labels embedded in evidence lines (Part 13G)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine
from meritranker_data_ingestion.services.answer_key_zone_detector import (
    is_answer_key_line,
    parse_mathsf_option_line,
    parse_pipe_table_option_line,
)
from meritranker_data_ingestion.services.semantic_key_normalizer import (
    HOMOGLYPH_TO_LATIN,
    normalize_option_key,
)

RE_OPT_BULLET_BOLD = re.compile(r"^[-*+]\s+\*\*([A-Da-d])\*\*\s+(.+)$")
RE_OPT_BOLD = re.compile(r"^\*\*?([A-Da-d])\*\*?\s+(.+)$")
RE_OPT_DASH_KEY = re.compile(r"^[-*+]\s+([A-Da-d])\s+(.+)$")
RE_OPT_DASH_PAREN = re.compile(r"^[-*+]\s+\(\*?([A-Da-d])\*?\)\s*(.+)$")
RE_OPT_PAREN = re.compile(r"^\s*\(\*?([A-Da-d])\*?\)\s*(.+)$")
RE_OPT_DOT = re.compile(r"^([A-Da-d])\.\s+(.+)$")
RE_OPT_PLAIN = re.compile(r"^([A-Da-d])\s+(.+)$")
RE_TICK_CROSS_OPTION = re.compile(
    r"^[✓✔√×xX★🔀\s]*(?:<[^>]+>)*\s*([A-Da-d])\.\s*(.+)$",
    re.IGNORECASE,
)
RE_HTML_TAG = re.compile(r"</?b>", re.IGNORECASE)
RE_TABLE_OPTION = re.compile(r"^\|?\s*\|?\s*([^\s|])\s*\|\s*(.+?)\s*\|?\s*$")
RE_MULTI_BOLD = re.compile(r"\*\*([A-Da-d])\*\*\s*([^*]+?)(?=\s*\*\*[A-Da-d]\*\*|$)")
RE_MULTI_BULLET_BOLD = re.compile(
    r"[-*+]\s+\*\*([A-Da-d])\*\*\s*([^-*]+?)(?=(?:\s*[-*+]\s+\*\*[A-Da-d]\*\*)|$)",
)
RE_MULTI_PAREN = re.compile(r"\(([A-Da-d])\)\s*([^()]+?)(?=\s*\([A-Da-d]\)|$)")
RE_MULTI_STAR_PAREN = re.compile(
    r"\(\*?([A-Da-d])\*?\)\s*([^(*]+?)(?=\s*\(\*?[A-Da-d]\*?\)|$)",
    re.IGNORECASE,
)
RE_MULTI_DOT = re.compile(r"(?:^|\s)([A-Da-d])\.\s+([^A-D.]+?)(?=(?:\s+[A-D]\.)|$)")


def extract_options_from_line(
    text: str,
    line: EvidenceLine,
) -> list[tuple[str, str, str, EvidenceLine]]:
    """Return (canonical, raw_key, text, line) tuples with explicit labels only."""
    stripped = text.strip()
    if not stripped:
        return []
    if is_answer_key_line(stripped):
        return []

    pipe_opt = parse_pipe_table_option_line(stripped)
    if pipe_opt is not None:
        canonical, raw_key, opt_text = pipe_opt
        norm, preserved = normalize_option_key(canonical)
        if norm:
            return [(norm, preserved or raw_key, opt_text, line)]

    mathsf_opt = parse_mathsf_option_line(stripped)
    if mathsf_opt is not None:
        canonical, raw_key, opt_text = mathsf_opt
        norm, preserved = normalize_option_key(canonical)
        if norm:
            return [(norm, preserved or raw_key, opt_text, line)]

    if stripped.count("|") >= 3:
        pipe_row = _parse_pipe_row_multi(stripped)
        if len(pipe_row) >= 2:
            return [
                (canonical, raw_key, opt_text, line)
                for canonical, raw_key, opt_text in pipe_row
            ]

    if _count_explicit_labels(stripped) > 1:
        embedded = _parse_embedded_options(stripped)
        if embedded:
            return [
                (canonical, raw_key, opt_text, line)
                for canonical, raw_key, opt_text in embedded
            ]

    single = _parse_single_option_line(stripped)
    if single is not None:
        canonical, raw_key, opt_text = single
        return [(canonical, raw_key, opt_text, line)]

    pipe_row = _parse_pipe_row_multi(stripped)
    if pipe_row:
        return [(canonical, raw_key, opt_text, line) for canonical, raw_key, opt_text in pipe_row]

    embedded = _parse_embedded_options(stripped)
    if embedded:
        return [(canonical, raw_key, opt_text, line) for canonical, raw_key, opt_text in embedded]

    return []


def _count_explicit_labels(text: str) -> int:
    count = len(re.findall(r"\*\*[A-Da-d]\*\*", text))
    count += len(re.findall(r"\([A-Da-d]\)", text))
    count += len(re.findall(r"\(\*?[A-Da-d]\*?\)", text, re.IGNORECASE))
    count += len(re.findall(r"(?:^|\s)[A-Da-d]\.", text))
    return count


def _normalize_tick_option_text(text: str) -> str:
    cleaned = RE_HTML_TAG.sub("", text).strip()
    cleaned = cleaned.lstrip("|").rstrip("|").strip()
    return cleaned


def _parse_single_option_line(text: str) -> tuple[str, str, str] | None:
    normalized = _normalize_tick_option_text(text)
    tick_match = RE_TICK_CROSS_OPTION.match(normalized)
    if tick_match:
        raw_key = tick_match.group(1)
        canonical, preserved = normalize_option_key(raw_key)
        if canonical:
            return canonical, preserved or raw_key, tick_match.group(2).strip()

    for pattern in (
        RE_OPT_BULLET_BOLD,
        RE_OPT_BOLD,
        RE_OPT_DASH_PAREN,
        RE_OPT_DASH_KEY,
        RE_OPT_PAREN,
        RE_OPT_DOT,
        RE_OPT_PLAIN,
    ):
        match = pattern.match(normalized)
        if match:
            raw_key = match.group(1)
            canonical, preserved = normalize_option_key(raw_key)
            if canonical:
                return canonical, preserved or raw_key, match.group(2).strip()

    table = RE_TABLE_OPTION.match(text)
    if table:
        raw_key = table.group(1).strip()
        mapped = HOMOGLYPH_TO_LATIN.get(raw_key, raw_key.upper())
        canonical, preserved = normalize_option_key(mapped)
        if canonical:
            return canonical, preserved or raw_key, table.group(2).strip()
    return None


def _parse_embedded_options(text: str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for pattern in (RE_MULTI_STAR_PAREN, RE_MULTI_BULLET_BOLD, RE_MULTI_BOLD, RE_MULTI_PAREN, RE_MULTI_DOT):
        for match in pattern.finditer(text):
            raw_key = match.group(1)
            canonical, preserved = normalize_option_key(raw_key)
            if not canonical or canonical in seen:
                continue
            opt_text = match.group(2).strip()
            if not opt_text:
                continue
            seen.add(canonical)
            found.append((canonical, preserved or raw_key, opt_text))
        if found:
            return found[:4]
    return found[:4]


def _parse_pipe_row_multi(text: str) -> list[tuple[str, str, str]]:
    if "|" not in text:
        return []
    cells = [cell.strip() for cell in text.split("|") if cell.strip()]
    if len(cells) < 2:
        return []

    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    idx = 0
    while idx < len(cells) - 1:
        raw_key = cells[idx]
        canonical, preserved = normalize_option_key(
            HOMOGLYPH_TO_LATIN.get(raw_key, raw_key),
        )
        if canonical and canonical not in seen:
            seen.add(canonical)
            found.append((canonical, preserved or raw_key, cells[idx + 1]))
            idx += 2
            continue
        idx += 1
    return found[:4]
