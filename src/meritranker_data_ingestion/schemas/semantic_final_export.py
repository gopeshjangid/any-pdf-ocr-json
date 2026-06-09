"""Semantic final export schemas for pattern-ingestion preparation (Part 13H)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from meritranker_data_ingestion.schemas.document_evidence import SourceSpan
from meritranker_data_ingestion.schemas.semantic_binding import SemanticBinderAnswerMode


class SemanticFinalExportMode(str, Enum):
    ACCEPTED_ONLY = "accepted-only"
    ACCEPTED_PLUS_PATCHED = "accepted-plus-patched"
    ALL_WITH_STATUS = "all-with-status"


class SemanticFinalExportStatus(str, Enum):
    READY_FOR_PATTERN_INPUT = "ready_for_pattern_input"
    HOLD_FOR_REVIEW = "hold_for_review"
    BLOCKED = "blocked"


class FinalGateStatus(str, Enum):
    ACCEPTED_SAFE = "accepted_safe"
    REVIEW_VISUAL_REQUIRED = "review_visual_required"
    REVIEW_EVIDENCE_CORRUPT = "review_evidence_corrupt"
    REVIEW_MANUAL_PATCH_REQUIRED = "review_manual_patch_required"
    BLOCKED_BAD_ITEM = "blocked_bad_item"


class AnswerSourceKind(str, Enum):
    SOURCE_GROUNDED = "source_grounded"
    MANUAL_REVIEW = "manual_review"
    UNAVAILABLE = "unavailable"


class ProvenanceKind(str, Enum):
    SEMANTIC_BINDING = "semantic_binding"
    SEMANTIC_REPAIR = "semantic_repair"
    MANUAL_PATCH = "manual_patch"


class PatchAction(str, Enum):
    HOLD_FOR_REVIEW = "hold_for_review"
    BLOCK = "block"
    ACCEPT_WITH_MANUAL_PATCH = "accept_with_manual_patch"


class SemanticFinalOption(BaseModel):
    key: str
    key_raw: str
    text_raw: str
    linked_asset_refs: list[str] = Field(default_factory=list)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)


class SemanticFinalSourceTrace(BaseModel):
    question_line_ids: list[str] = Field(default_factory=list)
    answer_line_ids: list[str] = Field(default_factory=list)
    solution_line_ids: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)


class SemanticFinalVisualReference(BaseModel):
    asset_path: str | None = None
    figure_id: str | None = None
    image_id: str | None = None
    role_hint: str = "unknown"
    option_key: str | None = None
    source_spans: list[SourceSpan] = Field(default_factory=list)


class SemanticFinalQuestionItem(BaseModel):
    final_question_id: str
    question_number: int | None = None
    question_number_raw: str | None = None
    question_text_raw: str
    raw_text: str
    options: list[SemanticFinalOption] = Field(default_factory=list)
    correct_answer_key: str | None = None
    correct_answer_text: str | None = None
    answer_source: AnswerSourceKind = AnswerSourceKind.UNAVAILABLE
    solution_text_raw: str | None = None
    solution_available: bool = False
    visual_references: list[SemanticFinalVisualReference] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    source_trace: SemanticFinalSourceTrace = Field(default_factory=SemanticFinalSourceTrace)
    semantic_status: str = "review_required"
    final_gate_status: str | None = None
    final_gate_reasons: list[str] = Field(default_factory=list)
    export_status: SemanticFinalExportStatus = SemanticFinalExportStatus.HOLD_FOR_REVIEW
    provenance: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)
    reviewer_notes: str | None = None


class SemanticFinalExportPackage(BaseModel):
    package_version: str = "1.0"
    source_file_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY
    export_mode: SemanticFinalExportMode = SemanticFinalExportMode.ACCEPTED_ONLY
    expected_count: int | None = None
    total_semantic_items: int = 0
    semantic_item_count: int = 0
    count_match: bool | None = None
    accepted_count: int = 0
    accepted_safe_count: int = 0
    unsafe_previously_accepted_count: int = 0
    unsafe_previously_accepted_question_numbers: list[int] = Field(default_factory=list)
    review_visual_required_count: int = 0
    review_evidence_corrupt_count: int = 0
    review_manual_patch_required_count: int = 0
    blocked_bad_item_count: int = 0
    exported_count: int = 0
    accepted_exported_count: int = 0
    patched_exported_count: int = 0
    review_required_count: int = 0
    rejected_count: int = 0
    excluded_count: int = 0
    bad_item_count: int = 0
    quarantined_item_count: int = 0
    extra_excluded_count: int = 0
    hallucination_suspected_count: int = 0
    source_span_missing_count: int = 0
    answer_key_not_in_options_count: int = 0
    missing_question_numbers: list[int] = Field(default_factory=list)
    duplicate_question_numbers: list[int] = Field(default_factory=list)
    overflow_count: int = 0
    extra_item_ids: list[str] = Field(default_factory=list)
    extra_question_numbers: list[int] = Field(default_factory=list)
    non_numeric_question_ids: list[str] = Field(default_factory=list)
    out_of_range_question_numbers: list[int] = Field(default_factory=list)
    quality_status_from_semantic_evaluation: str = "warning"
    final_export_quality_status: str = "partial"
    quality_status: str = "partial"
    source_quality_status: str = "warning"
    ready_for_full_paper_ingestion: bool = False
    ready_for_partial_accepted_ingestion: bool = False
    items: list[SemanticFinalQuestionItem] = Field(default_factory=list)
    review_items_path: str | None = None
    patch_template_path: str | None = None
    validation_report_path: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SemanticFinalGateReport(BaseModel):
    total_semantic_items: int = 0
    accepted_safe_count: int = 0
    review_visual_required_count: int = 0
    review_evidence_corrupt_count: int = 0
    review_manual_patch_required_count: int = 0
    blocked_bad_item_count: int = 0
    unsafe_previously_accepted_count: int = 0
    unsafe_previously_accepted_question_numbers: list[int] = Field(default_factory=list)
    exported_count: int = 0
    excluded_count: int = 0
    ready_for_full_paper_ingestion: bool = False
    ready_for_partial_accepted_ingestion: bool = False


class SemanticReviewExportItem(BaseModel):
    patch_id: str
    question_number: int | None = None
    semantic_question_id: str
    current_status: str
    final_gate_status: str | None = None
    final_gate_reasons: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    failure_class: str | None = None
    repairability: str | None = None
    question_text_preview: str = ""
    option_keys: list[str] = Field(default_factory=list)
    answer_key: str | None = None
    nearby_evidence_excerpt: list[str] = Field(default_factory=list)
    recommended_action: str = "manual_fix"
    current_options_preview: list[str] = Field(default_factory=list)


class SemanticReviewExportReport(BaseModel):
    total_review_items: int = 0
    review_required_count: int = 0
    rejected_count: int = 0
    items: list[SemanticReviewExportItem] = Field(default_factory=list)


class SemanticPatchOptionInput(BaseModel):
    key: str
    key_raw: str | None = None
    text_raw: str
    linked_asset_refs: list[str] = Field(default_factory=list)


class SemanticPatchItemInput(BaseModel):
    patch_id: str
    question_number: int | None = None
    action: PatchAction = PatchAction.HOLD_FOR_REVIEW
    question_text_raw: str | None = None
    options: list[SemanticPatchOptionInput] = Field(default_factory=list)
    correct_answer_key: str | None = None
    solution_text_raw: str | None = None
    reviewer_notes: str = ""
    manual_source_reference: str = ""
    confirm_no_guessing: bool = False


class SemanticReviewPatchFile(BaseModel):
    package_version: str = "1.0.0"
    source_file_name: str = ""
    reviewer: str = ""
    created_at: str = ""
    patch_items: list[SemanticPatchItemInput] = Field(default_factory=list)


class SemanticAppliedPatchItem(BaseModel):
    patch_id: str
    question_number: int | None = None
    action: PatchAction
    applied: bool = False
    reviewer_notes: str = ""
    manual_source_reference: str = ""
    final_item: SemanticFinalQuestionItem | None = None
    errors: list[str] = Field(default_factory=list)


class SemanticReviewPatchAppliedReport(BaseModel):
    source_file_name: str = ""
    reviewer: str = ""
    total_patch_items: int = 0
    applied_count: int = 0
    blocked_count: int = 0
    hold_count: int = 0
    rejected_patch_count: int = 0
    items: list[SemanticAppliedPatchItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
