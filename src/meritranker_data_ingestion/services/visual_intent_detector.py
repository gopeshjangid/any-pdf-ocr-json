"""Deterministic visual-intent detection from question text (Part 10)."""

from __future__ import annotations

VISUAL_DEPENDENT_PHRASES: tuple[str, ...] = (
    "following figure",
    "given figure",
    "figure series",
    "embedded figure",
    "figure is embedded",
    "mirror image",
    "dice",
    "cube",
    "rotated positions",
    "paper folding",
    "water image",
    "how many triangles",
    "how many squares",
    "figure shown",
    "same dice",
    "positions of the same dice",
    "select the option in which",
    "select the figure",
    "select the correct mirror",
    "select the term",
    "diagram",
    "shape",
)

EXPECTED_OPTION_KEYS = frozenset({"A", "B", "C", "D"})


def is_visual_dependent(question_text_raw: str) -> bool:
    """True when question wording indicates figure/visual reasoning is required."""
    text = question_text_raw.lower()
    return any(phrase in text for phrase in VISUAL_DEPENDENT_PHRASES)


def has_full_text_options(options: list) -> bool:
    """True when A-D exist and each has non-empty text."""
    keys = {getattr(opt, "key", None) for opt in options}
    if keys != EXPECTED_OPTION_KEYS:
        return False
    return all(getattr(opt, "text_raw", "").strip() for opt in options if opt.key in EXPECTED_OPTION_KEYS)


def has_visual_option_pattern(options: list) -> bool:
    """True when A-D exist with linked images and empty/minimal text (Q67 pattern)."""
    keys = {getattr(opt, "key", None) for opt in options}
    if keys != EXPECTED_OPTION_KEYS:
        return False
    relevant = [opt for opt in options if getattr(opt, "key", None) in EXPECTED_OPTION_KEYS]
    if not relevant:
        return False
    return all(getattr(opt, "linked_asset_paths", []) for opt in relevant)


def is_visual_text_options_question(question_text_raw: str, options: list) -> bool:
    """Visual-dependent question with text options A-D (Q31–Q40 pattern)."""
    return is_visual_dependent(question_text_raw) and has_full_text_options(options)


def has_source_backed_option_labels(options: list) -> bool:
    """True when at least one explicit option label exists in parsed options."""
    return any(getattr(opt, "key", None) for opt in options)


def is_image_only_without_labels(options: list, assets: list) -> bool:
    """Image assets present but no source-backed option labels (Q52 pattern)."""
    if options:
        return False
    return bool(assets)
