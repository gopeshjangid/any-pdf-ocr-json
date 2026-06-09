"""OCR evidence schemas (Part 14A)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class OcrEngine(str, Enum):
    """OCR engine identifiers."""

    AZURE_DI = "azure_ocr"
    PADDLE = "paddle_ocr"
    NONE = "none"


class OcrRoleHint(str, Enum):
    """Deterministic OCR line role hints."""

    QUESTION_ANCHOR_CANDIDATE = "question_anchor_candidate"
    OPTION_LABEL_CANDIDATE = "option_label_candidate"
    ANSWER_KEY_CANDIDATE = "answer_key_candidate"
    CHOSEN_OPTION_CANDIDATE = "chosen_option_candidate"
    STATUS_CANDIDATE = "status_candidate"
    SOLUTION_CANDIDATE = "solution_candidate"
    NOISE_CANDIDATE = "noise_candidate"
    VISUAL_TICK_CANDIDATE = "visual_tick_candidate"


class OcrWord(BaseModel):
    word_id: str
    line_id: str
    page_number: int
    text: str
    bbox: list[float] | None = None
    confidence: float = 0.0
    engine: str


class OcrLine(BaseModel):
    line_id: str
    page_number: int
    text: str
    normalized_text: str
    bbox: list[float] | None = None
    confidence: float = 0.0
    engine: str
    source_image_path: str | None = None
    role_hints: list[OcrRoleHint] = Field(default_factory=list)


class OcrPage(BaseModel):
    page_number: int
    width: float | None = None
    height: float | None = None
    image_path: str | None = None
    line_count: int = 0
    word_count: int = 0
    confidence_avg: float = 0.0


class OcrEvidencePackage(BaseModel):
    package_version: str = "1.0"
    source_file_name: str
    status: str = "succeeded"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    ocr_engines_used: list[str] = Field(default_factory=list)
    pages: list[OcrPage] = Field(default_factory=list)
    lines: list[OcrLine] = Field(default_factory=list)
    words: list[OcrWord] = Field(default_factory=list)
    blocks: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
