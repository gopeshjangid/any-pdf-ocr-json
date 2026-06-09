"""Load classified lines for downstream parsers (prefer content-lines)."""

from __future__ import annotations

import json
from pathlib import Path

from meritranker_data_ingestion.config import (
    CLASSIFIED_BLOCKS_NAME,
    CLASSIFIED_CONTENT_LINES_NAME,
    CLASSIFIED_DIR,
    CLASSIFIED_LINES_NAME,
)
from meritranker_data_ingestion.schemas.classification import (
    ContentLineRecord,
    MarkdownBlockRecord,
    MarkdownLineRecord,
)
from meritranker_data_ingestion.services.content_line_expander import content_line_to_markdown_line


def load_classified_lines_file(path: Path) -> list[MarkdownLineRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [MarkdownLineRecord.model_validate(item) for item in payload]


def load_content_lines_file(path: Path) -> list[ContentLineRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ContentLineRecord.model_validate(item) for item in payload]


def load_classified_blocks_file(path: Path) -> list[MarkdownBlockRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [MarkdownBlockRecord.model_validate(item) for item in payload]


def load_lines_for_downstream(
    package_dir: Path,
) -> tuple[list[MarkdownLineRecord], dict[int, ContentLineRecord], int, str, bool]:
    """
    Prefer content-lines.json; fallback to lines.json.

    Returns (lines, content_line_by_number, raw_line_count, source_path, content_lines_used).
    """
    classified_dir = package_dir / CLASSIFIED_DIR
    lines_path = classified_dir / CLASSIFIED_LINES_NAME
    content_path = classified_dir / CLASSIFIED_CONTENT_LINES_NAME

    raw_line_count = 0
    if lines_path.is_file():
        raw_line_count = len(load_classified_lines_file(lines_path))

    if content_path.is_file():
        content_lines = load_content_lines_file(content_path)
        by_num = {cl.content_line_number: cl for cl in content_lines}
        markdown_lines = [content_line_to_markdown_line(cl) for cl in content_lines]
        return markdown_lines, by_num, raw_line_count, str(content_path), True

    if lines_path.is_file():
        lines = load_classified_lines_file(lines_path)
        return lines, {}, raw_line_count, str(lines_path), False

    return [], {}, 0, str(lines_path), False
