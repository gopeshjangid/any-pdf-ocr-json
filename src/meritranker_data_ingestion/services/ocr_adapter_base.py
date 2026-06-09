"""OCR adapter protocol and engine resolution (Part 14A)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from meritranker_data_ingestion.config import (
    AZURE_DI_ENDPOINT_ENV,
    AZURE_DI_KEY_ENV,
    DEFAULT_OCR_ENGINE,
    OCR_AZURE_DI_ENDPOINT_ENV,
    OCR_AZURE_DI_KEY_ENV,
    OCR_ENGINE_ENV,
    PADDLE_OCR_LANG_ENV,
)
from meritranker_data_ingestion.schemas.ocr_evidence import OcrEvidencePackage


class OcrEngineMode(str, Enum):
    AZURE = "azure"
    PADDLE = "paddle"
    AUTO = "auto"
    NONE = "none"


class OcrAdapterError(Exception):
    """Raised when OCR adapter configuration or execution fails."""


@dataclass(frozen=True)
class OcrAdapterConfig:
    engine: OcrEngineMode
    azure_endpoint: str | None
    azure_key: str | None
    paddle_lang: str


class OcrAdapter(Protocol):
    """Protocol for OCR evidence extraction."""

    @property
    def engine_name(self) -> str:
        """Engine identifier for manifests."""

    def is_available(self) -> bool:
        """Return True when engine can run."""

    def extract(
        self,
        package_dir: Path,
        *,
        pdf_path: Path | None = None,
        page_images: list[Path] | None = None,
    ) -> OcrEvidencePackage:
        """Extract OCR evidence from PDF or page images."""


def resolve_ocr_engine_mode(engine: str | None = None) -> OcrEngineMode:
    raw = (engine or os.environ.get(OCR_ENGINE_ENV) or DEFAULT_OCR_ENGINE).strip().lower()
    return OcrEngineMode(raw)


def resolve_ocr_adapter_config(
    *,
    engine: str | None = None,
    azure_endpoint: str | None = None,
    azure_key: str | None = None,
    paddle_lang: str | None = None,
) -> OcrAdapterConfig:
    endpoint = (
        azure_endpoint
        or os.environ.get(OCR_AZURE_DI_ENDPOINT_ENV)
        or os.environ.get(AZURE_DI_ENDPOINT_ENV)
        or ""
    ).strip() or None
    key = (
        azure_key
        or os.environ.get(OCR_AZURE_DI_KEY_ENV)
        or os.environ.get(AZURE_DI_KEY_ENV)
        or ""
    ).strip() or None
    lang = (paddle_lang or os.environ.get(PADDLE_OCR_LANG_ENV) or "auto").strip()
    return OcrAdapterConfig(
        engine=resolve_ocr_engine_mode(engine),
        azure_endpoint=endpoint,
        azure_key=key,
        paddle_lang=lang,
    )


def select_ocr_adapters(config: OcrAdapterConfig) -> list[OcrAdapter]:
    """Return ordered OCR adapters to try for the given engine mode."""
    from meritranker_data_ingestion.services.azure_ocr_adapter import AzureOcrAdapter
    from meritranker_data_ingestion.services.paddle_ocr_adapter import PaddleOcrAdapter

    azure = AzureOcrAdapter(
        endpoint=config.azure_endpoint,
        api_key=config.azure_key,
    )
    paddle = PaddleOcrAdapter(lang=config.paddle_lang)

    if config.engine == OcrEngineMode.NONE:
        return []
    if config.engine == OcrEngineMode.AZURE:
        return [azure]
    if config.engine == OcrEngineMode.PADDLE:
        return [paddle]
    adapters: list[OcrAdapter] = []
    if azure.is_available():
        adapters.append(azure)
    if paddle.is_available():
        adapters.append(paddle)
    return adapters
