"""Split embedded option markers within option text (Part 10)."""

from __future__ import annotations

import re

RE_EMBEDDED_OPTION_MARKER = re.compile(r"\s+\(([A-Ea-e])\)\s*")


def split_option_text(
    text: str,
    primary_key: str | None,
) -> tuple[list[tuple[str, str, str]], bool]:
    """
    Split option text containing embedded markers like '493287 (d) 329417'.

    Returns list of (normalized_key, key_raw, segment_text) and whether a split was applied.
    Only intended for option parsing context — not question prose.
    """
    stripped = text.strip()
    if not stripped or primary_key is None:
        return [], False

    matches = list(RE_EMBEDDED_OPTION_MARKER.finditer(stripped))
    if not matches:
        return [(primary_key.upper(), f"({primary_key.lower()})", stripped)], False

    segments: list[tuple[str, str, str]] = []
    prefix = stripped[: matches[0].start()].strip()
    if prefix:
        segments.append((primary_key.upper(), f"({primary_key.lower()})", prefix))

    for idx, found in enumerate(matches):
        key = found.group(1).upper()
        key_raw = f"({found.group(1)})"
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(stripped)
        segment_text = stripped[found.end() : end].strip()
        segments.append((key, key_raw, segment_text))

    split_applied = len(segments) > 1
    return segments, split_applied
