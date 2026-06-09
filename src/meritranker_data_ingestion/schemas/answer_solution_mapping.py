"""Pydantic schemas for deterministic answer/solution mapping (Part 5)."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class MappingStatus(str, Enum):
    """Mapping status for a question candidate."""

    MAPPED = "mapped"
    ANSWER_ONLY_MAPPED = "answer_only_mapped"
    SOLUTION_ONLY_MAPPED = "solution_only_mapped"
    NOT_AVAILABLE = "not_available"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE_CONFLICT = "duplicate_conflict"


class MapperStatus(str, Enum):
    """Overall answer/solution mapping run status."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AnswerCandidate(BaseModel):
    """Explicit answer key extracted from source text."""

    question_number: int
    question_number_raw: str | None = None
    answer_key: str | None = None
    answer_key_raw: str | None = None
    source_line: int
    source_text_raw: str
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class SolutionCandidate(BaseModel):
    """Explicit solution block extracted from source text."""

    question_number: int
    question_number_raw: str | None = None
    raw_text: str
    start_line: int
    end_line: int
    page_start: int | None = None
    page_end: int | None = None
    line_numbers: list[int] = Field(default_factory=list)
    image_references: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class QuestionAnswerSolutionMapping(BaseModel):
    """Answer/solution mapping for one question candidate."""

    question_id: str
    question_number: int | None = None
    answer_available: bool = False
    answer: AnswerCandidate | None = None
    solution_available: bool = False
    solution: SolutionCandidate | None = None
    mapping_status: MappingStatus
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class CandidateWithMapping(BaseModel):
    """Question candidate plus mapping — not final ingestion JSON."""

    question_id: str
    question_number: int | None = None
    mapping: QuestionAnswerSolutionMapping


class AnswerSolutionMappingResult(BaseModel):
    """Full mapping output for an extraction package."""

    package_dir: str
    status: MapperStatus
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    mappings: list[QuestionAnswerSolutionMapping] = Field(default_factory=list)
    total_question_candidates: int = 0
    answers_detected: int = 0
    solutions_detected: int = 0
    mapped_count: int = 0
    answer_only_count: int = 0
    solution_only_count: int = 0
    not_available_count: int = 0
    needs_review_count: int = 0
    duplicate_solution_numbers: list[int] = Field(default_factory=list)
    unmatched_solution_numbers: list[int] = Field(default_factory=list)
    candidates_without_answers: int = 0
    candidates_without_solutions: int = 0
    content_lines_used: bool = False
    line_source_path: str | None = None
    mapping_source: str | None = None
    solution_anchor_count_seen_by_mapper: int = 0
    answer_candidate_count: int = 0
    solution_candidate_count: int = 0
    first_solution_anchor_line: int | None = None
    multi_anchor_solution_lines_count: int = 0
    solution_segments_created_from_splits: int = 0
    suspicious_merged_solution_count: int = 0
    unmapped_question_numbers: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
