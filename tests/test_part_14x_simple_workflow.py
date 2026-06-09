"""Part 14X simple commands, extractor routing, and review merge tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from meritranker_data_ingestion.services.layout_type_classifier import (
    LAYOUT_RESPONSE_SHEET,
    LAYOUT_SCREENSHOT_MCQ,
    classify_layout_type,
)
from meritranker_data_ingestion.services.pdf_extractor_router import (
    LAYOUT_HINT_RESPONSE_SHEET,
    LAYOUT_HINT_SCREENSHOT,
    STRATEGY_AZURE_PRIMARY,
    STRATEGY_DUAL_DEBUG,
    STRATEGY_MARKER_PRIMARY,
    route_extractor_strategy,
)
from meritranker_data_ingestion.services.review_json_exporter import export_review_json
from meritranker_data_ingestion.services.review_merge_service import merge_reviewed_questions
from meritranker_data_ingestion.services.public_questions_audit import audit_public_questions_json
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    resolve_ocr_engine_for_strategy,
)


def _write_minimal_pdf(path: Path, text: str = "Sample page") -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def _sample_question(qnum: int, status: str, *, options: int = 4, visual: bool = False) -> dict:
    opts = [
        {"label": label, "text": f"opt {label}"}
        for label in ("A", "B", "C", "D")[:options]
    ]
    visuals = []
    if visual:
        visuals = [{"syntax": None, "type": "image"}]
    return {
        "externalId": f"Q{qnum:03d}",
        "questionText": f"Question {qnum}",
        "questionType": "single_choice",
        "options": opts,
        "correctAnswer": {"label": None, "text": None},
        "solutionText": None,
        "solutionSource": "unavailable",
        "visuals": visuals,
        "metadata": {
            "exams": [],
            "years": [],
            "section": None,
            "sourcePaper": "test.pdf",
            "questionNumber": qnum,
            "status": status,
            "reviewIssues": [] if status == "ready" else ["incomplete_options"],
        },
    }


def _write_questions(path: Path, count: int = 2) -> None:
    questions = [_sample_question(1, "ready"), _sample_question(2, "review", options=2)]
    payload = {"fileMeta": {"sourceName": "test.pdf"}, "questions": questions}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _digital_signals() -> dict[str, float | int]:
    return {
        "page_count": 10,
        "selectable_text_char_count": 5000,
        "text_density_score": 0.7,
        "image_area_ratio": 0.05,
        "question_anchor_count": 12,
        "option_label_count": 40,
        "table_like_line_count": 0,
        "scanned_or_screenshot_score": 0.1,
        "response_sheet_marker_score": 0,
        "adda_or_screenshot_ui_score": 0,
    }


def _screenshot_signals() -> dict[str, float | int]:
    return {
        "page_count": 8,
        "selectable_text_char_count": 200,
        "text_density_score": 0.15,
        "image_area_ratio": 0.6,
        "question_anchor_count": 2,
        "option_label_count": 4,
        "table_like_line_count": 0,
        "scanned_or_screenshot_score": 0.7,
        "response_sheet_marker_score": 0,
        "adda_or_screenshot_ui_score": 4,
    }


def _response_sheet_signals() -> dict[str, float | int]:
    return {
        "page_count": 5,
        "selectable_text_char_count": 800,
        "text_density_score": 0.4,
        "image_area_ratio": 0.2,
        "question_anchor_count": 3,
        "option_label_count": 10,
        "table_like_line_count": 0,
        "scanned_or_screenshot_score": 0.3,
        "response_sheet_marker_score": 5,
        "adda_or_screenshot_ui_score": 0,
    }


def test_digital_profile_routes_marker_primary(tmp_path: Path) -> None:
    pdf = tmp_path / "digital.pdf"
    _write_minimal_pdf(pdf, "Q.1 text " * 20 + "(A) one (B) two")
    with patch(
        "meritranker_data_ingestion.services.pdf_extractor_router._collect_pdf_signals",
        return_value=_digital_signals(),
    ):
        profile = route_extractor_strategy(pdf, strategy="auto", allow_auto_fallback=True)
    assert profile.extractor_strategy_effective == STRATEGY_MARKER_PRIMARY
    assert profile.marker_used is True
    assert profile.azure_used is False


def test_screenshot_profile_routes_azure_primary(tmp_path: Path) -> None:
    pdf = tmp_path / "screenshot.pdf"
    _write_minimal_pdf(pdf)
    with patch(
        "meritranker_data_ingestion.services.pdf_extractor_router._collect_pdf_signals",
        return_value=_screenshot_signals(),
    ):
        profile = route_extractor_strategy(pdf, strategy="auto")
    assert profile.extractor_strategy_effective == STRATEGY_AZURE_PRIMARY
    assert profile.layout_hint == LAYOUT_HINT_SCREENSHOT
    assert profile.azure_used is True
    assert profile.marker_used is False


def test_response_sheet_routes_azure_with_layout_hint(tmp_path: Path) -> None:
    pdf = tmp_path / "response.pdf"
    _write_minimal_pdf(pdf, "Question ID 1 Chosen Option: A Status: Answered")
    with patch(
        "meritranker_data_ingestion.services.pdf_extractor_router._collect_pdf_signals",
        return_value=_response_sheet_signals(),
    ):
        profile = route_extractor_strategy(pdf, strategy="auto")
    assert profile.extractor_strategy_effective == STRATEGY_AZURE_PRIMARY
    assert profile.layout_hint == LAYOUT_HINT_RESPONSE_SHEET


def test_forced_marker_primary_strategy(tmp_path: Path) -> None:
    pdf = tmp_path / "forced_marker.pdf"
    _write_minimal_pdf(pdf)
    profile = route_extractor_strategy(pdf, strategy=STRATEGY_MARKER_PRIMARY)
    assert profile.extractor_strategy_effective == STRATEGY_MARKER_PRIMARY
    assert profile.marker_used is True
    assert profile.azure_used is False


def test_forced_azure_primary_strategy(tmp_path: Path) -> None:
    pdf = tmp_path / "forced_azure.pdf"
    _write_minimal_pdf(pdf)
    profile = route_extractor_strategy(pdf, strategy=STRATEGY_AZURE_PRIMARY)
    assert profile.extractor_strategy_effective == STRATEGY_AZURE_PRIMARY
    assert profile.azure_used is True
    assert profile.marker_used is False


def test_dual_debug_strategy(tmp_path: Path) -> None:
    pdf = tmp_path / "dual.pdf"
    _write_minimal_pdf(pdf)
    profile = route_extractor_strategy(pdf, strategy=STRATEGY_DUAL_DEBUG)
    assert profile.extractor_strategy_effective == STRATEGY_DUAL_DEBUG
    assert profile.dual_used is True


def test_marker_primary_ocr_engine_is_none() -> None:
    assert resolve_ocr_engine_for_strategy(STRATEGY_MARKER_PRIMARY, "auto") == "none"


def test_azure_primary_ocr_engine_defaults_auto() -> None:
    assert resolve_ocr_engine_for_strategy(STRATEGY_AZURE_PRIMARY, "none") == "auto"


def test_screenshot_layout_not_response_sheet() -> None:
    layout = classify_layout_type(
        {
            "chosen_option_detected_count": 0,
            "ocr_used": True,
            "ready_count": 65,
            "answer_source_mode": "inline_answer",
        },
        package_dir=_fake_package_with_hint("screenshot_mcq_layout"),
    )
    assert layout == LAYOUT_SCREENSHOT_MCQ


def test_response_sheet_layout_with_metadata() -> None:
    layout = classify_layout_type(
        {},
        package_dir=_fake_package_with_chosen_option(),
    )
    assert layout == LAYOUT_RESPONSE_SHEET


def test_review_json_contains_only_non_ready(tmp_path: Path) -> None:
    folder = tmp_path / "sample_pdf"
    folder.mkdir()
    questions_path = folder / "sample_pdf.questions.json"
    _write_questions(questions_path)
    review_path = export_review_json(questions_path)
    assert review_path is not None
    data = json.loads(review_path.read_text(encoding="utf-8"))
    assert len(data["questions"]) == 1
    assert data["questions"][0]["metadata"]["status"] == "review"


def test_merge_replaces_review_and_preserves_ready(tmp_path: Path) -> None:
    folder = tmp_path / "sample_pdf"
    folder.mkdir()
    questions_path = folder / "sample_pdf.questions.json"
    _write_questions(questions_path)

    review = {
        "fileMeta": {"sourceName": "test.pdf"},
        "questions": [
            _sample_question(2, "ready", options=4),
        ],
    }
    review_path = folder / "sample_pdf.review.json"
    review_path.write_text(json.dumps(review, indent=2), encoding="utf-8")

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )
    assert result.passed
    final = json.loads(result.final_questions_path.read_text(encoding="utf-8"))
    assert final["questions"][0]["metadata"]["status"] == "ready"
    assert final["questions"][1]["metadata"]["status"] == "ready"
    assert result.ready_copy_path is not None
    assert result.ready_copy_path.exists()


def test_merge_fails_ready_with_incomplete_options(tmp_path: Path) -> None:
    folder = tmp_path / "sample_pdf"
    folder.mkdir()
    questions_path = folder / "sample_pdf.questions.json"
    _write_questions(questions_path)

    bad_review = {
        "fileMeta": {"sourceName": "test.pdf"},
        "questions": [
            _sample_question(2, "ready", options=2),
        ],
    }
    review_path = folder / "sample_pdf.review.json"
    review_path.write_text(json.dumps(bad_review, indent=2), encoding="utf-8")

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )
    assert not result.passed
    assert result.final_questions_path is None
    assert any("ready_with_incomplete_options" in err for err in result.errors)


def test_merge_fails_ready_visual_missing_syntax(tmp_path: Path) -> None:
    folder = tmp_path / "sample_pdf"
    folder.mkdir()
    questions_path = folder / "sample_pdf.questions.json"
    _write_questions(questions_path)

    bad_review = {
        "fileMeta": {"sourceName": "test.pdf"},
        "questions": [
            _sample_question(2, "ready", options=4, visual=True),
        ],
    }
    review_path = folder / "sample_pdf.review.json"
    review_path.write_text(json.dumps(bad_review, indent=2), encoding="utf-8")

    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )
    assert not result.passed
    assert result.final_questions_path is None


def test_public_audit_after_merge(tmp_path: Path) -> None:
    folder = tmp_path / "sample_pdf"
    folder.mkdir()
    questions_path = folder / "sample_pdf.questions.json"
    _write_questions(questions_path)
    review_path = folder / "sample_pdf.review.json"
    review_path.write_text(
        json.dumps(
            {
                "fileMeta": {"sourceName": "test.pdf"},
                "questions": [_sample_question(2, "ready", options=4)],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = merge_reviewed_questions(
        questions_path,
        review_path,
        ready_dir=tmp_path / "ready",
        expected_count=2,
    )
    audit = audit_public_questions_json(result.final_questions_path, expected_count=2)
    assert audit.passed


def _fake_package_with_hint(hint: str) -> Path:
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    diag = tmp / "diagnostics"
    diag.mkdir(parents=True)
    (diag / "pdf-extractor-profile.json").write_text(
        json.dumps({"layout_hint": hint}),
        encoding="utf-8",
    )
    return tmp


def _fake_package_with_chosen_option() -> Path:
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    evidence = tmp / "evidence"
    evidence.mkdir(parents=True)
    (evidence / "extraction-capability-profile.json").write_text(
        json.dumps({"chosen_option_detected": True, "response_sheet_markers_detected": True}),
        encoding="utf-8",
    )
    return tmp
