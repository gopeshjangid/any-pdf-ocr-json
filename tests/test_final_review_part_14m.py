"""Tests for Part 14M review analyzer and readiness hardening."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine
from meritranker_data_ingestion.schemas.evidence_answer_solution_map import (
    AnswerSolutionMapEntry,
    AnswerSolutionMapPackage,
)
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalAnswerSource,
    FinalQuestionItem,
    FinalQuestionItemMetadata,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.services.answer_solution_join_diagnostics import (
    diagnose_answer_solution_join_gaps,
)
from meritranker_data_ingestion.services.deterministic_option_parser import parse_options_from_window_lines
from meritranker_data_ingestion.services.final_readiness_resolver import (
    apply_readiness_metadata,
    resolve_item_readiness,
)
from meritranker_data_ingestion.services.final_review_analyzer import build_final_review_analyzer
from meritranker_data_ingestion.services.option_recovery import recover_incomplete_options
from meritranker_data_ingestion.services.share_log_builder import (
    ShareLogContext,
    _partial_completion_reason,
    _problem_sample_priority,
    build_share_log,
)
from meritranker_data_ingestion.services.visual_detection import apply_visual_metadata


def _item(**kwargs) -> FinalQuestionItem:
    defaults = {
        "final_question_id": "fq_1",
        "global_order": 1,
        "question_number": 1,
        "question_text_raw": "Pick the correct option.",
        "options": [
            FinalQuestionOption(key="a", key_raw="a", text_raw="A", canonical_key="A"),
            FinalQuestionOption(key="b", key_raw="b", text_raw="B", canonical_key="B"),
            FinalQuestionOption(key="c", key_raw="c", text_raw="C", canonical_key="C"),
            FinalQuestionOption(key="d", key_raw="d", text_raw="D", canonical_key="D"),
        ],
    }
    defaults.update(kwargs)
    return FinalQuestionItem(**defaults)


def test_answer_unavailable_does_not_block_question_bank_ready() -> None:
    item = _item(
        quality_status=FinalQuestionQualityStatus.ANSWER_UNAVAILABLE,
        answer_source=FinalAnswerSource.UNAVAILABLE,
    )
    metadata = resolve_item_readiness(item, answers_expected=False)
    assert metadata.status == "ready"
    assert "expected_answer_missing" not in metadata.review_issues


def test_answer_unavailable_keeps_ready_with_expected_answer_issue() -> None:
    item = _item(quality_status=FinalQuestionQualityStatus.ANSWER_UNAVAILABLE)
    metadata = resolve_item_readiness(item, answers_expected=True)
    assert metadata.status == "ready"
    assert "expected_answer_missing" in metadata.review_issues


def test_missing_solution_does_not_block_question_bank_ready() -> None:
    item = _item(
        correct_answer_key="A",
        correct_answer_text="A",
        answer_source=FinalAnswerSource.SEPARATE_SOLUTION_SECTION,
        solution_text_raw=None,
        quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
    )
    metadata = resolve_item_readiness(item, answers_expected=True)
    assert metadata.status == "ready"


def test_visual_phrase_creates_visuals_item() -> None:
    item = _item(
        question_text_raw="In the given figure, find the shaded region.",
        options=[],
        quality_status=FinalQuestionQualityStatus.REVIEW_REQUIRED,
    )
    updated = apply_visual_metadata(item)
    assert len(updated.visuals) >= 1
    assert updated.visuals[0].extraction_status == "image_required"


def test_visual_phrase_without_render_spec_sets_visual_required() -> None:
    item = _item(
        question_text_raw="Study the following graph and answer.",
        options=[],
    )
    updated = apply_readiness_metadata(apply_visual_metadata(item))
    assert updated.metadata.status == "visual_required"


def test_incomplete_options_stay_review_required() -> None:
    item = _item(
        options=[FinalQuestionOption(key="a", key_raw="a", text_raw="Only", canonical_key="A")],
        issues=["incomplete_options"],
        quality_status=FinalQuestionQualityStatus.REVIEW_REQUIRED,
    )
    updated = apply_readiness_metadata(item)
    assert updated.metadata.status == "review"


def test_image_only_option_creates_visual_placeholder() -> None:
    line = EvidenceLine(
        line_id="img_a",
        text_raw="(a) ![](diagram.jpeg)",
        normalized_preview="(a) ![](diagram.jpeg)",
        source_extractor="marker",
    )
    item = _item(options=[], question_number=5)
    from meritranker_data_ingestion.schemas.question_window import QuestionWindow

    window = QuestionWindow(
        window_id="qw_0005",
        parsed_question_number=5,
        global_order=5,
        line_ids=["img_a"],
        option_candidate_line_ids=["img_a"],
    )
    from meritranker_data_ingestion.schemas.document_evidence import (
        DocumentEvidencePackage,
        EvidenceExtractionStatus,
    )

    evidence = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="t.pdf",
        primary_extractor="marker",
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=[line],
    )
    recovered = recover_incomplete_options(item, window=window, evidence=evidence)
    assert any(opt.text_raw == "[visual option A]" for opt in recovered.options)
    assert any(v.linked_option_label == "A" for v in recovered.visuals)


def test_answer_solution_join_gap_reported_with_reason() -> None:
    item = _item(
        question_number=19,
        options=[FinalQuestionOption(key="a", key_raw="a", text_raw="A", canonical_key="A")],
        issues=["incomplete_options", "answer_key_not_in_options"],
        quality_status=FinalQuestionQualityStatus.REVIEW_REQUIRED,
    )
    diag = diagnose_answer_solution_join_gaps(
        [item],
        AnswerSolutionMapPackage(
            source_file_name="t.pdf",
            total_mapped=1,
            map_usable=True,
            entries=[
                AnswerSolutionMapEntry(
                    question_number=19,
                    answer_label="C",
                    solution_text="work",
                    line_ids=["s1"],
                ),
            ],
        ),
    )
    assert diag.answer_solution_join_gap_count == 1
    assert diag.answer_solution_join_gap_items[0].reason == "option_incomplete"


def test_partial_reason_not_low_accepted_safe_when_full_export() -> None:
    metrics = {
        "expected_count": 100,
        "total_questions_detected": 100,
        "blocked_count": 0,
        "accepted_safe_with_incomplete_options_count": 0,
        "review_required_count": 5,
        "review_items_count": 19,
        "incomplete_options_count": 0,
        "visual_required_count": 0,
        "answer_unavailable_count": 12,
    }
    assert _partial_completion_reason(metrics) == "review_items_remaining"
    assert _partial_completion_reason(metrics) != "low_accepted_safe_count"


def test_problem_samples_prioritize_real_review_items() -> None:
    good = _item(
        metadata=FinalQuestionItemMetadata(status="ready", review_issues=[]),
        quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
    )
    bad = _item(
        question_number=2,
        final_question_id="fq_2",
        options=[],
        issues=["incomplete_options", "visual_required"],
        quality_status=FinalQuestionQualityStatus.VISUAL_REQUIRED,
        question_text_raw="In the given figure, find x.",
    )
    assert _problem_sample_priority(good, 4) == 0
    assert _problem_sample_priority(bad, 0) > 0


def test_star_paren_options_parse_abcd() -> None:
    from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine
    from meritranker_data_ingestion.services.deterministic_option_parser import (
        parse_options_from_window_lines,
    )

    lines = [
        EvidenceLine(
            line_id="1",
            text_raw="- (*a*) twittering (*b*) writing",
            normalized_preview="- (*a*) twittering (*b*) writing",
            source_extractor="marker",
        ),
        EvidenceLine(
            line_id="2",
            text_raw="- (*c*) rambling (*d*) littering",
            normalized_preview="- (*c*) rambling (*d*) littering",
            source_extractor="marker",
        ),
    ]
    result = parse_options_from_window_lines(lines)
    by_key = {opt.canonical_key: opt.text_raw for opt in result.options}
    assert by_key == {
        "A": "twittering",
        "B": "writing",
        "C": "rambling",
        "D": "littering",
    }


def test_paren_formula_options_parse_four_labels() -> None:
    lines = [
        EvidenceLine(
            line_id="1",
            text_raw="- (a) (23√21) 4",
            normalized_preview="- (a) (23√21) 4",
            source_extractor="marker",
        ),
        EvidenceLine(
            line_id="2",
            text_raw="- (b) 15√21 4",
            normalized_preview="- (b) 15√21 4",
            source_extractor="marker",
        ),
        EvidenceLine(
            line_id="3",
            text_raw="- (c) (17√21)/5 (d) (23√21)/5",
            normalized_preview="- (c) (17√21)/5 (d) (23√21)/5",
            source_extractor="marker",
        ),
    ]
    result = parse_options_from_window_lines(lines)
    assert len(result.options) == 4


def test_review_analyzer_writes_artifacts(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "extraction_package" / "final-questions"
    pkg_dir.mkdir(parents=True)
    package = FinalQuestionsPackage(
        source_file_name="t.pdf",
        total_questions_detected=1,
        items=[
            apply_readiness_metadata(
                _item(
                    options=[],
                    question_text_raw="In the given figure, find angle.",
                    quality_status=FinalQuestionQualityStatus.VISUAL_REQUIRED,
                ),
            ),
        ],
    )
    result = build_final_review_analyzer(tmp_path / "extraction_package", package=package)
    assert result.json_path.exists()
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert data["total_review_items"] >= 1
    assert data["items"][0]["reviewClass"] == "visual_required"


def test_missing_question_placeholders_are_generated() -> None:
    from meritranker_data_ingestion.services.final_completeness_verifier import (
        verify_and_fill_completeness,
    )

    item = _item(question_number=1)
    filled, report = verify_and_fill_completeness([item], expected_count=3, source_file_name="t.pdf")
    assert report.missing_question_numbers == [2, 3]
    assert report.placeholders_added == 2
    assert len(filled) == 3
    blocked = next(i for i in filled if i.question_number == 2)
    assert blocked.quality_status == FinalQuestionQualityStatus.BLOCKED
    assert "question_missing_from_extraction" in blocked.issues


def test_share_log_partial_reason_for_quant_like_metrics(tmp_path: Path) -> None:
    out = tmp_path / "paper"
    out.mkdir()
    pkg = out / "extraction_package"
    fq_dir = pkg / "final-questions"
    fq_dir.mkdir(parents=True)
    (fq_dir / "final-questions.json").write_text(
        FinalQuestionsPackage(
            source_file_name="paper.pdf",
            total_questions_detected=100,
            accepted_safe_count=81,
            review_required_count=5,
            visual_required_count=2,
            answer_unavailable_count=12,
            ready_count=88,
            review_count=12,
            review_items_count=19,
            items=[],
        ).model_dump_json(),
        encoding="utf-8",
    )
    (fq_dir / "final-questions-report.json").write_text(
        json.dumps(
            {
                "expected_count": 100,
                "total_questions_detected": 100,
                "accepted_safe_with_incomplete_options_count": 0,
                "review_items_count": 19,
                "ready_count": 88,
                "review_count": 5,
                "visual_required_count": 2,
                "incomplete_options_count": 3,
            },
        ),
        encoding="utf-8",
    )
    ctx = ShareLogContext(
        pdf_file_name="paper.pdf",
        input_path=tmp_path / "paper.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
        questions_json_path=fq_dir / "final-questions.json",
    )
    result = build_share_log(ctx)
    assert result.run_status == "partial_ready"
    assert result.main_failure_reason == "incomplete_options_remaining"
