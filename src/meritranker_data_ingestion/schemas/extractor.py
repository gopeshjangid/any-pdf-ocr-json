"""Extractor selection and evidence manifest schemas (Part 13A)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class ExtractorType(str, Enum):
    """Supported evidence extractor modes for prepare."""

    MARKER = "marker"
    AZURE_DI = "azure-di"
    BOTH = "both"


class ExtractorRunStatus(str, Enum):
    """Per-extractor run outcome."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExtractorManifest(BaseModel):
    """Manifest describing evidence extractors run for a source PDF."""

    selected_extractor: ExtractorType
    extractors_run: list[str] = Field(default_factory=list)
    source_file_name: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    marker_status: ExtractorRunStatus = ExtractorRunStatus.SKIPPED
    azure_di_status: ExtractorRunStatus = ExtractorRunStatus.SKIPPED
    azure_di_model: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifact_paths: dict[str, str | list[str] | None] = Field(default_factory=dict)

    def write_json(self, path: Path) -> Path:
        """Serialize manifest to JSON under the extraction package."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path
