"""Tests for Part 14S ready-recovery and issue severity."""

from __future__ import annotations

import json
from pathlib import Path

from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage, EvidenceLine
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.schemas.question_window import QuestionWindow
from meritranker_data_ingestion.services.deterministic_option_parser import parse_options_from_window_lines
from meritranker_data_ingestion.services.final_questions_public_serializer import serialize_public_package
from meritranker_data_ingestion.services.final_readiness_resolver import resolve_item_readiness
from meritranker_data_ingestion.services.hallucination_triage import triage_hallucination_blocked_item
from meritranker_data_ingestion.services.issue_severity_resolver import (
    is_blocking_extraction_issue,
    is_blocking_extraction_issue as blocking,
)
from meritranker_data_ingestion.services.public_questions_audit import audit_public_questions_json
from meritranker_data_ingestion.services.semantic_embedded_option_parser import extract_options_from_line


def _four_options() -> list[FinalQuestionOption]:
    return [
        FinalQuestionOption(key="a", key_raw="a", text_raw="Alpha", canonical_key="A"),
        FinalQuestionOption(key="b", key_raw="b", text_raw="Beta", canonical_key="B"),
        FinalQuestionOption(key="c", key_raw="c", text_raw="Gamma", canonical_key="C"),
        FinalQuestionOption(key="d", key_raw="d", text_raw="Delta", canonical_key="D"),
    ]


def _item(**kwargs) -> FinalQuestionItem:
    defaults = {
        "final_question_id": "fq_1",
        "global_order": 1,
        "question_number": 1,
        "question_text_raw": "Pick the correct option.",
        "options": _four_options(),
    }
    defaults.update(kwargs)
    return FinalQuestionItem(**defaults)


def test_answer_key_not_in_options_non_blocking() -> None:
    assert not is_blocking_extraction_issue("answer_key_not_in_options")
    meta = resolve_item_readiness(
        _item(
            correct_answer_key="D",
            issues=["answer_key_not_in_options"],
        ),
        answers_expected=True,
    )
    assert meta.status == "ready"
    assert "answer_key_not_in_options" in meta.review_issues


def test_expected_answer_missing_non_blocking() -> None:
    meta = resolve_item_readiness(_item(), answers_expected=True)
    assert meta.status == "ready"
    assert "expected_answer_missing" in meta.review_issues


def test_incomplete_options_blocks_review() -> None:
    meta = resolve_item_readiness(_item(options=[]), answers_expected=True)
    assert meta.status == "review"
    assert "incomplete_options" in meta.review_issues


def test_visual_syntax_missing_blocks_visual_required() -> None:
    from meritranker_data_ingestion.services.visual_detection import apply_visual_metadata

    meta = resolve_item_readiness(apply_visual_metadata(_item(
        question_text_raw="Study the following graph and answer.",
        options=[],
    )))
    assert meta.status == "visual_required"


def test_hallucination_stays_blocked_without_evidence() -> None:
    item = _item(
        quality_status=FinalQuestionQualityStatus.BLOCKED,
        issues=["hallucinated_question_text", "quarantined_or_excluded"],
    )
    triaged = triage_hallucination_blocked_item(item, window=None, evidence=None)
    assert triaged.quality_status == FinalQuestionQualityStatus.BLOCKED


def test_source_backed_hallucination_downgraded() -> None:
    line = EvidenceLine(
        line_id="ln_1",
        text_raw="63. Select the set in which the numbers are related in the same way. related",
        normalized_preview="",
        source_extractor="marker",
    )
    from meritranker_data_ingestion.schemas.document_evidence import EvidenceExtractionStatus

    evidence = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="t.pdf",
        primary_extractor="marker",
        lines=[line],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
    )
    window = QuestionWindow(
        window_id="qw_1",
        parsed_question_number=63,
        global_order=63,
        line_ids=["ln_1"],
        question_anchor_line_ids=["ln_1"],
    )
    item = _item(
        question_number=63,
        question_text_raw="63. Select the set in which the numbers are related in the same way.",
        options=[
            FinalQuestionOption(key="a", key_raw="a", text_raw="related", canonical_key="A"),
        ],
        quality_status=FinalQuestionQualityStatus.BLOCKED,
        issues=["hallucinated_question_text", "quarantined_or_excluded"],
    )
    triaged = triage_hallucination_blocked_item(item, window=window, evidence=evidence)
    assert triaged.quality_status != FinalQuestionQualityStatus.BLOCKED
    assert not any("hallucinat" in i for i in triaged.issues)


