"""Tests for source-grounded semantic binder (Part 13C)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    FINAL_QUESTIONS_NAME,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BOUND_QUESTIONS_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    EvidenceImage,
    RoleHint,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
    SemanticBoundSolution,
    SemanticMetadataCandidate,
)
from meritranker_data_ingestion.services.llm_provider import LlmProviderError, MockLlmProvider
from meritranker_data_ingestion.services.semantic_binder import (
    SemanticBindingError,
    bind_semantically_package,
    evaluate_binder_trigger,
)
from meritranker_data_ingestion.services.semantic_binding_validator import validate_semantic_items


def _evidence_package(lines: list[EvidenceLine], *, images: list[EvidenceImage] | None = None) -> DocumentEvidencePackage:
    return DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="exam.pdf",
        primary_extractor="marker",
        extractors_available=["marker"],
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=lines,
        images=images or [],
    )


def _line(line_id: str, text: str, hints: list[RoleHint] | None = None) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
        source_span=SourceSpan(extractor="marker", line_id=line_id),
        role_hints=hints or [],
    )


def _write_evidence(package_dir: Path, evidence: DocumentEvidencePackage) -> None:
    path = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")


def test_mock_binder_extracts_bold_options(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [
        _line("marker_l_000001", "**1.** What is the value?", [RoleHint.QUESTION_ANCHOR_CANDIDATE]),
        _line("marker_l_000002", "- **A** 50", [RoleHint.OPTION_LABEL_CANDIDATE]),
        _line("marker_l_000003", "- **B** 67", [RoleHint.OPTION_LABEL_CANDIDATE]),
        _line("marker_l_000004", "- **C** 52", [RoleHint.OPTION_LABEL_CANDIDATE]),
        _line("marker_l_000005", "- **D** 63", [RoleHint.OPTION_LABEL_CANDIDATE]),
    ]
    evidence = _evidence_package(lines)
    _write_evidence(package_dir, evidence)

    result = bind_semantically_package(
        package_dir,
        llm_provider=MockLlmProvider(),
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        force=True,
    )

    assert result.package.status != SemanticBindingStatus.FAILED
    assert len(result.package.items) == 1
    item = result.package.items[0]
    assert len(item.options) == 4
    assert item.options[0].text_raw == "50"
    assert item.options[0].key == "A"


def test_validator_accepts_source_grounded_options() -> None:
    evidence = _evidence_package(
        [
            _line("marker_l_000001", "**1.** Question text"),
            _line("marker_l_000002", "- **A** 50"),
            _line("marker_l_000003", "- **B** 67"),
        ],
    )
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="Question text",
        raw_text="**1.** Question text",
        options=[
            SemanticBoundOption(
                key="A",
                key_raw="A",
                text_raw="50",
                source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000002")],
            ),
            SemanticBoundOption(
                key="B",
                key_raw="B",
                text_raw="67",
                source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000003")],
            ),
        ],
        source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
    )
    report = validate_semantic_items(
        [item],
        [],
        evidence,
        answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY,
    )
    assert item.binding_status == SemanticBindingItemStatus.ACCEPTED
    assert report.accepted_count == 1


def test_validator_rejects_invented_option() -> None:
    evidence = _evidence_package([_line("marker_l_000001", "**1.** Question")])
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="Question",
        raw_text="**1.** Question",
        options=[
            SemanticBoundOption(
                key="A",
                key_raw="A",
                text_raw="999",
                source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
            ),
        ],
        source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
    )
    validate_semantic_items([item], [], evidence, answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY)
    assert item.binding_status == SemanticBindingItemStatus.REJECTED
    assert any("hallucinated_option" in issue for issue in item.issues)


def test_validator_rejects_missing_source_spans() -> None:
    evidence = _evidence_package([_line("marker_l_000001", "**1.** Question")])
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="Question",
        raw_text="**1.** Question",
        options=[],
        source_spans=[],
    )
    report = validate_semantic_items([item], [], evidence, answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY)
    assert item.binding_status == SemanticBindingItemStatus.REJECTED
    assert report.source_span_missing_count >= 1


def test_validator_flags_noise_in_question_text() -> None:
    evidence = _evidence_package(
        [_line("marker_l_000001", "Free Mock Test Download PDF question stem")],
    )
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="Free Mock Test Download PDF question stem",
        raw_text="Free Mock Test Download PDF question stem",
        options=[
            SemanticBoundOption(
                key="A",
                key_raw="A",
                text_raw="1",
                source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
            ),
        ],
        source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
    )
    validate_semantic_items([item], [], evidence, answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY)
    assert "noise_in_question_text" in item.issues


def test_answer_key_only_accepts_missing_solution() -> None:
    evidence = _evidence_package(
        [
            _line("marker_l_000001", "**1.** Q"),
            _line("marker_l_000002", "- **A** 1"),
            _line("marker_l_000003", "1.A"),
        ],
    )
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="Q",
        raw_text="**1.** Q",
        options=[
            SemanticBoundOption(
                key="A",
                key_raw="A",
                text_raw="1",
                source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000002")],
            ),
        ],
        answer=SemanticBoundAnswer(
            available=True,
            key="A",
            key_raw="A",
            answer_text_raw="A",
            source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000003")],
        ),
        solution=SemanticBoundSolution(available=False),
        source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
    )
    validate_semantic_items([item], [], evidence, answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY)
    assert item.binding_status == SemanticBindingItemStatus.ACCEPTED


def test_required_mode_flags_missing_solution() -> None:
    evidence = _evidence_package([_line("marker_l_000001", "**1.** Q")])
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="Q",
        raw_text="**1.** Q",
        options=[
            SemanticBoundOption(
                key="A",
                key_raw="A",
                text_raw="1",
                source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
            ),
        ],
        answer=SemanticBoundAnswer(available=True, key="A", source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")]),
        solution=SemanticBoundSolution(available=False),
        source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
    )
    validate_semantic_items([item], [], evidence, answer_mode=SemanticBinderAnswerMode.REQUIRED)
    assert "missing_solution_required_mode" in item.issues


def test_question_only_accepts_missing_answer() -> None:
    evidence = _evidence_package(
        [
            _line("marker_l_000001", "**1.** Q"),
            _line("marker_l_000002", "- **A** 1"),
        ],
    )
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="Q",
        raw_text="**1.** Q",
        options=[
            SemanticBoundOption(
                key="A",
                key_raw="A",
                text_raw="1",
                source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000002")],
            ),
        ],
        answer=SemanticBoundAnswer(available=False),
        source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
    )
    validate_semantic_items([item], [], evidence, answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY)
    assert item.binding_status == SemanticBindingItemStatus.ACCEPTED


def test_answer_not_in_options_review_required() -> None:
    evidence = _evidence_package(
        [
            _line("marker_l_000001", "**1.** Q"),
            _line("marker_l_000002", "- **A** 1"),
            _line("marker_l_000003", "Ans: B"),
        ],
    )
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="Q",
        raw_text="**1.** Q",
        options=[
            SemanticBoundOption(
                key="A",
                key_raw="A",
                text_raw="1",
                source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000002")],
            ),
        ],
        answer=SemanticBoundAnswer(
            available=True,
            key="B",
            answer_text_raw="B",
            source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000003")],
        ),
        source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
    )
    report = validate_semantic_items([item], [], evidence, answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY)
    assert "answer_key_not_in_options" in item.issues
    assert report.answer_key_not_in_options_count == 1


def test_duplicate_question_numbers_review_required() -> None:
    evidence = _evidence_package([_line("marker_l_000001", "**1.** Q1")])
    items = [
        SemanticBoundQuestion(
            semantic_question_id="sq_0001",
            question_number=1,
            question_text_raw="Q1",
            raw_text="**1.** Q1",
            options=[SemanticBoundOption(key="A", key_raw="A", text_raw="1", source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")])],
            source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
        ),
        SemanticBoundQuestion(
            semantic_question_id="sq_0002",
            question_number=1,
            question_text_raw="Different",
            raw_text="Different",
            options=[SemanticBoundOption(key="A", key_raw="A", text_raw="x", source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")])],
            source_spans=[SourceSpan(extractor="marker", line_id="marker_l_000001")],
        ),
    ]
    report = validate_semantic_items(items, [], evidence, answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY)
    assert report.duplicate_question_number_count >= 1
    assert any("duplicate_question_number" in item.issues for item in items)


def test_invalid_json_fails_safely(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    _write_evidence(package_dir, _evidence_package([_line("marker_l_000001", "**1.** Q")]))
    provider = MockLlmProvider(fail_on_call=LlmProviderError("invalid_json"))

    result = bind_semantically_package(
        package_dir,
        llm_provider=provider,
        force=True,
    )
    assert result.package.status == SemanticBindingStatus.FAILED
    assert result.package.errors


def test_cache_skips_second_run(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [
        _line("marker_l_000001", "**1.** Q"),
        _line("marker_l_000002", "- **A** 1"),
    ]
    _write_evidence(package_dir, _evidence_package(lines))
    provider = MockLlmProvider()

    first = bind_semantically_package(package_dir, llm_provider=provider, force=True)
    second = bind_semantically_package(package_dir, llm_provider=provider, force=False)

    assert first.from_cache is False
    assert second.from_cache is True
    assert len(provider.calls) == 1


def test_trigger_fires_on_poor_deterministic_quality(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    package_dir.mkdir(parents=True)
    (package_dir / "questions").mkdir(exist_ok=True)
    (package_dir / "questions" / "question-candidate-report.json").write_text(
        json.dumps({"total_candidates": 85, "valid_candidates": 0, "candidates_with_no_options": 83}),
        encoding="utf-8",
    )
    evidence = _evidence_package(
        [_line("marker_l_000001", "- **A** 1", [RoleHint.OPTION_LABEL_CANDIDATE])],
    )
    trigger = evaluate_binder_trigger(package_dir, evidence, force=False)
    assert trigger.should_run
    assert "valid_candidates_ratio_below_0_70" in trigger.reasons


def test_pipeline_semantic_binder_does_not_mutate_final(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    final_dir = package_dir / "final"
    final_dir.mkdir(parents=True)
    final_path = final_dir / FINAL_QUESTIONS_NAME
    original = '{"questions": [{"id": 1}]}'
    final_path.write_text(original, encoding="utf-8")

    lines = [
        _line("marker_l_000001", "**1.** Q"),
        _line("marker_l_000002", "- **A** 1"),
    ]
    _write_evidence(package_dir, _evidence_package(lines))

    bind_semantically_package(package_dir, llm_provider=MockLlmProvider(), force=True)

    assert final_path.read_text(encoding="utf-8") == original
    assert (package_dir / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_NAME).exists()


def test_missing_evidence_raises(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    package_dir.mkdir(parents=True)
    with pytest.raises(SemanticBindingError, match="Missing document evidence"):
        bind_semantically_package(package_dir, llm_provider=MockLlmProvider())
