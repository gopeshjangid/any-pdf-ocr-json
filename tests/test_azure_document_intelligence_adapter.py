"""Tests for Azure Document Intelligence evidence adapter (mocked client)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.config import (
    AZURE_DI_CONTENT_MD_NAME,
    AZURE_DI_DIR,
    AZURE_DI_EXTRACTION_LOG_NAME,
    AZURE_DI_FIGURES_NAME,
    AZURE_DI_LAYOUT_RESPONSE_NAME,
    AZURE_DI_LINES_NAME,
    AZURE_DI_PAGES_NAME,
    AZURE_DI_PARAGRAPHS_NAME,
    AZURE_DI_TABLES_NAME,
    EXTRACTORS_DIR,
    EXTRACTION_PACKAGE_DIR,
)
from meritranker_data_ingestion.schemas.extractor import ExtractorRunStatus
from meritranker_data_ingestion.services.azure_di_client import AzureDiClientError
from meritranker_data_ingestion.services.azure_document_intelligence_adapter import (
    AzureDocumentIntelligenceAdapter,
    normalize_azure_di_artifacts,
)
from meritranker_data_ingestion.services.extraction_package import build_package_paths


class FakeAzureDiClient:
    """Injected Azure DI client for tests."""

    def __init__(self, payload: dict | None = None, *, error: Exception | None = None) -> None:
        self.payload = payload or _sample_layout_response()
        self.error = error
        self.calls: list[tuple[Path, str]] = []

    def analyze_layout(self, pdf_path: Path, *, model_id: str) -> dict:
        self.calls.append((pdf_path, model_id))
        if self.error is not None:
            raise self.error
        return self.payload


def _sample_layout_response() -> dict:
    return {
        "content": "Question 1\n(A) one\n(B) two",
        "pages": [
            {
                "pageNumber": 1,
                "width": 8.5,
                "height": 11,
                "lines": [
                    {"content": "Question 1", "polygon": [0, 0, 1, 1]},
                    {"content": "(A) one", "polygon": [0, 1, 1, 2]},
                ],
            },
            {
                "pageNumber": 2,
                "width": 8.5,
                "height": 11,
                "lines": [
                    {"content": "Scanned line", "polygon": [0, 0, 1, 1]},
                ],
            },
        ],
        "tables": [
            {
                "rowCount": 2,
                "columnCount": 2,
                "cells": [{"content": "A1"}],
            },
        ],
        "paragraphs": [{"content": "Paragraph block", "role": "body"}],
        "figures": [{"id": "fig-1", "boundingRegions": []}],
    }


def _pdf_and_paths(tmp_path: Path) -> tuple[Path, object]:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    pdf = tmp_path / "exam.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\n%%EOF\n")
    paths = build_package_paths(output_dir)
    paths.azure_di.azure_di_dir.mkdir(parents=True, exist_ok=True)
    return pdf, paths


def test_normalize_azure_di_artifacts_flattens_lines() -> None:
    normalized = normalize_azure_di_artifacts(_sample_layout_response())
    assert len(normalized["pages"]) == 2
    assert len(normalized["lines"]) == 3
    assert normalized["lines"][0]["pageNumber"] == 1
    assert normalized["tables"][0]["rowCount"] == 2
    assert normalized["figures"][0]["id"] == "fig-1"


def test_azure_adapter_success_writes_artifacts(tmp_path: Path) -> None:
    pdf, paths = _pdf_and_paths(tmp_path)
    client = FakeAzureDiClient()
    adapter = AzureDocumentIntelligenceAdapter(client=client, endpoint="https://example", api_key="key")

    result = adapter.extract(pdf, paths)

    azure_dir = paths.output_dir / EXTRACTION_PACKAGE_DIR / EXTRACTORS_DIR / AZURE_DI_DIR
    assert result.status == ExtractorRunStatus.SUCCEEDED
    assert (azure_dir / AZURE_DI_LAYOUT_RESPONSE_NAME).exists()
    assert (azure_dir / AZURE_DI_CONTENT_MD_NAME).read_text(encoding="utf-8").startswith("Question 1")
    assert json.loads((azure_dir / AZURE_DI_PAGES_NAME).read_text(encoding="utf-8"))
    assert len(json.loads((azure_dir / AZURE_DI_LINES_NAME).read_text(encoding="utf-8"))) == 3
    assert len(json.loads((azure_dir / AZURE_DI_TABLES_NAME).read_text(encoding="utf-8"))) == 1
    assert len(json.loads((azure_dir / AZURE_DI_FIGURES_NAME).read_text(encoding="utf-8"))) == 1
    assert len(json.loads((azure_dir / AZURE_DI_PARAGRAPHS_NAME).read_text(encoding="utf-8"))) == 1
    log = json.loads((azure_dir / AZURE_DI_EXTRACTION_LOG_NAME).read_text(encoding="utf-8"))
    assert log["status"] == "succeeded"
    assert log["line_count"] == 3


def test_azure_adapter_failure_writes_failed_log(tmp_path: Path) -> None:
    pdf, paths = _pdf_and_paths(tmp_path)
    client = FakeAzureDiClient(error=RuntimeError("service unavailable"))
    adapter = AzureDocumentIntelligenceAdapter(client=client, endpoint="https://example", api_key="key")

    result = adapter.extract(pdf, paths)

    azure_dir = paths.output_dir / EXTRACTION_PACKAGE_DIR / EXTRACTORS_DIR / AZURE_DI_DIR
    assert result.status == ExtractorRunStatus.FAILED
    assert any("service unavailable" in err for err in result.errors)
    log = json.loads((azure_dir / AZURE_DI_EXTRACTION_LOG_NAME).read_text(encoding="utf-8"))
    assert log["status"] == "failed"


def test_azure_adapter_missing_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", raising=False)
    pdf, paths = _pdf_and_paths(tmp_path)
    adapter = AzureDocumentIntelligenceAdapter(client=FakeAzureDiClient())

    result = adapter.extract(pdf, paths)

    assert result.status == ExtractorRunStatus.FAILED
    assert any("Missing Azure Document Intelligence credentials" in err for err in result.errors)


def test_azure_adapter_client_error_without_real_call(tmp_path: Path) -> None:
    pdf, paths = _pdf_and_paths(tmp_path)

    class ExplodingClient:
        def analyze_layout(self, pdf_path: Path, *, model_id: str) -> dict:
            raise AzureDiClientError("boom")

    adapter = AzureDocumentIntelligenceAdapter(
        client=ExplodingClient(),
        endpoint="https://example",
        api_key="key",
    )
    result = adapter.extract(pdf, paths)
    assert result.status == ExtractorRunStatus.FAILED
    assert result.errors == ["boom"]
