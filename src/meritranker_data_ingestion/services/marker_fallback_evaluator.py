"""Quality-based Marker-primary → Azure fallback decision (Part 14Y)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.config import (
    MARKER_FALLBACK_GOOD_MISSING_RATIO,
    MARKER_FALLBACK_GOOD_OPTION_COVERAGE,
    MARKER_FALLBACK_GOOD_WINDOW_RATIO,
    MARKER_FALLBACK_HIGH_IMAGE_RATIO,
    MARKER_FALLBACK_MIN_LINES,
    MARKER_FALLBACK_MISSING_RATIO,
    MARKER_FALLBACK_OPTION_COVERAGE,
    MARKER_FALLBACK_SCANNED_THRESHOLD,
    MARKER_FALLBACK_WINDOW_RATIO,
)
from meritranker_data_ingestion.services.file_service import resolve_path

MARKER_QUALITY_SUMMARY_NAME = "marker-quality-summary.json"


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return default


@dataclass(frozen=True)
class MarkerFallbackDecision:
    should_fallback: bool
    reason: str | None
    marker_quality_summary: dict[str, Any]


def evaluate_marker_fallback(
    *,
    marker_line_count: int,
    question_window_count: int,
    windows_with_4_options: int,
    expected_count: int | None,
    scanned_or_screenshot_score: float,
    image_area_ratio: float,
    profile_question_anchor_count: int = 0,
    profile_option_label_count: int = 0,
) -> MarkerFallbackDecision:
    """Return whether marker-primary should fall back to Azure OCR."""
    marker_line_count = _safe_int(marker_line_count)
    question_window_count = _safe_int(question_window_count)
    windows_with_4_options = _safe_int(windows_with_4_options)
    expected = _safe_int(expected_count)
    scanned_or_screenshot_score = _safe_float(scanned_or_screenshot_score)
    image_area_ratio = _safe_float(image_area_ratio)

    window_ratio = (question_window_count / expected) if expected > 0 else 0.0
    option_coverage = (
        windows_with_4_options / question_window_count if question_window_count > 0 else 0.0
    )
    missing_count = max(0, expected - question_window_count) if expected > 0 else 0
    missing_ratio = (missing_count / expected) if expected > 0 else 0.0

    summary: dict[str, Any] = {
        "marker_line_count": marker_line_count,
        "question_window_count": question_window_count,
        "windows_with_4_options": windows_with_4_options,
        "expected_count": expected_count,
        "window_ratio": round(window_ratio, 4),
        "option_coverage": round(option_coverage, 4),
        "missing_question_count": missing_count,
        "missing_ratio": round(missing_ratio, 4),
        "scanned_or_screenshot_score": scanned_or_screenshot_score,
        "image_area_ratio": image_area_ratio,
        "profile_question_anchor_count": profile_question_anchor_count,
        "profile_option_label_count": profile_option_label_count,
    }

    if expected > 0 and (
        window_ratio >= MARKER_FALLBACK_GOOD_WINDOW_RATIO
        and option_coverage >= MARKER_FALLBACK_GOOD_OPTION_COVERAGE
        and missing_ratio <= MARKER_FALLBACK_GOOD_MISSING_RATIO
    ):
        summary["quality_verdict"] = "good"
        return MarkerFallbackDecision(False, None, summary)

    reasons: list[str] = []
    if marker_line_count < MARKER_FALLBACK_MIN_LINES:
        reasons.append("marker_line_count_low")
    if expected > 0:
        if window_ratio < MARKER_FALLBACK_WINDOW_RATIO:
            reasons.append("question_window_count_low")
        if option_coverage < MARKER_FALLBACK_OPTION_COVERAGE:
            reasons.append("option_coverage_low")
        if missing_ratio > MARKER_FALLBACK_MISSING_RATIO:
            reasons.append("missing_question_count_high")
    if scanned_or_screenshot_score >= MARKER_FALLBACK_SCANNED_THRESHOLD:
        reasons.append("scanned_or_screenshot_score_high")
    if (
        image_area_ratio >= MARKER_FALLBACK_HIGH_IMAGE_RATIO
        and marker_line_count < MARKER_FALLBACK_MIN_LINES * 2
    ):
        reasons.append("high_image_area_low_marker_lines")

    summary["quality_verdict"] = "weak" if reasons else "acceptable"
    summary["fallback_reasons"] = reasons
    reason = ",".join(reasons) if reasons else None
    return MarkerFallbackDecision(bool(reasons), reason, summary)


def write_marker_quality_summary(package_dir: Path, summary: dict[str, Any]) -> Path:
    """Persist marker quality summary under extraction_package/diagnostics/."""
    resolved = resolve_path(package_dir)
    diag = resolved / "diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    path = diag / MARKER_QUALITY_SUMMARY_NAME
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return path
