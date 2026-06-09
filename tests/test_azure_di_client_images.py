"""Azure DI client image upload behavior (Part 14D)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from meritranker_data_ingestion.services.azure_di_client import (
    AzureDiConfig,
    SdkAzureDiClient,
)


def test_image_bytes_sent_raw_not_json_wrapped() -> None:
    config = AzureDiConfig(endpoint="https://example.cognitiveservices.azure.com", api_key="secret", model_id="prebuilt-layout")
    client = SdkAzureDiClient(config)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    mock_poller = MagicMock()
    mock_poller.result.return_value = MagicMock(as_dict=lambda: {"pages": []})

    with patch(
        "azure.ai.documentintelligence.DocumentIntelligenceClient",
    ) as mock_cls:
        mock_cls.return_value.begin_analyze_document.return_value = mock_poller
        client.analyze_document_bytes(png_bytes, model_id="prebuilt-layout", content_type="image/png")

    call_args = mock_cls.return_value.begin_analyze_document.call_args
    body = call_args.args[1]
    assert body == png_bytes
    assert call_args.kwargs.get("content_type") is None
