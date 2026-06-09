"""Evidence extractor orchestration for prepare (Marker + Azure DI)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import DEFAULT_AZURE_DI_MODEL, EXTRACTION_PACKAGE_DIR
from meritranker_data_ingestion.schemas.extraction import (
    ExtractionPackageManifest,
    ExtractionStatus,
)
from meritranker_data_ingestion.schemas.extractor import (
    ExtractorManifest,
    ExtractorRunStatus,
    ExtractorType,
)
from meritranker_data_ingestion.services.azure_document_intelligence_adapter import (
    AzureDocumentIntelligenceAdapter,
)
from meritranker_data_ingestion.services.extraction_package import (
    build_package_paths,
    copy_source_pdf,
    create_package_directories,
    try_read_page_count,
)
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    resolve_path,
    validate_and_prepare_output_dir,
    validate_input_pdf,
)
from meritranker_data_ingestion.services.marker_adapter import MarkerAdapter


class PrepareError(Exception):
    """Raised when prepare cannot start."""


@dataclass(frozen=True)
class PrepareResult:
    """Combined outcome of evidence extraction for prepare."""

    succeeded: bool
    selected_extractor: ExtractorType
    extractor_manifest: ExtractorManifest
    package_manifest: ExtractionPackageManifest | None = None
    partial: bool = False


def _map_extraction_status(status: ExtractionStatus) -> ExtractorRunStatus:
    if status == ExtractionStatus.SUCCEEDED:
        return ExtractorRunStatus.SUCCEEDED
    if status == ExtractionStatus.FAILED:
        return ExtractorRunStatus.FAILED
    return ExtractorRunStatus.PENDING


class ExtractorOrchestrator:
    """Run Marker and/or Azure DI evidence extractors without semantic binding."""

    def __init__(
        self,
        *,
        marker_adapter: MarkerAdapter | None = None,
        azure_adapter: AzureDocumentIntelligenceAdapter | None = None,
    ) -> None:
        self._marker = marker_adapter or MarkerAdapter()
        self._azure = azure_adapter or AzureDocumentIntelligenceAdapter()

    def prepare_shell(
        self,
        input_pdf_path: Path,
        output_dir: Path,
        *,
        force: bool = False,
    ) -> PrepareResult:
        """Create package directories and copy source PDF without running extractors."""
        validated_input = validate_input_pdf(input_pdf_path)
        validated_output = validate_and_prepare_output_dir(output_dir)
        package_dir = resolve_path(validated_output) / EXTRACTION_PACKAGE_DIR

        if package_dir.exists() and not force:
            raise PrepareError(
                f"Extraction package already exists: {package_dir}. "
                "Use --force to rerun or remove output directory.",
            )

        paths = build_package_paths(validated_output)
        create_package_directories(paths)
        copy_source_pdf(validated_input, paths)

        extractor_manifest = ExtractorManifest(
            selected_extractor=ExtractorType.MARKER,
            source_file_name=validated_input.name,
            created_at=datetime.now(timezone.utc),
            marker_status=ExtractorRunStatus.SKIPPED,
            azure_di_status=ExtractorRunStatus.SKIPPED,
            warnings=["prepare_shell_only"],
        )
        extractor_manifest.write_json(paths.extractor_manifest)
        return PrepareResult(
            succeeded=True,
            partial=False,
            selected_extractor=ExtractorType.MARKER,
            extractor_manifest=extractor_manifest,
            package_manifest=None,
        )

    def prepare(
        self,
        input_pdf_path: Path,
        output_dir: Path,
        *,
        extractor: ExtractorType | str = ExtractorType.MARKER,
        azure_endpoint: str | None = None,
        azure_model: str | None = None,
        force: bool = False,
    ) -> PrepareResult:
        selected = (
            extractor
            if isinstance(extractor, ExtractorType)
            else ExtractorType(extractor)
        )
        validated_input = validate_input_pdf(input_pdf_path)
        validated_output = validate_and_prepare_output_dir(output_dir)
        package_dir = resolve_path(validated_output) / EXTRACTION_PACKAGE_DIR

        if package_dir.exists() and not force:
            raise PrepareError(
                f"Extraction package already exists: {package_dir}. "
                "Use --force to rerun or remove output directory.",
            )

        paths = build_package_paths(validated_output)
        run_marker = selected in (ExtractorType.MARKER, ExtractorType.BOTH)
        run_azure = selected in (ExtractorType.AZURE_DI, ExtractorType.BOTH)

        extractor_manifest = ExtractorManifest(
            selected_extractor=selected,
            source_file_name=validated_input.name,
            created_at=datetime.now(timezone.utc),
            marker_status=ExtractorRunStatus.SKIPPED,
            azure_di_status=ExtractorRunStatus.SKIPPED,
            azure_di_model=azure_model if run_azure else None,
        )

        package_manifest: ExtractionPackageManifest | None = None

        try:
            create_package_directories(paths)
            copy_source_pdf(validated_input, paths)

            if run_marker:
                package_manifest = self._marker.extract(validated_input, validated_output)
                extractor_manifest.marker_status = _map_extraction_status(
                    package_manifest.status,
                )
                extractor_manifest.errors.extend(package_manifest.errors)
                extractor_manifest.warnings.extend(package_manifest.warnings)
                extractor_manifest.extractors_run.append("marker")
                extractor_manifest.artifact_paths["marker_raw_md"] = (
                    str(package_manifest.markdown_path)
                    if package_manifest.markdown_path
                    else None
                )
                extractor_manifest.artifact_paths["marker_assets_dir"] = (
                    str(package_manifest.assets_dir)
                    if package_manifest.assets_dir
                    else None
                )
                extractor_manifest.artifact_paths["package_manifest"] = str(
                    paths.manifest_path,
                )

            if run_azure:
                azure_adapter = self._azure
                if azure_endpoint is not None or azure_model is not None:
                    azure_adapter = AzureDocumentIntelligenceAdapter(
                        endpoint=azure_endpoint,
                        model_id=azure_model or DEFAULT_AZURE_DI_MODEL,
                    )
                azure_result = azure_adapter.extract(
                    paths.source_pdf,
                    paths,
                    model_id=azure_model,
                )
                extractor_manifest.azure_di_status = azure_result.status
                extractor_manifest.azure_di_model = azure_result.model_id
                extractor_manifest.errors.extend(azure_result.errors)
                extractor_manifest.warnings.extend(azure_result.warnings)
                extractor_manifest.extractors_run.append("azure-di")
                extractor_manifest.artifact_paths.update(azure_result.artifact_paths)

            if not run_marker:
                page_count = try_read_page_count(paths.source_pdf)
                if page_count is not None:
                    extractor_manifest.warnings.append(
                        f"source_pdf_page_count_estimate={page_count}",
                    )

        except PathValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            extractor_manifest.errors.append(f"Unexpected prepare error: {exc}")
            if run_marker and extractor_manifest.marker_status == ExtractorRunStatus.SKIPPED:
                extractor_manifest.marker_status = ExtractorRunStatus.FAILED
            if run_azure and extractor_manifest.azure_di_status == ExtractorRunStatus.SKIPPED:
                extractor_manifest.azure_di_status = ExtractorRunStatus.FAILED
        finally:
            extractor_manifest.write_json(paths.extractor_manifest)

        succeeded, partial = self._evaluate_success(selected, extractor_manifest)
        if partial:
            if (
                extractor_manifest.marker_status == ExtractorRunStatus.SUCCEEDED
                and extractor_manifest.azure_di_status != ExtractorRunStatus.SUCCEEDED
            ):
                extractor_manifest.warnings.append("azure_di_failed_marker_available")
                if any("credentials" in err.lower() for err in extractor_manifest.errors):
                    extractor_manifest.warnings.append("azure_di_credentials_missing")
            elif (
                extractor_manifest.azure_di_status == ExtractorRunStatus.SUCCEEDED
                and extractor_manifest.marker_status != ExtractorRunStatus.SUCCEEDED
            ):
                extractor_manifest.warnings.append("marker_failed_azure_di_available")
            extractor_manifest.write_json(paths.extractor_manifest)

        return PrepareResult(
            succeeded=succeeded,
            partial=partial,
            selected_extractor=selected,
            extractor_manifest=extractor_manifest,
            package_manifest=package_manifest,
        )

    @staticmethod
    def _evaluate_success(
        selected: ExtractorType,
        manifest: ExtractorManifest,
    ) -> tuple[bool, bool]:
        marker_ok = manifest.marker_status == ExtractorRunStatus.SUCCEEDED
        azure_ok = manifest.azure_di_status == ExtractorRunStatus.SUCCEEDED

        if selected == ExtractorType.MARKER:
            return marker_ok, False
        if selected == ExtractorType.AZURE_DI:
            return azure_ok, False

        if marker_ok and azure_ok:
            return True, False
        if marker_ok or azure_ok:
            return True, True
        return False, False
