"""Shared deterministic text classification for raw lines and table segments."""

from __future__ import annotations

import html
import re

from meritranker_data_ingestion.schemas.classification import LineType

RE_PAGE_NUMBER = re.compile(r"<!--\s*PageNumber:\s*(\d+)\s*-->", re.IGNORECASE)
RE_PAGE_BREAK = re.compile(r"<!--\s*PageBreak\s*-->", re.IGNORECASE)
RE_IMAGE_REF = re.compile(r"!\[[^\]]*\]\([^)]+\)")
RE_HEADING = re.compile(r"^#{1,6}\s+")
RE_TABLE_ROW = re.compile(r"^\s*\|")
RE_MATH_DELIM = re.compile(r"^\s*\$\$")
RE_QUESTION_ANCHOR = re.compile(r"^Q\s*(\d+)\s*[\.\):\-]", re.IGNORECASE)
RE_QUESTION_DOT_NUM = re.compile(r"^Q\s*\.\s*(\d+)", re.IGNORECASE)
RE_QUESTION_WORD = re.compile(r"^Question\s+(?:No\.?\s*)?(\d+)", re.IGNORECASE)
RE_QUESTION_QUE = re.compile(r"^Que\.?\s*(\d+)", re.IGNORECASE)
RE_NUMERIC_QUESTION_DOT = re.compile(r"^(\d+)\.\s+(.+)$", re.DOTALL)
RE_NUMERIC_QUESTION_PAREN = re.compile(r"^(\d+)\)\s+(.+)$", re.DOTALL)
RE_NUMERIC_QUESTION_WRAPPED = re.compile(r"^\((\d+)\)\s+(.+)$", re.DOTALL)
RE_SOLUTION_HEADING = re.compile(
    r"^#{0,6}\s*(Solutions?|Answer\s*Key|Answers?\s*(?:Key|Sheet)?|Answer\s*Sheet)\s*:?\s*$",
    re.IGNORECASE,
)
RE_SOLUTION_ANCHOR = re.compile(r"^S\s*(\d+)\s*[\.\):\-]", re.IGNORECASE)
RE_ANSWER_MARKER = re.compile(
    r"\bAns(?:wer)?\s*[\.\:]?\s*\(?\s*([A-Da-d])\s*\)?",
    re.IGNORECASE,
)
RE_OPTION_PAREN = re.compile(r"^\s*\(([A-Da-d])\)\s*(.*)$", re.DOTALL)
RE_OPTION_CLOSE_PAREN = re.compile(r"^\s*([A-Da-d])\)\s*(.*)$", re.DOTALL)
RE_OPTION_DOT = re.compile(r"^([A-D])\.\s*(.*)$", re.DOTALL)
RE_METADATA = re.compile(
    r"^(?:Exam(?:ination)?|Subject|Paper|Year|Set|Code)\s*[:\-]",
    re.IGNORECASE,
)
RE_FOOTER_NOISE = re.compile(
    r"(?:www\.|http://|https://|©|copyright|all rights reserved|visit us at)",
    re.IGNORECASE,
)
RE_MARKER_PAGE_LINE = re.compile(
    r"^\s*\{?\s*PAGE\s*_?\s*NUMBER\s*:?\s*(\d+)\s*\}?\s*$",
    re.IGNORECASE,
)
RE_TABLE_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
RE_LIST_PREFIX = re.compile(r"^[-*+]\s+")
RE_BOLD_WRAP = re.compile(r"\*\*([^*]+)\*\*")
RE_HEADING_PREFIX = re.compile(r"^#{1,6}\s+")


class TextClassification:
    """Result of classifying a text fragment."""

    def __init__(
        self,
        *,
        line_type: LineType,
        detected_label: str | None = None,
        confidence: float = 0.9,
        issues: list[str] | None = None,
        page_number: int | None = None,
    ) -> None:
        self.line_type = line_type
        self.detected_label = detected_label
        self.confidence = confidence
        self.issues = list(issues or [])
        self.page_number = page_number


def normalize_preview(raw_text: str) -> str:
    """Debug-only preview; does not replace raw_text."""
    return html.unescape(classification_target(raw_text))


def classification_target(raw_text: str) -> str:
    """Strip list/bold/heading decorations for matching only; raw_text stays unchanged."""
    target = raw_text.strip()
    target = RE_LIST_PREFIX.sub("", target)
    target = RE_BOLD_WRAP.sub(r"\1", target)
    target = RE_HEADING_PREFIX.sub("", target)
    return target.strip()


def _is_answer_key_like_numeric_remainder(remainder: str) -> bool:
    stripped = remainder.strip()
    if not stripped:
        return True
    if len(stripped) <= 3 and re.fullmatch(r"[A-Da-d]\.?", stripped):
        return True
    if re.fullmatch(r"[A-Da-d]\s*[\.\)]?\s*", stripped):
        return True
    return False


def _match_question_anchor(text: str) -> tuple[str, str] | None:
    """Return (label, pattern_name) if text starts with a question anchor."""
    stripped = classification_target(text)
    if not stripped:
        return None

    for pattern, label_fn in (
        (RE_QUESTION_ANCHOR, lambda m: f"Q{m.group(1)}"),
        (RE_QUESTION_DOT_NUM, lambda m: f"Q{m.group(1)}"),
        (RE_QUESTION_WORD, lambda m: f"Q{m.group(1)}"),
        (RE_QUESTION_QUE, lambda m: f"Q{m.group(1)}"),
    ):
        match = pattern.match(stripped)
        if match:
            return label_fn(match), "q_prefix"

    for pattern in (RE_NUMERIC_QUESTION_DOT, RE_NUMERIC_QUESTION_PAREN, RE_NUMERIC_QUESTION_WRAPPED):
        match = pattern.match(stripped)
        if match:
            remainder = match.group(2)
            if _is_answer_key_like_numeric_remainder(remainder):
                continue
            if len(remainder.strip()) < 4:
                continue
            return f"Q{match.group(1)}", "numeric_prefix"

    return None


