"""Azure Document Intelligence OCR adapter (Part 14A–14D split/resize/image fallback)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.config import (
    AZURE_DI_DIR,
    AZURE_DI_LINES_NAME,
    AZURE_DI_PAGES_NAME,
    DEFAULT_AZURE_DI_MODEL,
    EXTRACTORS_DIR,
    OCR_DIR,
    OCR_ENGINE_LOGS_DIR,
    OCR_PAGE_IMAGES_DIR,
    SOURCE_DIR,
    ORIGINAL_PDF_NAME,
)
from meritranker_data_ingestion.schemas.ocr_evidence import (
    OcrEvidencePackage,
    OcrLine,
    OcrPage,
)
from meritranker_data_ingestion.services.azure_di_client import (
    AzureDiClientError,
    SdkAzureDiClient,
    resolve_azure_di_config,
)
from meritranker_data_ingestion.services.ocr_input_sizer import (
    DEFAULT_MAX_PDF_BYTES,
    SizedOcrInput,
    content_type_for_image_format,
    extract_azure_error_code,
    extract_pdf_page_bytes,
    iter_render_attempts,
    pdf_page_count,
    prepare_pdf_input,
    pymupdf_available,
    should_fallback_to_rendered_image,
)
from meritranker_data_ingestion.services.ocr_role_hints import detect_ocr_role_hints


@dataclass
class AzurePageOcrAttempt:
    input_type: str
    status: str
    error_code: str | None = None
    error: str | None = None
    image_format: str | None = None
    dpi: int | None = None
    quality: int | None = None
    original_size_bytes: int = 0
    final_size_bytes: int = 0
    retry_count: int = 0
    line_count: int = 0


@dataclass
class AzurePageOcrPageStatus:
    page_number: int
    attempts: list[AzurePageOcrAttempt] = field(default_factory=list)
    final_status: str = "failed"
    line_count: int = 0


# Backward-compatible alias for older tests.
AzurePageOcrStatus = AzurePageOcrPageStatus


@dataclass
class AzureOcrExtractResult:
    package: OcrEvidencePackage
    page_statuses: list[AzurePageOcrPageStatus] = field(default_factory=list)
    ocr_failed: bool = False
    ocr_failed_reason: str | None = None
    ocr_fallback_used: bool = False
    pages_attempted: int = 0
    pages_succeeded: int = 0
    pages_failed: int = 0


class AzureOcrAdapter:
    """Azure DI OCR with page split, rendered-image fallback (Part 14D)."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        model_id: str = DEFAULT_AZURE_DI_MODEL,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._model_id = model_id

    @property
    def engine_name(self) -> str:
        return "azure_ocr"

    def is_available(self) -> bool:
        if self._endpoint and self._api_key:
            return True
        try:
            resolve_azure_di_config(
                endpoint=self._endpoint,
                api_key=self._api_key,
                model_id=self._model_id,
            )
            return True
        except AzureDiClientError:
            return False

    def extract(
        self,
        package_dir: Path,
        *,
        pdf_path: Path | None = None,
        page_images: list[Path] | None = None,
    ) -> OcrEvidencePackage:
        result = self.extract_with_status(package_dir, pdf_path=pdf_path, page_images=page_images)
        return result.package

    def extract_with_status(
        self,
        package_dir: Path,
        *,
        pdf_path: Path | None = None,
        page_images: list[Path] | None = None,
    ) -> AzureOcrExtractResult:
        source_name = _source_file_name(package_dir)
        warnings: list[str] = []
        errors: list[str] = []

        lines_raw, pages_raw, artifact_warning = _load_existing_azure_lines(package_dir)
        if artifact_warning:
            warnings.append(artifact_warning)

        if lines_raw:
            ocr_lines = _build_ocr_lines(lines_raw)
            ocr_pages = _build_pages(pages_raw, ocr_lines)
            return AzureOcrExtractResult(
                package=_success_package(source_name, ocr_lines, ocr_pages, warnings, errors),
                pages_succeeded=len(ocr_pages),
            )

        pdf = pdf_path or (package_dir / SOURCE_DIR / ORIGINAL_PDF_NAME)
        if not pdf.exists():
            return _failed_result(
                source_name,
                warnings,
                errors=["pdf_not_found"],
                reason="pdf_not_found",
            )

        try:
            config = resolve_azure_di_config(
                endpoint=self._endpoint,
                api_key=self._api_key,
                model_id=self._model_id,
            )
            client = SdkAzureDiClient(config)
        except (AzureDiClientError, OSError) as exc:
            return _failed_result(source_name, warnings, errors=[str(exc)], reason=str(exc))

        page_images_dir = package_dir / OCR_DIR / OCR_PAGE_IMAGES_DIR
        page_statuses: list[AzurePageOcrPageStatus] = []
        all_lines: list[dict[str, Any]] = []
        all_pages: list[dict[str, Any]] = []

        full_input = prepare_pdf_input(pdf, max_bytes=DEFAULT_MAX_PDF_BYTES)
        if full_input is not None:
            full_status = AzurePageOcrPageStatus(page_number=0)
            lines, pages, attempt = _analyze_bytes(
                client,
                full_input,
                model_id=self._model_id,
                input_type="pdf",
            )
            full_status.attempts.append(attempt)
            if attempt.status == "succeeded":
                all_lines = lines
                all_pages = pages
                full_status.final_status = "succeeded"
                full_status.line_count = len(lines)
                page_statuses.append(full_status)
            elif should_fallback_to_rendered_image(Exception(attempt.error or "")):
                warnings.append("azure_full_pdf_fallback_to_page_split")
            else:
                page_statuses.append(full_status)
                errors.append(attempt.error or "azure_full_pdf_failed")
        else:
            warnings.append("azure_full_pdf_oversized_using_page_split")

        if not all_lines:
            page_total = pdf_page_count(pdf)
            for page_idx in range(page_total):
                page_status, page_lines = _ocr_single_page(
                    client,
                    pdf,
                    page_idx,
                    model_id=self._model_id,
                    page_images_dir=page_images_dir,
                )
                page_statuses.append(page_status)
                all_lines.extend(page_lines)

        ocr_lines = _build_ocr_lines(all_lines)
        ocr_pages = _build_pages(all_pages, ocr_lines)
        page_results = [s for s in page_statuses if s.page_number > 0]
        pages_attempted = len(page_results) or len(page_statuses)
        pages_succeeded = sum(1 for s in page_results if s.final_status == "succeeded")
        pages_failed = sum(1 for s in page_results if s.final_status == "failed")

        ocr_failed = not ocr_lines
        if ocr_failed:
            errors.append("azure_ocr_produced_zero_lines")

        return AzureOcrExtractResult(
            package=(
                _failed_package(source_name, warnings, errors)
                if ocr_failed
                else _success_package(source_name, ocr_lines, ocr_pages, warnings, errors)
            ),
            page_statuses=page_statuses,
            ocr_failed=ocr_failed,
            ocr_failed_reason="azure_ocr_produced_zero_lines" if ocr_failed else None,
            pages_attempted=pages_attempted,
            pages_succeeded=pages_succeeded,
            pages_failed=pages_failed,
        )


