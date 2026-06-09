"""Build OCR evidence package for an extraction package (Part 14A + 14C)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import (
    EVIDENCE_DIR,
    OCR_DIR,
    OCR_ENGINE_LOGS_DIR,
    OCR_EVIDENCE_JSON_NAME,
    OCR_EVIDENCE_MD_NAME,
    OCR_EVIDENCE_PACKAGE_VERSION,
    OCR_PAGE_IMAGES_DIR,
    SOURCE_DIR,
    ORIGINAL_PDF_NAME,
)
from meritranker_data_ingestion.schemas.ocr_evidence import OcrEvidencePackage
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.ocr_adapter_base import (
    OcrAdapterConfig,
    OcrEngineMode,
    resolve_ocr_adapter_config,
    select_ocr_adapters,
)
from meritranker_data_ingestion.services.pdf_page_renderer import render_pdf_pages


class OcrEvidenceError(Exception):
    """Raised when OCR evidence build cannot start or explicit OCR fails."""


@dataclass(frozen=True)
class OcrEvidenceResult:
    package: OcrEvidencePackage
    json_path: Path
    md_path: Path
    page_images_dir: Path
    ocr_failed: bool = False
    ocr_failed_reason: str | None = None
    ocr_fallback_used: bool = False
    ocr_pages_attempted: int = 0
    ocr_pages_succeeded: int = 0
    ocr_pages_failed: int = 0
    requested_engine: str = "auto"
    effective_engine: str | None = None


def build_ocr_evidence_package(
    package_dir: Path,
    *,
    engine: str | None = None,
    azure_endpoint: str | None = None,
    azure_key: str | None = None,
    paddle_lang: str | None = None,
    render_pages: bool = True,
    allow_fallback: bool = False,
) -> OcrEvidenceResult:
    """Run OCR adapters and write ocr-evidence.json."""
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise OcrEvidenceError(f"Package directory does not exist: {resolved}")

    config = resolve_ocr_adapter_config(
        engine=engine,
        azure_endpoint=azure_endpoint,
        azure_key=azure_key,
        paddle_lang=paddle_lang,
    )
    ocr_dir = resolved / OCR_DIR
    page_images_dir = ocr_dir / OCR_PAGE_IMAGES_DIR
    logs_dir = ocr_dir / OCR_ENGINE_LOGS_DIR
    ocr_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = resolved / SOURCE_DIR / ORIGINAL_PDF_NAME
    page_images: list[Path] = []
    warnings: list[str] = []
    errors: list[str] = []
    ocr_failed = False
    ocr_failed_reason: str | None = None
    pages_attempted = 0
    pages_succeeded = 0
    pages_failed = 0
    effective_engine: str | None = None

    if config.engine == OcrEngineMode.NONE:
        warnings.append("ocr_engine_disabled")
        package = OcrEvidencePackage(
            source_file_name=_source_file_name(resolved),
            warnings=warnings,
        )
        return _write_result(
            resolved,
            package,
            requested_engine=config.engine.value,
            effective_engine=None,
        )

    if render_pages and pdf_path.exists():
        page_images, render_warnings = render_pdf_pages(pdf_path, page_images_dir)
        warnings.extend(render_warnings)

    adapters = select_ocr_adapters(config)
    if not adapters:
        warnings.append("no_ocr_adapter_available")
        ocr_failed = True
        ocr_failed_reason = "no_ocr_adapter_available"
        package = OcrEvidencePackage(
            source_file_name=_source_file_name(resolved),
            warnings=warnings,
        )
        return _write_result(
            resolved,
            package,
            ocr_failed=ocr_failed,
            ocr_failed_reason=ocr_failed_reason,
            requested_engine=config.engine.value,
        )

    merged_lines = []
    merged_pages = []
    engines_used: list[str] = []

    for adapter in adapters:
        try:
            if hasattr(adapter, "extract_with_status"):
                result = adapter.extract_with_status(
                    resolved,
                    pdf_path=pdf_path,
                    page_images=page_images or None,
                )
                pages_attempted = result.pages_attempted
                pages_succeeded = result.pages_succeeded
                pages_failed = result.pages_failed
                if result.page_statuses:
                    from meritranker_data_ingestion.services.azure_ocr_adapter import (
                        write_azure_page_ocr_status,
                    )

                    write_azure_page_ocr_status(
                        resolved,
                        result.page_statuses,
                        ocr_failed=result.ocr_failed,
                        ocr_failed_reason=result.ocr_failed_reason,
                    )
                ocr_failed = result.ocr_failed
                ocr_failed_reason = result.ocr_failed_reason
                extract_pkg = result.package
            else:
                extract_pkg = adapter.extract(
                    resolved,
                    pdf_path=pdf_path,
                    page_images=page_images or None,
                )
        except Exception as exc:
            warnings.append(f"{adapter.engine_name}_failed:{exc}")
            (logs_dir / f"{adapter.engine_name}-error.log").write_text(
                str(exc),
                encoding="utf-8",
            )
            ocr_failed = True
            ocr_failed_reason = str(exc)
            continue

        if extract_pkg.ocr_engines_used:
            engines_used.extend(extract_pkg.ocr_engines_used)
            effective_engine = extract_pkg.ocr_engines_used[0]
        merged_lines.extend(extract_pkg.lines)
        merged_pages.extend(extract_pkg.pages)
        warnings.extend(extract_pkg.warnings)
        errors.extend(extract_pkg.errors)

        (logs_dir / f"{adapter.engine_name}-summary.json").write_text(
            json.dumps(
                {
                    "engine": adapter.engine_name,
                    "line_count": len(extract_pkg.lines),
                    "warnings": extract_pkg.warnings,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    explicit = config.engine in {OcrEngineMode.AZURE, OcrEngineMode.PADDLE}

    if not merged_lines:
        ocr_failed = True
        ocr_failed_reason = ocr_failed_reason or "ocr_produced_zero_lines"
        effective_engine = None
        if config.engine == OcrEngineMode.AZURE and "azure_ocr_produced_zero_lines" not in errors:
            errors.append("azure_ocr_produced_zero_lines")

    if explicit and ocr_failed and allow_fallback:
        warnings.append("explicit_ocr_failed_marker_fallback_allowed")

    package = OcrEvidencePackage(
        package_version=OCR_EVIDENCE_PACKAGE_VERSION,
        source_file_name=_source_file_name(resolved),
        created_at=datetime.now(timezone.utc),
        status="failed" if ocr_failed else "succeeded",
        ocr_engines_used=_dedupe(engines_used),
        pages=merged_pages,
        lines=merged_lines,
        warnings=_dedupe(warnings),
        errors=_dedupe(errors),
    )
    result = _write_result(
        resolved,
        package,
        ocr_failed=ocr_failed,
        ocr_failed_reason=ocr_failed_reason,
        ocr_fallback_used=explicit and ocr_failed and allow_fallback,
        ocr_pages_attempted=pages_attempted,
        ocr_pages_succeeded=pages_succeeded,
        ocr_pages_failed=pages_failed,
        requested_engine=config.engine.value,
        effective_engine=effective_engine,
    )

    if explicit and ocr_failed and not allow_fallback:
        raise OcrEvidenceError(
            f"Explicit OCR engine '{config.engine.value}' failed: {ocr_failed_reason}. "
            "Pass --allow-ocr-fallback to continue with Marker-only evidence.",
        )

    return result


def _write_result(
    package_dir: Path,
    package: OcrEvidencePackage,
    *,
    ocr_failed: bool = False,
    ocr_failed_reason: str | None = None,
    ocr_fallback_used: bool = False,
    ocr_pages_attempted: int = 0,
    ocr_pages_succeeded: int = 0,
    ocr_pages_failed: int = 0,
    requested_engine: str = "auto",
    effective_engine: str | None = None,
) -> OcrEvidenceResult:
    ocr_dir = package_dir / OCR_DIR
    json_path = ocr_dir / OCR_EVIDENCE_JSON_NAME
    md_path = ocr_dir / OCR_EVIDENCE_MD_NAME
    json_path.write_text(
        json.dumps(package.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    md_path.write_text(_render_md(package), encoding="utf-8")
    return OcrEvidenceResult(
        package=package,
        json_path=json_path,
        md_path=md_path,
        page_images_dir=ocr_dir / OCR_PAGE_IMAGES_DIR,
        ocr_failed=ocr_failed,
        ocr_failed_reason=ocr_failed_reason,
        ocr_fallback_used=ocr_fallback_used,
        ocr_pages_attempted=ocr_pages_attempted,
        ocr_pages_succeeded=ocr_pages_succeeded,
        ocr_pages_failed=ocr_pages_failed,
        requested_engine=requested_engine,
        effective_engine=effective_engine if package.lines else None,
    )


def _render_md(package: OcrEvidencePackage) -> str:
    lines = [
        "# OCR Evidence Summary",
        "",
        f"- status: {package.status}",
        f"- source_file_name: {package.source_file_name}",
        f"- ocr_engines_used: {', '.join(package.ocr_engines_used) or 'none'}",
        f"- line_count: {len(package.lines)}",
        f"- page_count: {len(package.pages)}",
        "",
    ]
    if package.status == "failed":
        lines.append("## OCR failed")
        lines.extend(f"- {e}" for e in package.errors)
        lines.append("")
    if package.warnings:
        lines.append("## Warnings")
        lines.extend(f"- {w}" for w in package.warnings)
        lines.append("")
    return "\n".join(lines)


def _source_file_name(package_dir: Path) -> str:
    doc_evidence = package_dir / EVIDENCE_DIR / "document-evidence.json"
    if doc_evidence.exists():
        try:
            data = json.loads(doc_evidence.read_text(encoding="utf-8"))
            return str(data.get("source_file_name") or "unknown.pdf")
        except (json.JSONDecodeError, OSError):
            pass
    return "unknown.pdf"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
