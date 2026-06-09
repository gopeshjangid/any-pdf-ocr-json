"""PDF page image rendering for OCR (Part 14A)."""

from __future__ import annotations

from pathlib import Path


def render_pdf_pages(
    pdf_path: Path,
    output_dir: Path,
    *,
    dpi: int = 150,
) -> tuple[list[Path], list[str]]:
    """Render PDF pages to PNG images. Returns (image_paths, warnings)."""
    warnings: list[str] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import fitz  # PyMuPDF
    except ImportError:
        warnings.append(
            "pymupdf_not_installed: page image OCR skipped. "
            "Install with: uv sync --extra ocr",
        )
        return [], warnings

    if not pdf_path.exists():
        warnings.append(f"pdf_not_found:{pdf_path}")
        return [], warnings

    image_paths: list[Path] = []
    doc = fitz.open(pdf_path)
    try:
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            out_path = output_dir / f"page-{page_index + 1:04d}.png"
            pix.save(str(out_path))
            image_paths.append(out_path)
    finally:
        doc.close()

    return image_paths, warnings