def _ocr_single_page(
    client: SdkAzureDiClient,
    pdf: Path,
    page_idx: int,
    *,
    model_id: str,
    page_images_dir: Path,
) -> tuple[AzurePageOcrPageStatus, list[dict[str, Any]]]:
    page_num = page_idx + 1
    status = AzurePageOcrPageStatus(page_number=page_num)

    page_bytes = extract_pdf_page_bytes(pdf, page_idx)
    if page_bytes:
        sized = SizedOcrInput(
            data=page_bytes,
            input_type="pdf_page",
            page_number=page_num,
            original_size_bytes=len(page_bytes),
            final_size_bytes=len(page_bytes),
            dpi=None,
            jpeg_quality=None,
            image_format=None,
            retry_count=0,
        )
        lines, _, attempt = _analyze_bytes(
            client,
            sized,
            model_id=model_id,
            input_type="pdf_page",
        )
        status.attempts.append(attempt)
        if attempt.status == "succeeded":
            status.final_status = "succeeded"
            status.line_count = len(lines)
            return status, lines

        if not should_fallback_to_rendered_image(Exception(attempt.error or "")):
            status.final_status = "failed"
            return status, []

    if not pymupdf_available():
        status.attempts.append(
            AzurePageOcrAttempt(
                input_type="rendered_image",
                status="failed",
                error_code="pymupdf_missing",
                error="pymupdf_missing_for_ocr_image_fallback: uv sync --extra ocr",
            ),
        )
        status.final_status = "failed"
        return status, []

    render_profiles = iter_render_attempts(
        pdf,
        page_idx,
        output_dir=page_images_dir,
    )
    if not render_profiles:
        status.attempts.append(
            AzurePageOcrAttempt(
                input_type="rendered_image",
                status="failed",
                error="all_render_profiles_exceeded_size_budget",
            ),
        )
        status.final_status = "failed"
        return status, []

    for sized in render_profiles:
        lines, _, attempt = _analyze_bytes(
            client,
            sized,
            model_id=model_id,
            input_type="rendered_image",
        )
        status.attempts.append(attempt)
        if attempt.status == "succeeded":
            status.final_status = "succeeded"
            status.line_count = len(lines)
            return status, lines

    status.final_status = "failed"
    return status, []


