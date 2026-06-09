"""Extraction package manifest schema — stable contract for pipeline stages."""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class ExtractionStatus(str, Enum):
    """Lifecycle status of an extraction package."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExtractionPackageManifest(BaseModel):
    """
    Manifest describing an extraction package on disk.

    Part 1: metadata and paths only. Markdown, assets, and page_count are
    populated in Part 2 when Marker extraction runs.
    """

    input_pdf_path: Path
    source_file_name: str
    output_dir: Path
    parser_engine: str
    status: ExtractionStatus = ExtractionStatus.PENDING
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    markdown_path: Path | None = None
    assets_dir: Path | None = None
    page_count: int | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def write_json(self, path: Path | None = None) -> Path:
        """Serialize manifest to JSON. Defaults to output_dir/manifest filename."""
        target = path or (self.output_dir / "extraction-manifest.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return target
