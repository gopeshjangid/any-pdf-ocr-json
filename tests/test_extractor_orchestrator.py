"""Tests for evidence extractor orchestration (mocked Marker and Azure DI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.config import (
    EXTRACTOR_MANIFEST_NAME,
    EXTRACTORS_DIR,
    EXTRACTION_PACKAGE_DIR,
    PACKAGE_MANIFEST_NAME,
    RAW_MARKDOWN_NAME,
)
from meritranker_data_ingestion.schemas.extraction import ExtractionPackageManifest, ExtractionStatus
from meritranker_data_ingestion.schemas.extractor import ExtractorRunStatus, ExtractorType
from meritranker_data_ingestion.services.azure_document_intelligence_adapter import (
    AzureDiExtractionResult,
    AzureDocumentIntelligenceAdapter,
)
from meritranker_data_ingestion.services.extractor_orchestrator import (
    ExtractorOrchestrator,
    PrepareError,
)
from meritranker_data_ingestion.services.marker_adapter import MarkerAdapter
from meritranker_data_ingestion.services.marker_runner import MarkerRunResult


class FakeMarkerRunner:
    def run(self, command: list[str], *, log_path: Path) -> MarkerRunResult:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("log\n", encoding="utf-8")
        work_dir = Path(command[-1])
        out = work_dir / "original"
        out.mkdir(parents=True)
        (out / "original.md").write_text("# marker md\n", encoding="utf-8")
        return MarkerRunResult(returncode=0, stdout="", stderr="", command=command)


class FakeAzureAdapter:
    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed

    def extract(self, source_pdf: Path, paths, *, model_id: str | None = None) -> AzureDiExtractionResult:
        azure_dir = paths.azure_di.azure_di_dir
        azure_dir.mkdir(parents=True, exist_ok=True)
        layout = azure_dir / "layout-response.json"
        layout.write_text('{"content": "azure text"}', encoding="utf-8")
        if not self.succeed:
            return AzureDiExtractionResult(
                status=ExtractorRunStatus.FAILED,
                model_id="prebuilt-layout",
                artifact_paths={"extraction_log": str(azure_dir / "extraction-log.json")},
                errors=["azure failed"],
                warnings=[],
            )
        return AzureDiExtractionResult(
            status=ExtractorRunStatus.SUCCEEDED,
            model_id="prebuilt-layout",
            artifact_paths={"layout_response": str(layout)},
            errors=[],
            warnings=[],
            page_count=1,
        )


def _pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "exam.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\n%%EOF\n")
    return pdf


def test_orchestrator_marker_default(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    orchestrator = ExtractorOrchestrator(
        marker_adapter=MarkerAdapter(runner=FakeMarkerRunner()),
        azure_adapter=FakeAzureAdapter(),
    )

    result = orchestrator.prepare(_pdf(tmp_path), output_dir, extractor=ExtractorType.MARKER)

    package = output_dir / EXTRACTION_PACKAGE_DIR
    assert result.succeeded
    assert result.package_manifest is not None
    assert result.package_manifest.status == ExtractionStatus.SUCCEEDED
    assert (package / "marker" / RAW_MARKDOWN_NAME).exists()
    assert (package / PACKAGE_MANIFEST_NAME).exists()
    manifest = json.loads((package / EXTRACTORS_DIR / EXTRACTOR_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["marker_status"] == "succeeded"
    assert manifest["azure_di_status"] == "skipped"
    assert manifest["extractors_run"] == ["marker"]


def test_orchestrator_azure_di_only(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    orchestrator = ExtractorOrchestrator(
        marker_adapter=MarkerAdapter(runner=FakeMarkerRunner()),
        azure_adapter=FakeAzureAdapter(),
    )

    result = orchestrator.prepare(_pdf(tmp_path), output_dir, extractor=ExtractorType.AZURE_DI)

    package = output_dir / EXTRACTION_PACKAGE_DIR
    assert result.succeeded
    assert result.package_manifest is None
    assert (package / EXTRACTORS_DIR / "azure-di" / "layout-response.json").exists()
    manifest = json.loads((package / EXTRACTORS_DIR / EXTRACTOR_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["azure_di_status"] == "succeeded"
    assert manifest["marker_status"] == "skipped"


def test_orchestrator_both_writes_marker_and_azure(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    orchestrator = ExtractorOrchestrator(
        marker_adapter=MarkerAdapter(runner=FakeMarkerRunner()),
        azure_adapter=FakeAzureAdapter(),
    )

    result = orchestrator.prepare(_pdf(tmp_path), output_dir, extractor=ExtractorType.BOTH)

    package = output_dir / EXTRACTION_PACKAGE_DIR
    assert result.succeeded
    assert (package / "marker" / RAW_MARKDOWN_NAME).exists()
    assert (package / EXTRACTORS_DIR / "azure-di" / "layout-response.json").exists()
    manifest = json.loads((package / EXTRACTORS_DIR / EXTRACTOR_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["extractors_run"] == ["marker", "azure-di"]
    assert manifest["marker_status"] == "succeeded"
    assert manifest["azure_di_status"] == "succeeded"


def test_orchestrator_both_partial_when_azure_fails(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    orchestrator = ExtractorOrchestrator(
        marker_adapter=MarkerAdapter(runner=FakeMarkerRunner()),
        azure_adapter=FakeAzureAdapter(succeed=False),
    )

    result = orchestrator.prepare(_pdf(tmp_path), output_dir, extractor=ExtractorType.BOTH)

    assert result.succeeded
    assert result.partial
    assert "azure_di_failed_marker_available" in result.extractor_manifest.warnings
    assert (output_dir / EXTRACTION_PACKAGE_DIR / "marker" / RAW_MARKDOWN_NAME).exists()


def test_orchestrator_refuses_existing_package_without_force(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    package_dir = output_dir / EXTRACTION_PACKAGE_DIR
    package_dir.mkdir(parents=True)
    orchestrator = ExtractorOrchestrator(
        marker_adapter=MarkerAdapter(runner=FakeMarkerRunner()),
        azure_adapter=FakeAzureAdapter(),
    )

    with pytest.raises(PrepareError, match="already exists"):
        orchestrator.prepare(_pdf(tmp_path), output_dir, extractor=ExtractorType.MARKER)
