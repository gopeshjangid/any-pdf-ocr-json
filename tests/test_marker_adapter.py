"""Tests for MarkerAdapter extraction package generation (mocked subprocess)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from meritranker_data_ingestion.config import (
    ASSETS_DIR_NAME,
    EXTRACTION_PACKAGE_DIR,
    ORIGINAL_PDF_NAME,
    PACKAGE_MANIFEST_NAME,
    RAW_MARKDOWN_NAME,
    SOURCE_DIR,
)
from meritranker_data_ingestion.schemas.extraction import ExtractionStatus
from meritranker_data_ingestion.services.marker_adapter import MarkerAdapter
from meritranker_data_ingestion.services.marker_runner import MarkerRunResult


class FakeMarkerRunner:
    """Injected Marker runner for tests."""

    def __init__(self, result: MarkerRunResult, *, setup: Callable[[list[str]], None] | None = None) -> None:
        self.result = result
        self.setup = setup
        self.commands: list[list[str]] = []

    def run(self, command: list[str], *, log_path: Path) -> MarkerRunResult:
        self.commands.append(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("fake marker log\n", encoding="utf-8")
        if self.setup is not None:
            self.setup(command)
        return self.result


def _pdf_path(tmp_path: Path, name: str = "exam.pdf") -> Path:
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\n%%EOF\n")
    return pdf


def _success_result(command: list[str]) -> MarkerRunResult:
    return MarkerRunResult(returncode=0, stdout="ok", stderr="", command=command)


def _make_success_setup(tmp_path: Path, pdf_name: str = "original") -> Callable[[list[str]], None]:
    def setup(command: list[str]) -> None:
        # Marker --output_dir is last arg
        work_dir = Path(command[-1])
        stem_dir = work_dir / pdf_name
        stem_dir.mkdir(parents=True, exist_ok=True)
        (stem_dir / f"{pdf_name}.md").write_text("# Raw markdown\n\nQuestion 1\n", encoding="utf-8")
        images = stem_dir / "images"
        images.mkdir()
        (images / "fig1.png").write_bytes(b"\x89PNG\r\n")

    return setup


def test_extract_success_creates_package(tmp_path: Path) -> None:
    pdf = _pdf_path(tmp_path)
    output_dir = tmp_path / "output"
    runner = FakeMarkerRunner(
        _success_result([]),
        setup=_make_success_setup(tmp_path),
    )

    manifest = MarkerAdapter(runner=runner).extract(pdf, output_dir)

    package = output_dir / EXTRACTION_PACKAGE_DIR
    assert manifest.status == ExtractionStatus.SUCCEEDED
    assert (package / SOURCE_DIR / ORIGINAL_PDF_NAME).exists()
    assert (package / "marker" / RAW_MARKDOWN_NAME).read_text(encoding="utf-8") == (
        "# Raw markdown\n\nQuestion 1\n"
    )
    assert (package / "marker" / ASSETS_DIR_NAME / "fig1.png").exists()
    assert manifest.markdown_path == package / "marker" / RAW_MARKDOWN_NAME
    assert manifest.assets_dir == package / "marker" / ASSETS_DIR_NAME
    assert (package / PACKAGE_MANIFEST_NAME).exists()
    assert (package / "logs" / "extraction.log").exists()


def test_extract_marker_command_missing(tmp_path: Path) -> None:
    pdf = _pdf_path(tmp_path)
    output_dir = tmp_path / "output"
    runner = FakeMarkerRunner(
        MarkerRunResult(
            returncode=127,
            stdout="",
            stderr="not found",
            command=["missing_marker"],
            error_message="Marker command not found: 'missing_marker'.",
        ),
    )

    manifest = MarkerAdapter(runner=runner).extract(pdf, output_dir)

    assert manifest.status == ExtractionStatus.FAILED
    assert any("not found" in err.lower() for err in manifest.errors)
    assert (output_dir / EXTRACTION_PACKAGE_DIR / PACKAGE_MANIFEST_NAME).exists()
    assert (output_dir / EXTRACTION_PACKAGE_DIR / SOURCE_DIR / ORIGINAL_PDF_NAME).exists()


def test_extract_marker_nonzero_exit(tmp_path: Path) -> None:
    pdf = _pdf_path(tmp_path)
    output_dir = tmp_path / "output"
    runner = FakeMarkerRunner(
        MarkerRunResult(returncode=1, stdout="", stderr="boom", command=["marker_single"]),
    )

    manifest = MarkerAdapter(runner=runner).extract(pdf, output_dir)

    assert manifest.status == ExtractionStatus.FAILED
    assert any("exit code 1" in err for err in manifest.errors)


def test_extract_missing_markdown_after_marker(tmp_path: Path) -> None:
    pdf = _pdf_path(tmp_path)
    output_dir = tmp_path / "output"
    runner = FakeMarkerRunner(_success_result([]))

    manifest = MarkerAdapter(runner=runner).extract(pdf, output_dir)

    assert manifest.status == ExtractionStatus.FAILED
    assert any("no markdown" in err.lower() for err in manifest.errors)


def test_extract_continues_without_assets_with_warning(tmp_path: Path) -> None:
    pdf = _pdf_path(tmp_path)
    output_dir = tmp_path / "output"

    def setup(command: list[str]) -> None:
        work_dir = Path(command[-1])
        stem_dir = work_dir / "original"
        stem_dir.mkdir(parents=True)
        (stem_dir / "original.md").write_text("# Only md\n", encoding="utf-8")

    runner = FakeMarkerRunner(_success_result([]), setup=setup)
    manifest = MarkerAdapter(runner=runner).extract(pdf, output_dir)

    assert manifest.status == ExtractionStatus.SUCCEEDED
    assert manifest.assets_dir is None
    assert any("assets" in w.lower() for w in manifest.warnings)


def test_extract_copies_source_pdf_byte_for_byte(tmp_path: Path) -> None:
    pdf = _pdf_path(tmp_path)
    original_bytes = pdf.read_bytes()
    output_dir = tmp_path / "output"
    runner = FakeMarkerRunner(_success_result([]), setup=_make_success_setup(tmp_path))

    MarkerAdapter(runner=runner).extract(pdf, output_dir)

    copied = output_dir / EXTRACTION_PACKAGE_DIR / SOURCE_DIR / ORIGINAL_PDF_NAME
    assert copied.read_bytes() == original_bytes
