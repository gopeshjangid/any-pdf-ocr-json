"""Pydantic schemas for source-faithful final question package (Part 6)."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from meritranker_data_ingestion.schemas.question_candidates import AssetRole


class ValidationStatus(str, Enum):
    """Validation status for a final question item."""

    VALIDATED = "validated"
    QUESTION_ONLY_VALIDATED = "question_only_validated"
    NEEDS_REVIEW = "needs_review"
    INCOMPLETE = "incomplete"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    REJECTED = "rejected"


class FinalizeStatus(str, Enum):
    """Overall finalization run status."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FinalQuestionSourceTrace(BaseModel):
    """Source trace preserved from candidate."""

    start_line: int
    end_line: int
    page_start: int | None = None
    page_end: int | None = None
    line_numbers: list[int] = Field(default_factory=list)


class FinalQuestionOption(BaseModel):
    """Final option — verbatim from candidate."""

    key: str | None = None
    key_raw: str | None = None
    text_raw: str
    source_trace: FinalQuestionSourceTrace
    linked_asset_paths: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class FinalQuestionAsset(BaseModel):
    """Final asset reference — verbatim from candidate."""

    raw_markdown: str
    asset_path: str | None = None
    role: AssetRole = AssetRole.UNKNOWN
    option_key: str | None = None
    line_number: int
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class FinalQuestionAnswer(BaseModel):
    """Final answer from explicit mapping — null when unavailable."""

    available: bool = False
    key: str | None = None
    key_raw: str | None = None
    source_text_raw: str | None = None
    source_line: int | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    issues: list[str] = Field(default_factory=list)


class FinalQuestionSolution(BaseModel):
    """Final solution from explicit mapping — null when unavailable."""

    available: bool = False
    text_raw: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    image_references: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    issues: list[str] = Field(default_factory=list)


class FinalQuestionItem(BaseModel):
    """Source-faithful final question item — ingestion-prep artifact."""

    question_id: str
    question_number: int | None = None
    question_number_raw: str | None = None
    question_text_raw: str
    raw_text: str
    options: list[FinalQuestionOption] = Field(default_factory=list)
    answer: FinalQuestionAnswer
    solution: FinalQuestionSolution
    assets: list[FinalQuestionAsset] = Field(default_factory=list)
    source_trace: FinalQuestionSourceTrace
    validation_status: ValidationStatus
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class FinalQuestionPackage(BaseModel):
    """Complete source-faithful question package."""

    package_version: str = "1.0.0"
    source_file_name: str | None = None
    parser_engine: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    total_questions: int = 0
    valid_questions: int = 0
    review_required_questions: int = 0
    question_only_count: int = 0
    answered_count: int = 0
    solved_count: int = 0
    visual_question_count: int = 0
    items: list[FinalQuestionItem] = Field(default_factory=list)


class FinalQuestionValidationReport(BaseModel):
    """Validation report for final question package."""

    status: FinalizeStatus
    package_version: str = "1.0.0"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    total_questions: int = 0
    validated_count: int = 0
    question_only_validated_count: int = 0
    needs_review_count: int = 0
    incomplete_count: int = 0
    duplicate_conflict_count: int = 0
    rejected_count: int = 0
    visual_question_count: int = 0
    answer_option_mismatch_count: int = 0
    missing_question_numbers: list[int] = Field(default_factory=list)
    duplicate_question_numbers: list[int] = Field(default_factory=list)
    status_distribution: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