def test_styled_star_paren_options_parse() -> None:
    line = EvidenceLine(
        line_id="ln_1",
        text_raw="(*a*) ÷ and (*b*) and + (*c*) ÷ and + (*d*) + and ×",
        normalized_preview="",
        source_extractor="marker",
    )
    parsed = extract_options_from_line(line.text_raw, line)
    assert len(parsed) == 4
    assert {p[0] for p in parsed} == {"A", "B", "C", "D"}


def test_window_parser_handles_styled_inline_options() -> None:
    line = EvidenceLine(
        line_id="ln_1",
        text_raw="- (*c*) ÷ and + (*d*) + and ×",
        normalized_preview="",
        source_extractor="marker",
    )
    result = parse_options_from_window_lines([line])
    assert len(result.options) >= 2


def test_over_detection_not_fixed_by_pdf_hack() -> None:
    """Canonical 100 slots with over-detection remain reported, not inflated ready."""
    assert blocking("unsupported_layout_detected") is False
    assert blocking("question_missing_from_extraction") is True


def test_token_overlap_recovers_blank_question_hallucination_block() -> None:
    from meritranker_data_ingestion.schemas.document_evidence import EvidenceExtractionStatus

    line = EvidenceLine(
        line_id="ln_1",
        text_raw="78. Fundamental duties are _________ and not enforceable by law. (*a*) regulatory (*b*) non-statutory (*c*) statutory (*d*) common",
        normalized_preview="",
        source_extractor="marker",
    )
    evidence = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="t.pdf",
        primary_extractor="marker",
        lines=[line],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
    )
    window = QuestionWindow(
        window_id="qw_78",
        parsed_question_number=78,
        global_order=78,
        line_ids=["ln_1"],
        question_anchor_line_ids=["ln_1"],
    )
    item = _item(
        question_number=78,
        question_text_raw="Fundamental duties are _________ and not enforceable by law.",
        options=[
            FinalQuestionOption(key="a", key_raw="a", text_raw="regulatory", canonical_key="A"),
            FinalQuestionOption(key="b", key_raw="b", text_raw="non-statutory", canonical_key="B"),
            FinalQuestionOption(key="c", key_raw="c", text_raw="statutory", canonical_key="C"),
            FinalQuestionOption(key="d", key_raw="d", text_raw="common", canonical_key="D"),
        ],
        quality_status=FinalQuestionQualityStatus.BLOCKED,
        final_gate_status="blocked_bad_item",
        issues=["hallucinated_question_text", "quarantined_or_excluded"],
    )
    triaged = triage_hallucination_blocked_item(item, window=window, evidence=evidence)
    assert triaged.quality_status != FinalQuestionQualityStatus.BLOCKED
    assert not any("hallucinat" in i for i in triaged.issues)


def test_public_audit_still_passes_ready_with_answer_issue(tmp_path: Path) -> None:
    item = resolve_item_readiness(
        _item(correct_answer_key="Z", issues=["answer_key_not_in_options"]),
        answers_expected=True,
    )
    package = FinalQuestionsPackage(
        source_file_name="paper.pdf",
        total_questions_detected=1,
        items=[_item(
            question_text_raw="Question?",
            options=_four_options(),
            metadata=item,
        )],
    )
    payload = serialize_public_package(package)
    result = audit_public_questions_json(payload)
    assert result.passed, result.errors
