"""Tests for CLI path validation and prepare command behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from meritranker_data_ingestion.cli import main
from meritranker_data_ingestion.config import EXTRACTION_PACKAGE_DIR, PACKAGE_MANIFEST_NAME
from meritranker_data_ingestion.schemas.extraction import (
    ExtractionPackageManifest,
    ExtractionStatus,
)
from meritranker_data_ingestion.schemas.extractor import ExtractorManifest, ExtractorRunStatus, ExtractorType
from meritranker_data_ingestion.services.azure_document_intelligence_adapter import (
    AzureDiExtractionResult,
)
from meritranker_data_ingestion.services.extractor_orchestrator import (
    ExtractorOrchestrator,
    PrepareResult,
)
from meritranker_data_ingestion.services.marker_adapter import MarkerAdapter
from meritranker_data_ingestion.services.marker_runner import MarkerRunResult


class _FakeAzureAdapter:
    def extract(self, source_pdf: Path, paths, *, model_id: str | None = None) -> AzureDiExtractionResult:
        return AzureDiExtractionResult(
            status=ExtractorRunStatus.SKIPPED,
            model_id="prebuilt-layout",
            artifact_paths={},
            errors=[],
            warnings=[],
        )


class _FakeRunner:
    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed

    def run(self, command: list[str], *, log_path: Path) -> MarkerRunResult:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("log\n", encoding="utf-8")
        if not self.succeed:
            return MarkerRunResult(
                returncode=127,
                stdout="",
                stderr="missing",
                command=command,
                error_message="Marker command not found: 'marker_single'.",
            )
        work_dir = Path(command[-1])
        out = work_dir / "original"
        out.mkdir(parents=True)
        (out / "original.md").write_text("# test\n", encoding="utf-8")
        return MarkerRunResult(returncode=0, stdout="", stderr="", command=command)


def test_cli_rejects_missing_input(tmp_path: Path) -> None:
    exit_code = main(
        [
            "prepare",
            "--input",
            str(tmp_path / "missing.pdf"),
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert exit_code == 1


def test_cli_rejects_non_pdf(tmp_path: Path) -> None:
    text_file = tmp_path / "notes.txt"
    text_file.write_text("not a pdf", encoding="utf-8")
    output_dir = tmp_path / "out"

    exit_code = main(
        [
            "prepare",
            "--input",
            str(text_file),
            "--output",
            str(output_dir),
        ],
    )
    assert exit_code == 1


@patch("meritranker_data_ingestion.cli.ExtractorOrchestrator.prepare")
def test_cli_prepare_success(
    mock_prepare: pytest.Mock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    output_dir = tmp_path / "output"
    package_manifest = ExtractionPackageManifest(
        input_pdf_path=pdf,
        source_file_name="paper.pdf",
        output_dir=output_dir,
        parser_engine="marker",
        status=ExtractionStatus.SUCCEEDED,
        markdown_path=output_dir / EXTRACTION_PACKAGE_DIR / "marker" / "raw.md",
    )
    mock_prepare.return_value = PrepareResult(
        succeeded=True,
        selected_extractor=ExtractorType.MARKER,
        extractor_manifest=ExtractorManifest(
            selected_extractor=ExtractorType.MARKER,
            source_file_name="paper.pdf",
            marker_status=ExtractorRunStatus.SUCCEEDED,
        ),
        package_manifest=package_manifest,
    )

    exit_code = main(
        [
            "prepare",
            "--input",
            str(pdf),
            "--output",
            str(output_dir),
        ],
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "succeeded" in captured.out


def test_cli_prepare_extraction_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    output_dir = tmp_path / "output"

    with patch("meritranker_data_ingestion.cli.ExtractorOrchestrator.prepare") as mock_prepare:
        mock_prepare.return_value = PrepareResult(
            succeeded=False,
            selected_extractor=ExtractorType.MARKER,
            extractor_manifest=ExtractorManifest(
                selected_extractor=ExtractorType.MARKER,
                source_file_name="paper.pdf",
                marker_status=ExtractorRunStatus.FAILED,
                errors=["Marker command not found: 'marker_single'."],
            ),
            package_manifest=ExtractionPackageManifest(
                input_pdf_path=pdf,
                source_file_name="paper.pdf",
                output_dir=output_dir,
                parser_engine="marker",
                status=ExtractionStatus.FAILED,
                errors=["Marker command not found: 'marker_single'."],
            ),
        )
        exit_code = main(
            [
                "prepare",
                "--input",
                str(pdf),
                "--output",
                str(output_dir),
            ],
        )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err


def test_cli_prepare_integration_with_fake_runner(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\n%%EOF\n")
    output_dir = tmp_path / "output"

    orchestrator = ExtractorOrchestrator(
        marker_adapter=MarkerAdapter(runner=_FakeRunner(succeed=True)),
        azure_adapter=_FakeAzureAdapter(),
    )
    with patch(
        "meritranker_data_ingestion.cli.ExtractorOrchestrator",
        return_value=orchestrator,
    ):
        exit_code = main(
            [
                "prepare",
                "--input",
                str(pdf),
                "--output",
                str(output_dir),
            ],
        )

    assert exit_code == 0
    assert (output_dir / EXTRACTION_PACKAGE_DIR / "marker" / "raw.md").exists()
    assert (output_dir / EXTRACTION_PACKAGE_DIR / "extractors" / "extractor-manifest.json").exists()
