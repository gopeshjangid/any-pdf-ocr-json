"""PaddleOCR local fallback adapter (Part 14A)."""

from __future__ import annotations

import json
from pathlib import Path

from meritranker_data_ingestion.schemas.ocr_evidence import (
    OcrEvidencePackage,
    OcrLine,
    OcrPage,
)
from meritranker_data_ingestion.services.ocr_role_hints import detect_ocr_role_hints


class PaddleOcrAdapter:
    """Optional local PaddleOCR — graceful skip when not installed."""

    def __init__(self, *, lang: str = "auto") -> None:
        self._lang = lang

    @property
    def engine_name(self) -> str:
        return "paddle_ocr"

    def is_available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except ImportError:
            return False

    def extract(
        self,
        package_dir: Path,
        *,
        pdf_path: Path | None = None,
        page_images: list[Path] | None = None,
    ) -> OcrEvidencePackage:
        source_name = _source_file_name(package_dir)
        if not self.is_available():
            return OcrEvidencePackage(
                source_file_name=source_name,
                warnings=["paddle_ocr_not_installed"],
            )

        if not page_images:
            return OcrEvidencePackage(
                source_file_name=source_name,
                warnings=["paddle_ocr_no_page_images"],
            )

        from paddleocr import PaddleOCR

        lang = _resolve_paddle_lang(self._lang)
        ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

        lines: list[OcrLine] = []
        line_idx = 0
        pages: list[OcrPage] = []

        for image_index, image_path in enumerate(page_images, start=1):
            page_lines: list[OcrLine] = []
            try:
                result = ocr.ocr(str(image_path), cls=True)
            except Exception as exc:
                return OcrEvidencePackage(
                    source_file_name=source_name,
                    warnings=[f"paddle_ocr_failed:{exc}"],
                )

            for block in result or []:
                for entry in block or []:
                    if not entry or len(entry) < 2:
                        continue
                    bbox_points, text_info = entry[0], entry[1]
                    text = str(text_info[0] if isinstance(text_info, (list, tuple)) else text_info)
                    confidence = float(text_info[1]) if isinstance(text_info, (list, tuple)) and len(text_info) > 1 else 0.0
                    if not text.strip():
                        continue
                    line_idx += 1
                    ocr_line = OcrLine(
                        line_id=f"paddle_ocr_l_{line_idx:06d}",
                        page_number=image_index,
                        text=text,
                        normalized_text=text.strip(),
                        bbox=_points_to_bbox(bbox_points),
                        confidence=confidence,
                        engine=self.engine_name,
                        source_image_path=str(image_path),
                        role_hints=detect_ocr_role_hints(text),
                    )
                    lines.append(ocr_line)
                    page_lines.append(ocr_line)

            confs = [ln.confidence for ln in page_lines]
            pages.append(
                OcrPage(
                    page_number=image_index,
                    image_path=str(image_path),
                    line_count=len(page_lines),
                    confidence_avg=sum(confs) / len(confs) if confs else 0.0,
                ),
            )

        return OcrEvidencePackage(
            source_file_name=source_name,
            ocr_engines_used=[self.engine_name] if lines else [],
            pages=pages,
            lines=lines,
        )


def _resolve_paddle_lang(lang: str) -> str:
    mapping = {
        "hi": "hi",
        "en": "en",
        "devanagari": "hi",
        "auto": "en",
    }
    return mapping.get(lang.lower(), "en")


def _points_to_bbox(points: object) -> list[float] | None:
    if not isinstance(points, list) or not points:
        return None
    try:
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        return [min(xs), min(ys), max(xs), max(ys)]
    except (TypeError, ValueError, IndexError):
        return None


def _source_file_name(package_dir: Path) -> str:
    manifest = package_dir / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return str(data.get("source_file_name") or "unknown.pdf")
        except (json.JSONDecodeError, OSError):
            pass
    return "unknown.pdf"
