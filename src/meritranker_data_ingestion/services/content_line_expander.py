"""Expand raw markdown lines into source-traced logical content lines."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.classification import (
    ContentLineRecord,
    ContentSourceKind,
    LineType,
    MarkdownLineRecord,
)
from meritranker_data_ingestion.services.line_text_classifier import (
    RE_TABLE_SEPARATOR_CELL,
    classify_text,
    normalize_preview,
)

RE_BR_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)


def split_table_cells(row_text: str) -> list[str]:
    """Split a markdown table row into non-separator cells."""
    inner = row_text.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]

    cells = [cell.strip() for cell in inner.split("|")]
    result: list[str] = []
    for cell in cells:
        if not cell:
            continue
        compact = cell.replace(" ", "")
        if RE_TABLE_SEPARATOR_CELL.fullmatch(compact):
            continue
        result.append(cell)
    return result


def split_br_segments(cell_text: str) -> list[str]:
    """Split table cell text on <br> tags without removing inner content."""
    parts = RE_BR_TAG.split(cell_text)
    return [part for part in parts if part != ""]


def expand_content_lines(line_records: list[MarkdownLineRecord]) -> list[ContentLineRecord]:
    """Build source-traced content lines from classified raw lines."""
    content_lines: list[ContentLineRecord] = []
    counter = 0

    for line in line_records:
        if line.line_type == LineType.TABLE_ROW:
            cells = split_table_cells(line.raw_text)
            for cell_index, cell_text in enumerate(cells):
                segments = split_br_segments(cell_text)
                if not segments:
                    segments = [cell_text]

                for segment_index, segment_text in enumerate(segments):
                    if segment_text == "" or segment_text.isspace():
                        continue
                    classification = classify_text(
                        segment_text,
                        page_number=line.page_number,
                        allow_table_row=False,
                        allow_page_markers=False,
                    )
                    counter += 1
                    content_lines.append(
                        ContentLineRecord(
                            content_line_number=counter,
                            raw_text=segment_text,
                            normalized_preview=normalize_preview(segment_text),
                            line_type=classification.line_type,
                            detected_label=classification.detected_label,
                            confidence=classification.confidence,
                            page_number=classification.page_number or line.page_number,
                            source_kind=ContentSourceKind.TABLE_CELL_SEGMENT,
                            parent_line_number=line.line_number,
                            table_cell_index=cell_index,
                            table_segment_index=segment_index,
                            issues=list(classification.issues),
                        ),
                    )
            continue

        counter += 1
        content_lines.append(
            ContentLineRecord(
                content_line_number=counter,
                raw_text=line.raw_text,
                normalized_preview=line.normalized_preview,
                line_type=line.line_type,
                detected_label=line.detected_label,
                confidence=line.confidence,
                page_number=line.page_number,
                source_kind=ContentSourceKind.RAW_LINE,
                parent_line_number=line.line_number,
                issues=list(line.issues),
            ),
        )

    return content_lines


def content_line_to_markdown_line(content_line: ContentLineRecord) -> MarkdownLineRecord:
    """Adapt a content line for parsers that consume MarkdownLineRecord."""
    return MarkdownLineRecord(
        line_number=content_line.content_line_number,
        raw_text=content_line.raw_text,
        normalized_preview=content_line.normalized_preview,
        page_number=content_line.page_number,
        line_type=content_line.line_type,
        detected_label=content_line.detected_label,
        confidence=content_line.confidence,
        issues=list(content_line.issues),
    )
