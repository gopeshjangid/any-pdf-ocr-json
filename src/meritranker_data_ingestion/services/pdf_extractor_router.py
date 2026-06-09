"""PDF pre-extraction profiling and extractor strategy routing (Part 14X/14Y)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import (
    PDF_PROFILE_FIRST_PAGES,
    PDF_PROFILE_LAST_PAGES,
    PDF_PROFILE_MIDDLE_PAGES,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.ocr_input_sizer import pymupdf_available

STRATEGY_AUTO = "auto"
STRATEGY_MARKER_PRIMARY = "marker_primary"
STRATEGY_AZURE_PRIMARY = "azure_primary"
STRATEGY_DUAL_DEBUG = "dual_debug"

LAYOUT_HINT_SCREENSHOT = "screenshot_mcq_layout"
LAYOUT_HINT_RESPONSE_SHEET = "response_sheet_layout"
LAYOUT_HINT_DIGITAL = "pyq_standard_mcq"

RE_Q_ANCHOR = re.compile(r"\bQ\.?\s*\d{1,3}\b", re.IGNORECASE)
RE_NUM_ANCHOR = re.compile(r"(?:^|\n)\s*\d{1,3}\s*[\.\)]\s+\S", re.MULTILINE)
RE_OPTION_LABEL = re.compile(
    r"(?:\([A-Da-d]\)|[A-Da-d]\.\s|\(\*?[A-Da-d]\*?\))",
    re.IGNORECASE,
)
RE_RESPONSE_SHEET = re.compile(
    r"(?:question\s*id|chosen\s+option\s*:|status\s*:|marked\s+for\s+review)",
    re.IGNORECASE,
)
RE_ADDA_SCREENSHOT = re.compile(
    r"(?:options?\s+shown\s+in\s+green|tick\s+icon|all\s+exams|one\s+subscription|"
    r"attempt\s+free\s+mock|\*\*Ans\*\*|✓|×)",
    re.IGNORECASE,
)
RE_INLINE_ANS = re.compile(
    r"\bAns(?:wer)?\s*(?:\*{1,2})?\s*[\.\:]?\s*\(?\s*[A-Da-d]\s*\)?",
    re.IGNORECASE,
)
RE_TABLE_PIPE = re.compile(r"\|[^|]+\|")
RE_INSTRUCTION = re.compile(
    r"(?:instructions?\s+to\s+candidates|read\s+the\s+following|time\s+allowed|"
    r"maximum\s+marks|duration\s*:|general\s+awareness|negative\s+marking)",
    re.IGNORECASE,
)
RE_COVER = re.compile(
    r"(?:syllabus|table\s+of\s+contents|^\s*contents\s*$|front\s+cover)",
    re.IGNORECASE | re.MULTILINE,
)
RE_AD_WATERMARK = re.compile(
    r"(?:watermark|sample\s+copy|not\s+for\s+sale|adda\s*247|gradeup|"
    r"unauthorized\s+reproduction|demo\s+copy)",
    re.IGNORECASE,
)
RE_ANSWER_KEY = re.compile(
    r"(?:answer\s*key|answers?\s+and\s+solutions?|solutions?\s+to\s+questions?)",
    re.IGNORECASE,
)

PAGE_TYPE_COVER = "cover_page"
PAGE_TYPE_INSTRUCTION = "instruction_page"
PAGE_TYPE_ADVERTISEMENT = "advertisement_page"
PAGE_TYPE_WATERMARK = "watermark_noise_page"
PAGE_TYPE_QUESTION = "question_page"

IGNORED_PAGE_TYPES = frozenset({
    PAGE_TYPE_COVER,
    PAGE_TYPE_INSTRUCTION,
    PAGE_TYPE_ADVERTISEMENT,
    PAGE_TYPE_WATERMARK,
})

PAGE_TYPE_WEIGHT = {
    PAGE_TYPE_QUESTION: 1.0,
    PAGE_TYPE_COVER: 0.1,
    PAGE_TYPE_INSTRUCTION: 0.1,
    PAGE_TYPE_ADVERTISEMENT: 0.1,
    PAGE_TYPE_WATERMARK: 0.1,
}


@dataclass(frozen=True)
class PdfExtractorProfile:
    source_file_name: str
    page_count: int
    selectable_text_char_count: int
    text_density_score: float
    image_area_ratio: float
    question_anchor_count: int
    option_label_count: int
    table_like_line_count: int
    scanned_or_screenshot_score: float
    response_sheet_marker_score: float
    adda_or_screenshot_ui_score: float
    extractor_strategy_requested: str
    extractor_strategy_effective: str
    layout_hint: str
    confidence: float
    marker_used: bool
    azure_used: bool
    dual_used: bool
    fallback_allowed: bool
    profiled_at: str
    sampled_pages: tuple[int, ...] = ()
    ignored_profile_pages_count: int = 0
    answer_key_marker_count: int = 0
    fallback_reason: str | None = None
    marker_quality_summary: dict | None = None


def route_extractor_strategy(
    pdf_path: Path,
    *,
    strategy: str = STRATEGY_AUTO,
    allow_auto_fallback: bool = True,
) -> PdfExtractorProfile:
    """Inspect PDF signals and return effective extractor strategy."""
    resolved = resolve_path(pdf_path)
    requested = strategy if strategy in {
        STRATEGY_AUTO,
        STRATEGY_MARKER_PRIMARY,
        STRATEGY_AZURE_PRIMARY,
        STRATEGY_DUAL_DEBUG,
    } else STRATEGY_AUTO

    signals = _collect_pdf_signals(resolved)
    effective, layout_hint, confidence = _resolve_effective_strategy(
        requested,
        signals,
        allow_auto_fallback=allow_auto_fallback,
    )

    marker_used = effective in {STRATEGY_MARKER_PRIMARY, STRATEGY_DUAL_DEBUG}
    azure_used = effective in {STRATEGY_AZURE_PRIMARY, STRATEGY_DUAL_DEBUG}
    dual_used = effective == STRATEGY_DUAL_DEBUG

    return PdfExtractorProfile(
        source_file_name=resolved.name,
        page_count=signals["page_count"],
        selectable_text_char_count=signals["selectable_text_char_count"],
        text_density_score=signals["text_density_score"],
        image_area_ratio=signals["image_area_ratio"],
        question_anchor_count=signals["question_anchor_count"],
        option_label_count=signals["option_label_count"],
        table_like_line_count=signals["table_like_line_count"],
        scanned_or_screenshot_score=signals["scanned_or_screenshot_score"],
        response_sheet_marker_score=signals["response_sheet_marker_score"],
        adda_or_screenshot_ui_score=signals["adda_or_screenshot_ui_score"],
        extractor_strategy_requested=requested,
        extractor_strategy_effective=effective,
        layout_hint=layout_hint,
        confidence=confidence,
        marker_used=marker_used,
        azure_used=azure_used,
        dual_used=dual_used,
        fallback_allowed=allow_auto_fallback and effective == STRATEGY_MARKER_PRIMARY,
        profiled_at=datetime.now(timezone.utc).isoformat(),
        sampled_pages=tuple(signals.get("sampled_pages") or []),
        ignored_profile_pages_count=int(signals.get("ignored_profile_pages_count") or 0),
        answer_key_marker_count=int(signals.get("answer_key_marker_count") or 0),
    )


def write_pdf_extractor_profile(
    package_dir: Path,
    profile: PdfExtractorProfile,
) -> Path:
    """Persist profile under extraction_package/diagnostics/."""
    resolved = resolve_path(package_dir)
    diag = resolved / "diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    path = diag / "pdf-extractor-profile.json"
    payload = asdict(profile)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def update_pdf_extractor_profile_fields(
    package_dir: Path,
    *,
    fallback_reason: str | None = None,
    marker_quality_summary: dict | None = None,
    fallback_used: bool | None = None,
    azure_used: bool | None = None,
) -> None:
    """Patch routing fields on an existing pdf-extractor-profile.json."""
    resolved = resolve_path(package_dir)
    path = resolved / "diagnostics" / "pdf-extractor-profile.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(data, dict):
        return
    if fallback_reason is not None:
        data["fallback_reason"] = fallback_reason
    if marker_quality_summary is not None:
        data["marker_quality_summary"] = marker_quality_summary
    if fallback_used is not None:
        data["fallback_used"] = fallback_used
    if azure_used is not None:
        data["azure_used"] = azure_used
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _empty_pdf_signals() -> dict[str, float | int | list[int]]:
    return {
        "page_count": 0,
        "selectable_text_char_count": 0,
        "text_density_score": 0.0,
        "image_area_ratio": 0.0,
        "question_anchor_count": 0,
        "option_label_count": 0,
        "table_like_line_count": 0,
        "scanned_or_screenshot_score": 0.0,
        "response_sheet_marker_score": 0,
        "adda_or_screenshot_ui_score": 0,
        "answer_key_marker_count": 0,
        "sampled_pages": [],
        "ignored_profile_pages_count": 0,
    }


def _sample_page_indices(page_count: int) -> list[int]:
    """Return deduplicated first/middle/last page indices (0-based)."""
    if page_count <= 0:
        return []

    first = list(range(min(PDF_PROFILE_FIRST_PAGES, page_count)))
    middle: list[int] = []
    last: list[int] = []

    if page_count > PDF_PROFILE_FIRST_PAGES + PDF_PROFILE_LAST_PAGES:
        mid_center = page_count // 2
        half_middle = PDF_PROFILE_MIDDLE_PAGES // 2
        for offset in range(PDF_PROFILE_MIDDLE_PAGES):
            idx = mid_center - half_middle + offset
            if 0 <= idx < page_count:
                middle.append(idx)

    for offset in range(PDF_PROFILE_LAST_PAGES):
        idx = page_count - PDF_PROFILE_LAST_PAGES + offset
        if 0 <= idx < page_count:
            last.append(idx)

    seen: set[int] = set()
    ordered: list[int] = []
    for page_idx in first + middle + last:
        if page_idx not in seen:
            seen.add(page_idx)
            ordered.append(page_idx)
    return ordered


def _classify_page(
    text: str,
    *,
    page_index: int,
    page_count: int,
    text_chars: int,
    image_ratio: float,
) -> str:
    """Classify a sampled page for routing weighting."""
    if RE_AD_WATERMARK.search(text) or RE_ADDA_SCREENSHOT.search(text):
        return PAGE_TYPE_ADVERTISEMENT
    if page_index == 0 and text_chars < 120 and not RE_Q_ANCHOR.search(text):
        return PAGE_TYPE_COVER
    if RE_INSTRUCTION.search(text) and len(RE_Q_ANCHOR.findall(text)) < 2:
        return PAGE_TYPE_INSTRUCTION
    if text_chars < 40 and image_ratio > 0.6:
        return PAGE_TYPE_WATERMARK
    if RE_COVER.search(text) and len(RE_NUM_ANCHOR.findall(text)) < 2:
        return PAGE_TYPE_COVER
    if (
        RE_ANSWER_KEY.search(text)
        and len(RE_Q_ANCHOR.findall(text)) < 2
        and page_index in {0, page_count - 1}
    ):
        return PAGE_TYPE_INSTRUCTION
    return PAGE_TYPE_QUESTION


def _page_metrics(text: str, image_area: float, page_area: float) -> dict[str, float | int]:
    image_ratio = min(1.0, image_area / page_area) if page_area else 0.0
    text_chars = len(text.strip())
    text_density = min(1.0, (text_chars / 500.0))
    return {
        "selectable_text_char_count": text_chars,
        "text_density_score": round(text_density, 4),
        "image_area_ratio": round(image_ratio, 4),
        "question_anchor_count": (
            len(RE_Q_ANCHOR.findall(text)) + len(RE_NUM_ANCHOR.findall(text))
        ),
        "option_label_count": len(RE_OPTION_LABEL.findall(text)),
        "answer_key_marker_count": len(RE_ANSWER_KEY.findall(text)),
        "response_sheet_marker_count": len(RE_RESPONSE_SHEET.findall(text)),
        "screenshot_ui_marker_count": (
            len(RE_ADDA_SCREENSHOT.findall(text)) + len(RE_INLINE_ANS.findall(text))
        ),
        "ad_or_watermark_score": len(RE_AD_WATERMARK.findall(text)),
        "instruction_score": len(RE_INSTRUCTION.findall(text)),
    }


def _collect_pdf_signals(pdf_path: Path) -> dict[str, float | int | list[int]]:
    if not pdf_path.exists():
        return _empty_pdf_signals()

    if not pymupdf_available():
        return _empty_pdf_signals()

    import fitz

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return _empty_pdf_signals()

    page_count = 0
    sampled_indices: list[int] = []
    ignored_count = 0
    weighted: dict[str, float] = {
        "text_chars": 0.0,
        "image_area": 0.0,
        "page_area": 0.0,
        "q_anchors": 0.0,
        "option_labels": 0.0,
        "table_lines": 0.0,
        "response_score": 0.0,
        "adda_score": 0.0,
        "answer_key_score": 0.0,
    }
    sample_text = ""

    try:
        page_count = len(doc)
        sampled_indices = _sample_page_indices(page_count)
        for page_index in sampled_indices:
            page = doc[page_index]
            text = page.get_text("text") or ""
            rect = page.rect
            page_area = float(rect.width * rect.height)
            image_area = 0.0
            for block in page.get_images(full=True):
                try:
                    xref = block[0]
                    pb = doc.extract_image(xref)
                    w, h = pb.get("width", 0), pb.get("height", 0)
                    image_area += float(w * h)
                except Exception:
                    continue

            metrics = _page_metrics(text, image_area, page_area)
            page_type = _classify_page(
                text,
                page_index=page_index,
                page_count=page_count,
                text_chars=int(metrics["selectable_text_char_count"]),
                image_ratio=float(metrics["image_area_ratio"]),
            )
            weight = PAGE_TYPE_WEIGHT.get(page_type, 1.0)
            if page_type in IGNORED_PAGE_TYPES:
                ignored_count += 1

            weighted["text_chars"] += metrics["selectable_text_char_count"] * weight
            weighted["image_area"] += image_area * weight
            weighted["page_area"] += page_area * weight
            weighted["q_anchors"] += metrics["question_anchor_count"] * weight
            weighted["option_labels"] += metrics["option_label_count"] * weight
            weighted["table_lines"] += len(RE_TABLE_PIPE.findall(text)) * weight
            weighted["response_score"] += metrics["response_sheet_marker_count"] * weight
            weighted["adda_score"] += metrics["screenshot_ui_marker_count"] * weight
            weighted["answer_key_score"] += metrics["answer_key_marker_count"] * weight

            if page_type == PAGE_TYPE_QUESTION:
                sample_text += text + "\n"
    finally:
        doc.close()

    text_chars = int(weighted["text_chars"])
    text_density = (text_chars / max(len(sampled_indices), 1)) / 500.0
    text_density = min(1.0, text_density)
    image_ratio = (
        min(1.0, weighted["image_area"] / weighted["page_area"])
        if weighted["page_area"]
        else 0.0
    )

    q_anchors = int(weighted["q_anchors"])
    option_labels = int(weighted["option_labels"])
    table_lines = int(weighted["table_lines"])
    response_score = int(weighted["response_score"])
    adda_score = int(weighted["adda_score"])
    answer_key_score = int(weighted["answer_key_score"])

    scanned_score = 0.0
    if text_density < 0.25:
        scanned_score += 0.4
    if image_ratio > 0.35:
        scanned_score += 0.35
    if adda_score >= 2:
        scanned_score += 0.25
    scanned_score = min(1.0, scanned_score)

    return {
        "page_count": page_count,
        "selectable_text_char_count": text_chars,
        "text_density_score": round(text_density, 4),
        "image_area_ratio": round(image_ratio, 4),
        "question_anchor_count": q_anchors,
        "option_label_count": option_labels,
        "table_like_line_count": table_lines,
        "scanned_or_screenshot_score": round(scanned_score, 4),
        "response_sheet_marker_score": response_score,
        "adda_or_screenshot_ui_score": adda_score,
        "answer_key_marker_count": answer_key_score,
        "sampled_pages": sampled_indices,
        "ignored_profile_pages_count": ignored_count,
    }


def _resolve_effective_strategy(
    requested: str,
    signals: dict[str, float | int],
    *,
    allow_auto_fallback: bool,
) -> tuple[str, str, float]:
    if requested == STRATEGY_MARKER_PRIMARY:
        return STRATEGY_MARKER_PRIMARY, _layout_hint(signals), 0.95
    if requested == STRATEGY_AZURE_PRIMARY:
        return STRATEGY_AZURE_PRIMARY, _layout_hint(signals), 0.95
    if requested == STRATEGY_DUAL_DEBUG:
        return STRATEGY_DUAL_DEBUG, _layout_hint(signals), 0.95

    response = float(signals["response_sheet_marker_score"])
    adda = float(signals["adda_or_screenshot_ui_score"])
    scanned = float(signals["scanned_or_screenshot_score"])
    text_density = float(signals["text_density_score"])
    q_anchors = int(signals["question_anchor_count"])

    if response >= 3:
        return STRATEGY_AZURE_PRIMARY, LAYOUT_HINT_RESPONSE_SHEET, 0.9

    if scanned >= 0.55 or (adda >= 3 and text_density < 0.5):
        return STRATEGY_AZURE_PRIMARY, LAYOUT_HINT_SCREENSHOT, 0.85

    if text_density >= 0.45 and q_anchors >= 5:
        return STRATEGY_MARKER_PRIMARY, LAYOUT_HINT_DIGITAL, 0.9

    if allow_auto_fallback:
        return STRATEGY_MARKER_PRIMARY, LAYOUT_HINT_DIGITAL, 0.6

    return STRATEGY_MARKER_PRIMARY, LAYOUT_HINT_DIGITAL, 0.5


def _layout_hint(signals: dict[str, float | int]) -> str:
    if float(signals["response_sheet_marker_score"]) >= 3:
        return LAYOUT_HINT_RESPONSE_SHEET
    if float(signals["scanned_or_screenshot_score"]) >= 0.5 or float(
        signals["adda_or_screenshot_ui_score"],
    ) >= 3:
        return LAYOUT_HINT_SCREENSHOT
    return LAYOUT_HINT_DIGITAL


def marker_quality_passes(
    *,
    question_window_count: int,
    expected_count: int | None,
    marker_line_count: int,
) -> bool:
    """Return True when Marker output is good enough to skip Azure fallback."""
    if marker_line_count < 20:
        return False
    if expected_count and expected_count > 0:
        ratio = question_window_count / expected_count
        return ratio >= 0.7
    return question_window_count >= 50
