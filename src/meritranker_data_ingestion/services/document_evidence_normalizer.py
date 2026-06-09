"""Unified evidence normalization from Marker and Azure DI artifacts (Part 13B)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.config import (
    ASSETS_DIR_NAME,
    AZURE_DI_CONTENT_MD_NAME,
    AZURE_DI_DIR,
    AZURE_DI_FIGURES_NAME,
    AZURE_DI_LAYOUT_RESPONSE_NAME,
    AZURE_DI_LINES_NAME,
    AZURE_DI_PAGES_NAME,
    AZURE_DI_PARAGRAPHS_NAME,
    AZURE_DI_TABLES_NAME,
    DOCUMENT_EVIDENCE_MD_NAME,
    DOCUMENT_EVIDENCE_PACKAGE_VERSION,
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    EVIDENCE_SUMMARY_JSON_NAME,
    EXTRACTOR_COMPARISON_JSON_NAME,
    EXTRACTOR_COMPARISON_MD_NAME,
    EXTRACTOR_MANIFEST_NAME,
    EXTRACTORS_DIR,
    MARKER_DIR,
    RAW_MARKDOWN_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceFigure,
    EvidenceImage,
    EvidenceLine,
    EvidencePage,
    EvidenceSummary,
    EvidenceTable,
    ExtractorComparison,
    MetadataCandidate,
    PrimaryExtractorMode,
    RoleHint,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.extractor import ExtractorManifest, ExtractorRunStatus, ExtractorType
from meritranker_data_ingestion.services.evidence_role_hints import (
    detect_role_hints,
    extract_metadata_candidates,
    is_noise_line,
    preview_text,
)
from meritranker_data_ingestion.services.file_service import PathValidationError, resolve_path

RE_IMAGE_MD = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class EvidenceNormalizationError(Exception):
    """Raised when evidence normalization cannot complete."""


@dataclass(frozen=True)
class NormalizationResult:
    """Outcome of evidence normalization."""

    package: DocumentEvidencePackage
    summary: EvidenceSummary
    comparison: ExtractorComparison
    evidence_json_path: Path
    summary_json_path: Path
    comparison_json_path: Path


def normalize_evidence_package(
    package_dir: Path,
    *,
    primary_extractor: PrimaryExtractorMode | str = PrimaryExtractorMode.AUTO,
) -> NormalizationResult:
    """Normalize Marker/Azure artifacts into canonical document evidence."""
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")

    mode = (
        primary_extractor
        if isinstance(primary_extractor, PrimaryExtractorMode)
        else PrimaryExtractorMode(primary_extractor)
    )

    manifest = _load_extractor_manifest(resolved)
    paths = _resolve_artifact_paths(resolved, manifest)

    marker_lines, marker_images = _normalize_marker(paths)
    azure_pages, azure_lines, azure_tables, azure_figures, azure_images = _normalize_azure(paths)

    marker_available = paths.marker_raw_md.exists()
    azure_available = paths.azure_lines.exists() or paths.azure_pages.exists()

    marker_status = _extractor_status(manifest, "marker", marker_available)
    azure_status = _extractor_status(manifest, "azure-di", azure_available)

    warnings: list[str] = []
    errors: list[str] = []
    if manifest is not None:
        warnings.extend(manifest.warnings)
        errors.extend(manifest.errors)

    selected = manifest.selected_extractor if manifest else ExtractorType.MARKER
    if selected == ExtractorType.BOTH:
        marker_ok = marker_status == ExtractorRunStatus.SUCCEEDED.value
        azure_ok = azure_status == ExtractorRunStatus.SUCCEEDED.value
        if marker_ok and not azure_ok:
            warnings.append("azure_di_failed_marker_available")
            if any("credentials" in e.lower() for e in errors):
                warnings.append("azure_di_credentials_missing")
        elif azure_ok and not marker_ok:
            warnings.append("marker_failed_azure_di_available")

    primary, fallback_reason = _select_primary_extractor(
        mode=mode,
        marker_status=marker_status,
        azure_status=azure_status,
        marker_available=marker_available,
        azure_available=azure_available,
        marker_line_count=len(marker_lines),
        azure_line_count=len(azure_lines),
        warnings=warnings,
    )

    extraction_status = _derive_extraction_status(
        selected_extractor=selected.value if manifest else "marker",
        marker_status=marker_status,
        azure_status=azure_status,
        marker_available=marker_available,
        azure_available=azure_available,
        primary=primary,
    )

    if extraction_status == EvidenceExtractionStatus.FAILED:
        raise EvidenceNormalizationError(
            "No extractor evidence available to normalize. "
            f"marker_status={marker_status}, azure_di_status={azure_status}",
        )

    all_lines = marker_lines + azure_lines
    metadata_candidates = _collect_metadata_candidates(all_lines)
    noise_candidates = [line for line in all_lines if is_noise_line(line.role_hints)]

    extractors_used: list[str] = []
    if marker_lines:
        extractors_used.append("marker")
    if azure_lines or azure_pages:
        extractors_used.append("azure-di")

    extractors_available: list[str] = []
    if marker_available:
        extractors_available.append("marker")
    if azure_available:
        extractors_available.append("azure-di")

    evidence_package = DocumentEvidencePackage(
        package_version=DOCUMENT_EVIDENCE_PACKAGE_VERSION,
        source_file_name=manifest.source_file_name if manifest else resolved.name,
        primary_extractor=primary,
        extractors_available=extractors_available,
        extractors_used=extractors_used,
        extraction_status=extraction_status,
        pages=azure_pages,
        lines=all_lines,
        tables=azure_tables,
        figures=azure_figures,
        images=marker_images + azure_images,
        metadata_candidates=metadata_candidates,
        noise_candidates=noise_candidates,
        source_artifact_paths={
            "extractor_manifest": str(paths.extractor_manifest) if paths.extractor_manifest.exists() else None,
            "marker_raw_md": str(paths.marker_raw_md) if paths.marker_raw_md.exists() else None,
            "marker_assets_dir": str(paths.marker_assets_dir) if paths.marker_assets_dir.exists() else None,
            "azure_layout_response": str(paths.azure_layout) if paths.azure_layout.exists() else None,
            "azure_lines": str(paths.azure_lines) if paths.azure_lines.exists() else None,
            "azure_pages": str(paths.azure_pages) if paths.azure_pages.exists() else None,
        },
        warnings=_dedupe(warnings),
        errors=errors,
    )

    summary = _build_summary(evidence_package, marker_available, azure_available)
    comparison = ExtractorComparison(
        marker_status=marker_status,
        azure_di_status=azure_status,
        marker_line_count=len(marker_lines),
        azure_line_count=len(azure_lines),
        marker_image_count=len(marker_images),
        azure_figure_count=len(azure_figures),
        azure_table_count=len(azure_tables),
        pages_detected=len(azure_pages),
        primary_extractor=primary,
        fallback_reason=fallback_reason,
        likely_scanned_pages=_estimate_scanned_pages(azure_pages, azure_lines),
        warnings=_dedupe(warnings),
        errors=errors,
    )

    evidence_dir = resolved / EVIDENCE_DIR
    diagnostics_dir = resolved / "diagnostics"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    evidence_json = evidence_dir / DOCUMENT_EVIDENCE_JSON_NAME
    evidence_md = evidence_dir / DOCUMENT_EVIDENCE_MD_NAME
    summary_json = evidence_dir / EVIDENCE_SUMMARY_JSON_NAME
    comparison_json = diagnostics_dir / EXTRACTOR_COMPARISON_JSON_NAME
    comparison_md = diagnostics_dir / EXTRACTOR_COMPARISON_MD_NAME

    evidence_json.write_text(evidence_package.model_dump_json(indent=2), encoding="utf-8")
    summary_json.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    comparison_json.write_text(comparison.model_dump_json(indent=2), encoding="utf-8")
    evidence_md.write_text(_render_evidence_md(evidence_package, summary), encoding="utf-8")
    comparison_md.write_text(_render_comparison_md(comparison), encoding="utf-8")

    return NormalizationResult(
        package=evidence_package,
        summary=summary,
        comparison=comparison,
        evidence_json_path=evidence_json,
        summary_json_path=summary_json,
        comparison_json_path=comparison_json,
    )


@dataclass(frozen=True)
class _ArtifactPaths:
    extractor_manifest: Path
    marker_raw_md: Path
    marker_assets_dir: Path
    azure_dir: Path
    azure_layout: Path
    azure_lines: Path
    azure_pages: Path
    azure_tables: Path
    azure_figures: Path
    azure_paragraphs: Path
    azure_content_md: Path


def _resolve_artifact_paths(package_dir: Path, manifest: ExtractorManifest | None) -> _ArtifactPaths:
    azure_dir = package_dir / EXTRACTORS_DIR / AZURE_DI_DIR
    return _ArtifactPaths(
        extractor_manifest=package_dir / EXTRACTORS_DIR / EXTRACTOR_MANIFEST_NAME,
        marker_raw_md=package_dir / MARKER_DIR / RAW_MARKDOWN_NAME,
        marker_assets_dir=package_dir / MARKER_DIR / ASSETS_DIR_NAME,
        azure_dir=azure_dir,
        azure_layout=azure_dir / AZURE_DI_LAYOUT_RESPONSE_NAME,
        azure_lines=azure_dir / AZURE_DI_LINES_NAME,
        azure_pages=azure_dir / AZURE_DI_PAGES_NAME,
        azure_tables=azure_dir / AZURE_DI_TABLES_NAME,
        azure_figures=azure_dir / AZURE_DI_FIGURES_NAME,
        azure_paragraphs=azure_dir / AZURE_DI_PARAGRAPHS_NAME,
        azure_content_md=azure_dir / AZURE_DI_CONTENT_MD_NAME,
    )


def _load_extractor_manifest(package_dir: Path) -> ExtractorManifest | None:
    path = package_dir / EXTRACTORS_DIR / EXTRACTOR_MANIFEST_NAME
    if not path.exists():
        return None
    return ExtractorManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _normalize_marker(paths: _ArtifactPaths) -> tuple[list[EvidenceLine], list[EvidenceImage]]:
    if not paths.marker_raw_md.exists():
        return [], _scan_marker_assets(paths)

    raw_md_path = str(paths.marker_raw_md)
    text = paths.marker_raw_md.read_text(encoding="utf-8")
    lines: list[EvidenceLine] = []
    images: list[EvidenceImage] = []
    image_index = 0

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line_id = f"marker_l_{line_no:06d}"
        hints = detect_role_hints(raw_line)
        line = EvidenceLine(
            line_id=line_id,
            page_number=None,
            text_raw=raw_line,
            normalized_preview=preview_text(raw_line),
            bbox=None,
            source_extractor="marker",
            source_artifact_path=raw_md_path,
            source_line_number=line_no,
            source_span=SourceSpan(
                extractor="marker",
                line_id=line_id,
                source_artifact_path=raw_md_path,
                raw_index=line_no,
            ),
            role_hints=hints,
            confidence=1.0,
        )
        lines.append(line)

        for match in RE_IMAGE_MD.finditer(raw_line):
            image_index += 1
            rel_path = match.group(2)
            asset_path = str(paths.marker_assets_dir / Path(rel_path).name)
            images.append(
                EvidenceImage(
                    image_id=f"marker_img_{image_index:04d}",
                    asset_path=asset_path if paths.marker_assets_dir.exists() else rel_path,
                    page_number=None,
                    source_extractor="marker",
                    source_refs=[
                        SourceSpan(
                            extractor="marker",
                            line_id=line_id,
                            source_artifact_path=raw_md_path,
                            raw_index=line_no,
                        ),
                    ],
                    role_hints=[RoleHint.IMAGE_REFERENCE_CANDIDATE],
                    confidence=1.0,
                ),
            )

    images.extend(_scan_marker_assets(paths, start_index=image_index))
    return lines, images


def _scan_marker_assets(paths: _ArtifactPaths, start_index: int = 0) -> list[EvidenceImage]:
    if not paths.marker_assets_dir.exists():
        return []
    images: list[EvidenceImage] = []
    idx = start_index
    for asset in sorted(paths.marker_assets_dir.rglob("*")):
        if not asset.is_file() or asset.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        idx += 1
        images.append(
            EvidenceImage(
                image_id=f"marker_img_{idx:04d}",
                asset_path=str(asset),
                page_number=None,
                source_extractor="marker",
                source_refs=[
                    SourceSpan(
                        extractor="marker",
                        source_artifact_path=str(paths.marker_assets_dir),
                    ),
                ],
                confidence=0.9,
            ),
        )
    return images


def _normalize_azure(
    paths: _ArtifactPaths,
) -> tuple[list[EvidencePage], list[EvidenceLine], list[EvidenceTable], list[EvidenceFigure], list[EvidenceImage]]:
    pages_raw = _load_json_list(paths.azure_pages)
    lines_raw = _load_json_list(paths.azure_lines)
    tables_raw = _load_json_list(paths.azure_tables)
    figures_raw = _load_json_list(paths.azure_figures)

    pages: list[EvidencePage] = []
    page_line_map: dict[int, list[str]] = {}

    for page_idx, page in enumerate(pages_raw, start=1):
        page_number = int(page.get("pageNumber") or page_idx)
        page_id = f"azure_p_{page_number:04d}"
        line_ids: list[str] = []
        for line in page.get("lines") or []:
            if isinstance(line, dict) and line.get("content"):
                line_ids.append(f"azure_l_pending_{page_number}_{len(line_ids)}")

        evidence_page = EvidencePage(
            page_id=page_id,
            page_number=page_number,
            width=_as_float(page.get("width")),
            height=_as_float(page.get("height")),
            text_raw=_page_text(page),
            line_ids=line_ids,
            source_refs=[
                SourceSpan(
                    extractor="azure-di",
                    page_number=page_number,
                    source_artifact_path=str(paths.azure_pages) if paths.azure_pages.exists() else None,
                ),
            ],
            confidence=1.0,
        )
        pages.append(evidence_page)
        page_line_map[page_number] = line_ids

    lines: list[EvidenceLine] = []
    for line_idx, line in enumerate(lines_raw, start=1):
        line_id = f"azure_l_{line_idx:06d}"
        page_number = line.get("pageNumber")
        if isinstance(page_number, (int, float)):
            page_number = int(page_number)
        else:
            page_number = None

        text_raw = str(line.get("content") or "")
        hints = detect_role_hints(text_raw)
        bbox = _polygon_to_bbox(line.get("polygon"))

        evidence_line = EvidenceLine(
            line_id=line_id,
            page_number=page_number,
            text_raw=text_raw,
            normalized_preview=preview_text(text_raw),
            bbox=bbox,
            source_extractor="azure-di",
            source_artifact_path=str(paths.azure_lines) if paths.azure_lines.exists() else None,
            source_line_number=line_idx,
            source_span=SourceSpan(
                extractor="azure-di",
                page_number=page_number,
                line_id=line_id,
                source_artifact_path=str(paths.azure_lines) if paths.azure_lines.exists() else None,
                raw_index=line_idx,
                bbox=bbox,
            ),
            role_hints=hints,
            confidence=1.0,
        )
        lines.append(evidence_line)

        if page_number is not None and page_number in page_line_map:
            pending = page_line_map[page_number]
            if pending:
                pending.pop(0)
            page_line_map[page_number].append(line_id)

    for page in pages:
        page.line_ids = page_line_map.get(page.page_number, page.line_ids)

    tables: list[EvidenceTable] = []
    for table_idx, table in enumerate(tables_raw, start=1):
        page_number = _table_page_number(table)
        tables.append(
            EvidenceTable(
                table_id=f"azure_t_{table_idx:06d}",
                page_number=page_number,
                cells=_normalize_table_cells(table),
                bbox=_region_bbox(table),
                source_extractor="azure-di",
                source_refs=[
                    SourceSpan(
                        extractor="azure-di",
                        page_number=page_number,
                        source_artifact_path=str(paths.azure_tables) if paths.azure_tables.exists() else None,
                    ),
                ],
                confidence=1.0,
            ),
        )

    figures: list[EvidenceFigure] = []
    images: list[EvidenceImage] = []
    for fig_idx, figure in enumerate(figures_raw, start=1):
        page_number = _figure_page_number(figure)
        bbox = _region_bbox(figure)
        figure_id = f"azure_f_{fig_idx:06d}"
        figures.append(
            EvidenceFigure(
                figure_id=figure_id,
                page_number=page_number,
                asset_path=None,
                bbox=bbox,
                caption=figure.get("caption") if isinstance(figure.get("caption"), str) else None,
                source_extractor="azure-di",
                source_refs=[
                    SourceSpan(
                        extractor="azure-di",
                        page_number=page_number,
                        source_artifact_path=str(paths.azure_figures) if paths.azure_figures.exists() else None,
                        bbox=bbox,
                    ),
                ],
                confidence=1.0,
            ),
        )
        images.append(
            EvidenceImage(
                image_id=f"azure_img_{fig_idx:04d}",
                asset_path=None,
                page_number=page_number,
                source_extractor="azure-di",
                source_refs=[
                    SourceSpan(
                        extractor="azure-di",
                        page_number=page_number,
                        source_artifact_path=str(paths.azure_figures) if paths.azure_figures.exists() else None,
                        bbox=bbox,
                    ),
                ],
                confidence=0.95,
            ),
        )

    return pages, lines, tables, figures, images


def _select_primary_extractor(
    *,
    mode: PrimaryExtractorMode,
    marker_status: str,
    azure_status: str,
    marker_available: bool,
    azure_available: bool,
    marker_line_count: int,
    azure_line_count: int,
    warnings: list[str],
) -> tuple[str, str | None]:
    fallback_reason: str | None = None

    if mode == PrimaryExtractorMode.MARKER:
        if marker_available and marker_line_count > 0:
            return "marker", None
        raise EvidenceNormalizationError("Marker primary selected but marker evidence unavailable.")

    if mode == PrimaryExtractorMode.AZURE_DI:
        if azure_available and azure_line_count > 0:
            return "azure-di", None
        raise EvidenceNormalizationError("Azure DI primary selected but azure evidence unavailable.")

    azure_ok = azure_status == ExtractorRunStatus.SUCCEEDED.value and azure_line_count > 0
    marker_ok = marker_status == ExtractorRunStatus.SUCCEEDED.value and marker_line_count > 0

    if azure_ok:
        if marker_ok:
            return "azure-di", None
        warnings.append("primary_extractor_fallback_to_azure_di")
        return "azure-di", "marker_unavailable_or_empty"

    if marker_ok:
        if azure_available and azure_status != ExtractorRunStatus.SUCCEEDED.value:
            warnings.append("primary_extractor_fallback_to_marker")
            fallback_reason = "azure_di_unavailable_or_empty"
        return "marker", fallback_reason

    if marker_available and marker_line_count > 0:
        warnings.append("primary_extractor_fallback_to_marker")
        return "marker", "azure_di_unavailable_or_empty"

    if azure_available and azure_line_count > 0:
        warnings.append("primary_extractor_fallback_to_azure_di")
        return "azure-di", "marker_unavailable_or_empty"

    raise EvidenceNormalizationError("No primary extractor evidence available.")


def _derive_extraction_status(
    *,
    selected_extractor: str,
    marker_status: str,
    azure_status: str,
    marker_available: bool,
    azure_available: bool,
    primary: str,
) -> EvidenceExtractionStatus:
    marker_ok = marker_status == ExtractorRunStatus.SUCCEEDED.value or (
        marker_available and marker_status in {ExtractorRunStatus.SKIPPED.value, "unknown"}
    )
    azure_ok = azure_status == ExtractorRunStatus.SUCCEEDED.value

    if not primary:
        return EvidenceExtractionStatus.FAILED

    if selected_extractor == ExtractorType.BOTH.value:
        if marker_ok and azure_ok:
            return EvidenceExtractionStatus.SUCCEEDED
        if marker_ok or azure_ok:
            return EvidenceExtractionStatus.PARTIAL
        return EvidenceExtractionStatus.FAILED

    if primary == "marker" and marker_available:
        return EvidenceExtractionStatus.SUCCEEDED
    if primary == "azure-di" and azure_ok:
        return EvidenceExtractionStatus.SUCCEEDED
    if marker_available or azure_available:
        return EvidenceExtractionStatus.PARTIAL
    return EvidenceExtractionStatus.FAILED


def _extractor_status(
    manifest: ExtractorManifest | None,
    extractor: str,
    available: bool,
) -> str:
    if manifest is None:
        return ExtractorRunStatus.SUCCEEDED.value if available else ExtractorRunStatus.SKIPPED.value
    if extractor == "marker":
        return manifest.marker_status.value
    return manifest.azure_di_status.value


def _collect_metadata_candidates(lines: list[EvidenceLine]) -> list[MetadataCandidate]:
    candidates: list[MetadataCandidate] = []
    for index, line in enumerate(lines):
        candidate = extract_metadata_candidates(
            line.line_id,
            line.text_raw,
            extractor=line.source_extractor,
            source_artifact_path=line.source_artifact_path,
            page_number=line.page_number,
            source_line_number=line.source_line_number,
            line_index=index,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _build_summary(
    package: DocumentEvidencePackage,
    marker_available: bool,
    azure_available: bool,
) -> EvidenceSummary:
    role_counts: dict[str, int] = {}
    for line in package.lines:
        for hint in line.role_hints:
            role_counts[hint.value] = role_counts.get(hint.value, 0) + 1

    primary_lines = [line for line in package.lines if line.source_extractor == package.primary_extractor]

    return EvidenceSummary(
        source_file_name=package.source_file_name,
        primary_extractor=package.primary_extractor,
        extraction_status=package.extraction_status,
        marker_available=marker_available,
        azure_di_available=azure_available,
        page_count=len(package.pages),
        line_count=len(primary_lines),
        table_count=len(package.tables),
        figure_count=len(package.figures),
        image_count=len(package.images),
        role_hint_counts=role_counts,
        metadata_candidate_count=len(package.metadata_candidates),
        noise_candidate_count=len(package.noise_candidates),
        warnings=package.warnings,
        errors=package.errors,
    )


def _estimate_scanned_pages(pages: list[EvidencePage], lines: list[EvidenceLine]) -> int | None:
    if not pages:
        return None
    lines_by_page: dict[int, int] = {}
    for line in lines:
        if line.page_number is not None:
            lines_by_page[line.page_number] = lines_by_page.get(line.page_number, 0) + 1
    scanned = sum(1 for page in pages if lines_by_page.get(page.page_number, 0) > 0)
    return scanned if scanned > 0 else None


def _polygon_to_bbox(polygon: Any) -> list[float] | None:
    if not isinstance(polygon, list) or len(polygon) < 4:
        return None
    try:
        xs = [float(polygon[i]) for i in range(0, len(polygon), 2)]
        ys = [float(polygon[i + 1]) for i in range(0, len(polygon) - 1, 2)]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _region_bbox(item: dict[str, Any]) -> list[float] | None:
    regions = item.get("boundingRegions") or item.get("bounding_regions")
    if isinstance(regions, list) and regions:
        first = regions[0]
        if isinstance(first, dict):
            return _polygon_to_bbox(first.get("polygon"))
    return _polygon_to_bbox(item.get("polygon"))


def _table_page_number(table: dict[str, Any]) -> int | None:
    regions = table.get("boundingRegions") or table.get("bounding_regions")
    if isinstance(regions, list) and regions:
        first = regions[0]
        if isinstance(first, dict) and first.get("pageNumber") is not None:
            return int(first["pageNumber"])
    return None


def _figure_page_number(figure: dict[str, Any]) -> int | None:
    regions = figure.get("boundingRegions") or figure.get("bounding_regions")
    if isinstance(regions, list) and regions:
        first = regions[0]
        if isinstance(first, dict) and first.get("pageNumber") is not None:
            return int(first["pageNumber"])
    return None


def _normalize_table_cells(table: dict[str, Any]) -> list[dict[str, Any]]:
    cells = table.get("cells")
    if isinstance(cells, list):
        return [cell for cell in cells if isinstance(cell, dict)]
    return [{"raw": table}]


def _page_text(page: dict[str, Any]) -> str | None:
    lines = page.get("lines")
    if not isinstance(lines, list):
        return None
    parts = [str(line.get("content")) for line in lines if isinstance(line, dict) and line.get("content")]
    return "\n".join(parts) if parts else None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _render_evidence_md(package: DocumentEvidencePackage, summary: EvidenceSummary) -> str:
    lines = [
        "# Document Evidence Summary",
        "",
        f"- Source: `{package.source_file_name}`",
        f"- Status: `{package.extraction_status.value}`",
        f"- Primary extractor: `{package.primary_extractor}`",
        f"- Lines (primary): {summary.line_count}",
        f"- Pages: {summary.page_count}",
        f"- Tables: {summary.table_count}",
        f"- Figures: {summary.figure_count}",
        f"- Images: {summary.image_count}",
        f"- Metadata candidates: {summary.metadata_candidate_count}",
        f"- Noise candidates: {summary.noise_candidate_count}",
        "",
    ]
    if package.warnings:
        lines.append("## Warnings")
        lines.extend(f"- {warning}" for warning in package.warnings)
        lines.append("")
    return "\n".join(lines)


def _render_comparison_md(comparison: ExtractorComparison) -> str:
    lines = [
        "# Extractor Comparison",
        "",
        f"- Marker status: `{comparison.marker_status}`",
        f"- Azure DI status: `{comparison.azure_di_status}`",
        f"- Marker lines: {comparison.marker_line_count}",
        f"- Azure lines: {comparison.azure_line_count}",
        f"- Marker images: {comparison.marker_image_count}",
        f"- Azure figures: {comparison.azure_figure_count}",
        f"- Azure tables: {comparison.azure_table_count}",
        f"- Pages detected: {comparison.pages_detected}",
        f"- Primary extractor: `{comparison.primary_extractor}`",
    ]
    if comparison.fallback_reason:
        lines.append(f"- Fallback reason: `{comparison.fallback_reason}`")
    if comparison.likely_scanned_pages is not None:
        lines.append(f"- Likely scanned pages: {comparison.likely_scanned_pages}")
    lines.append("")
    if comparison.warnings:
        lines.append("## Warnings")
        lines.extend(f"- {warning}" for warning in comparison.warnings)
        lines.append("")
    return "\n".join(lines)
