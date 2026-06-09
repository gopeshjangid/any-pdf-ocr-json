"""Deterministic markdown line/block classifier — regex rules only, no AI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    CLASSIFIED_CONTENT_LINES_NAME,
    CLASSIFIED_DIR,
    RAW_MARKDOWN_NAME,
)
from meritranker_data_ingestion.schemas.classification import (
    BlockType,
    ClassificationStatus,
    LineType,
    MarkdownBlockRecord,
    MarkdownClassificationResult,
    MarkdownLineRecord,
)
from meritranker_data_ingestion.services.content_line_expander import expand_content_lines
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)
from meritranker_data_ingestion.services.line_text_classifier import (
    classify_text,
    normalize_preview,
)

LINE_TYPE_TO_BLOCK_TYPE: dict[LineType, BlockType] = {
    LineType.BLANK: BlockType.TEXT_BLOCK,
    LineType.HEADING: BlockType.HEADING_BLOCK,
    LineType.PAGE_NUMBER_MARKER: BlockType.PAGE_MARKER_BLOCK,
    LineType.PAGE_FOOTER_MARKER: BlockType.NOISE_BLOCK,
    LineType.PAGE_BREAK_MARKER: BlockType.PAGE_MARKER_BLOCK,
    LineType.QUESTION_ANCHOR: BlockType.QUESTION_CANDIDATE_BLOCK,
    LineType.OPTION_CANDIDATE: BlockType.OPTION_CANDIDATE_BLOCK,
    LineType.SOLUTION_SECTION_HEADING: BlockType.SOLUTION_CANDIDATE_BLOCK,
    LineType.SOLUTION_ANCHOR: BlockType.SOLUTION_CANDIDATE_BLOCK,
    LineType.ANSWER_MARKER: BlockType.SOLUTION_CANDIDATE_BLOCK,
    LineType.IMAGE_REFERENCE: BlockType.IMAGE_BLOCK,
    LineType.TABLE_ROW: BlockType.TABLE_BLOCK,
    LineType.MATH_BLOCK: BlockType.MATH_BLOCK,
    LineType.METADATA_CANDIDATE: BlockType.TEXT_BLOCK,
    LineType.NOISE_CANDIDATE: BlockType.NOISE_BLOCK,
    LineType.TEXT: BlockType.TEXT_BLOCK,
}

GROUPABLE_BLOCK_TYPES = {
    BlockType.HEADING_BLOCK,
    BlockType.PAGE_MARKER_BLOCK,
    BlockType.OPTION_CANDIDATE_BLOCK,
    BlockType.SOLUTION_CANDIDATE_BLOCK,
    BlockType.IMAGE_BLOCK,
    BlockType.TABLE_BLOCK,
    BlockType.MATH_BLOCK,
    BlockType.NOISE_BLOCK,
    BlockType.TEXT_BLOCK,
}

MIN_LINES_FOR_ZERO_ANCHOR_GATE = 100
ZERO_ANCHOR_ERROR = "no_question_anchors_detected"


class ClassificationError(Exception):
    """Raised when classification cannot proceed."""


@dataclass(frozen=True)
class ClassifiedPackagePaths:
    """Paths for classification input/output within an extraction package."""

    package_dir: Path
    raw_markdown: Path
    classified_dir: Path
    lines_json: Path
    blocks_json: Path
    content_lines_json: Path
    report_json: Path


def build_classification_paths(package_dir: Path) -> ClassifiedPackagePaths:
    """Resolve classification paths for a given extraction package directory."""
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")

    classified_dir = resolved / CLASSIFIED_DIR
    return ClassifiedPackagePaths(
        package_dir=resolved,
        raw_markdown=resolved / "marker" / RAW_MARKDOWN_NAME,
        classified_dir=classified_dir,
        lines_json=classified_dir / "lines.json",
        blocks_json=classified_dir / "blocks.json",
        content_lines_json=classified_dir / CLASSIFIED_CONTENT_LINES_NAME,
        report_json=classified_dir / "classification-report.json",
    )


def classify_line(
    line_number: int,
    raw_text: str,
    current_page: int | None,
) -> MarkdownLineRecord:
    """Classify a single markdown line using deterministic regex rules."""
    classification = classify_text(raw_text, page_number=current_page)
    page_number = classification.page_number if classification.page_number is not None else current_page

    return MarkdownLineRecord(
        line_number=line_number,
        raw_text=raw_text,
        normalized_preview=normalize_preview(raw_text),
        page_number=page_number,
        line_type=classification.line_type,
        detected_label=classification.detected_label,
        confidence=classification.confidence,
        issues=classification.issues,
    )


def classify_lines(raw_markdown: str) -> list[MarkdownLineRecord]:
    """Classify every line in raw markdown; preserves all lines including blanks."""
    lines = raw_markdown.splitlines(keepends=False)
    records: list[MarkdownLineRecord] = []
    current_page: int | None = None

    if not lines and raw_markdown == "":
        return records

    for index, raw_line in enumerate(lines, start=1):
        record = classify_line(index, raw_line, current_page)
        if record.line_type == LineType.PAGE_NUMBER_MARKER and record.page_number is not None:
            current_page = record.page_number
        elif record.page_number is None:
            record.page_number = current_page
        records.append(record)

    return records


def _block_type_for_line(line: MarkdownLineRecord) -> BlockType:
    if line.line_type == LineType.QUESTION_ANCHOR:
        return BlockType.QUESTION_CANDIDATE_BLOCK
    return LINE_TYPE_TO_BLOCK_TYPE[line.line_type]


def _can_group(block_type: BlockType, line: MarkdownLineRecord) -> bool:
    if line.line_type == LineType.QUESTION_ANCHOR:
        return False
    if line.line_type == LineType.BLANK:
        return block_type == BlockType.TEXT_BLOCK
    return _block_type_for_line(line) == block_type and block_type in GROUPABLE_BLOCK_TYPES


def group_blocks(lines: list[MarkdownLineRecord]) -> list[MarkdownBlockRecord]:
    """Group consecutive compatible lines into simple blocks."""
    if not lines:
        return []

    blocks: list[MarkdownBlockRecord] = []
    block_counter = 0

    current_type = _block_type_for_line(lines[0])
    current_lines: list[MarkdownLineRecord] = [lines[0]]

    def flush() -> None:
        nonlocal block_counter, current_lines, current_type
        if not current_lines:
            return
        block_counter += 1
        raw_parts = [line.raw_text for line in current_lines]
        confidences = [line.confidence for line in current_lines]
        all_issues: list[str] = []
        for line in current_lines:
            all_issues.extend(line.issues)
        page_start = next((ln.page_number for ln in current_lines if ln.page_number is not None), None)
        page_end = next(
            (ln.page_number for ln in reversed(current_lines) if ln.page_number is not None),
            None,
        )
        blocks.append(
            MarkdownBlockRecord(
                block_id=f"blk_{block_counter:04d}",
                block_type=current_type,
                start_line=current_lines[0].line_number,
                end_line=current_lines[-1].line_number,
                page_start=page_start,
                page_end=page_end,
                raw_text="\n".join(raw_parts),
                line_numbers=[ln.line_number for ln in current_lines],
                confidence=sum(confidences) / len(confidences),
                issues=sorted(set(all_issues)),
            ),
        )
        current_lines = []

    for line in lines[1:]:
        line_block_type = _block_type_for_line(line)
        if _can_group(current_type, line):
            current_lines.append(line)
        else:
            flush()
            current_type = line_block_type
            current_lines = [line]

    flush()
    return blocks


def classify_markdown(raw_markdown: str, package_dir: Path) -> MarkdownClassificationResult:
    """Run full line + block + content-line classification on raw markdown text."""
    line_records = classify_lines(raw_markdown)
    block_records = group_blocks(line_records)
    content_lines = expand_content_lines(line_records)

    page_numbers = {
        ln.page_number
        for ln in line_records
        if ln.line_type == LineType.PAGE_NUMBER_MARKER and ln.page_number is not None
    }

    content_question_anchors = sum(
        1 for ln in content_lines if ln.line_type == LineType.QUESTION_ANCHOR
    )
    content_options = sum(
        1 for ln in content_lines if ln.line_type == LineType.OPTION_CANDIDATE
    )
    table_row_count = sum(1 for ln in line_records if ln.line_type == LineType.TABLE_ROW)

    warnings: list[str] = []
    errors: list[str] = []
    status = ClassificationStatus.SUCCEEDED

    if (
        len(line_records) > MIN_LINES_FOR_ZERO_ANCHOR_GATE
        and content_question_anchors == 0
    ):
        errors.append(ZERO_ANCHOR_ERROR)
        status = ClassificationStatus.FAILED

    return MarkdownClassificationResult(
        package_dir=str(package_dir),
        source_markdown=str(package_dir / "marker" / RAW_MARKDOWN_NAME),
        status=status,
        lines=line_records,
        blocks=block_records,
        total_lines=len(line_records),
        total_blocks=len(block_records),
        question_anchor_count=sum(
            1 for ln in line_records if ln.line_type == LineType.QUESTION_ANCHOR
        ),
        option_candidate_count=sum(
            1 for ln in line_records if ln.line_type == LineType.OPTION_CANDIDATE
        ),
        solution_anchor_count=sum(
            1
            for ln in line_records
            if ln.line_type in {LineType.SOLUTION_ANCHOR, LineType.SOLUTION_SECTION_HEADING}
        ),
        image_reference_count=sum(
            1 for ln in line_records if ln.line_type == LineType.IMAGE_REFERENCE
        ),
        page_count_detected=len(page_numbers),
        content_lines=content_lines,
        content_line_count=len(content_lines),
        content_question_anchor_count=content_question_anchors,
        content_option_candidate_count=content_options,
        table_row_count=table_row_count,
        warnings=warnings,
        errors=errors,
    )


def write_classification_outputs(
    result: MarkdownClassificationResult,
    paths: ClassifiedPackagePaths,
) -> None:
    """Write lines.json, blocks.json, content-lines.json, and classification-report.json."""
    assert_output_contains(paths.package_dir, paths.classified_dir)
    paths.classified_dir.mkdir(parents=True, exist_ok=True)

    lines_payload = [line.model_dump(mode="json") for line in result.lines]
    blocks_payload = [block.model_dump(mode="json") for block in result.blocks]
    content_payload = [line.model_dump(mode="json") for line in result.content_lines]

    for target, payload in (
        (paths.lines_json, lines_payload),
        (paths.blocks_json, blocks_payload),
        (paths.content_lines_json, content_payload),
    ):
        assert_output_contains(paths.package_dir, target)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report = {
        "package_dir": result.package_dir,
        "source_markdown": result.source_markdown,
        "status": result.status.value,
        "created_at": result.created_at.isoformat(),
        "total_lines": result.total_lines,
        "total_blocks": result.total_blocks,
        "question_anchor_count": result.question_anchor_count,
        "option_candidate_count": result.option_candidate_count,
        "solution_anchor_count": result.solution_anchor_count,
        "image_reference_count": result.image_reference_count,
        "page_count_detected": result.page_count_detected,
        "content_line_count": result.content_line_count,
        "content_question_anchor_count": result.content_question_anchor_count,
        "content_option_candidate_count": result.content_option_candidate_count,
        "table_row_count": result.table_row_count,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    assert_output_contains(paths.package_dir, paths.report_json)
    paths.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")


def classify_package(package_dir: Path) -> MarkdownClassificationResult:
    """Read raw.md from package, classify, and write outputs under classified/."""
    paths = build_classification_paths(package_dir)

    if not paths.raw_markdown.is_file():
        result = MarkdownClassificationResult(
            package_dir=str(paths.package_dir),
            source_markdown=str(paths.raw_markdown),
            status=ClassificationStatus.FAILED,
            errors=[f"Missing markdown file: {paths.raw_markdown}"],
        )
        try:
            paths.classified_dir.mkdir(parents=True, exist_ok=True)
            write_classification_outputs(result, paths)
        except PathValidationError:
            pass
        raise ClassificationError(result.errors[0])

    raw_markdown = paths.raw_markdown.read_text(encoding="utf-8")
    result = classify_markdown(raw_markdown, paths.package_dir)
    write_classification_outputs(result, paths)
    return result
