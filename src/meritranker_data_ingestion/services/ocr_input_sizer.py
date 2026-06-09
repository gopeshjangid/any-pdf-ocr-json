"""OCR input sizing, resize, and page-split helpers (Part 14C + 14D)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Azure DI practical limits — conservative defaults for image payloads.
DEFAULT_MAX_PDF_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_IMAGE_BYTES = 4 * 1024 * 1024

# Part 14D: ordered render profiles (dpi, format, jpeg_quality).
RENDER_PROFILES: tuple[tuple[int, str, int | None], ...] = (
    (200, "png", None),
    (150, "jpeg", 85),
    (120, "jpeg", 75),
    (100, "jpeg", 70),
)


@dataclass(frozen=True)
class SizedOcrInput:
    """Prepared OCR payload ready for Azure DI."""

    data: bytes
    input_type: str
    page_number: int | None
    original_size_bytes: int
    final_size_bytes: int
    dpi: int | None
    jpeg_quality: int | None
    image_format: str | None
    retry_count: int
    image_path: str | None = None


def pymupdf_available() -> bool:
    try:
        import fitz  # noqa: F401
        return True
    except ImportError:
        return False


def file_size_bytes(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def extract_azure_error_code(exc: BaseException) -> str | None:
    message = str(exc)
    match = re.search(r"(\w+Content\w*|\w+Error):\s*", message)
    if match:
        return match.group(1).rstrip(":")
    if "UnsupportedContent" in message:
        return "UnsupportedContent"
    if "InvalidContentLength" in message:
        return "InvalidContentLength"
    return None


def is_invalid_content_length_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "invalidcontentlength" in message or "content length" in message


def is_unsupported_content_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "unsupportedcontent" in message or "not supported" in message


def should_fallback_to_rendered_image(exc: BaseException) -> bool:
    """True when Azure rejected bytes and image retry may succeed."""
    return is_invalid_content_length_error(exc) or is_unsupported_content_error(exc)


def content_type_for_image_format(image_format: str) -> str:
    if image_format == "png":
        return "image/png"
    return "image/jpeg"


def extract_pdf_page_bytes(pdf_path: Path, page_index: int) -> bytes | None:
    """Extract a single PDF page as bytes (0-based page index)."""
    if not pymupdf_available():
        return None
    import fitz

    doc = fitz.open(pdf_path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            return None
        out = fitz.open()
        out.insert_pdf(doc, from_page=page_index, to_page=page_index)
        return out.tobytes()
    finally:
        doc.close()


def pdf_page_count(pdf_path: Path) -> int:
    if not pymupdf_available():
        return 0
    import fitz

    doc = fitz.open(pdf_path)
    try:
        return doc.page_count
    finally:
        doc.close()


def iter_render_attempts(
    pdf_path: Path,
    page_index: int,
    *,
    output_dir: Path | None = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> list[SizedOcrInput]:
    """Render page with Part 14D profiles; save first successful image to output_dir."""
    if not pymupdf_available():
        return []

    import fitz

    doc = fitz.open(pdf_path)
    results: list[SizedOcrInput] = []
    try:
        if page_index < 0 or page_index >= doc.page_count:
            return []
        page = doc.load_page(page_index)
        page_num = page_index + 1

        for retry_count, (dpi, image_format, quality) in enumerate(RENDER_PROFILES):
            zoom = dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            original = len(pix.tobytes("png"))

            if image_format == "png":
                img_bytes = pix.tobytes("png")
            else:
                img_bytes = pix.tobytes("jpeg", jpg_quality=quality or 85)

            if len(img_bytes) > max_bytes:
                continue

            image_path: str | None = None
            if output_dir is not None:
                output_dir.mkdir(parents=True, exist_ok=True)
                ext = "png" if image_format == "png" else "jpg"
                out_file = output_dir / f"page-{page_num:04d}-{image_format}.{ext}"
                out_file.write_bytes(img_bytes)
                image_path = str(out_file)

            results.append(
                SizedOcrInput(
                    data=img_bytes,
                    input_type="rendered_image",
                    page_number=page_num,
                    original_size_bytes=original,
                    final_size_bytes=len(img_bytes),
                    dpi=dpi,
                    jpeg_quality=quality,
                    image_format=image_format,
                    retry_count=retry_count,
                    image_path=image_path,
                ),
            )
    finally:
        doc.close()

    return results


def render_page_image_bytes(
    pdf_path: Path,
    page_index: int,
    *,
    output_dir: Path | None = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> SizedOcrInput | None:
    """Render one PDF page using the first fitting Part 14D profile."""
    attempts = iter_render_attempts(
        pdf_path,
        page_index,
        output_dir=output_dir,
        max_bytes=max_bytes,
    )
    return attempts[0] if attempts else None


def prepare_pdf_input(
    pdf_path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_PDF_BYTES,
) -> SizedOcrInput | None:
    """Return full PDF bytes if within size budget."""
    size = file_size_bytes(pdf_path)
    if size == 0:
        return None
    data = pdf_path.read_bytes()
    if size <= max_bytes:
        return SizedOcrInput(
            data=data,
            input_type="pdf",
            page_number=None,
            original_size_bytes=size,
            final_size_bytes=size,
            dpi=None,
            jpeg_quality=None,
            image_format=None,
            retry_count=0,
        )
    return None