def _analyze_bytes(
    client: SdkAzureDiClient,
    sized: SizedOcrInput,
    *,
    model_id: str,
    input_type: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], AzurePageOcrAttempt]:
    content_type = (
        content_type_for_image_format(sized.image_format or "jpeg")
        if input_type == "rendered_image"
        else "application/pdf"
    )
    try:
        payload = client.analyze_document_bytes(
            sized.data,
            model_id=model_id,
            content_type=content_type,
        )
        lines = _lines_from_layout_payload(payload)
        pages = _pages_from_layout_payload(payload)
        page_num = sized.page_number
        if page_num:
            for line in lines:
                line["pageNumber"] = page_num
        return (
            lines,
            pages,
            AzurePageOcrAttempt(
                input_type=input_type,
                status="succeeded",
                image_format=sized.image_format,
                dpi=sized.dpi,
                quality=sized.jpeg_quality,
                original_size_bytes=sized.original_size_bytes,
                final_size_bytes=sized.final_size_bytes,
                retry_count=sized.retry_count,
                line_count=len(lines),
            ),
        )
    except Exception as exc:
        return (
            [],
            [],
            AzurePageOcrAttempt(
                input_type=input_type,
                status="failed",
                error_code=extract_azure_error_code(exc),
                error=str(exc),
                image_format=sized.image_format,
                dpi=sized.dpi,
                quality=sized.jpeg_quality,
                original_size_bytes=sized.original_size_bytes,
                final_size_bytes=sized.final_size_bytes,
                retry_count=sized.retry_count,
            ),
        )


def _success_package(
    source_name: str,
    ocr_lines: list[OcrLine],
    ocr_pages: list[OcrPage],
    warnings: list[str],
    errors: list[str],
) -> OcrEvidencePackage:
    return OcrEvidencePackage(
        source_file_name=source_name,
        status="succeeded",
        ocr_engines_used=["azure_ocr"] if ocr_lines else [],
        pages=ocr_pages,
        lines=ocr_lines,
        warnings=warnings,
        errors=errors,
    )


def _failed_package(
    source_name: str,
    warnings: list[str],
    errors: list[str],
) -> OcrEvidencePackage:
    return OcrEvidencePackage(
        source_file_name=source_name,
        status="failed",
        ocr_engines_used=[],
        warnings=warnings,
        errors=errors,
    )


def _failed_result(
    source_name: str,
    warnings: list[str],
    *,
    errors: list[str],
    reason: str,
) -> AzureOcrExtractResult:
    return AzureOcrExtractResult(
        package=_failed_package(source_name, warnings, errors),
        ocr_failed=True,
        ocr_failed_reason=reason,
    )


