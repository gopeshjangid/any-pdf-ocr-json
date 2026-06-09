"""Azure Document Intelligence evidence extractor (layout/OCR only, no semantic binding)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.config import (
    AZURE_DI_ENDPOINT_ENV,
    AZURE_DI_KEY_ENV,
    DEFAULT_AZURE_DI_MODEL,
)
from meritranker_data_ingestion.schemas.extractor import ExtractorRunStatus
from meritranker_data_ingestion.services.azure_di_client import (
    AzureDiClient,
    AzureDiClientError,
    AzureDiConfig,
    SdkAzureDiClient,
    resolve_azure_di_config,
)
from meritranker_data_ingestion.services.extraction_package import (
    AzureDiArtifactPaths,
    ExtractionPackagePaths,
)
from meritranker_data_ingestion.services.file_service import assert_output_contains


@dataclass(frozen=True)
class AzureDiExtractionResult:
    """Outcome of an Azure DI evidence extraction run."""

    status: ExtractorRunStatus
    model_id: str
    artifact_paths: dict[str, str | list[str] | None]
    errors: list[str]
    warnings: list[str]
    page_count: int | None = None


def normalize_azure_di_artifacts(
    response: dict[str, Any],
) -> dict[str, Any]:
    """Split raw Azure DI layout response into normalized evidence artifacts."""
    pages = response.get("pages") or []
    if not isinstance(pages, list):
        pages = []

    lines: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_number = page.get("pageNumber")
        for line in page.get("lines") or []:
            if not isinstance(line, dict):
                continue
            entry = dict(line)
            entry["pageNumber"] = page_number
            lines.append(entry)

    tables = response.get("tables") or []
    paragraphs = response.get("paragraphs") or []
    figures = response.get("figures") or []
    content = response.get("content")

    return {
        "pages": pages,
        "lines": lines,
        "tables": tables if isinstance(tables, list) else [],
        "paragraphs": paragraphs if isinstance(paragraphs, list) else [],
        "figures": figures if isinstance(figures, list) else [],
        "content": content if isinstance(content, str) else None,
    }


class AzureDocumentIntelligenceAdapter:
    """
    Adapter for Azure Document Intelligence layout extraction.

    Persists raw layout response and normalized evidence slices. Does not infer
    questions, options, or answers.
    """

    def __init__(
        self,
        *,
        client: AzureDiClient | None = None,
        endpoint: str | None = None,
        api_key: str | None = None,
        model_id: str = DEFAULT_AZURE_DI_MODEL,
    ) -> None:
        self._client = client
        self._endpoint = endpoint
        self._api_key = api_key
        self._model_id = model_id

    @property
    def name(self) -> str:
        return "azure-di"

    def extract(
        self,
        source_pdf: Path,
        paths: ExtractionPackagePaths,
        *,
        model_id: str | None = None,
    ) -> AzureDiExtractionResult:
        resolved_model = model_id or self._model_id
        azure_paths = paths.azure_di
        errors: list[str] = []
        warnings: list[str] = []

        try:
            config = resolve_azure_di_config(
                endpoint=self._endpoint or os.environ.get(AZURE_DI_ENDPOINT_ENV),
                api_key=self._api_key or os.environ.get(AZURE_DI_KEY_ENV),
                model_id=resolved_model,
            )
            client = self._client or SdkAzureDiClient(config)
            response = client.analyze_layout(source_pdf, model_id=resolved_model)
            return self._persist_response(
                response,
                azure_paths,
                paths.output_dir,
                model_id=resolved_model,
            )
        except AzureDiClientError as exc:
            errors.append(str(exc))
            log_payload = {
                "status": ExtractorRunStatus.FAILED.value,
                "model_id": resolved_model,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "errors": errors,
                "warnings": warnings,
            }
            self._write_json(azure_paths.extraction_log, log_payload, paths.output_dir)
            return AzureDiExtractionResult(
                status=ExtractorRunStatus.FAILED,
                model_id=resolved_model,
                artifact_paths={"extraction_log": str(azure_paths.extraction_log)},
                errors=errors,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001 — surface as failed extraction
            errors.append(f"Azure DI extraction failed: {exc}")
            log_payload = {
                "status": ExtractorRunStatus.FAILED.value,
                "model_id": resolved_model,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "errors": errors,
                "warnings": warnings,
            }
            self._write_json(azure_paths.extraction_log, log_payload, paths.output_dir)
            return AzureDiExtractionResult(
                status=ExtractorRunStatus.FAILED,
                model_id=resolved_model,
                artifact_paths={"extraction_log": str(azure_paths.extraction_log)},
                errors=errors,
                warnings=warnings,
            )

    def _persist_response(
        self,
        response: dict[str, Any],
        azure_paths: AzureDiArtifactPaths,
        output_dir: Path,
        *,
        model_id: str,
    ) -> AzureDiExtractionResult:
        normalized = normalize_azure_di_artifacts(response)
        warnings: list[str] = []

        self._write_json(azure_paths.layout_response, response, output_dir)
        self._write_json(azure_paths.pages, normalized["pages"], output_dir)
        self._write_json(azure_paths.lines, normalized["lines"], output_dir)
        self._write_json(azure_paths.tables, normalized["tables"], output_dir)
        self._write_json(azure_paths.paragraphs, normalized["paragraphs"], output_dir)
        self._write_json(azure_paths.figures, normalized["figures"], output_dir)

        content = normalized["content"]
        if content:
            assert_output_contains(output_dir, azure_paths.content_md)
            azure_paths.content_md.parent.mkdir(parents=True, exist_ok=True)
            azure_paths.content_md.write_text(content, encoding="utf-8")
        else:
            warnings.append("Azure DI response did not include top-level content text.")

        page_count = len(normalized["pages"]) if normalized["pages"] else None
        log_payload = {
            "status": ExtractorRunStatus.SUCCEEDED.value,
            "model_id": model_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "page_count": page_count,
            "line_count": len(normalized["lines"]),
            "table_count": len(normalized["tables"]),
            "paragraph_count": len(normalized["paragraphs"]),
            "figure_count": len(normalized["figures"]),
            "warnings": warnings,
            "errors": [],
        }
        self._write_json(azure_paths.extraction_log, log_payload, output_dir)

        artifact_paths: dict[str, str | list[str] | None] = {
            "layout_response": str(azure_paths.layout_response),
            "content_md": str(azure_paths.content_md) if content else None,
            "pages": str(azure_paths.pages),
            "lines": str(azure_paths.lines),
            "tables": str(azure_paths.tables),
            "figures": str(azure_paths.figures),
            "paragraphs": str(azure_paths.paragraphs),
            "extraction_log": str(azure_paths.extraction_log),
        }

        return AzureDiExtractionResult(
            status=ExtractorRunStatus.SUCCEEDED,
            model_id=model_id,
            artifact_paths=artifact_paths,
            errors=[],
            warnings=warnings,
            page_count=page_count,
        )

    @staticmethod
    def _write_json(path: Path, payload: Any, output_dir: Path) -> None:
        assert_output_contains(output_dir, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
