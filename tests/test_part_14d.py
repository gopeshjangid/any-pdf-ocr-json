"""Tests for Part 14D Azure OCR rendered-image fallback + failed OCR artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    EXTRACTION_PACKAGE_DIR,
    OCR_DIR,
    OCR_EVIDENCE_JSON_NAME,
    OCR_EVIDENCE_MD_NAME,
    SOURCE_DIR,
    ORIGINAL_PDF_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
)
from meritranker_data_ingestion.services.azure_ocr_adapter import (
    AzureOcrAdapter,
    _analyze_bytes,
    _build_ocr_lines,
    _lines_from_layout_payload,
    _ocr_single_page,
)
from meritranker_data_ingestion.services.ocr_evidence_builder import OcrEvidenceError, build_ocr_evidence_package
from meritranker_data_ingestion.services.ocr_input_sizer import (
    SizedOcrInput,
    is_unsupported_content_error,
    pymupdf_available,
    should_fallback_to_rendered_image,
)
from meritranker_data_ingestion.services.ocr_runtime_preflight import resolve_ocr_used
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    SemanticPipelineError,
    SemanticPipelineOptions,
    run_semantic_pipeline,
)

HINDI_LAYOUT_PAYLOAD = {
    "pages": [
        {
            "pageNumber": 1,
            "lines": [
                {"content": "1. प्लाज्मा", "confidence": 0.95},
                {"content": "2. पेड़", "confidence": 0.94},
                {"content": "3. क्लोरोफिल", "confidence": 0.93},
                {"content": "4. तना", "confidence": 0.92},
            ],
        },
    ],
}


def test_unsupported_content_triggers_image_fallback() -> None:
    exc = Exception(
        "UnsupportedContent: Content is not supported: Bad or unrecognizable request binary file or Json.",
    )
    assert is_unsupported_content_error(exc)
    assert should_fallback_to_rendered_image(exc)


def test_rendered_image_lines_use_source_page_number_not_azure_page_one() -> None:
    client = MagicMock()
    client.analyze_document_bytes.return_value = HINDI_LAYOUT_PAYLOAD
    sized = SizedOcrInput(
        data=b"\x89PNG",
        input_type="rendered_image",
        page_number=5,
        original_size_bytes=10,
        final_size_bytes=10,
        dpi=150,
        jpeg_quality=85,
        image_format="jpeg",
        retry_count=1,
    )
    lines, _, attempt = _analyze_bytes(
        client,
        sized,
        model_id="prebuilt-layout",
        input_type="rendered_image",
    )
    assert attempt.status == "succeeded"
    assert all(line["pageNumber"] == 5 for line in lines)


def test_hindi_image_response_parses_into_ocr_lines() -> None:
    raw = _lines_from_layout_payload(HINDI_LAYOUT_PAYLOAD)
    lines = _build_ocr_lines(raw)
    assert len(lines) == 4
    texts = [ln.text for ln in lines]
    assert "1. प्लाज्मा" in texts
    assert "4. तना" in texts
    assert all(ln.engine == "azure_ocr" for ln in lines)


@pytest.mark.skipif(not pymupdf_available(), reason="PyMuPDF required for render fallback tests")
def test_pdf_page_unsupported_content_falls_back_to_rendered_image(tmp_path: Path) -> None:
    pdf = tmp_path / "page.pdf"
    _write_minimal_pdf(pdf)
    client = MagicMock()

    def analyze_side_effect(
        data: bytes,
        *,
        model_id: str,
        content_type: str = "application/pdf",
    ) -> dict:
        if content_type == "application/pdf":
            raise Exception(
                "UnsupportedContent: Content is not supported: Bad or unrecognizable request binary file or Json.",
            )
        return HINDI_LAYOUT_PAYLOAD

    client.analyze_document_bytes.side_effect = analyze_side_effect
    page_images_dir = tmp_path / "page-images"
    status, lines = _ocr_single_page(
        client,
        pdf,
        0,
        model_id="prebuilt-layout",
        page_images_dir=page_images_dir,
    )
    assert status.final_status == "succeeded"
    assert len(lines) == 4
    assert status.attempts[0].input_type == "pdf_page"
    assert status.attempts[0].status == "failed"
    assert status.attempts[0].error_code == "UnsupportedContent"
    image_attempts = [a for a in status.attempts if a.input_type == "rendered_image"]
    assert image_attempts
    assert image_attempts[-1].status == "succeeded"
    saved_images = list(page_images_dir.glob("page-0001-*"))
    assert saved_images


@pytest.mark.skipif(not pymupdf_available(), reason="PyMuPDF required for full PDF split test")
def test_full_pdf_invalid_content_length_triggers_page_split(tmp_path: Path) -> None:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    pdf = pkg / SOURCE_DIR / ORIGINAL_PDF_NAME
    pdf.parent.mkdir(parents=True, exist_ok=True)
    _write_minimal_pdf(pdf)
    _write_min_evidence(pkg)

    full_bytes = pdf.read_bytes()
    call_log: list[str] = []

    def analyze_side_effect(
        data: bytes,
        *,
        model_id: str,
        content_type: str = "application/pdf",
    ) -> dict:
        if data == full_bytes:
            call_log.append("full_pdf")
            raise Exception("InvalidContentLength: Input file is too large.")
        call_log.append(content_type)
        return HINDI_LAYOUT_PAYLOAD

    with patch(
        "meritranker_data_ingestion.services.azure_ocr_adapter.resolve_azure_di_config",
        return_value=MagicMock(endpoint="https://x", api_key="k", model_id="prebuilt-layout"),
    ), patch(
        "meritranker_data_ingestion.services.azure_ocr_adapter.SdkAzureDiClient",
    ) as mock_client_cls:
        mock_client_cls.return_value.analyze_document_bytes.side_effect = analyze_side_effect
        adapter = AzureOcrAdapter(endpoint="https://x", api_key="k")
        result = adapter.extract_with_status(pkg, pdf_path=pdf)

    assert "full_pdf" in call_log
    assert result.ocr_failed is False
    assert len(result.package.lines) == 4
    assert result.pages_succeeded >= 1


def test_pymupdf_missing_reports_clean_error_when_image_fallback_needed(tmp_path: Path) -> None:
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(b"%PDF-1.4 minimal")
    client = MagicMock()
    client.analyze_document_bytes.side_effect = Exception(
        "UnsupportedContent: Content is not supported",
    )
    with patch(
        "meritranker_data_ingestion.services.azure_ocr_adapter.pymupdf_available",
        return_value=False,
    ), patch(
        "meritranker_data_ingestion.services.azure_ocr_adapter.extract_pdf_page_bytes",
        return_value=b"page-bytes",
    ):
        status, lines = _ocr_single_page(
            client,
            pdf,
            0,
            model_id="prebuilt-layout",
            page_images_dir=tmp_path / "imgs",
        )
    assert lines == []
    assert status.final_status == "failed"
    render_attempt = status.attempts[-1]
    assert render_attempt.input_type == "rendered_image"
    assert "pymupdf_missing_for_ocr_image_fallback" in (render_attempt.error or "")


def test_ocr_evidence_json_written_when_all_attempts_fail(tmp_path: Path) -> None:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    _write_min_package(pkg)
    with patch(
        "meritranker_data_ingestion.services.ocr_evidence_builder.render_pdf_pages",
        return_value=([], []),
    ), patch(
        "meritranker_data_ingestion.services.ocr_evidence_builder.select_ocr_adapters",
    ) as mock_adapters:
        adapter = MagicMock()
        adapter.engine_name = "azure_ocr"
        adapter.extract_with_status.return_value = MagicMock(
            package=MagicMock(
                lines=[],
                pages=[],
                ocr_engines_used=[],
                warnings=[],
                errors=["azure_ocr_produced_zero_lines"],
            ),
            page_statuses=[],
            ocr_failed=True,
            ocr_failed_reason="azure_ocr_produced_zero_lines",
            pages_attempted=2,
            pages_succeeded=0,
            pages_failed=2,
        )
        mock_adapters.return_value = [adapter]
        with pytest.raises(OcrEvidenceError):
            build_ocr_evidence_package(pkg, engine="azure", allow_fallback=False)

    json_path = pkg / OCR_DIR / OCR_EVIDENCE_JSON_NAME
    md_path = pkg / OCR_DIR / OCR_EVIDENCE_MD_NAME
    assert json_path.exists()
    assert md_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert data["lines"] == []
    assert "azure_ocr_produced_zero_lines" in data["errors"]
    assert "OCR failed" in md_path.read_text(encoding="utf-8")


def test_ocr_used_false_when_line_count_zero() -> None:
    assert resolve_ocr_used(ocr_line_count=0, ocr_engines_used=["azure_ocr"]) is False
    assert resolve_ocr_used(ocr_line_count=3, ocr_engines_used=["azure_ocr"]) is True


def test_explicit_azure_zero_lines_stops_before_binder(tmp_path: Path) -> None:
    with patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.ExtractorOrchestrator",
    ) as mock_orch, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.normalize_evidence_package",
    ) as mock_norm, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.run_ocr_runtime_preflight",
    ) as mock_preflight, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.build_ocr_evidence_package",
    ) as mock_ocr, patch(
        "meritranker_data_ingestion.services.semantic_pipeline_runner.bind_semantically_package",
    ) as mock_bind:
        mock_orch.return_value.prepare.return_value.succeeded = True
        mock_orch.return_value.prepare.return_value.package_manifest = None
        mock_orch.return_value.prepare.return_value.extractor_manifest = MagicMock(
            source_file_name="t.pdf",
        )
        mock_norm.return_value.package.extraction_status.value = "succeeded"
        mock_preflight.return_value = MagicMock(
            requested_engine="azure",
            effective_engine="azure",
            strict_failure=False,
            ocr_available=True,
            ocr_failed_reason=None,
            warnings=[],
        )
        mock_ocr.side_effect = OcrEvidenceError(
            "Explicit OCR engine 'azure' failed: azure_ocr_produced_zero_lines.",
        )
        with pytest.raises(SemanticPipelineError, match="azure_ocr_produced_zero_lines"):
            run_semantic_pipeline(
                SemanticPipelineOptions(
                    input_pdf=tmp_path / "in.pdf",
                    output_dir=tmp_path / "out",
                    ocr_engine="azure",
                    allow_ocr_fallback=False,
                ),
            )
        mock_bind.assert_not_called()


def test_allow_ocr_fallback_continues_with_warning(tmp_path: Path) -> None:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    _write_min_package(pkg)
    with patch(
        "meritranker_data_ingestion.services.ocr_evidence_builder.render_pdf_pages",
        return_value=([], []),
    ), patch(
        "meritranker_data_ingestion.services.ocr_evidence_builder.select_ocr_adapters",
    ) as mock_adapters:
        adapter = MagicMock()
        adapter.engine_name = "azure_ocr"
        adapter.extract_with_status.return_value = MagicMock(
            package=MagicMock(
                lines=[],
                pages=[],
                ocr_engines_used=[],
                warnings=[],
                errors=[],
            ),
            page_statuses=[],
            ocr_failed=True,
            ocr_failed_reason="azure_ocr_produced_zero_lines",
            pages_attempted=1,
            pages_succeeded=0,
            pages_failed=1,
        )
        mock_adapters.return_value = [adapter]
        result = build_ocr_evidence_package(pkg, engine="azure", allow_fallback=True)
    assert result.ocr_fallback_used is True
    assert result.ocr_failed is True
    assert "explicit_ocr_failed_marker_fallback_allowed" in result.package.warnings


def _write_min_package(pkg: Path) -> None:
    (pkg / SOURCE_DIR).mkdir(parents=True, exist_ok=True)
    (pkg / SOURCE_DIR / ORIGINAL_PDF_NAME).write_bytes(b"%PDF-1.4")
    _write_min_evidence(pkg)


def _write_min_evidence(pkg: Path) -> None:
    ev = pkg / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / DOCUMENT_EVIDENCE_JSON_NAME).write_text(
        DocumentEvidencePackage(
            package_version="1.0",
            source_file_name="t.pdf",
            primary_extractor="marker",
            extractors_used=["marker"],
            extraction_status=EvidenceExtractionStatus.SUCCEEDED,
            lines=[],
        ).model_dump_json(),
        encoding="utf-8",
    )


def _write_minimal_pdf(path: Path) -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Sample OCR page")
    doc.save(path)
    doc.close()
