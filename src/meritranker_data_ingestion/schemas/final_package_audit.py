"""Pydantic schemas for final package quality audit (Part 7)."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class AuditStatus(str, Enum):
    """Overall audit outcome."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class AuditIssueSeverity(str, Enum):
    """Severity for an audit issue."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FinalPackageAuditIssue(BaseModel):
    """Single deterministic audit finding."""

    severity: AuditIssueSeverity
    issue_type: str
    question_id: str | None = None
    question_number: int | None = None
    message: str
    source_trace: dict | None = None


class FinalPackageAuditSummary(BaseModel):
    """Condensed audit metrics for quick inspection."""

    status: AuditStatus
    total_questions: int = 0
    expected_question_count: int | None = None
    expected_count_match: bool | None = None
    validated_count: int = 0
    question_only_validated_count: int = 0
    needs_review_count: int = 0
    incomplete_count: int = 0
    duplicate_conflict_count: int = 0
    visual_question_count: int = 0
    answered_count: int = 0
    solved_count: int = 0
    candidates_without_options: int = 0
    answer_option_mismatch_count: int = 0
    high_risk_count: int = 0


class FinalPackageAuditReport(BaseModel):
    """Complete quality audit report for a final question package."""

    status: AuditStatus
    source_file_name: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    total_questions: int = 0
    expected_question_count: int | None = None
    expected_count_match: bool | None = None
    validated_count: int = 0
    question_only_validated_count: int = 0
    needs_review_count: int = 0
    incomplete_count: int = 0
    duplicate_conflict_count: int = 0
    visual_question_count: int = 0
    answered_count: int = 0
    solved_count: int = 0
    candidates_without_options: int = 0
    answer_option_mismatch_count: int = 0
    missing_question_numbers: list[int] = Field(default_factory=list)
    duplicate_question_numbers: list[int] = Field(default_factory=list)
    high_risk_items: list[str] = Field(default_factory=list)
    issues: list[FinalPackageAuditIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> FinalPackageAuditSummary:
        return FinalPackageAuditSummary(
            status=self.status,
            total_questions=self.total_questions,
            expected_question_count=self.expected_question_count,
            expected_count_match=self.expected_count_match,
            validated_count=self.validated_count,
            question_only_validated_count=self.question_only_validated_count,
            needs_review_count=self.needs_review_count,
            incomplete_count=self.incomplete_count,
            duplicate_conflict_count=self.duplicate_conflict_count,
            visual_question_count=self.visual_question_count,
            answered_count=self.answered_count,
            solved_count=self.solved_count,
            candidates_without_options=self.candidates_without_options,
            answer_option_mismatch_count=self.answer_option_mismatch_count,
            high_risk_count=len(self.high_risk_items),
        )
