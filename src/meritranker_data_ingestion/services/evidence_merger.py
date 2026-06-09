"""Merge Marker document evidence with OCR evidence (Part 14A)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    MERGED_DOCUMENT_EVIDENCE_JSON_NAME,
    MERGED_EVIDENCE_SUMMARY_JSON_NAME,
    OCR_DIR,
    OCR_EVIDENCE_JSON_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    AlternateEvidence,
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    RoleHint,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.ocr_evidence import OcrEvidencePackage, OcrLine
from meritranker_data_ingestion.services.evidence_role_hints import detect_role_hints, preview_text
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.ocr_role_hints import OcrRoleHint

RE_BLANK_OPTION = re.compile(r"^(\d+)\s*[\.\)]\s*$")
RE_NUMERIC_OPTION = re.compile(r"^(\d+)\s*[\.\)]\s+(.+)$")
RE_OPTION_LABEL_ONLY = re.compile(r"^([A-Da-d])\s*[\.\)]\s*$")


class EvidenceMergerError(Exception):
    """Raised when evidence merge cannot complete."""


@dataclass(frozen=True)
class EvidenceMergeResult:
    package: DocumentEvidencePackage
    summary: dict
    merged_json_path: Path
    summary_json_path: Path


def merge_evidence_package(package_dir: Path) -> EvidenceMergeResult:
    """Merge document-evidence.json with ocr-evidence.json when available."""
    resolved = resolve_path(package_dir)
    doc_path = resolved / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    ocr_path = resolved / OCR_DIR / OCR_EVIDENCE_JSON_NAME

    if not doc_path.exists():
        raise EvidenceMergerError(f"Missing document evidence: {doc_path}")

    doc = DocumentEvidencePackage.model_validate_json(doc_path.read_text(encoding="utf-8"))
    ocr: OcrEvidencePackage | None = None
    if ocr_path.exists():
        ocr = OcrEvidencePackage.model_validate_json(ocr_path.read_text(encoding="utf-8"))

    merged_lines = [line.model_copy(deep=True) for line in doc.lines]
    ocr_only_lines: list[EvidenceLine] = []
    recoveries = 0
    deduped = 0

    if ocr and ocr.lines:
        used_ocr_ids: set[str] = set()
        for marker_line in merged_lines:
            if _is_blank_option_line(marker_line.text_raw):
                recovery = _find_ocr_option_recovery(marker_line, ocr.lines, used_ocr_ids)
                if recovery:
                    ocr_line, text = recovery
                    used_ocr_ids.add(ocr_line.line_id)
                    marker_line.alternate_evidence.append(
                        AlternateEvidence(
                            engine=ocr_line.engine,
                            text_raw=text,
                            line_id=ocr_line.line_id,
                            confidence=ocr_line.confidence,
                            bbox=ocr_line.bbox,
                        ),
                    )
                    recoveries += 1

        for ocr_line in ocr.lines:
            if ocr_line.line_id in used_ocr_ids:
                continue
            if _is_duplicate_of_marker(ocr_line, merged_lines):
                deduped += 1
                continue
            evidence_line = _ocr_line_to_evidence_line(ocr_line, ocr_path)
            ocr_only_lines.append(evidence_line)

    all_lines = merged_lines + ocr_only_lines
    extractors_used = list(doc.extractors_used)
    for engine in (ocr.ocr_engines_used if ocr else []):
        if engine not in extractors_used:
            extractors_used.append(engine)

    merged = doc.model_copy(
        update={
            "created_at": datetime.now(timezone.utc),
            "extractors_used": extractors_used,
            "lines": all_lines,
            "extraction_status": (
                EvidenceExtractionStatus.PARTIAL
                if ocr and ocr.warnings
                else doc.extraction_status
            ),
            "warnings": _dedupe(doc.warnings + (ocr.warnings if ocr else [])),
            "source_artifact_paths": {
                **doc.source_artifact_paths,
                "merged_document_evidence": str(
                    resolved / EVIDENCE_DIR / MERGED_DOCUMENT_EVIDENCE_JSON_NAME,
                ),
                "ocr_evidence": str(ocr_path) if ocr_path.exists() else None,
            },
        },
    )

    summary = {
        "source_file_name": merged.source_file_name,
        "marker_line_count": len(doc.lines),
        "ocr_line_count": len(ocr.lines) if ocr else 0,
        "merged_line_count": len(all_lines),
        "ocr_option_recoveries": recoveries,
        "ocr_deduplicated_count": deduped,
        "ocr_engines_used": ocr.ocr_engines_used if ocr else [],
        "warnings": merged.warnings,
    }

    evidence_dir = resolved / EVIDENCE_DIR
    merged_path = evidence_dir / MERGED_DOCUMENT_EVIDENCE_JSON_NAME
    summary_path = evidence_dir / MERGED_EVIDENCE_SUMMARY_JSON_NAME
    merged_path.write_text(
        json.dumps(merged.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return EvidenceMergeResult(
        package=merged,
        summary=summary,
        merged_json_path=merged_path,
        summary_json_path=summary_path,
    )


def _is_blank_option_line(text: str) -> bool:
    stripped = text.strip()
    return bool(
        RE_BLANK_OPTION.match(stripped)
        or RE_OPTION_LABEL_ONLY.match(stripped)
        or stripped in {"Ans", "Ans.", "1.", "2.", "3.", "4."}
    )


def _find_ocr_option_recovery(
    marker_line: EvidenceLine,
    ocr_lines: list[OcrLine],
    used: set[str],
) -> tuple[OcrLine, str] | None:
    marker_page = marker_line.page_number
    best: tuple[OcrLine, str, float] | None = None
    for ocr_line in ocr_lines:
        if ocr_line.line_id in used:
            continue
        if marker_page is not None and ocr_line.page_number != marker_page:
            continue
        if OcrRoleHint.OPTION_LABEL_CANDIDATE not in ocr_line.role_hints:
            match = RE_NUMERIC_OPTION.match(ocr_line.text.strip())
            if not match or not match.group(2).strip():
                continue
            text = match.group(2).strip()
        else:
            match = RE_NUMERIC_OPTION.match(ocr_line.text.strip())
            text = match.group(2).strip() if match else ocr_line.text.strip()
        if not text:
            continue
        score = ocr_line.confidence
        if best is None or score > best[2]:
            best = (ocr_line, text, score)
    if best:
        return best[0], best[1]
    return None


def _is_duplicate_of_marker(ocr_line: OcrLine, marker_lines: list[EvidenceLine]) -> bool:
    ocr_text = ocr_line.normalized_text.lower().strip()
    if not ocr_text:
        return True
    for marker in marker_lines:
        marker_text = marker.normalized_preview.lower().strip()
        if not marker_text:
            continue
        if marker.page_number is not None and marker.page_number != ocr_line.page_number:
            continue
        ratio = SequenceMatcher(None, ocr_text, marker_text).ratio()
        if ratio >= 0.92:
            return True
    return False


def _ocr_line_to_evidence_line(ocr_line: OcrLine, ocr_path: Path) -> EvidenceLine:
    hints = detect_role_hints(ocr_line.text)
    if OcrRoleHint.CHOSEN_OPTION_CANDIDATE in ocr_line.role_hints:
        hints = list(hints)
    return EvidenceLine(
        line_id=ocr_line.line_id,
        page_number=ocr_line.page_number,
        text_raw=ocr_line.text,
        normalized_preview=preview_text(ocr_line.text),
        bbox=ocr_line.bbox,
        source_extractor=ocr_line.engine,
        source_artifact_path=str(ocr_path),
        source_span=SourceSpan(
            extractor=ocr_line.engine,
            page_number=ocr_line.page_number,
            line_id=ocr_line.line_id,
            source_artifact_path=str(ocr_path),
            bbox=ocr_line.bbox,
        ),
        role_hints=hints,
        confidence=ocr_line.confidence,
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
