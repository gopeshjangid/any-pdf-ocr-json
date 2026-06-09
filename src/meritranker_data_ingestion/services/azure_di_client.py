"""Azure Document Intelligence client boundary (mockable, no secrets in logs)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class AzureDiClientError(Exception):
    """Raised when Azure DI configuration or API calls fail."""


@dataclass(frozen=True)
class AzureDiConfig:
    """Resolved Azure Document Intelligence credentials and endpoint."""

    endpoint: str
    api_key: str
    model_id: str


class AzureDiClient(Protocol):
    """Protocol for Azure Document Intelligence layout analysis."""

    def analyze_layout(self, pdf_path: Path, *, model_id: str) -> dict[str, Any]:
        """Analyze PDF with layout model and return a JSON-serializable dict."""

    def analyze_document_bytes(
        self,
        data: bytes,
        *,
        model_id: str,
        content_type: str = "application/pdf",
    ) -> dict[str, Any]:
        """Analyze document bytes (PDF page or image) and return JSON dict."""


def resolve_azure_di_config(
    *,
    endpoint: str | None,
    api_key: str | None,
    model_id: str,
) -> AzureDiConfig:
    """Resolve Azure DI config from CLI args and environment variables."""
    resolved_endpoint = (endpoint or "").strip()
    resolved_key = (api_key or "").strip()

    missing: list[str] = []
    if not resolved_endpoint:
        missing.append("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    if not resolved_key:
        missing.append("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    if missing:
        raise AzureDiClientError(
            "Missing Azure Document Intelligence credentials. "
            f"Set environment variables: {', '.join(missing)}.",
        )

    return AzureDiConfig(
        endpoint=resolved_endpoint.rstrip("/"),
        api_key=resolved_key,
        model_id=model_id,
    )


class SdkAzureDiClient:
    """Azure DI client using the official SDK (lazy import)."""

    def __init__(self, config: AzureDiConfig) -> None:
        self._config = config

    def analyze_layout(self, pdf_path: Path, *, model_id: str) -> dict[str, Any]:
        return self.analyze_document_bytes(
            pdf_path.read_bytes(),
            model_id=model_id,
            content_type="application/pdf",
        )

    def analyze_document_bytes(
        self,
        data: bytes,
        *,
        model_id: str,
        content_type: str = "application/pdf",
    ) -> dict[str, Any]:
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
            from azure.core.credentials import AzureKeyCredential
        except ImportError as exc:
            raise AzureDiClientError(
                "azure-ai-documentintelligence is not installed. "
                "Install with: uv sync --extra azure",
            ) from exc

        client = DocumentIntelligenceClient(
            endpoint=self._config.endpoint,
            credential=AzureKeyCredential(self._config.api_key),
        )

        # Images must be sent as raw bytes; JSON base64 wrapper yields InvalidContent.
        if content_type.startswith("image/"):
            body: bytes | AnalyzeDocumentRequest = data
        else:
            body = AnalyzeDocumentRequest(bytes_source=data)

        poller = client.begin_analyze_document(
            model_id,
            body,
            content_type=content_type if not isinstance(body, bytes) else None,
        )
        result = poller.result()
        return _serialize_analyze_result(result)


def _serialize_analyze_result(result: Any) -> dict[str, Any]:
    """Convert SDK analyze result to a JSON-serializable dictionary."""
    if hasattr(result, "as_dict"):
        payload = result.as_dict()
    elif hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json")
    else:
        payload = json.loads(json.dumps(result, default=str))
    if not isinstance(payload, dict):
        raise AzureDiClientError("Azure DI response was not a JSON object.")
    return payload
