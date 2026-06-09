"""Source-grounded semantic binding schemas (Part 13C)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from meritranker_data_ingestion.schemas.document_evidence import SourceSpan


class SemanticBinderAnswerMode(str, Enum):
    """Answer/solution expectations for semantic binding validation."""

    REQUIRED = "required"
    ANSWER_KEY_ONLY = "answer-key-only"
    QUESTION_ONLY = "question-only"
    OPTIONAL = "optional"
    AUTO = "auto"


class SemanticBindingStatus(str, Enum):
    """Overall semantic binding package status."""

    SUCCEEDED = "succeeded"
    WARNING = "warning"
    FAILED = "failed"


class SemanticBindingItemStatus(str, Enum):
    """Per-question binding outcome after validation."""

    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


class SemanticVisualRoleHint(str, Enum):
    """Visual reference role — no image content interpretation."""

    QUESTION_IMAGE = "question_image"
    OPTION_IMAGE = "option_image"
    SUPPORT_IMAGE = "support_image"
    UNKNOWN = "unknown"


class SemanticBoundOption(BaseModel):
    key: str
    key_raw: str
    text_raw: str
    asset_refs: list[str] = Field(default_factory=list)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    option_source_window_id: str | None = None
    option_source_line_id: str | None = None
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)


class SemanticBoundAnswer(BaseModel):
    available: bool = False
    key: str | None = None
    key_raw: str | None = None
    answer_text_raw: str | None = None
    source_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)


class SemanticBoundSolution(BaseModel):
    available: bool = False
    text_raw: str | None = None
    source_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)


class SemanticVisualReference(BaseModel):
    asset_path: str | None = None
    figure_id: str | None = None
    image_id: str | None = None
    role_hint: SemanticVisualRoleHint = SemanticVisualRoleHint.UNKNOWN
    option_key: str | None = None
    source_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)


class SemanticMetadataCandidate(BaseModel):
    key_hint: str
    value_raw: str
    source_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)


class SemanticBoundQuestion(BaseModel):
    semantic_question_id: str
    question_number: int | None = None
    question_number_raw: str | None = None
    question_text_raw: str
    raw_text: str
    options: list[SemanticBoundOption] = Field(default_factory=list)
    answer: SemanticBoundAnswer = Field(default_factory=SemanticBoundAnswer)
    solution: SemanticBoundSolution = Field(default_factory=SemanticBoundSolution)
    visual_references: list[SemanticVisualReference] = Field(default_factory=list)
    section: str | None = None
    subject: str | None = None
    metadata_refs: list[str] = Field(default_factory=list)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 0.0
    binding_status: SemanticBindingItemStatus = SemanticBindingItemStatus.REVIEW_REQUIRED
    issues: list[str] = Field(default_factory=list)
    chunk_id: str | None = None
    window_id: str | None = None
    quarantine_status: str | None = None
    excluded_from_export: bool = False
    bad_item_classes: list[str] = Field(default_factory=list)


class SemanticBindingValidationReport(BaseModel):
    total_items: int = 0
    accepted_count: int = 0
    review_required_count: int = 0
    rejected_count: int = 0
    hallucination_suspected_count: int = 0
    source_span_missing_count: int = 0
    option_key_missing_count: int = 0
    answer_key_not_in_options_count: int = 0
    duplicate_question_number_count: int = 0
    missing_question_numbers: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class AnswerKeyEvidenceCandidate(BaseModel):
    """Deterministic answer-key candidate from evidence lines."""

    question_number: int
    answer_key: str
    source_line_id: str
    source_text_raw: str
    confidence: float = 0.85
    issues: list[str] = Field(default_factory=list)


class SemanticBindingQualityThresholds(BaseModel):
    """Configurable quality thresholds for semantic binding evaluation."""

    target_expected_count_match: bool = True
    min_semantic_item_ratio: float = 0.95
    min_questions_with_4_options_ratio: float = 0.90
    min_answer_available_ratio: float = 0.90
    max_hallucination_suspected_count: int = 0
    max_source_span_missing_count: int = 0
    max_answer_key_not_in_options_ratio: float = 0.05


class DeterministicSemanticComparison(BaseModel):
    """Comparison of deterministic parser vs semantic binder outcomes."""

    deterministic_total_candidates: int | None = None
    deterministic_valid_candidates: int | None = None
    deterministic_no_options_count: int | None = None
    deterministic_eligible_count: int | None = None
    semantic_item_count: int = 0
    semantic_accepted_count: int = 0
    semantic_review_required_count: int = 0
    semantic_rejected_count: int = 0
    semantic_questions_with_4_options: int = 0
    semantic_answer_available_count: int = 0
    options_recovered_from_deterministic_failure: bool | None = None
    improvement_summary: str = ""


class SemanticBindingEvaluationReport(BaseModel):
    """Evaluation metrics for semantic binding quality."""

    expected_count: int | None = None
    semantic_item_count: int = 0
    accepted_count: int = 0
    review_required_count: int = 0
    rejected_count: int = 0
    questions_with_options_count: int = 0
    questions_with_4_options_count: int = 0
    answer_available_count: int = 0
    solution_available_count: int = 0
    answer_key_not_in_options_count: int = 0
    source_span_missing_count: int = 0
    hallucination_suspected_count: int = 0
    duplicate_question_numbers: list[int] = Field(default_factory=list)
    missing_question_numbers: list[int] = Field(default_factory=list)
    noise_in_question_text_count: int = 0
    visual_reference_count: int = 0
    provider: str = ""
    model: str = ""
    estimated_chunk_count: int = 0
    estimated_prompt_chars: int = 0
    cache_hit: bool = False
    quality_status: str = "warning"
    thresholds: SemanticBindingQualityThresholds = Field(
        default_factory=SemanticBindingQualityThresholds,
    )
    threshold_violations: list[str] = Field(default_factory=list)
    deterministic_comparison: DeterministicSemanticComparison | None = None
    top_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SemanticBindingPackage(BaseModel):
    package_version: str
    source_file_name: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    binder_provider: str
    binder_model: str
    answer_mode: SemanticBinderAnswerMode
    input_evidence_hash: str
    status: SemanticBindingStatus = SemanticBindingStatus.SUCCEEDED
    metadata_candidates: list[SemanticMetadataCandidate] = Field(default_factory=list)
    items: list[SemanticBoundQuestion] = Field(default_factory=list)
    validation_summary: SemanticBindingValidationReport = Field(
        default_factory=SemanticBindingValidationReport,
    )
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    source_artifact_paths: dict[str, str | None] = Field(default_factory=dict)