def write_azure_page_ocr_status(
    package_dir: Path,
    statuses: list[AzurePageOcrPageStatus],
    *,
    ocr_failed: bool = False,
    ocr_failed_reason: str | None = None,
    ocr_fallback_used: bool = False,
) -> Path:
    logs_dir = package_dir / OCR_DIR / OCR_ENGINE_LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "azure-page-ocr-status.json"
    page_results = [s for s in statuses if s.page_number > 0]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ocr_failed": ocr_failed,
        "ocr_failed_reason": ocr_failed_reason,
        "ocr_fallback_used": ocr_fallback_used,
        "pages_attempted": len(page_results) or len(statuses),
        "pages_succeeded": sum(1 for s in page_results if s.final_status == "succeeded"),
        "pages_failed": sum(1 for s in page_results if s.final_status == "failed"),
        "pages": [
            {
                "page_number": s.page_number,
                "attempts": [
                    {
                        "input_type": a.input_type,
                        "status": a.status,
                        "error_code": a.error_code,
                        "error": a.error,
                        "image_format": a.image_format,
                        "dpi": a.dpi,
                        "quality": a.quality,
                        "original_size_bytes": a.original_size_bytes,
                        "final_size_bytes": a.final_size_bytes,
                        "retry_count": a.retry_count,
                        "line_count": a.line_count,
                    }
                    for a in s.attempts
                ],
                "final_status": s.final_status,
                "line_count": s.line_count,
            }
            for s in statuses
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _build_ocr_lines(lines_raw: list[dict[str, Any]]) -> list[OcrLine]:
    ocr_lines: list[OcrLine] = []
    for idx, line in enumerate(lines_raw, start=1):
        text = str(line.get("content") or line.get("text") or "")
        if not text.strip():
            continue
        page_number = int(line.get("pageNumber") or line.get("page_number") or 1)
        bbox = _polygon_to_bbox(line.get("polygon") or line.get("bounding_box"))
        confidence = float(line.get("confidence") or 1.0)
        ocr_lines.append(
            OcrLine(
                line_id=f"azure_ocr_l_{idx:06d}",
                page_number=page_number,
                text=text,
                normalized_text=text.strip(),
                bbox=bbox,
                confidence=confidence,
                engine="azure_ocr",
                role_hints=detect_ocr_role_hints(text),
            ),
        )
    return ocr_lines


def _source_file_name(package_dir: Path) -> str:
    manifest = package_dir / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return str(data.get("source_file_name") or "unknown.pdf")
        except (json.JSONDecodeError, OSError):
            pass
    return "unknown.pdf"


def _load_existing_azure_lines(
    package_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    lines_path = package_dir / EXTRACTORS_DIR / AZURE_DI_DIR / AZURE_DI_LINES_NAME
    pages_path = package_dir / EXTRACTORS_DIR / AZURE_DI_DIR / AZURE_DI_PAGES_NAME
    if lines_path.exists():
        lines = json.loads(lines_path.read_text(encoding="utf-8"))
        pages = (
            json.loads(pages_path.read_text(encoding="utf-8"))
            if pages_path.exists()
            else []
        )
        if isinstance(lines, list):
            return lines, pages if isinstance(pages, list) else [], None
    return [], [], "azure_di_artifacts_missing"


def _lines_from_layout_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for page in payload.get("pages") or []:
        page_number = page.get("pageNumber") or page.get("page_number")
        for line in page.get("lines") or []:
            if isinstance(line, dict):
                entry = dict(line)
                entry.setdefault("pageNumber", page_number)
                lines.append(entry)
    return lines


def _pages_from_layout_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pages = payload.get("pages") or []
    return pages if isinstance(pages, list) else []


def _build_pages(pages_raw: list[dict[str, Any]], lines: list[OcrLine]) -> list[OcrPage]:
    if pages_raw:
        out: list[OcrPage] = []
        for page in pages_raw:
            page_number = int(page.get("pageNumber") or page.get("page_number") or len(out) + 1)
            page_lines = [ln for ln in lines if ln.page_number == page_number]
            confs = [ln.confidence for ln in page_lines if ln.confidence]
            out.append(
                OcrPage(
                    page_number=page_number,
                    width=_as_float(page.get("width")),
                    height=_as_float(page.get("height")),
                    line_count=len(page_lines),
                    word_count=0,
                    confidence_avg=sum(confs) / len(confs) if confs else 0.0,
                ),
            )
        return out

    by_page: dict[int, list[OcrLine]] = {}
    for line in lines:
        by_page.setdefault(line.page_number, []).append(line)
    return [
        OcrPage(
            page_number=page_number,
            line_count=len(page_lines),
            confidence_avg=(
                sum(ln.confidence for ln in page_lines) / len(page_lines)
                if page_lines
                else 0.0
            ),
        )
        for page_number, page_lines in sorted(by_page.items())
    ]


def _polygon_to_bbox(polygon: Any) -> list[float] | None:
    if not polygon or not isinstance(polygon, list):
        return None
    if len(polygon) < 4:
        return None
    try:
        xs = [float(polygon[i]) for i in range(0, len(polygon), 2)]
        ys = [float(polygon[i + 1]) for i in range(0, len(polygon) - 1, 2)]
        return [min(xs), min(ys), max(xs), max(ys)]
    except (TypeError, ValueError, IndexError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
