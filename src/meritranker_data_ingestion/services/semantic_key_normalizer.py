"""Canonical option/answer key normalization for semantic binding repair (Part 13F)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBindingPackage,
    SemanticBoundQuestion,
)

VALID_KEYS = frozenset({"A", "B", "C", "D", "E"})

HOMOGLYPH_TO_LATIN: dict[str, str] = {
    "Α": "A",
    "А": "A",
    "Β": "B",
    "В": "B",
    "С": "C",
    "C": "C",
    "Δ": "D",
    "D": "D",
    "a": "A",
    "b": "B",
    "c": "C",
    "d": "D",
}

RE_OPTION_PREFIX = re.compile(r"^option\s+", re.IGNORECASE)
RE_ANS_PREFIX = re.compile(
    r"^(?:ans(?:wer)?|key)\s*[\.\(]?\s*\(?\s*([A-Da-d])\s*\)?\.?$",
    re.IGNORECASE,
)
RE_PAREN_KEY = re.compile(r"^\(?\s*([A-Da-d])\s*\)?\.?$")
RE_BOLD_KEY = re.compile(r"^\*\*?([A-Da-d])\*\*?\.?$")
RE_DOT_KEY = re.compile(r"^([A-Da-d])\.\s*$")
RE_BULLET_KEY = re.compile(r"^[-*+]\s+\*\*?([A-Da-d])\*\*?\s*$")
RE_PLAIN_KEY = re.compile(r"^([A-Da-d])$")


def normalize_option_key(raw: str | None) -> tuple[str | None, str]:
    """Return canonical key (A–E) and preserved key_raw."""
    if raw is None:
        return None, ""
    key_raw = raw.strip()
    if not key_raw:
        return None, key_raw

    candidate = key_raw
    candidate = RE_OPTION_PREFIX.sub("", candidate).strip()

    for pattern in (
        RE_BULLET_KEY,
        RE_BOLD_KEY,
        RE_PAREN_KEY,
        RE_DOT_KEY,
        RE_PLAIN_KEY,
    ):
        match = pattern.match(candidate)
        if match:
            canonical = match.group(1).upper()
            if canonical in VALID_KEYS:
                return canonical, key_raw

    # Embedded in markdown bullet: "- **A**"
    bullet = re.match(r"^[-*+]\s+\*\*?([A-Da-d])\*\*?", candidate)
    if bullet:
        canonical = bullet.group(1).upper()
        if canonical in VALID_KEYS:
            return canonical, key_raw

    single = candidate.strip("(). ").upper()
    if len(single) == 1:
        mapped = HOMOGLYPH_TO_LATIN.get(single, single)
        if mapped in VALID_KEYS:
            return mapped, key_raw

    if len(key_raw) == 1 and key_raw in HOMOGLYPH_TO_LATIN:
        mapped = HOMOGLYPH_TO_LATIN[key_raw]
        if mapped in VALID_KEYS:
            return mapped, key_raw

    return None, key_raw


def normalize_answer_key(raw: str | None) -> tuple[str | None, str]:
    """Return canonical answer key (A–E) and preserved key_raw."""
    if raw is None:
        return None, ""
    key_raw = raw.strip()
    if not key_raw:
        return None, key_raw

    ans = RE_ANS_PREFIX.match(key_raw)
    if ans:
        canonical = ans.group(1).upper()
        if canonical in VALID_KEYS:
            return canonical, key_raw

    return normalize_option_key(key_raw)


def apply_key_normalization(package: SemanticBindingPackage) -> tuple[int, int]:
    """Normalize option and answer keys in place. Returns (option_count, answer_count)."""
    option_count = 0
    answer_count = 0
    for item in package.items:
        option_count += _normalize_item_options(item)
        if item.answer.available or item.answer.key or item.answer.key_raw:
            answer_count += _normalize_item_answer(item)
    return option_count, answer_count


def _normalize_item_options(item: SemanticBoundQuestion) -> int:
    changed = 0
    for option in item.options:
        raw = option.key_raw or option.key
        canonical, preserved = normalize_option_key(raw)
        if canonical and option.key != canonical:
            if not option.key_raw:
                option.key_raw = preserved or option.key or raw
            option.key = canonical
            changed += 1
        elif canonical is None and raw:
            option.issues = _dedupe(option.issues + [f"unnormalizable_option_key:{raw}"])
    return changed


def _normalize_item_answer(item: SemanticBoundQuestion) -> int:
    raw = item.answer.key_raw or item.answer.key
    canonical, preserved = normalize_answer_key(raw)
    if canonical and item.answer.key != canonical:
        if not item.answer.key_raw:
            item.answer.key_raw = preserved or item.answer.key or raw
        item.answer.key = canonical
        return 1
    if canonical is None and raw:
        item.answer.issues = _dedupe(item.answer.issues + [f"unnormalizable_answer_key:{raw}"])
    return 0


def canonical_option_keys(item: SemanticBoundQuestion) -> set[str]:
    """Collect normalized option keys for validator comparison."""
    from meritranker_data_ingestion.services.option_key_normalizer import numeric_to_canonical

    keys: set[str] = set()
    for option in item.options:
        raw = (option.key or option.key_raw or "").strip().rstrip(".")
        if raw in {"1", "2", "3", "4"}:
            mapped = numeric_to_canonical(raw)
            if mapped:
                keys.add(mapped)
                keys.add(raw)
            continue
        canonical, _ = normalize_option_key(option.key or option.key_raw)
        if canonical:
            keys.add(canonical)
    return keys


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
