"""Marker parser adapter — subprocess-based extraction (no Marker Python imports)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import DEFAULT_PARSER_ENGINE
from meritranker_data_ingestion.schemas.extraction import (
    ExtractionPackageManifest,
    ExtractionStatus,
)
from meritranker_data_ingestion.services.extraction_package import (
    ExtractionPackagePaths,
    build_package_paths,
    copy_marker_assets,
    copy_marker_markdown,
    copy_source_pdf,
    count_image_references_in_markdown,
    create_package_directories,
    discover_marker_assets_dir,
    discover_marker_image_files,
    discover_marker_markdown,
    try_read_page_count,
)
from meritranker_data_ingestion.services.file_service import (
    assert_output_contains,
    validate_and_prepare_output_dir,
    validate_input_pdf,
)
from meritranker_data_ingestion.services.marker_runner import (
    MarkerCommandRunner,
    SubprocessMarkerRunner,
    build_marker_command,
)


class MarkerAdapter:
    """
    Adapter for Marker PDF extraction via external CLI subprocess.

    Produces a standard extraction package with source PDF copy, raw markdown,
    optional assets, logs, and manifest. Does not parse questions or transform
    extracted content.
    """

    def __init__(self, runner: MarkerCommandRunner | None = None) -> None:
        self._runner = runner or SubprocessMarkerRunner()

    @property
    def name(self) -> str:
        return DEFAULT_PARSER_ENGINE

    def extract(
        self,
        input_pdf_path: Path,
        output_dir: Path,
    ) -> ExtractionPackageManifest:
        validated_input = validate_input_pdf(input_pdf_path)
        validated_output = validate_and_prepare_output_dir(output_dir)
        paths = build_package_paths(validated_output)

        manifest = ExtractionPackageManifest(
            input_pdf_path=validated_input,
            source_file_name=validated_input.name,
            output_dir=validated_output,
            parser_engine=self.name,
            status=ExtractionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )

        try:
            self._run_extraction(validated_input, paths, manifest)
        except Exception as exc:  # noqa: BLE001 — surface as failed manifest, not stack trace
            if not manifest.errors:
                manifest.errors.append(f"Unexpected extraction error: {exc}")
            manifest.status = ExtractionStatus.FAILED
        finally:
            assert_output_contains(validated_output, paths.manifest_path)
            manifest.write_json(paths.manifest_path)

        return manifest

    def _run_extraction(
        self,
        validated_input: Path,
        paths: ExtractionPackagePaths,
        manifest: ExtractionPackageManifest,
    ) -> None:
        create_package_directories(paths)
        copy_source_pdf(validated_input, paths)

        page_count = try_read_page_count(paths.source_pdf)
        if page_count is not None:
            manifest.page_count = page_count

        command = build_marker_command(paths.source_pdf, paths.marker_work_dir)
        result = self._runner.run(command, log_path=paths.extraction_log)

        if result.error_message:
            manifest.errors.append(result.error_message)
            manifest.status = ExtractionStatus.FAILED
            return

        if result.returncode != 0:
            manifest.errors.append(
                f"Marker command failed with exit code {result.returncode}. "
                f"See log: {paths.extraction_log}",
            )
            manifest.status = ExtractionStatus.FAILED
            return

        pdf_stem = paths.source_pdf.stem
        discovered_md = discover_marker_markdown(paths.marker_work_dir, pdf_stem)
        if discovered_md is None:
            manifest.errors.append(
                "Marker completed but no markdown file was found in Marker output. "
                f"Checked: {paths.marker_work_dir}",
            )
            manifest.status = ExtractionStatus.FAILED
            return

        copy_marker_markdown(discovered_md, paths.raw_markdown, paths.output_dir)
        manifest.markdown_path = paths.raw_markdown

        discovered_assets = discover_marker_assets_dir(paths.marker_work_dir, discovered_md)
        if discovered_assets is not None:
            copy_marker_assets(discovered_assets, paths.assets_dir, paths.output_dir)
            manifest.assets_dir = paths.assets_dir
        else:
            nested_images = discover_marker_image_files(paths.marker_work_dir)
            if nested_images:
                copy_marker_assets(paths.marker_work_dir, paths.assets_dir, paths.output_dir)
                manifest.assets_dir = paths.assets_dir
            else:
                manifest.warnings.append(
                    "No Marker assets/images directory found; continuing with markdown only.",
                )

        image_ref_count = count_image_references_in_markdown(paths.raw_markdown)
        if image_ref_count > 0 and manifest.assets_dir is None:
            manifest.warnings.append("image_references_found_but_assets_missing")
        elif image_ref_count > 0 and manifest.assets_dir is not None:
            copied_images = discover_marker_image_files(paths.assets_dir)
            if not copied_images:
                manifest.warnings.append("image_references_found_but_assets_missing")

        manifest.status = ExtractionStatus.SUCCEEDED
