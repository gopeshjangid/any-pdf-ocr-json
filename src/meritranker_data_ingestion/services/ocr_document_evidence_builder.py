"""Build document-evidence.json from OCR lines when Marker is skipped (Part 14X)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    DOCUMENT_EVIDENCE_PACKAGE_VERSION,
    EVIDENCE_DIR,
    EVIDENCE_SUMMARY_JSON_NAME,
    OCR_DIR,
    OCR_EVIDENCE_JSON_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    EvidenceSummary,
    PrimaryExtractorMode,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.ocr_evidence import OcrEvidencePackage
from meritranker_data_ingestion.services.evidence_role_hints import detect_role_hints, preview_text
from meritranker_data_ingestion.services.file_service import resolve_path


class OcrDocumentEvidenceError(Exception):
    """Raised when OCR-only document evidence cannot be built."""


def build_document_evidence_from_ocr(package_dir: Path) -> Path:
    """Write document-evidence.json using OCR lines as primary evidence."""
    resolved = resolve_path(package_dir)
    ocr_path = resolved / OCR_DIR / OCR_EVIDENCE_JSON_NAME
    if not ocr_path.exists():
        raise OcrDocumentEvidenceError(f"Missing OCR evidence: {ocr_path}")

    ocr_pkg = OcrEvidencePackage.model_validate_json(ocr_path.read_text(encoding="utf-8"))
    if not ocr_pkg.lines:
        raise OcrDocumentEvidenceError("OCR evidence has no lines.")

    lines: list[EvidenceLine] = []
    for idx, ocr_line in enumerate(ocr_pkg.lines):
        text = ocr_line.text or ""
        if not text.strip():
            continue
        role_hints = detect_role_hints(text)
        lines.append(
            EvidenceLine(
                line_id=f"ocr_doc_l_{idx:06d}",
                text_raw=text,
                normalized_preview=preview_text(text),
                source_extractor="azure_ocr",
                role_hints=role_hints,
                source_spans=[
                    SourceSpan(
                        extractor="azure_ocr",
                        page_number=ocr_line.page_number,
                        line_id=ocr_line.line_id,
                        raw_index=idx,
                    ),
                ],
            ),
        )

    package = DocumentEvidencePackage(
        package_version=DOCUMENT_EVIDENCE_PACKAGE_VERSION,
        source_file_name=ocr_pkg.source_file_name,
        primary_extractor=PrimaryExtractorMode.AZURE_DI,
        extractors_available=["azure_ocr"],
        extractors_used=["azure_ocr"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=lines,
        warnings=["ocr_primary_document_evidence"],
        source_artifact_paths={"ocr_evidence": str(ocr_path)},
    )

    ev_dir = resolved / EVIDENCE_DIR
    ev_dir.mkdir(parents=True, exist_ok=True)
    out = ev_dir / DOCUMENT_EVIDENCE_JSON_NAME
    out.write_text(package.model_dump_json(indent=2), encoding="utf-8")

    summary = EvidenceSummary(
        source_file_name=ocr_pkg.source_file_name,
        primary_extractor=PrimaryExtractorMode.AZURE_DI.value,
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        marker_available=False,
        azure_di_available=True,
        page_count=0,
        line_count=len(lines),
        table_count=0,
        figure_count=0,
        image_count=0,
        metadata_candidate_count=0,
        noise_candidate_count=0,
    )
    (ev_dir / EVIDENCE_SUMMARY_JSON_NAME).write_text(
        json.dumps(summary.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return out
