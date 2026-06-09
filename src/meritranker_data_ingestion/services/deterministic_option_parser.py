"""Parse MCQ options from local question-window lines only (Part 14L)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine
from meritranker_data_ingestion.services.option_key_normalizer import (
    numeric_to_canonical,
    parse_multiple_numeric_options,
    parse_numeric_option_line,
)
from meritranker_data_ingestion.services.answer_key_zone_detector import is_answer_key_line
from meritranker_data_ingestion.services.semantic_embedded_option_parser import extract_options_from_line
from meritranker_data_ingestion.services.semantic_key_normalizer import normalize_option_key

RE_LIST_PREFIX = re.compile(r"^[-•]\s+")
RE_Q_ANCHOR_STRIP = re.compile(
    r"^(?:[-•*]\s*)?(?:\*\*)?(?:Q\.?\s*)?\d{1,3}\s*[\.\)]\s*(?:\*\*)?\s*",
    re.IGNORECASE,
)
RE_FIRST_OPTION_MARKER = re.compile(
    r"(?:\(\*?[A-Da-d]\*?\)|\([A-Da-d]\)|(?:^|\s)[A-Da-d]\.\s|(?:^|\s)[1-4]\s*[\.\)]\s|"
    r"(?:^|\s)(?:Ans(?:wer)?\s*)?[1-4]\s*[\.\)])",
    re.IGNORECASE,
)
RE_MULTI_LETTER_DOT = re.compile(
    r"(?:^|\s)([A-Da-d])\.\s+([^A-D.]+?)(?=(?:\s+[A-D]\.)|$)",
)
RE_MULTI_ANS_NUMERIC = re.compile(
    r"(?:Ans(?:wer)?\s*)?(\d+)\s*[\.\)]\s*([^0-9]+?)(?=(?:\s*(?:Ans(?:wer)?\s*)?\d+\s*[\.\)])|$)",
    re.IGNORECASE,
)
RE_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
RE_PAREN_LABEL = re.compile(r"\(\*?([A-Da-d])\*?\)")
RE_OPTION_BOLD_LINE = re.compile(r"^\*\*([A-Da-d])\*\*")


@dataclass(frozen=True)
class ParsedOption:
    canonical_key: str
    key_raw: str
    text_raw: str
    source_line: EvidenceLine


@dataclass(frozen=True)
class WindowOptionParseResult:
    options: list[ParsedOption]
    question_text_parts: list[str]


def parse_options_from_window_lines(
    window_lines: list[EvidenceLine],
    *,
    anchor_line_ids: set[str] | None = None,
    option_candidate_line_ids: set[str] | None = None,
) -> WindowOptionParseResult:
    """Parse options and question text from lines scoped to one question window."""
    anchor_ids = anchor_line_ids or set()
    option_ids = option_candidate_line_ids or set()
    options: list[ParsedOption] = []
    seen: set[str] = set()
    question_parts: list[str] = []

    ordered = sorted(
        window_lines,
        key=lambda ln: (0 if ln.line_id in option_ids else 1, window_lines.index(ln)),
    )

    for line in ordered:
        text = line.text_raw.strip()
        if not text or _is_noise(text):
            continue
        if is_answer_key_line(text):
            continue
        if _is_image_only_line(text):
            continue

        is_anchor = line.line_id in anchor_ids
        option_parse_text = _option_parse_text(line.text_raw, is_anchor=is_anchor)
        line_options = (
            _parse_options_from_line_text(option_parse_text, line)
            if option_parse_text is not None
            else []
        )
        if line_options:
            for parsed in line_options:
                if parsed.canonical_key in seen or not parsed.text_raw.strip():
                    continue
                seen.add(parsed.canonical_key)
                options.append(parsed)
            if not is_anchor and _line_is_option_dominated(text):
                continue

        if is_anchor or (line.line_id not in option_ids and not _line_is_option_dominated(text)):
            stem = _question_stem_from_line(text, is_anchor=is_anchor)
            if stem:
                question_parts.append(stem)
        elif line.line_id in option_ids and not line_options:
            stem = _question_stem_from_line(text, is_anchor=False)
            if stem and not _line_is_option_dominated(stem):
                question_parts.append(stem)

    options.sort(key=lambda opt: opt.canonical_key)
    return WindowOptionParseResult(
        options=options[:5],
        question_text_parts=question_parts,
    )


def _option_parse_text(text: str, *, is_anchor: bool) -> str | None:
    normalized = _normalize_line_for_parse(text)
    if not normalized:
        return None
    if is_anchor or _has_question_anchor_markers(normalized):
        remainder = RE_Q_ANCHOR_STRIP.sub("", normalized, count=1).strip()
        if not RE_FIRST_OPTION_MARKER.search(remainder):
            return None
        return remainder
    return normalized


def _has_question_anchor_markers(text: str) -> bool:
    if RE_OPTION_BOLD_LINE.match(text):
        return False
    return bool(
        re.match(r"^[-•*]", text)
        or re.match(r"^\*\*", text)
        or re.match(r"^Q\.?\s*\d", text, re.IGNORECASE)
    )


def _parse_options_from_line_text(text: str, line: EvidenceLine) -> list[ParsedOption]:
    normalized = _normalize_line_for_parse(text)
    if not normalized:
        return []
    if _has_question_anchor_markers(normalized) and not RE_FIRST_OPTION_MARKER.search(
        RE_Q_ANCHOR_STRIP.sub("", normalized, count=1),
    ):
        return []

    found: list[ParsedOption] = []
    seen: set[str] = set()

    for canon, raw_key, opt_text, _src in extract_options_from_line(normalized, line):
        if canon in seen or not opt_text.strip():
            continue
        seen.add(canon)
        found.append(
            ParsedOption(
                canonical_key=canon,
                key_raw=raw_key,
                text_raw=opt_text.strip(),
                source_line=line,
            ),
        )
    if found:
        delimiter_found = _parse_paren_delimited_options(normalized, line)
        if len(delimiter_found) > len(found):
            return delimiter_found
        return found

    delimiter_found = _parse_paren_delimited_options(normalized, line)
    if delimiter_found:
        return delimiter_found

    for pattern in (RE_MULTI_LETTER_DOT, RE_MULTI_ANS_NUMERIC):
        for match in pattern.finditer(normalized):
            raw_key = match.group(1)
            canon = _canonical_from_raw(raw_key)
            if not canon or canon in seen:
                continue
            opt_text = match.group(2).strip()
            if not opt_text:
                continue
            seen.add(canon)
            found.append(
                ParsedOption(
                    canonical_key=canon,
                    key_raw=raw_key,
                    text_raw=opt_text,
                    source_line=line,
                ),
            )
        if found:
            return found

    for norm in parse_multiple_numeric_options(normalized):
        canon = norm.canonical_key or numeric_to_canonical(norm.key)
        if not canon or canon in seen or not norm.text_raw.strip():
            continue
        seen.add(canon)
        found.append(
            ParsedOption(
                canonical_key=canon,
                key_raw=norm.key_raw,
                text_raw=norm.text_raw.strip(),
                source_line=line,
            ),
        )
    if found:
        return found

    single = parse_numeric_option_line(normalized)
    if single:
        canon = single.canonical_key or numeric_to_canonical(single.key)
        if canon and canon not in seen and single.text_raw.strip():
            found.append(
                ParsedOption(
                    canonical_key=canon,
                    key_raw=single.key_raw,
                    text_raw=single.text_raw.strip(),
                    source_line=line,
                ),
            )
    return found


def _question_stem_from_line(text: str, *, is_anchor: bool) -> str:
    stripped = text.strip()
    if not stripped or _is_noise(stripped) or _is_image_only_line(stripped):
        return ""
    if is_anchor:
        stripped = RE_Q_ANCHOR_STRIP.sub("", stripped, count=1).strip()
    marker = RE_FIRST_OPTION_MARKER.search(stripped)
    if marker:
        stripped = stripped[: marker.start()].strip()
    return stripped


def _normalize_line_for_parse(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return RE_LIST_PREFIX.sub("", stripped).strip()


def _line_is_option_dominated(text: str) -> bool:
    normalized = _normalize_line_for_parse(text)
    if not normalized:
        return False
    if _has_question_anchor_markers(normalized):
        remainder = RE_Q_ANCHOR_STRIP.sub("", normalized, count=1).strip()
        if not RE_FIRST_OPTION_MARKER.search(remainder):
            return False
        normalized = remainder
    return bool(_parse_options_from_line_text(normalized, _dummy_line(normalized)))


def _parse_paren_delimited_options(text: str, line: EvidenceLine) -> list[ParsedOption]:
    """Split on `(a)` markers so nested parentheses in option text are preserved."""
    markers = list(RE_PAREN_LABEL.finditer(text))
    if len(markers) < 1:
        return []

    found: list[ParsedOption] = []
    seen: set[str] = set()
    for idx, match in enumerate(markers):
        raw_key = match.group(1)
        canon = _canonical_from_raw(raw_key)
        if not canon or canon in seen:
            continue
        start = match.end()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(text)
        opt_text = text[start:end].strip(" -:;,")
        if not opt_text:
            continue
        seen.add(canon)
        found.append(
            ParsedOption(
                canonical_key=canon,
                key_raw=raw_key,
                text_raw=opt_text,
                source_line=line,
            ),
        )
    return found


def _canonical_from_raw(raw_key: str) -> str | None:
    canon, _ = normalize_option_key(raw_key)
    if canon:
        return canon
    mapped = numeric_to_canonical(raw_key.strip())
    return mapped


def _is_noise(text: str) -> bool:
    return bool(
        re.search(
            r"(?:free\s+mock|download\s+pdf|www\.|subscribe\s+now|"
            r"all\s+exams|one\s+subscription|attempt\s+free\s+mock|"
            r"personalised\s+report|unlimited\s+re-?attempt|exam\s+covered|refund)",
            text,
            re.IGNORECASE,
        ),
    )


def _is_image_only_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    without_image = RE_IMAGE.sub("", stripped).strip()
    return bool(RE_IMAGE.search(stripped)) and len(without_image) < 20


def _dummy_line(text: str) -> EvidenceLine:
    return EvidenceLine(
        line_id="_",
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
    )


def count_usable_options(options: list) -> int:
    """Count options with both label and non-empty text."""
    count = 0
    for opt in options:
        label = (getattr(opt, "canonical_key", None) or getattr(opt, "key", None) or getattr(opt, "key_raw", None) or "").strip()
        text = (getattr(opt, "text_raw", None) or "").strip()
        if label and text:
            count += 1
    return count
