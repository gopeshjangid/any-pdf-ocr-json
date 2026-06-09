"""Unified final questions export schema (Part 14A)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from meritranker_data_ingestion.schemas.document_evidence import SourceSpan


class FinalAnswerSource(str, Enum):
    ANSWER_KEY_TABLE = "answer_key_table"
    INLINE_ANSWER = "inline_answer"
    SEPARATE_SOLUTION_SECTION = "separate_solution_section"
    VISUAL_TICK = "visual_tick"
    MANUAL_REVIEW = "manual_review"
    UNAVAILABLE = "unavailable"


class FinalQuestionQualityStatus(str, Enum):
    ACCEPTED_SAFE = "accepted_safe"
    REVIEW_REQUIRED = "review_required"
    VISUAL_REQUIRED = "visual_required"
    ANSWER_UNAVAILABLE = "answer_unavailable"
    BLOCKED = "blocked"


class FinalQuestionOption(BaseModel):
    key: str
    key_raw: str
    text_raw: str
    canonical_key: str | None = None
    option_index: int | None = None
    visual_asset_refs: list[str] = Field(default_factory=list)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    source_engine: str = "marker"
    confidence: float = 0.0


class FinalQuestionSourceTrace(BaseModel):
    question_line_ids: list[str] = Field(default_factory=list)
    answer_line_ids: list[str] = Field(default_factory=list)
    solution_line_ids: list[str] = Field(default_factory=list)
    ocr_line_ids: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)


class FinalQuestionVisual(BaseModel):
    visual_id: str
    type: str = "diagram"
    role: str = "question"
    linked_option_label: str | None = None
    description: str = ""
    extraction_status: str = "image_required"
    render_target: str | None = "canva"
    render_spec: dict | None = None
    asset_ref: str | None = None
    issues: list[str] = Field(default_factory=list)


class FinalQuestionItemMetadata(BaseModel):
    status: str = "review"
    review_issues: list[str] = Field(default_factory=list)


class FinalQuestionItem(BaseModel):
    final_question_id: str
    global_order: int
    source_question_number_raw: str | None = None
    question_number: int | None = None
    question_text_raw: str
    options: list[FinalQuestionOption] = Field(default_factory=list)
    correct_answer_key: str | None = None
    correct_answer_text: str | None = None
    answer_source: FinalAnswerSource = FinalAnswerSource.UNAVAILABLE
    chosen_option_key: str | None = None
    chosen_option_canonical_key: str | None = None
    chosen_option_source: str | None = None
    solution_text_raw: str | None = None
    solution_source: str | None = None
    visual_assets: list[str] = Field(default_factory=list)
    visuals: list[FinalQuestionVisual] = Field(default_factory=list)
    source_trace: FinalQuestionSourceTrace = Field(default_factory=FinalQuestionSourceTrace)
    quality_status: FinalQuestionQualityStatus = FinalQuestionQualityStatus.REVIEW_REQUIRED
    final_gate_status: str | None = None
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)
    metadata: FinalQuestionItemMetadata = Field(default_factory=FinalQuestionItemMetadata)


class ExtractionProfileSummary(BaseModel):
    language: str = "unknown"
    text_availability: str = "unknown"
    option_availability: str = "unknown"
    answer_source_mode: str = "unknown"
    ocr_required: bool = False
    ocr_used: bool = False
    ocr_requested_engine: str | None = None
    ocr_effective_engine: str | None = None
    ocr_available: bool = False
    ocr_failed_reason: str | None = None
    ocr_failed: bool = False
    ocr_fallback_used: bool = False
    ocr_pages_attempted: int = 0
    ocr_pages_succeeded: int = 0
    ocr_pages_failed: int = 0
    unsupported_layout_detected: bool = False
    recommended_pipeline_action: str | None = None
    vlm_required_later: bool = False
    recommended_answer_mode: str = "answer-key-only"
    chosen_option_detected: bool = False


class FinalQuestionsPackage(BaseModel):
    package_version: str = "1.0"
    source_file_name: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    extraction_profile_summary: ExtractionProfileSummary = Field(
        default_factory=ExtractionProfileSummary,
    )
    total_questions_detected: int = 0
    accepted_safe_count: int = 0
    review_required_count: int = 0
    visual_required_count: int = 0
    answer_unavailable_count: int = 0
    blocked_count: int = 0
    ocr_requested_engine: str | None = None
    ocr_effective_engine: str | None = None
    ocr_line_count: int = 0
    ocr_used: bool = False
    numeric_option_questions_count: int = 0
    question_only_items_count: int = 0
    chosen_option_detected_count: int = 0
    chosen_option_as_correct_answer_count: int = 0
    ready_count: int = 0
    review_count: int = 0
    review_items_count: int = 0
    answer_available_count: int = 0
    solution_available_count: int = 0
    incomplete_options_count: int = 0
    missing_question_count: int = 0
    answer_solution_join_gap_count: int = 0
    unsupported_layout_detected: bool = False
    experimental_low_confidence: bool = False
    items: list[FinalQuestionItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
