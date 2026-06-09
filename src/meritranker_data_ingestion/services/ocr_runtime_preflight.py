"""OCR runtime preflight before semantic binding (Part 14B)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    AZURE_DI_ENDPOINT_ENV,
    AZURE_DI_KEY_ENV,
    OCR_AZURE_DI_ENDPOINT_ENV,
    OCR_AZURE_DI_KEY_ENV,
    OCR_DIR,
    OCR_ENGINE_LOGS_DIR,
)
from meritranker_data_ingestion.services.ocr_adapter_base import OcrEngineMode, resolve_ocr_engine_mode


class OcrPreflightError(Exception):
    """Raised when explicitly requested OCR cannot run."""


@dataclass(frozen=True)
class OcrPreflightResult:
    requested_engine: str
    effective_engine: str | None
    ocr_available: bool
    ocr_failed_reason: str | None
    warnings: list[str]
    strict_failure: bool
    azure_dependency_ok: bool
    paddle_dependency_ok: bool
    azure_config_ok: bool


def run_ocr_runtime_preflight(
    *,
    engine: str | None = None,
    azure_endpoint: str | None = None,
    azure_key: str | None = None,
) -> OcrPreflightResult:
    """Check OCR engine availability before expensive pipeline stages."""
    requested = resolve_ocr_engine_mode(engine).value
    warnings: list[str] = []

    azure_dep = _azure_dependency_available()
    paddle_dep = _paddle_dependency_available()
    endpoint = (
        azure_endpoint
        or os.environ.get(OCR_AZURE_DI_ENDPOINT_ENV)
        or os.environ.get(AZURE_DI_ENDPOINT_ENV)
        or ""
    ).strip()
    key = (
        azure_key
        or os.environ.get(OCR_AZURE_DI_KEY_ENV)
        or os.environ.get(AZURE_DI_KEY_ENV)
        or ""
    ).strip()
    azure_config_ok = bool(endpoint and key)

    if requested == OcrEngineMode.AZURE.value:
        if not azure_dep:
            return _failure(
                requested,
                "azure_dependency_missing: install with `uv sync --extra azure --extra ocr`",
                azure_dep=False,
                paddle_dep=paddle_dep,
                azure_config_ok=azure_config_ok,
            )
        if not azure_config_ok:
            return _failure(
                requested,
                "azure_config_missing: set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and KEY",
                azure_dep=True,
                paddle_dep=paddle_dep,
                azure_config_ok=False,
            )
        return OcrPreflightResult(
            requested_engine=requested,
            effective_engine="azure_ocr",
            ocr_available=True,
            ocr_failed_reason=None,
            warnings=warnings,
            strict_failure=False,
            azure_dependency_ok=True,
            paddle_dependency_ok=paddle_dep,
            azure_config_ok=True,
        )

    if requested == OcrEngineMode.PADDLE.value:
        if not paddle_dep:
            return _failure(
                requested,
                "paddle_dependency_missing: install with `uv sync --extra paddle --extra ocr`",
                azure_dep=azure_dep,
                paddle_dep=False,
                azure_config_ok=azure_config_ok,
            )
        return OcrPreflightResult(
            requested_engine=requested,
            effective_engine="paddle_ocr",
            ocr_available=True,
            ocr_failed_reason=None,
            warnings=warnings,
            strict_failure=False,
            azure_dependency_ok=azure_dep,
            paddle_dependency_ok=True,
            azure_config_ok=azure_config_ok,
        )

    if requested == OcrEngineMode.NONE.value:
        return OcrPreflightResult(
            requested_engine=requested,
            effective_engine=None,
            ocr_available=False,
            ocr_failed_reason=None,
            warnings=["ocr_engine_disabled"],
            strict_failure=False,
            azure_dependency_ok=azure_dep,
            paddle_dependency_ok=paddle_dep,
            azure_config_ok=azure_config_ok,
        )

    if azure_dep and azure_config_ok:
        return OcrPreflightResult(
            requested_engine=requested,
            effective_engine="azure_ocr",
            ocr_available=True,
            ocr_failed_reason=None,
            warnings=warnings,
            strict_failure=False,
            azure_dependency_ok=True,
            paddle_dependency_ok=paddle_dep,
            azure_config_ok=True,
        )
    if paddle_dep:
        return OcrPreflightResult(
            requested_engine=requested,
            effective_engine="paddle_ocr",
            ocr_available=True,
            ocr_failed_reason=None,
            warnings=["azure_ocr_unavailable_using_paddle_fallback"],
            strict_failure=False,
            azure_dependency_ok=azure_dep,
            paddle_dependency_ok=True,
            azure_config_ok=azure_config_ok,
        )

    warnings.append("ocr_unavailable_marker_only_fallback")
    return OcrPreflightResult(
        requested_engine=requested,
        effective_engine=None,
        ocr_available=False,
        ocr_failed_reason="no_ocr_engine_available",
        warnings=warnings,
        strict_failure=False,
        azure_dependency_ok=azure_dep,
        paddle_dependency_ok=paddle_dep,
        azure_config_ok=azure_config_ok,
    )


def write_ocr_preflight_failure_artifact(
    package_dir: Path,
    result: OcrPreflightResult,
) -> Path:
    ocr_dir = package_dir / OCR_DIR / OCR_ENGINE_LOGS_DIR
    ocr_dir.mkdir(parents=True, exist_ok=True)
    path = ocr_dir / "ocr-preflight-failure.json"
    payload = {
        "requested_engine": result.requested_engine,
        "ocr_failed_reason": result.ocr_failed_reason,
        "action": "uv sync --extra azure --extra ocr",
        "env_vars": [
            "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
            "AZURE_DOCUMENT_INTELLIGENCE_KEY",
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def resolve_ocr_used(
    *,
    ocr_line_count: int,
    ocr_engines_used: list[str],
) -> bool:
    """True only when OCR produced at least one line (Part 14B/14D)."""
    _ = ocr_engines_used
    return ocr_line_count > 0


def _failure(
    requested: str,
    reason: str,
    *,
    azure_dep: bool,
    paddle_dep: bool,
    azure_config_ok: bool,
) -> OcrPreflightResult:
    return OcrPreflightResult(
        requested_engine=requested,
        effective_engine=None,
        ocr_available=False,
        ocr_failed_reason=reason,
        warnings=[],
        strict_failure=True,
        azure_dependency_ok=azure_dep,
        paddle_dependency_ok=paddle_dep,
        azure_config_ok=azure_config_ok,
    )


def _azure_dependency_available() -> bool:
    try:
        import azure.ai.documentintelligence  # noqa: F401
        return True
    except ImportError:
        return False


def _paddle_dependency_available() -> bool:
    try:
        import paddleocr  # noqa: F401
        return True
    except ImportError:
        return False
