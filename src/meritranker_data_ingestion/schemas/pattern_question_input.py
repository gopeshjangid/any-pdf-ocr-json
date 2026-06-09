"""Pydantic schemas for pattern-ingestion handoff package (Part 12)."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from meritranker_data_ingestion.schemas.ingestion_eligibility import AnswerMode
from meritranker_data_ingestion.schemas.question_candidates import AssetRole


class PatternExportMode(str, Enum):
    """Filter mode for pattern input export."""

    ELIGIBLE_ONLY = "eligible-only"
    INCLUDE_REVIEW = "include-review"
    INCLUDE_BLOCKED = "include-blocked"
    ALL = "all"


class PatternIngestionAction(str, Enum):
    """Recommended action for downstream pattern ingestion."""

    READY_FOR_PATTERN_INGESTION = "ready_for_pattern_ingestion"
    HOLD_FOR_REVIEW = "hold_for_review"
    BLOCKED_DO_NOT_INGEST = "blocked_do_not_ingest"


class PatternQuestionInputSourceTrace(BaseModel):
    """Source trace copied verbatim from final package."""

    start_line: int
    end_line: int
    page_start: int | None = None
    page_end: int | None = None
    line_numbers: list[int] = Field(default_factory=list)


class PatternQuestionInputOption(BaseModel):
    """Option copied from final package — no text rewriting."""

    key: str | None = None
    key_raw: str | None = None
    text_raw: str
    linked_asset_paths: list[str] = Field(default_factory=list)
    source_trace: PatternQuestionInputSourceTrace
    issues: list[str] = Field(default_factory=list)


class PatternQuestionInputAnswer(BaseModel):
    """Answer copied from final/mapping — no inference."""

    available: bool = False
    key: str | None = None
    key_raw: str | None = None
    source_text_raw: str | None = None
    source_line: int | None = None
    issues: list[str] = Field(default_factory=list)


class PatternQuestionInputSolution(BaseModel):
    """Solution copied from final/mapping — no rewriting."""

    available: bool = False
    text_raw: str | None = None
    source_trace: PatternQuestionInputSourceTrace | None = None
    image_references: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class PatternQuestionInputAsset(BaseModel):
    """Visual asset reference preserved from final package."""

    asset_path: str | None = None
    role: AssetRole = AssetRole.UNKNOWN
    option_key: str | None = None
    line_number: int
    issues: list[str] = Field(default_factory=list)


class PatternQuestionInputItem(BaseModel):
    """One source-faithful handoff item for future pattern ingestion."""

    input_id: str
    question_id: str
    question_number: int | None = None
    source_order: int
    eligibility_status: str
    ingestion_action: PatternIngestionAction
    question_text_raw: str
    raw_text: str
    options: list[PatternQuestionInputOption] = Field(default_factory=list)
    answer: PatternQuestionInputAnswer
    solution: PatternQuestionInputSolution
    visual_assets: list[PatternQuestionInputAsset] = Field(default_factory=list)
    source_trace: PatternQuestionInputSourceTrace
    mapping_status: str | None = None
    validation_status: str
    review_reasons: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    eligibility_reasons: list[str] = Field(default_factory=list)
    audit_flags: list[str] = Field(default_factory=list)
    source_refs: dict[str, str] = Field(default_factory=dict)


class PatternQuestionInputPackage(BaseModel):
    """Complete handoff package — does not perform pattern ingestion."""

    package_version: str = "1.0.0"
    source_file_name: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    answer_mode: AnswerMode = AnswerMode.REQUIRED
    quality_gate_status: str | None = None
    total_source_questions: int = 0
    exported_count: int = 0
    export_mode: PatternExportMode = PatternExportMode.ELIGIBLE_ONLY
    items: list[PatternQuestionInputItem] = Field(default_factory=list)
    source_artifact_paths: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PatternInputBuildStatus(str, Enum):
    """Overall pattern input build outcome."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PatternQuestionInputBuildResult(BaseModel):
    """Result of pattern input build including split outputs."""

    status: PatternInputBuildStatus
    package: PatternQuestionInputPackage
    eligible_items: list[PatternQuestionInputItem] = Field(default_factory=list)
    review_items: list[PatternQuestionInputItem] = Field(default_factory=list)
    blocked_items: list[PatternQuestionInputItem] = Field(default_factory=list)
    output_paths: dict[str, str] = Field(default_factory=dict)
