"""Canonical document evidence schemas (Part 13B — normalization only)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EvidenceExtractionStatus(str, Enum):
    """Overall normalized evidence package status."""

    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class PrimaryExtractorMode(str, Enum):
    """Primary extractor selection for normalization."""

    MARKER = "marker"
    AZURE_DI = "azure-di"
    AUTO = "auto"


class RoleHint(str, Enum):
    """Deterministic role hints — not semantic bindings."""

    QUESTION_ANCHOR_CANDIDATE = "question_anchor_candidate"
    OPTION_LABEL_CANDIDATE = "option_label_candidate"
    ANSWER_KEY_CANDIDATE = "answer_key_candidate"
    SOLUTION_HEADING_CANDIDATE = "solution_heading_candidate"
    SECTION_HEADING_CANDIDATE = "section_heading_candidate"
    METADATA_CANDIDATE = "metadata_candidate"
    INSTRUCTION_CANDIDATE = "instruction_candidate"
    ADVERTISEMENT_CANDIDATE = "advertisement_candidate"
    FOOTER_CANDIDATE = "footer_candidate"
    HEADER_CANDIDATE = "header_candidate"
    DOWNLOAD_LINK_CANDIDATE = "download_link_candidate"
    IMAGE_REFERENCE_CANDIDATE = "image_reference_candidate"
    TABLE_CANDIDATE = "table_candidate"


class SourceSpan(BaseModel):
    """Trace back to extractor artifact coordinates."""

    extractor: str
    page_number: int | None = None
    line_id: str | None = None
    source_artifact_path: str | None = None
    raw_index: int | None = None
    bbox: list[float] | None = None


class AlternateEvidence(BaseModel):
    """Attached OCR or alternate stream text — does not replace raw Marker text."""

    engine: str
    text_raw: str
    line_id: str | None = None
    confidence: float = 0.0
    bbox: list[float] | None = None


class EvidenceLine(BaseModel):
    """Normalized text line from Marker or Azure DI."""

    line_id: str
    page_number: int | None = None
    text_raw: str
    normalized_preview: str
    bbox: list[float] | None = None
    source_extractor: str
    source_artifact_path: str | None = None
    source_line_number: int | None = None
    source_span: SourceSpan | None = None
    role_hints: list[RoleHint] = Field(default_factory=list)
    confidence: float = 1.0
    issues: list[str] = Field(default_factory=list)
    alternate_evidence: list[AlternateEvidence] = Field(default_factory=list)


class EvidencePage(BaseModel):
    """Normalized page from extractor output."""

    page_id: str
    page_number: int
    width: float | None = None
    height: float | None = None
    text_raw: str | None = None
    line_ids: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    figure_ids: list[str] = Field(default_factory=list)
    image_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 1.0
    issues: list[str] = Field(default_factory=list)


class EvidenceTable(BaseModel):
    """Normalized table from Azure DI (or table-row lines from Marker)."""

    table_id: str
    page_number: int | None = None
    cells: list[dict[str, Any]] = Field(default_factory=list)
    bbox: list[float] | None = None
    source_extractor: str
    source_refs: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 1.0
    issues: list[str] = Field(default_factory=list)


class EvidenceFigure(BaseModel):
    """Normalized figure region from Azure DI."""

    figure_id: str
    page_number: int | None = None
    asset_path: str | None = None
    bbox: list[float] | None = None
    caption: str | None = None
    source_extractor: str
    source_refs: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 1.0
    issues: list[str] = Field(default_factory=list)


class EvidenceImage(BaseModel):
    """Image asset or markdown image reference."""

    image_id: str
    asset_path: str | None = None
    page_number: int | None = None
    source_extractor: str
    source_refs: list[SourceSpan] = Field(default_factory=list)
    role_hints: list[RoleHint] = Field(default_factory=list)
    confidence: float = 1.0
    issues: list[str] = Field(default_factory=list)


class MetadataCandidate(BaseModel):
    """Weak metadata candidate — not finalized."""

    key_hint: str
    value_raw: str
    source_trace: SourceSpan
    confidence: float = 0.7
    issues: list[str] = Field(default_factory=list)


class DocumentEvidencePackage(BaseModel):
    """Canonical normalized evidence for future semantic binding."""

    package_version: str
    source_file_name: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    primary_extractor: str
    extractors_available: list[str] = Field(default_factory=list)
    extractors_used: list[str] = Field(default_factory=list)
    extraction_status: EvidenceExtractionStatus
    pages: list[EvidencePage] = Field(default_factory=list)
    lines: list[EvidenceLine] = Field(default_factory=list)
    tables: list[EvidenceTable] = Field(default_factory=list)
    figures: list[EvidenceFigure] = Field(default_factory=list)
    images: list[EvidenceImage] = Field(default_factory=list)
    metadata_candidates: list[MetadataCandidate] = Field(default_factory=list)
    noise_candidates: list[EvidenceLine] = Field(default_factory=list)
    source_artifact_paths: dict[str, str | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class EvidenceSummary(BaseModel):
    """Compact summary of normalized evidence."""

    source_file_name: str
    primary_extractor: str
    extraction_status: EvidenceExtractionStatus
    marker_available: bool
    azure_di_available: bool
    page_count: int
    line_count: int
    table_count: int
    figure_count: int
    image_count: int
    role_hint_counts: dict[str, int] = Field(default_factory=dict)
    metadata_candidate_count: int = 0
    noise_candidate_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ExtractorComparison(BaseModel):
    """Side-by-side extractor statistics (no line-level merge)."""

    marker_status: str
    azure_di_status: str
    marker_line_count: int = 0
    azure_line_count: int = 0
    marker_image_count: int = 0
    azure_figure_count: int = 0
    azure_table_count: int = 0
    pages_detected: int = 0
    primary_extractor: str
    fallback_reason: str | None = None
    likely_scanned_pages: int | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
