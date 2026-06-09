"""Parser adapter protocol — isolates PDF extractors behind a stable interface."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from meritranker_data_ingestion.schemas.extraction import ExtractionPackageManifest


@runtime_checkable
class ParserAdapter(Protocol):
    """Contract for PDF-to-extraction-package converters."""

    @property
    def name(self) -> str:
        """Human-readable parser engine identifier."""
        ...

    def extract(
        self,
        input_pdf_path: Path,
        output_dir: Path,
    ) -> ExtractionPackageManifest:
        """
        Run extraction and return an updated manifest.

        Implementations must write artifacts under output_dir only.
        """
        ...
