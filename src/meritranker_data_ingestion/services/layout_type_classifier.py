"""Classify PDF layout type from pipeline metrics and evidence artifacts (Part 14T)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LAYOUT_PYQ_SOLVED = "pyq_solved_mcq"
LAYOUT_PYQ_STANDARD = "pyq_standard_mcq"
LAYOUT_ANSWER_KEY_ONLY = "answer_key_only_mcq"
LAYOUT_SCREENSHOT_MCQ = "screenshot_mcq_layout"
LAYOUT_RESPONSE_SHEET = "response_sheet_layout"
LAYOUT_UNSUPPORTED = "unsupported_layout"
LAYOUT_OCR_DEGRADED = "ocr_quality_degraded"
LAYOUT_OVER_DETECTION = "over_detection_layout"


def classify_layout_type(
    metrics: dict[str, Any],
    *,
    package_dir: Path | None = None,
    main_issue: str | None = None,
) -> str:
    """Return a generic layout label derived from evidence, not PDF filenames."""
    artifact = _load_layout_artifacts(package_dir)

    layout_hint = artifact.get("layout_hint")
    if layout_hint == LAYOUT_SCREENSHOT_MCQ:
        return LAYOUT_SCREENSHOT_MCQ
    if layout_hint == LAYOUT_RESPONSE_SHEET:
        return LAYOUT_RESPONSE_SHEET
    if layout_hint == LAYOUT_PYQ_STANDARD:
        sol_windows = int(metrics.get("solution_window_count") or 0)
        if sol_windows >= 20:
            return LAYOUT_PYQ_SOLVED
        return LAYOUT_PYQ_STANDARD

    answer_source = artifact.get("answer_source") or metrics.get("answer_source_mode")
    if answer_source == "answer_key_table" and not artifact.get("response_sheet_markers_detected"):
        return LAYOUT_ANSWER_KEY_ONLY

    if artifact.get("response_sheet_markers_detected") or artifact.get("chosen_option_detected"):
        return LAYOUT_RESPONSE_SHEET

    if _screenshot_markers_without_response_sheet(artifact, metrics):
        return LAYOUT_SCREENSHOT_MCQ
    repeated = artifact.get("repeated_question_numbers") or []
    if repeated and not artifact.get("response_sheet_markers_detected"):
        return LAYOUT_PYQ_STANDARD
    if artifact.get("unsupported_layout_detected"):
        return LAYOUT_UNSUPPORTED

    expected = int(metrics.get("expected_count") or 0)
    raw = int(metrics.get("raw_candidate_count") or metrics.get("semantic_returned_item_count") or 0)
    extra = int(metrics.get("extra_candidate_count") or 0)
    qw = int(metrics.get("question_window_count") or 0)
    if expected and (raw > int(expected * 1.5) or extra > int(expected * 0.3) or qw > int(expected * 1.3)):
        if metrics.get("chosen_option_detected_count", 0) > 0:
            return LAYOUT_RESPONSE_SHEET
        return LAYOUT_OVER_DETECTION

    if main_issue in {"question_window_over_detection", "unsupported_layout"}:
        if metrics.get("chosen_option_detected_count", 0) > 0:
            return LAYOUT_RESPONSE_SHEET
        if main_issue == "unsupported_layout":
            return LAYOUT_UNSUPPORTED
        return LAYOUT_OVER_DETECTION

    ready = int(metrics.get("ready_count") or 0)
    blocked = int(metrics.get("blocked_count") or 0)
    detected = int(metrics.get("total_questions_detected") or 0)
    if metrics.get("ocr_used") and detected and ready < max(15, int(detected * 0.25)) and blocked > int(detected * 0.5):
        return LAYOUT_OCR_DEGRADED

    sol_windows = int(metrics.get("solution_window_count") or 0)
    if sol_windows >= max(20, int((expected or detected) * 0.5)):
        return LAYOUT_PYQ_SOLVED
    return LAYOUT_PYQ_STANDARD


def _load_layout_artifacts(package_dir: Path | None) -> dict[str, Any]:
    if package_dir is None or not package_dir.exists():
        return {}

    candidates = [
        package_dir / "diagnostics" / "pdf-extractor-profile.json",
        package_dir / "evidence" / "extraction-capability-profile.json",
        package_dir / "evidence" / "question-windows.json",
        package_dir / "diagnostics" / "unsupported-layout-report.json",
    ]
    merged: dict[str, Any] = {}
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        for key in (
            "unsupported_layout_detected",
            "response_sheet_markers_detected",
            "chosen_option_detected",
            "unsupported_reason",
            "repeated_question_numbers",
            "layout_hint",
            "answer_source",
            "adda_or_screenshot_ui_score",
        ):
            if key in data and data[key] is not None:
                merged[key] = data[key]
    return merged


def _screenshot_markers_without_response_sheet(
    artifact: dict[str, Any],
    metrics: dict[str, Any],
) -> bool:
    """Adda/screenshot UI markers without true response-sheet metadata."""
    layout_hint = artifact.get("layout_hint")
    if layout_hint in {LAYOUT_PYQ_STANDARD, LAYOUT_PYQ_SOLVED}:
        return False
    if artifact.get("chosen_option_detected"):
        return False
    if artifact.get("response_sheet_markers_detected"):
        return False
    adda_score = int(artifact.get("adda_or_screenshot_ui_score") or 0)
    if adda_score >= 2:
        return True
    if metrics.get("ocr_used") and int(metrics.get("ready_count") or 0) < 85:
        inline = metrics.get("answer_source_mode") == "inline_answer"
        if inline and not metrics.get("chosen_option_detected_count"):
            return True
    return False
