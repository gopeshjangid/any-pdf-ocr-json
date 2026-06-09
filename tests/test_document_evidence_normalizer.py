"""Tests for unified document evidence normalization (Part 13B)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    EVIDENCE_SUMMARY_JSON_NAME,
    EXTRACTOR_COMPARISON_JSON_NAME,
    EXTRACTOR_MANIFEST_NAME,
    EXTRACTORS_DIR,
    RAW_MARKDOWN_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    EvidenceExtractionStatus,
    PrimaryExtractorMode,
    RoleHint,
)
from meritranker_data_ingestion.schemas.extractor import (
    ExtractorManifest,
    ExtractorRunStatus,
    ExtractorType,
)
from meritranker_data_ingestion.services.document_evidence_normalizer import (
    EvidenceNormalizationError,
    normalize_evidence_package,
)


def _write_manifest(
    package_dir: Path,
    *,
    selected: ExtractorType = ExtractorType.MARKER,
    marker_status: ExtractorRunStatus = ExtractorRunStatus.SUCCEEDED,
    azure_status: ExtractorRunStatus = ExtractorRunStatus.SKIPPED,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> None:
    manifest = ExtractorManifest(
        selected_extractor=selected,
        extractors_run=["marker"] if marker_status != ExtractorRunStatus.SKIPPED else [],
        source_file_name="exam.pdf",
        marker_status=marker_status,
        azure_di_status=azure_status,
        errors=errors or [],
        warnings=warnings or [],
    )
    if azure_status != ExtractorRunStatus.SKIPPED:
        manifest.extractors_run.append("azure-di")
    out = package_dir / EXTRACTORS_DIR / EXTRACTOR_MANIFEST_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_json(out)


def _write_marker(package_dir: Path, content: str, *, with_image: bool = False) -> None:
    marker_dir = package_dir / "marker"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / RAW_MARKDOWN_NAME).write_text(content, encoding="utf-8")
    if with_image:
        assets = marker_dir / "assets"
        assets.mkdir(exist_ok=True)
        (assets / "fig1.png").write_bytes(b"\x89PNG\r\n")


def _write_azure(
    package_dir: Path,
    *,
    lines: list[dict] | None = None,
    pages: list[dict] | None = None,
    tables: list[dict] | None = None,
    figures: list[dict] | None = None,
) -> None:
    azure_dir = package_dir / EXTRACTORS_DIR / "azure-di"
    azure_dir.mkdir(parents=True, exist_ok=True)
    (azure_dir / "lines.json").write_text(
        json.dumps(lines or []),
        encoding="utf-8",
    )
    (azure_dir / "pages.json").write_text(
        json.dumps(pages or []),
        encoding="utf-8",
    )
    (azure_dir / "tables.json").write_text(
        json.dumps(tables or []),
        encoding="utf-8",
    )
    (azure_dir / "figures.json").write_text(
        json.dumps(figures or []),
        encoding="utf-8",
    )


def test_marker_only_normalization(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(package)
    _write_marker(
        package,
        "# SSC CGL Tier 2 Shift 1\n\n**1.** What is 2+2?\n- **A** 3\n- **B** 4\n![fig](fig1.png)\n",
        with_image=True,
    )

    result = normalize_evidence_package(package)

    assert result.package.extraction_status == EvidenceExtractionStatus.SUCCEEDED
    assert result.package.primary_extractor == "marker"
    marker_lines = [line for line in result.package.lines if line.source_extractor == "marker"]
    assert len(marker_lines) >= 4
    option_line = next(line for line in marker_lines if line.text_raw == "- **A** 3")
    question_line = next(line for line in marker_lines if "**1.**" in line.text_raw)
    assert RoleHint.OPTION_LABEL_CANDIDATE in option_line.role_hints
    assert RoleHint.QUESTION_ANCHOR_CANDIDATE in question_line.role_hints
    assert any(img.asset_path and "fig1.png" in img.asset_path for img in result.package.images)
    assert (package / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME).exists()


def test_azure_only_normalization(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(package, selected=ExtractorType.AZURE_DI, azure_status=ExtractorRunStatus.SUCCEEDED)
    _write_azure(
        package,
        pages=[{"pageNumber": 1, "width": 8.5, "height": 11, "lines": [{"content": "Line A"}]}],
        lines=[{"content": "Line A", "pageNumber": 1, "polygon": [0, 0, 2, 0, 2, 1, 0, 1]}],
        tables=[{"rowCount": 1, "columnCount": 1, "cells": [{"content": "A1"}], "boundingRegions": [{"pageNumber": 1, "polygon": [0, 0, 1, 1]}]}],
        figures=[{"id": "f1", "boundingRegions": [{"pageNumber": 1, "polygon": [1, 1, 2, 2]}]}],
    )

    result = normalize_evidence_package(package)

    assert result.package.primary_extractor == "azure-di"
    azure_line = next(line for line in result.package.lines if line.source_extractor == "azure-di")
    assert azure_line.text_raw == "Line A"
    assert azure_line.bbox == [0.0, 0.0, 2.0, 1.0]
    assert len(result.package.tables) == 1
    assert len(result.package.figures) == 1


def test_both_success_auto_prefers_azure(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(package, selected=ExtractorType.BOTH, azure_status=ExtractorRunStatus.SUCCEEDED)
    _write_marker(package, "**1.** Sample question\n")
    _write_azure(
        package,
        lines=[{"content": "Azure line 1", "pageNumber": 1}],
        pages=[{"pageNumber": 1, "lines": [{"content": "Azure line 1"}]}],
    )

    result = normalize_evidence_package(package, primary_extractor=PrimaryExtractorMode.AUTO)

    assert result.package.extraction_status == EvidenceExtractionStatus.SUCCEEDED
    assert result.package.primary_extractor == "azure-di"
    assert result.comparison.marker_line_count >= 1
    assert result.comparison.azure_line_count == 1
    assert (package / "diagnostics" / EXTRACTOR_COMPARISON_JSON_NAME).exists()


def test_partial_marker_succeeded_azure_failed(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(
        package,
        selected=ExtractorType.BOTH,
        azure_status=ExtractorRunStatus.FAILED,
        errors=["Missing Azure Document Intelligence credentials."],
    )
    _write_marker(package, "**1.** Question text\n1.A\n")

    result = normalize_evidence_package(package)

    assert result.package.extraction_status == EvidenceExtractionStatus.PARTIAL
    assert result.package.primary_extractor == "marker"
    assert "azure_di_failed_marker_available" in result.package.warnings
    assert result.summary.marker_available
    assert not result.summary.azure_di_available or result.comparison.azure_line_count == 0


def test_partial_azure_succeeded_marker_failed(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(
        package,
        selected=ExtractorType.BOTH,
        marker_status=ExtractorRunStatus.FAILED,
        azure_status=ExtractorRunStatus.SUCCEEDED,
    )
    _write_azure(
        package,
        lines=[{"content": "Azure only", "pageNumber": 1}],
        pages=[{"pageNumber": 1}],
    )

    result = normalize_evidence_package(package)

    assert result.package.extraction_status == EvidenceExtractionStatus.PARTIAL
    assert result.package.primary_extractor == "azure-di"
    assert "marker_failed_azure_di_available" in result.package.warnings


def test_all_failed_raises(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(
        package,
        selected=ExtractorType.BOTH,
        marker_status=ExtractorRunStatus.FAILED,
        azure_status=ExtractorRunStatus.FAILED,
    )

    with pytest.raises(EvidenceNormalizationError, match="No primary extractor evidence"):
        normalize_evidence_package(package)


def test_role_hints_answer_key_and_advertisement(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(package)
    _write_marker(
        package,
        "1.A\nAns.(d)\nFree Mock Test Download PDF at www.example.com\n",
    )

    result = normalize_evidence_package(package)
    hints = {hint for line in result.package.lines for hint in line.role_hints}

    assert RoleHint.ANSWER_KEY_CANDIDATE in hints
    assert RoleHint.ADVERTISEMENT_CANDIDATE in hints
    assert RoleHint.DOWNLOAD_LINK_CANDIDATE in hints
    assert result.summary.noise_candidate_count >= 1


def test_metadata_candidate_from_ssc_line(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(package)
    _write_marker(package, "SSC CGL Tier 2 Shift 1 Exam Paper\n")

    result = normalize_evidence_package(package)

    assert result.summary.metadata_candidate_count >= 1
    assert any("SSC" in c.value_raw for c in result.package.metadata_candidates)


def test_no_question_candidates_created(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(package)
    _write_marker(package, "**1.** Question?\n(A) one\n(B) two\n")

    result = normalize_evidence_package(package)

    payload = json.loads((package / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME).read_text(encoding="utf-8"))
    assert "questions" not in payload
    assert "options" not in payload
    assert "question_candidates" not in payload
    assert result.summary.line_count >= 1


def test_evidence_summary_written(tmp_path: Path) -> None:
    package = tmp_path / "extraction_package"
    _write_manifest(package)
    _write_marker(package, "line one\n")

    result = normalize_evidence_package(package)

    summary = json.loads((package / EVIDENCE_DIR / EVIDENCE_SUMMARY_JSON_NAME).read_text(encoding="utf-8"))
    assert summary["primary_extractor"] == "marker"
    assert summary["line_count"] >= 1
