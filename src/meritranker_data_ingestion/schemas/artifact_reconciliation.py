"""Pydantic schemas for cross-artifact reconciliation (Part 11)."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReconciliationSeverity(str, Enum):
    """Severity for a single reconciliation check."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class QualityGateStatus(str, Enum):
    """Overall package quality gate outcome."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class ReconciliationCheck(BaseModel):
    """One deterministic reconciliation check result."""

    check_id: str
    category: str
    severity: ReconciliationSeverity
    message: str
    expected: Any | None = None
    actual: Any | None = None


class ArtifactReconciliationSummary(BaseModel):
    """Compact batch-ready reconciliation summary."""

    source_file_name: str | None = None
    total_questions: int = 0
    expected_count_match: bool | None = None
    mapped_count: int | None = None
    eligible_count: int | None = None
    review_required_count: int | None = None
    blocked_count: int | None = None
    quality_gate_status: QualityGateStatus
    failed_check_count: int = 0
    warning_count: int = 0
    top_issue_counts: dict[str, int] = Field(default_factory=dict)


class ArtifactReconciliationReport(BaseModel):
    """Full artifact reconciliation report for an extraction package."""

    package_dir: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    quality_gate_status: QualityGateStatus
    failed_check_count: int = 0
    warning_count: int = 0
    passed_check_count: int = 0
    checks: list[ReconciliationCheck] = Field(default_factory=list)
    summary: ArtifactReconciliationSummary
    eligibility_built: bool = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
