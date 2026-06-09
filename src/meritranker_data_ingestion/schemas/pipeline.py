"""Pydantic schemas for pipeline orchestration (Part 8)."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class PipelineStageStatus(str, Enum):
    """Status for a single pipeline stage."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStageResult(BaseModel):
    """Result of one pipeline stage."""

    stage: str
    status: PipelineStageStatus
    summary: dict = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class PipelineRunStatus(str, Enum):
    """Overall pipeline run outcome."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    WARNING = "warning"


class PipelineRunResult(BaseModel):
    """Complete pipeline run result."""

    status: PipelineRunStatus
    package_dir: str
    output_dir: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    stages: list[PipelineStageResult] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