def classify_text(
    raw_text: str,
    *,
    page_number: int | None = None,
    allow_table_row: bool = True,
    allow_page_markers: bool = True,
) -> TextClassification:
    """Classify a text fragment deterministically (line or table segment)."""
    issues: list[str] = []

    if raw_text == "" or raw_text.isspace():
        return TextClassification(line_type=LineType.BLANK, confidence=1.0, page_number=page_number)

    if allow_page_markers:
        page_num_match = RE_PAGE_NUMBER.search(raw_text)
        if page_num_match:
            return TextClassification(
                line_type=LineType.PAGE_NUMBER_MARKER,
                detected_label=page_num_match.group(1),
                confidence=1.0,
                page_number=int(page_num_match.group(1)),
            )

        marker_page = RE_MARKER_PAGE_LINE.match(raw_text)
        if marker_page:
            return TextClassification(
                line_type=LineType.PAGE_NUMBER_MARKER,
                detected_label=marker_page.group(1),
                confidence=0.9,
                page_number=int(marker_page.group(1)),
            )

        if RE_PAGE_BREAK.search(raw_text):
            return TextClassification(
                line_type=LineType.PAGE_BREAK_MARKER,
                confidence=1.0,
                page_number=page_number,
            )

    if RE_IMAGE_REF.search(raw_text) and raw_text.strip().startswith("!["):
        return TextClassification(
            line_type=LineType.IMAGE_REFERENCE,
            confidence=1.0,
            page_number=page_number,
        )

    if RE_MATH_DELIM.match(raw_text) or raw_text.strip().endswith("$$"):
        return TextClassification(
            line_type=LineType.MATH_BLOCK,
            confidence=0.95,
            page_number=page_number,
        )

    if allow_table_row and RE_TABLE_ROW.match(raw_text):
        return TextClassification(
            line_type=LineType.TABLE_ROW,
            confidence=0.95,
            page_number=page_number,
        )

    match_text = classification_target(raw_text)

    if RE_SOLUTION_HEADING.match(match_text):
        return TextClassification(
            line_type=LineType.SOLUTION_SECTION_HEADING,
            confidence=0.95,
            page_number=page_number,
        )

    question_match = _match_question_anchor(match_text)
    if question_match:
        label, _ = question_match
        return TextClassification(
            line_type=LineType.QUESTION_ANCHOR,
            detected_label=label,
            confidence=0.95,
            page_number=page_number,
        )

    solution_match = RE_SOLUTION_ANCHOR.match(match_text)
    if solution_match:
        answer_match = RE_ANSWER_MARKER.search(match_text)
        return TextClassification(
            line_type=LineType.SOLUTION_ANCHOR,
            detected_label=f"S{solution_match.group(1)}",
            confidence=0.9,
            issues=["contains_answer_marker"] if answer_match else [],
            page_number=page_number,
        )

    if RE_HEADING.match(raw_text.strip()):
        return TextClassification(
            line_type=LineType.HEADING,
            confidence=0.95,
            page_number=page_number,
        )

    question_match = _match_question_anchor(raw_text)
    if question_match:
        label, _ = question_match
        return TextClassification(
            line_type=LineType.QUESTION_ANCHOR,
            detected_label=label,
            confidence=0.95,
            page_number=page_number,
        )

    if RE_ANSWER_MARKER.search(match_text) and match_text.lower().startswith(
        ("ans", "answer"),
    ):
        answer_match = RE_ANSWER_MARKER.search(match_text)
        return TextClassification(
            line_type=LineType.ANSWER_MARKER,
            detected_label=answer_match.group(1) if answer_match else None,
            confidence=0.85,
            page_number=page_number,
        )

    option_paren = RE_OPTION_PAREN.match(match_text)
    if option_paren:
        return TextClassification(
            line_type=LineType.OPTION_CANDIDATE,
            detected_label=option_paren.group(1).upper(),
            confidence=0.9,
            page_number=page_number,
        )

    option_close = RE_OPTION_CLOSE_PAREN.match(match_text)
    if option_close:
        return TextClassification(
            line_type=LineType.OPTION_CANDIDATE,
            detected_label=option_close.group(1).upper(),
            confidence=0.85,
            issues=["close_paren_option"],
            page_number=page_number,
        )

    option_dot = RE_OPTION_DOT.match(match_text)
    if option_dot:
        remainder = option_dot.group(2)
        if "=" in remainder or "^" in remainder or "_" in remainder:
            issues.append("equation_like_content_low_confidence_option")
            return TextClassification(
                line_type=LineType.TEXT,
                confidence=0.6,
                issues=issues,
                page_number=page_number,
            )
        return TextClassification(
            line_type=LineType.OPTION_CANDIDATE,
            detected_label=option_dot.group(1).upper(),
            confidence=0.7,
            issues=["dot_option_lower_confidence"],
            page_number=page_number,
        )

    if RE_FOOTER_NOISE.search(raw_text):
        return TextClassification(
            line_type=LineType.PAGE_FOOTER_MARKER,
            confidence=0.85,
            page_number=page_number,
        )

    if RE_METADATA.match(match_text):
        return TextClassification(
            line_type=LineType.METADATA_CANDIDATE,
            confidence=0.8,
            page_number=page_number,
        )

    return TextClassification(
        line_type=LineType.TEXT,
        confidence=0.9,
        page_number=page_number,
    )
