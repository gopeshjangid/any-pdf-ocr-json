"""Pydantic schemas for review item export (Part 8)."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionSourceTrace,
    ValidationStatus,
)


class ReviewExportItem(BaseModel):
    """Read-only review export record for a flagged final question item."""

    question_id: str
    question_number: int | None = None
    validation_status: ValidationStatus
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)
    source_trace: FinalQuestionSourceTrace
    has_answer: bool = False
    has_solution: bool = False
    has_assets: bool = False
    answer_key: str | None = None
    raw_text_preview: str
    question_text_preview: str
    option_count: int = 0
    asset_count: int = 0
    review_reason: str
    recommended_action: str


class ReviewExportReport(BaseModel):
    """Complete review export artifact."""

    package_dir: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    total_final_questions: int = 0
    review_item_count: int = 0
    include_validated: bool = False
    status_counts: dict[str, int] = Field(default_factory=dict)
    reason_counts: dict[str, int] = Field(default_factory=dict)
    items: list[ReviewExportItem] = Field(default_factory=list)
