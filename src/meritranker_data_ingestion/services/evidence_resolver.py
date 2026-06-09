"""Resolve document evidence path for binding and export (Part 14A)."""

from __future__ import annotations

from pathlib import Path

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    MERGED_DOCUMENT_EVIDENCE_JSON_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage, EvidenceLine


def resolve_document_evidence_path(package_dir: Path) -> Path:
    """Prefer merged-document-evidence.json when present."""
    merged = package_dir / EVIDENCE_DIR / MERGED_DOCUMENT_EVIDENCE_JSON_NAME
    if merged.exists():
        return merged
    return package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME


def load_document_evidence(package_dir: Path) -> tuple[DocumentEvidencePackage, Path]:
    path = resolve_document_evidence_path(package_dir)
    if not path.exists():
        raise FileNotFoundError(f"Missing document evidence: {path}")
    package = DocumentEvidencePackage.model_validate_json(path.read_text(encoding="utf-8"))
    return package, path


def select_binding_lines(
    evidence: DocumentEvidencePackage,
    evidence_path: Path,
) -> list[EvidenceLine]:
    """Select evidence lines for semantic binding."""
    if evidence_path.name == MERGED_DOCUMENT_EVIDENCE_JSON_NAME:
        return list(evidence.lines)
    primary_lines = [
        line for line in evidence.lines
        if line.source_extractor == evidence.primary_extractor
    ]
    return primary_lines if primary_lines else list(evidence.lines)


def is_merged_evidence(evidence_path: Path) -> bool:
    return evidence_path.name == MERGED_DOCUMENT_EVIDENCE_JSON_NAME
