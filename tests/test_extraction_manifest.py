"""Tests for ExtractionPackageManifest schema."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from meritranker_data_ingestion.schemas.extraction import (
    ExtractionPackageManifest,
    ExtractionStatus,
)
from meritranker_data_ingestion.services.file_service import PathValidationError
from meritranker_data_ingestion.services.marker_adapter import MarkerAdapter
from meritranker_data_ingestion.services.marker_runner import MarkerRunResult


class _NoOpRunner:
    def run(self, command: list[str], *, log_path: Path) -> MarkerRunResult:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("log\n", encoding="utf-8")
        return MarkerRunResult(
            returncode=127,
            stdout="",
            stderr="",
            command=command,
            error_message="Marker command not found.",
        )


def test_manifest_instantiation(tmp_path: Path) -> None:
    manifest = ExtractionPackageManifest(
        input_pdf_path=tmp_path / "sample.pdf",
        source_file_name="sample.pdf",
        output_dir=tmp_path / "out",
        parser_engine="marker",
    )
    assert manifest.status == ExtractionStatus.PENDING
    assert manifest.errors == []
    assert manifest.warnings == []
    assert manifest.markdown_path is None
    assert manifest.page_count is None


def test_manifest_write_json(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    manifest = ExtractionPackageManifest(
        input_pdf_path=tmp_path / "sample.pdf",
        source_file_name="sample.pdf",
        output_dir=output_dir,
        parser_engine="marker",
    )
    path = manifest.write_json(output_dir / "manifest.json")
    assert path.exists()
    assert path.name == "manifest.json"


def test_manifest_requires_fields() -> None:
    with pytest.raises(ValidationError):
        ExtractionPackageManifest()  # type: ignore[call-arg]


def test_adapter_writes_failed_manifest_for_missing_input(tmp_path: Path) -> None:
    with pytest.raises(PathValidationError, match="does not exist"):
        MarkerAdapter(runner=_NoOpRunner()).extract(
            tmp_path / "missing.pdf",
            tmp_path / "out",
        )
