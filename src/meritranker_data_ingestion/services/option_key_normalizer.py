"""Numeric and letter option key normalization (Part 14B)."""

from __future__ import annotations

import re
from dataclasses import dataclass

NUMERIC_TO_CANONICAL: dict[str, str] = {
    "1": "A",
    "2": "B",
    "3": "C",
    "4": "D",
}

RE_NUMERIC_OPTION = re.compile(
    r"^(?:Ans(?:wer)?\s*)?(\d+)\s*[\.\)]\s*(.*)$",
    re.IGNORECASE,
)
RE_PAREN_NUMERIC = re.compile(r"^\(\s*(\d+)\s*\)\s*(.*)$")
RE_MULTI_NUMERIC = re.compile(
    r"(\d+)\s*[\.\)]\s*([^0-9]+?)(?=\s*\d+\s*[\.\)]|$)",
)


@dataclass(frozen=True)
class NormalizedOptionKey:
    key: str
    key_raw: str
    canonical_key: str | None
    option_index: int | None
    text_raw: str


def numeric_to_canonical(key: str) -> str | None:
    return NUMERIC_TO_CANONICAL.get(key.strip())


def parse_numeric_option_line(text: str) -> NormalizedOptionKey | None:
    """Parse a single numeric option line like `1. text` or `1) text`."""
    stripped = _strip_html_breaks(text).strip()
    if not stripped:
        return None

    for pattern in (RE_NUMERIC_OPTION, RE_PAREN_NUMERIC):
        match = pattern.match(stripped)
        if match:
            return _build_normalized(match.group(1), match.group(2), stripped)

    return None


def parse_multiple_numeric_options(text: str) -> list[NormalizedOptionKey]:
    """Parse multiple numeric options in one line/cell."""
    stripped = _strip_html_breaks(text).strip()
    if not stripped:
        return []

    options: list[NormalizedOptionKey] = []
    for match in RE_MULTI_NUMERIC.finditer(stripped):
        built = _build_normalized(match.group(1), match.group(2), match.group(0).strip())
        if built:
            options.append(built)

    if options:
        return _dedupe_by_key(options)

    single = parse_numeric_option_line(stripped)
    return [single] if single else []


def split_combined_option_text(text: str) -> NormalizedOptionKey | None:
    """Split `1. कनाटक` when key fields are empty but text contains numeric prefix."""
    return parse_numeric_option_line(text)


def is_valid_option_key(key: str) -> bool:
    upper = key.strip().upper()
    if upper in {"A", "B", "C", "D", "E"}:
        return True
    return key.strip() in NUMERIC_TO_CANONICAL


def _build_normalized(
    raw_index: str,
    raw_text: str,
    source_raw: str,
) -> NormalizedOptionKey | None:
    index = raw_index.strip()
    if index not in NUMERIC_TO_CANONICAL:
        return None
    text = raw_text.strip()
    if not text:
        return None
    key_raw = f"{index}."
    return NormalizedOptionKey(
        key=index,
        key_raw=key_raw,
        canonical_key=numeric_to_canonical(index),
        option_index=int(index),
        text_raw=text,
    )


def _strip_html_breaks(text: str) -> str:
    return re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)


def _dedupe_by_key(options: list[NormalizedOptionKey]) -> list[NormalizedOptionKey]:
    seen: set[str] = set()
    out: list[NormalizedOptionKey] = []
    for opt in sorted(options, key=lambda o: o.option_index or 99):
        if opt.key in seen:
            continue
        seen.add(opt.key)
        out.append(opt)
    return out
