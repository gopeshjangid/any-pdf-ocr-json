"""Extraction package path layout and artifact helpers."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    ASSETS_DIR_NAME,
    AZURE_DI_CONTENT_MD_NAME,
    AZURE_DI_DIR,
    AZURE_DI_EXTRACTION_LOG_NAME,
    AZURE_DI_FIGURES_NAME,
    AZURE_DI_LAYOUT_RESPONSE_NAME,
    AZURE_DI_LINES_NAME,
    AZURE_DI_PAGES_NAME,
    AZURE_DI_PARAGRAPHS_NAME,
    AZURE_DI_TABLES_NAME,
    EXTRACTOR_MANIFEST_NAME,
    EXTRACTORS_DIR,
    EXTRACTION_LOG_NAME,
    EXTRACTION_PACKAGE_DIR,
    LOGS_DIR,
    MARKER_DIR,
    MARKER_WORK_DIR,
    ORIGINAL_PDF_NAME,
    PACKAGE_MANIFEST_NAME,
    RAW_MARKDOWN_NAME,
    SOURCE_DIR,
)
from meritranker_data_ingestion.services.file_service import (
    assert_output_contains,
    copy_file_into_output,
)


@dataclass(frozen=True)
class AzureDiArtifactPaths:
    """Resolved paths for Azure Document Intelligence evidence artifacts."""

    azure_di_dir: Path
    layout_response: Path
    content_md: Path
    pages: Path
    lines: Path
    tables: Path
    figures: Path
    paragraphs: Path
    extraction_log: Path


@dataclass(frozen=True)
class ExtractionPackagePaths:
    """Resolved paths for a standard extraction package."""

    output_dir: Path
    package_dir: Path
    source_pdf: Path
    marker_dir: Path
    marker_work_dir: Path
    raw_markdown: Path
    assets_dir: Path
    logs_dir: Path
    extraction_log: Path
    manifest_path: Path
    extractors_dir: Path
    extractor_manifest: Path
    azure_di: AzureDiArtifactPaths


def build_azure_di_paths(package_dir: Path) -> AzureDiArtifactPaths:
    """Compute Azure DI artifact paths under extractors/azure-di/."""
    azure_di_dir = package_dir / EXTRACTORS_DIR / AZURE_DI_DIR
    return AzureDiArtifactPaths(
        azure_di_dir=azure_di_dir,
        layout_response=azure_di_dir / AZURE_DI_LAYOUT_RESPONSE_NAME,
        content_md=azure_di_dir / AZURE_DI_CONTENT_MD_NAME,
        pages=azure_di_dir / AZURE_DI_PAGES_NAME,
        lines=azure_di_dir / AZURE_DI_LINES_NAME,
        tables=azure_di_dir / AZURE_DI_TABLES_NAME,
        figures=azure_di_dir / AZURE_DI_FIGURES_NAME,
        paragraphs=azure_di_dir / AZURE_DI_PARAGRAPHS_NAME,
        extraction_log=azure_di_dir / AZURE_DI_EXTRACTION_LOG_NAME,
    )


def build_package_paths(output_dir: Path) -> ExtractionPackagePaths:
    """Compute standard extraction package paths under output_dir."""
    package_dir = output_dir / EXTRACTION_PACKAGE_DIR
    marker_dir = package_dir / MARKER_DIR
    extractors_dir = package_dir / EXTRACTORS_DIR
    return ExtractionPackagePaths(
        output_dir=output_dir,
        package_dir=package_dir,
        source_pdf=package_dir / SOURCE_DIR / ORIGINAL_PDF_NAME,
        marker_dir=marker_dir,
        marker_work_dir=marker_dir / MARKER_WORK_DIR,
        raw_markdown=marker_dir / RAW_MARKDOWN_NAME,
        assets_dir=marker_dir / ASSETS_DIR_NAME,
        logs_dir=package_dir / LOGS_DIR,
        extraction_log=package_dir / LOGS_DIR / EXTRACTION_LOG_NAME,
        manifest_path=package_dir / PACKAGE_MANIFEST_NAME,
        extractors_dir=extractors_dir,
        extractor_manifest=extractors_dir / EXTRACTOR_MANIFEST_NAME,
        azure_di=build_azure_di_paths(package_dir),
    )


def create_package_directories(paths: ExtractionPackagePaths) -> None:
    """Create extraction package directory tree."""
    for directory in (
        paths.package_dir,
        paths.source_pdf.parent,
        paths.marker_dir,
        paths.marker_work_dir,
        paths.assets_dir,
        paths.logs_dir,
        paths.extractors_dir,
        paths.azure_di.azure_di_dir,
    ):
        assert_output_contains(paths.output_dir, directory)
        directory.mkdir(parents=True, exist_ok=True)


def copy_source_pdf(input_pdf: Path, paths: ExtractionPackagePaths) -> None:
    """Byte-copy input PDF to package source/original.pdf (no transformation)."""
    assert_output_contains(paths.output_dir, paths.source_pdf)
    copy_file_into_output(input_pdf, paths.source_pdf, paths.output_dir)


def discover_marker_markdown(marker_work_dir: Path, pdf_stem: str) -> Path | None:
    """
    Locate Marker-generated markdown under its work output directory.

    Prefers a file whose stem matches the PDF stem; otherwise the sole .md file.
    """
    if not marker_work_dir.exists():
        return None

    md_files = sorted(marker_work_dir.rglob("*.md"))
    if not md_files:
        return None

    stem_matches = [p for p in md_files if p.stem == pdf_stem]
    if len(stem_matches) == 1:
        return stem_matches[0]
    if len(stem_matches) > 1:
        return stem_matches[0]

    if len(md_files) == 1:
        return md_files[0]

    return md_files[0]


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS


def discover_marker_image_files(marker_work_dir: Path) -> list[Path]:
    """Find all image files under Marker work output (including nested folders)."""
    if not marker_work_dir.exists():
        return []
    return sorted(p for p in marker_work_dir.rglob("*") if _is_image_file(p))


def discover_marker_assets_dir(marker_work_dir: Path, markdown_path: Path | None) -> Path | None:
    """Find Marker images/assets directory near generated markdown."""
    candidates: list[Path] = []

    if markdown_path is not None:
        parent = markdown_path.parent
        for name in ("images", ASSETS_DIR_NAME, "figures"):
            candidate = parent / name
            if candidate.is_dir():
                candidates.append(candidate)

    for name in ("images", ASSETS_DIR_NAME, "figures"):
        found = list(marker_work_dir.rglob(name))
        for candidate in found:
            if candidate.is_dir():
                candidates.append(candidate)

    if not candidates:
        image_files = discover_marker_image_files(marker_work_dir)
        if image_files:
            return marker_work_dir
        return None

    def image_count(path: Path) -> int:
        return sum(1 for p in path.rglob("*") if _is_image_file(p))

    return max(candidates, key=image_count)


def copy_marker_markdown(source_md: Path, dest_md: Path, output_dir: Path) -> None:
    """Copy Marker markdown verbatim to package marker/raw.md."""
    assert_output_contains(output_dir, dest_md)
    dest_md.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_md, dest_md)


def copy_marker_assets(source_assets: Path, dest_assets: Path, output_dir: Path) -> None:
    """Copy Marker asset files verbatim into package marker/assets/."""
    assert_output_contains(output_dir, dest_assets)
    if dest_assets.exists():
        shutil.rmtree(dest_assets)
    dest_assets.mkdir(parents=True, exist_ok=True)

    image_files = [p for p in source_assets.rglob("*") if _is_image_file(p)]
    if not image_files:
        return

    copied_basenames: set[str] = set()
    for item in image_files:
        relative = item.relative_to(source_assets)
        target = dest_assets / relative
        assert_output_contains(output_dir, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)

        flat_target = dest_assets / item.name
        if item.name not in copied_basenames:
            assert_output_contains(output_dir, flat_target)
            shutil.copy2(item, flat_target)
            copied_basenames.add(item.name)


def count_image_references_in_markdown(markdown_path: Path) -> int:
    """Count markdown image references in raw.md."""
    try:
        text = markdown_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return text.count("![")


def try_read_page_count(pdf_path: Path) -> int | None:
    """
    Best-effort page count from PDF without extra dependencies.

    Counts /Type /Page occurrences (excludes /Pages). May be inaccurate for
    some PDFs; returns None if unreadable.
    """
    try:
        data = pdf_path.read_bytes()
    except OSError:
        return None

    marker = b"/Type /Page"
    end_marker = b"/Type /Pages"
    count = 0
    idx = 0
    while True:
        pos = data.find(marker, idx)
        if pos == -1:
            break
        # Exclude /Type /Pages (longer match)
        if data[pos : pos + len(end_marker)] != end_marker:
            count += 1
        idx = pos + len(marker)

    return count if count > 0 else None
