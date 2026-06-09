"""Pydantic schemas for ingestion eligibility report (Part 9)."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from meritranker_data_ingestion.schemas.final_question_package import FinalQuestionSourceTrace


class EligibilityStatus(str, Enum):
    """Ingestion safety category for a final question item."""

    ELIGIBLE_FOR_INGESTION = "eligible_for_ingestion"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class AnswerMode(str, Enum):
    """How strictly answers/solutions are required for eligibility."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    QUESTION_ONLY = "question-only"


class EligibilityBuildStatus(str, Enum):
    """Overall eligibility build outcome."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DuplicateSafetyDecision(str, Enum):
    """Safety decision for duplicate solution sources."""

    HARMLESS_DUPLICATE_SAME_TEXT = "harmless_duplicate_same_text"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    NEEDS_REVIEW = "needs_review"


class SolutionSourceSummary(BaseModel):
    """One detected solution source for duplicate diagnostics."""

    start_line: int
    end_line: int
    line_numbers: list[int] = Field(default_factory=list)
    answer_key: str | None = None
    raw_text_preview: str = ""


class DuplicateSolutionDiagnostic(BaseModel):
    """Diagnostic for duplicated solution number sources."""

    solution_number: int
    source_count: int
    sources: list[SolutionSourceSummary] = Field(default_factory=list)
    answers_identical: bool = False
    solution_texts_identical: bool = False
    mapped_question_ids: list[str] = Field(default_factory=list)
    chosen_source_start_line: int | None = None
    safety_decision: DuplicateSafetyDecision
    recommended_action: str


class IngestionEligibilityItem(BaseModel):
    """Eligibility record for one final question item."""

    question_id: str
    question_number: int | None = None
    validation_status: str
    eligibility_status: EligibilityStatus
    eligibility_reasons: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    answer_available: bool = False
    solution_available: bool = False
    has_visual_assets: bool = False
    has_linked_option_assets: bool = False
    duplicate_solution_issue: bool = False
    source_trace: FinalQuestionSourceTrace
    recommended_action: str
    question_text_preview: str = ""


class EligibilityOutputPaths(BaseModel):
    """Paths to written eligibility artifacts."""

    report_json: str
    eligible_json: str
    review_required_json: str
    blocked_json: str
    duplicate_diagnostics_json: str
    markdown: str


class IngestionEligibilityReport(BaseModel):
    """Complete ingestion eligibility report."""

    status: EligibilityBuildStatus
    package_dir: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    answer_mode: AnswerMode = AnswerMode.REQUIRED
    total_questions: int = 0
    eligible_count: int = 0
    review_required_count: int = 0
    blocked_count: int = 0
    visual_question_count: int = 0
    visual_review_count: int = 0
    duplicate_solution_count: int = 0
    duplicate_solution_conflict_count: int = 0
    answer_option_mismatch_count: int = 0
    incomplete_count: int = 0
    missing_asset_count: int = 0
    items: list[IngestionEligibilityItem] = Field(default_factory=list)
    duplicate_diagnostics: list[DuplicateSolutionDiagnostic] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    output_paths: EligibilityOutputPaths | None = None
