"""Part 14Z Group D regression and semantic underbound fallback tests."""

from __future__ import annotations

from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionQualityStatus,
)
from meritranker_data_ingestion.services.layout_type_classifier import (
    LAYOUT_PYQ_SOLVED,
    LAYOUT_SCREENSHOT_MCQ,
    classify_layout_type,
)
from meritranker_data_ingestion.services.window_final_question_builder import (
    WindowFinalBuildResult,
    _semantic_underbound_detected,
    merge_semantic_and_window_exports,
)


def _semantic_item(qnum: int) -> FinalQuestionItem:
    return FinalQuestionItem(
        final_question_id=f"fq_{qnum}",
        global_order=qnum,
        question_number=qnum,
        question_text_raw=f"Q{qnum}",
        quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
    )


def _window_item(qnum: int) -> FinalQuestionItem:
    return FinalQuestionItem(
        final_question_id=f"fq_win_{qnum}",
        global_order=qnum,
        question_number=qnum,
        question_text_raw=f"Window Q{qnum}",
        quality_status=FinalQuestionQualityStatus.ANSWER_UNAVAILABLE,
    )


def test_semantic_underbound_by_expected_count_not_only_window_ratio() -> None:
    """Group D pattern: 84 semantic / 99 windows / 100 expected should underbound."""
    assert _semantic_underbound_detected(
        semantic_count=84,
        question_window_count=99,
        expected_count=100,
    )


def test_semantic_not_underbound_when_expected_met() -> None:
    assert not _semantic_underbound_detected(
        semantic_count=93,
        question_window_count=99,
        expected_count=100,
    )


def test_underbound_fills_missing_questions_from_windows() -> None:
    semantic = [_semantic_item(n) for n in range(1, 40)] + [_semantic_item(n) for n in range(57, 85)]
    window_result = WindowFinalBuildResult(
        items=[_window_item(n) for n in range(40, 57)],
        answers_mapped_count=0,
        solutions_mapped_count=0,
        deterministic_window_questions_built=17,
        warnings=[],
    )
    merge = merge_semantic_and_window_exports(
        semantic_items=semantic,
        window_result=window_result,
        question_window_count=99,
        expected_count=100,
    )
    assert merge.semantic_underbound_window_fallback_used
    qnums = {item.question_number for item in merge.items if item.question_number}
    assert all(n in qnums for n in range(40, 57))


def test_pyq_standard_hint_not_misclassified_as_screenshot() -> None:
    layout = classify_layout_type(
        {
            "expected_count": 100,
            "solution_window_count": 29,
            "ready_count": 78,
            "ocr_used": False,
        },
        package_dir=_package_with_hint("pyq_standard_mcq", adda_score=2),
    )
    assert layout == LAYOUT_PYQ_SOLVED
    assert layout != LAYOUT_SCREENSHOT_MCQ


def test_screenshot_layout_still_detected_without_pyq_hint() -> None:
    layout = classify_layout_type(
        {"ready_count": 65, "ocr_used": True, "answer_source_mode": "inline_answer"},
        package_dir=_package_with_hint("screenshot_mcq_layout", adda_score=5),
    )
    assert layout == LAYOUT_SCREENSHOT_MCQ


def _package_with_hint(hint: str, *, adda_score: int = 0):
    import json
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp())
    diag = tmp / "diagnostics"
    diag.mkdir(parents=True)
    (diag / "pdf-extractor-profile.json").write_text(
        json.dumps(
            {
                "layout_hint": hint,
                "adda_or_screenshot_ui_score": adda_score,
            },
        ),
        encoding="utf-8",
    )
    return tmp
