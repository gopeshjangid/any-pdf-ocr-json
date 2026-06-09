"""Part 14Y PDF router sampling and marker fallback tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from meritranker_data_ingestion.services.marker_fallback_evaluator import (
    evaluate_marker_fallback,
)
from meritranker_data_ingestion.services.pdf_extractor_router import (
    LAYOUT_HINT_RESPONSE_SHEET,
    LAYOUT_HINT_SCREENSHOT,
    STRATEGY_AZURE_PRIMARY,
    STRATEGY_DUAL_DEBUG,
    STRATEGY_MARKER_PRIMARY,
    _sample_page_indices,
    route_extractor_strategy,
)
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    resolve_ocr_engine_for_strategy,
)


def _write_multipage_pdf(path: Path, pages: list[str]) -> None:
    import fitz

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_sample_page_indices_first_middle_last() -> None:
    indices = _sample_page_indices(20)
    assert 0 in indices
    assert 1 in indices
    assert 2 in indices
    assert 19 in indices
    assert 18 in indices
    assert 9 in indices or 10 in indices
    assert len(indices) == len(set(indices))


def test_short_pdf_avoids_duplicate_samples() -> None:
    indices = _sample_page_indices(4)
    assert len(indices) == len(set(indices))
    assert max(indices) <= 3


def test_cover_page_downweighted_in_routing(tmp_path: Path) -> None:
    pdf = tmp_path / "mixed.pdf"
    pages = [
        "Syllabus and Contents front cover",
        "Q.1 What is 2+2? (A) 3 (B) 4 (C) 5 (D) 6 " * 5,
        "Q.2 Capital of India? (A) Delhi (B) Mumbai (C) Kolk (D) Chennai " * 5,
        "Q.3 Largest planet? (A) Earth (B) Mars (C) Jupiter (D) Venus " * 5,
    ] + ["Q.4 Sample? (A) one (B) two (C) three (D) four " * 3] * 6
    _write_multipage_pdf(pdf, pages)

    profile = route_extractor_strategy(pdf, strategy="auto")
    assert profile.extractor_strategy_effective == STRATEGY_MARKER_PRIMARY
    assert profile.sampled_pages
    assert profile.ignored_profile_pages_count >= 0


def test_digital_pdf_routes_marker_without_azure(tmp_path: Path) -> None:
    pdf = tmp_path / "digital.pdf"
    text = " ".join(f"Q.{i} question text? (A) a (B) b (C) c (D) d" for i in range(1, 15))
    _write_multipage_pdf(pdf, [text] * 8)

    with patch(
        "meritranker_data_ingestion.services.pdf_extractor_router._collect_pdf_signals",
    ) as mock_signals:
        mock_signals.return_value = {
            "page_count": 8,
            "selectable_text_char_count": 8000,
            "text_density_score": 0.8,
            "image_area_ratio": 0.05,
            "question_anchor_count": 20,
            "option_label_count": 60,
            "table_like_line_count": 0,
            "scanned_or_screenshot_score": 0.1,
            "response_sheet_marker_score": 0,
            "adda_or_screenshot_ui_score": 0,
            "answer_key_marker_count": 0,
            "sampled_pages": [0, 1, 2, 4, 5, 6, 7],
            "ignored_profile_pages_count": 1,
        }
        profile = route_extractor_strategy(pdf, strategy="auto")

    assert profile.extractor_strategy_effective == STRATEGY_MARKER_PRIMARY
    assert profile.azure_used is False
    assert resolve_ocr_engine_for_strategy(STRATEGY_MARKER_PRIMARY, "auto") == "none"


def test_screenshot_pdf_routes_azure_without_marker(tmp_path: Path) -> None:
    pdf = tmp_path / "screenshot.pdf"
    _write_multipage_pdf(pdf, ["low text page"])

    with patch(
        "meritranker_data_ingestion.services.pdf_extractor_router._collect_pdf_signals",
    ) as mock_signals:
        mock_signals.return_value = {
            "page_count": 5,
            "selectable_text_char_count": 100,
            "text_density_score": 0.1,
            "image_area_ratio": 0.7,
            "question_anchor_count": 1,
            "option_label_count": 2,
            "table_like_line_count": 0,
            "scanned_or_screenshot_score": 0.8,
            "response_sheet_marker_score": 0,
            "adda_or_screenshot_ui_score": 5,
            "answer_key_marker_count": 0,
            "sampled_pages": [0, 1, 2, 3, 4],
            "ignored_profile_pages_count": 2,
        }
        profile = route_extractor_strategy(pdf, strategy="auto")

    assert profile.extractor_strategy_effective == STRATEGY_AZURE_PRIMARY
    assert profile.layout_hint == LAYOUT_HINT_SCREENSHOT
    assert profile.marker_used is False


def test_response_sheet_routes_azure_with_layout_hint(tmp_path: Path) -> None:
    pdf = tmp_path / "response.pdf"
    _write_multipage_pdf(pdf, ["Question ID 1 Chosen Option: A Status: Answered"])

    with patch(
        "meritranker_data_ingestion.services.pdf_extractor_router._collect_pdf_signals",
    ) as mock_signals:
        mock_signals.return_value = {
            "page_count": 3,
            "selectable_text_char_count": 500,
            "text_density_score": 0.4,
            "image_area_ratio": 0.2,
            "question_anchor_count": 2,
            "option_label_count": 4,
            "table_like_line_count": 0,
            "scanned_or_screenshot_score": 0.3,
            "response_sheet_marker_score": 5,
            "adda_or_screenshot_ui_score": 0,
            "answer_key_marker_count": 0,
            "sampled_pages": [0, 1, 2],
            "ignored_profile_pages_count": 0,
        }
        profile = route_extractor_strategy(pdf, strategy="auto")

    assert profile.extractor_strategy_effective == STRATEGY_AZURE_PRIMARY
    assert profile.layout_hint == LAYOUT_HINT_RESPONSE_SHEET


def test_marker_fallback_when_quality_weak() -> None:
    decision = evaluate_marker_fallback(
        marker_line_count=10,
        question_window_count=40,
        windows_with_4_options=20,
        expected_count=100,
        scanned_or_screenshot_score=0.2,
        image_area_ratio=0.1,
    )
    assert decision.should_fallback
    assert decision.reason


def test_marker_no_fallback_when_quality_good() -> None:
    decision = evaluate_marker_fallback(
        marker_line_count=500,
        question_window_count=90,
        windows_with_4_options=80,
        expected_count=100,
        scanned_or_screenshot_score=0.1,
        image_area_ratio=0.05,
    )
    assert not decision.should_fallback
    assert decision.marker_quality_summary["quality_verdict"] == "good"


def test_dual_debug_only_strategy_runs_both(tmp_path: Path) -> None:
    pdf = tmp_path / "dual.pdf"
    _write_multipage_pdf(pdf, ["page"])
    profile = route_extractor_strategy(pdf, strategy=STRATEGY_DUAL_DEBUG)
    assert profile.dual_used is True
    assert profile.marker_used is True
    assert profile.azure_used is True


def test_profile_includes_sampled_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "long.pdf"
    _write_multipage_pdf(pdf, [f"page {i}" for i in range(12)])
    profile = route_extractor_strategy(pdf, strategy="auto")
    assert len(profile.sampled_pages) >= 3
