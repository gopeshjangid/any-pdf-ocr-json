"""Pydantic schemas for deterministic question candidate parsing (Part 4)."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class AssetRole(str, Enum):
    """Role of an image reference within a question candidate."""

    UNKNOWN = "unknown"
    QUESTION_IMAGE = "question_image"
    QUESTION_SUPPORT_IMAGE = "question_support_image"
    OPTION_IMAGE = "option_image"
    OPTION_IMAGE_CANDIDATE = "option_image_candidate"
    NOISE_CANDIDATE = "noise_candidate"


class CandidateReviewStatus(str, Enum):
    """Review status for a question candidate shell."""

    CANDIDATE_VALID = "candidate_valid"
    CANDIDATE_NEEDS_REVIEW = "candidate_needs_review"
    CANDIDATE_INCOMPLETE = "candidate_incomplete"
    CANDIDATE_DUPLICATE = "candidate_duplicate"
    CANDIDATE_REJECTED = "candidate_rejected"


class ParseStatus(str, Enum):
    """Overall question candidate parse run status."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OptionSourceTrace(BaseModel):
    """Source provenance for an option candidate."""

    start_line: int
    end_line: int
    parent_line_number: int | None = None
    source_kind: str | None = None
    table_cell_index: int | None = None
    table_segment_index: int | None = None


class QuestionOptionCandidate(BaseModel):
    """Option candidate attached to a question shell."""

    key: str | None = None
    key_raw: str | None = None
    text_raw: str
    start_line: int
    end_line: int
    confidence: float = Field(ge=0.0, le=1.0)
    source_trace: OptionSourceTrace | None = None
    linked_asset_paths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class QuestionAssetReference(BaseModel):
    """Image reference within a question candidate."""

    raw_markdown: str
    asset_path: str | None = None
    role: AssetRole = AssetRole.UNKNOWN
    option_key: str | None = None
    line_number: int
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class QuestionSourceTrace(BaseModel):
    """Source line/page trace for a question candidate."""

    start_line: int
    end_line: int
    page_start: int | None = None
    page_end: int | None = None
    line_numbers: list[int] = Field(default_factory=list)


class QuestionCandidate(BaseModel):
    """Source-faithful question candidate shell — not final ingestion JSON."""

    question_id: str
    question_number: int | None = None
    question_number_raw: str | None = None
    raw_text: str
    question_text_raw: str
    options: list[QuestionOptionCandidate] = Field(default_factory=list)
    assets: list[QuestionAssetReference] = Field(default_factory=list)
    source_trace: QuestionSourceTrace
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: CandidateReviewStatus
    issues: list[str] = Field(default_factory=list)


class QuestionCandidateParseResult(BaseModel):
    """Full parse output for an extraction package."""

    package_dir: str
    status: ParseStatus
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    candidates: list[QuestionCandidate] = Field(default_factory=list)
    total_candidates: int = 0
    valid_candidates: int = 0
    needs_review_candidates: int = 0
    duplicate_question_numbers: list[int] = Field(default_factory=list)
    missing_question_numbers: list[int] = Field(default_factory=list)
    candidates_with_images: int = 0
    candidates_with_question_images: int = 0
    candidates_with_question_support_images: int = 0
    candidates_with_option_images: int = 0
    candidates_with_linked_option_assets: int = 0
    candidates_with_noise: int = 0
    noise_asset_count: int = 0
    candidates_with_no_options: int = 0
    candidates_with_partial_options: int = 0
    candidates_with_invalid_option_count: int = 0
    visual_dependent_count: int = 0
    visual_text_option_count: int = 0
    visual_image_option_count: int = 0
    visual_missing_option_labels_count: int = 0
    same_line_option_split_count: int = 0
    source_backed_option_labels_missing_count: int = 0
    unlabeled_visual_assets_count: int = 0
    possible_noise_asset_after_options_count: int = 0
    incomplete_candidates: int = 0
    duplicate_candidates: int = 0
    rejected_candidates: int = 0
    status_distribution: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
