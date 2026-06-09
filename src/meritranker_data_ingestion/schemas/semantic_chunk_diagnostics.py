"""Per-chunk semantic binding diagnostics (Part 13I)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class ChunkDiagnosticStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"


class SemanticChunkDiagnostic(BaseModel):
    chunk_id: str
    chunk_index: int
    line_start_id: str | None = None
    line_end_id: str | None = None
    evidence_line_count: int = 0
    requested_question_range: str | None = None
    returned_item_count: int = 0
    returned_question_numbers: list[int] = Field(default_factory=list)
    returned_semantic_question_ids: list[str] = Field(default_factory=list)
    non_numeric_question_items: list[str] = Field(default_factory=list)
    out_of_range_question_numbers: list[int] = Field(default_factory=list)
    duplicate_question_numbers: list[int] = Field(default_factory=list)
    suspected_noise_items: list[str] = Field(default_factory=list)
    validation_issue_counts: dict[str, int] = Field(default_factory=dict)
    raw_response_saved: bool = False
    prompt_saved: bool = False
    cache_hit: bool = False
    status: ChunkDiagnosticStatus = ChunkDiagnosticStatus.OK
    error: str | None = None


class SemanticChunkDiagnosticAggregate(BaseModel):
    total_returned_items: int = 0
    expected_count: int | None = None
    count_match: bool | None = None
    extra_item_count: int = 0
    missing_question_numbers: list[int] = Field(default_factory=list)
    duplicate_question_numbers: list[int] = Field(default_factory=list)
    non_numeric_item_count: int = 0
    hallucination_suspected_count: int = 0
    chunks_requiring_replay: list[str] = Field(default_factory=list)


class SemanticChunkDiagnosticPackage(BaseModel):
    package_version: str = "1.0"
    source_file_name: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str = ""
    model: str = ""
    expected_count: int | None = None
    total_chunks: int = 0
    chunks: list[SemanticChunkDiagnostic] = Field(default_factory=list)
    aggregate: SemanticChunkDiagnosticAggregate = Field(
        default_factory=SemanticChunkDiagnosticAggregate,
    )
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SemanticChunkOutputRecord(BaseModel):
    """Compact per-chunk bind output stored under semantic-binding/chunks/."""

    chunk_id: str
    chunk_index: int
    line_start_id: str | None = None
    line_end_id: str | None = None
    evidence_line_count: int = 0
    returned_item_count: int = 0
    returned_question_numbers: list[int] = Field(default_factory=list)
    returned_semantic_question_ids: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    status: ChunkDiagnosticStatus = ChunkDiagnosticStatus.OK
    validation_summary: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    cache_hit: bool = False


class SemanticChunkReplayPlanItem(BaseModel):
    chunk_id: str
    chunk_index: int
    reasons: list[str] = Field(default_factory=list)
    will_execute: bool = False


class SemanticChunkReplayPlan(BaseModel):
    package_version: str = "1.0"
    source_file_name: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    dry_run: bool = True
    suspicious_chunk_count: int = 0
    chunks: list[SemanticChunkReplayPlanItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
