"""Pydantic schemas for deterministic markdown line/block classification."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class LineType(str, Enum):
    """Classification label for a single markdown line."""

    BLANK = "blank"
    HEADING = "heading"
    PAGE_NUMBER_MARKER = "page_number_marker"
    PAGE_FOOTER_MARKER = "page_footer_marker"
    PAGE_BREAK_MARKER = "page_break_marker"
    QUESTION_ANCHOR = "question_anchor"
    OPTION_CANDIDATE = "option_candidate"
    SOLUTION_SECTION_HEADING = "solution_section_heading"
    SOLUTION_ANCHOR = "solution_anchor"
    ANSWER_MARKER = "answer_marker"
    IMAGE_REFERENCE = "image_reference"
    TABLE_ROW = "table_row"
    MATH_BLOCK = "math_block"
    METADATA_CANDIDATE = "metadata_candidate"
    NOISE_CANDIDATE = "noise_candidate"
    TEXT = "text"


class BlockType(str, Enum):
    """Grouped block label derived from consecutive compatible lines."""

    HEADING_BLOCK = "heading_block"
    PAGE_MARKER_BLOCK = "page_marker_block"
    QUESTION_CANDIDATE_BLOCK = "question_candidate_block"
    OPTION_CANDIDATE_BLOCK = "option_candidate_block"
    SOLUTION_CANDIDATE_BLOCK = "solution_candidate_block"
    IMAGE_BLOCK = "image_block"
    TABLE_BLOCK = "table_block"
    MATH_BLOCK = "math_block"
    NOISE_BLOCK = "noise_block"
    TEXT_BLOCK = "text_block"


class ClassificationStatus(str, Enum):
    """Overall classification run status."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ContentSourceKind(str, Enum):
    """Origin of a logical content line."""

    RAW_LINE = "raw_line"
    TABLE_CELL_SEGMENT = "table_cell_segment"
    IMAGE_ALT_SEGMENT = "image_alt_segment"


class MarkdownLineRecord(BaseModel):
    """One line from raw.md with deterministic classification metadata."""

    line_number: int
    raw_text: str
    normalized_preview: str
    page_number: int | None = None
    line_type: LineType
    detected_label: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class ContentLineRecord(BaseModel):
    """Source-traced logical content line for downstream parsers."""

    content_line_number: int
    raw_text: str
    normalized_preview: str
    line_type: LineType
    detected_label: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    page_number: int | None = None
    source_kind: ContentSourceKind
    parent_line_number: int
    table_cell_index: int | None = None
    table_segment_index: int | None = None
    issues: list[str] = Field(default_factory=list)


class MarkdownBlockRecord(BaseModel):
    """Consecutive compatible lines grouped into a simple block."""

    block_id: str
    block_type: BlockType
    start_line: int
    end_line: int
    page_start: int | None = None
    page_end: int | None = None
    raw_text: str
    line_numbers: list[int]
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class MarkdownClassificationResult(BaseModel):
    """Full classification output for an extraction package."""

    package_dir: str
    source_markdown: str
    status: ClassificationStatus
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    lines: list[MarkdownLineRecord] = Field(default_factory=list)
    blocks: list[MarkdownBlockRecord] = Field(default_factory=list)
    total_lines: int = 0
    total_blocks: int = 0
    question_anchor_count: int = 0
    option_candidate_count: int = 0
    solution_anchor_count: int = 0
    image_reference_count: int = 0
    page_count_detected: int = 0
    content_lines: list[ContentLineRecord] = Field(default_factory=list)
    content_line_count: int = 0
    content_question_anchor_count: int = 0
    content_option_candidate_count: int = 0
    table_row_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
