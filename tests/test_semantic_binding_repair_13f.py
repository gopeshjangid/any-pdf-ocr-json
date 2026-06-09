"""Tests for Part 13F semantic binding repair (key normalization + source-span resolver)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME,
    SEMANTIC_BINDING_REPAIR_REPORT_NAME,
    SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingPackage,
    SemanticBindingStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
)
from meritranker_data_ingestion.services.semantic_binding_repair import repair_semantic_binding_package
from meritranker_data_ingestion.services.semantic_binding_validator import validate_semantic_items
from meritranker_data_ingestion.services.semantic_binder import evaluate_semantic_binding_package
from meritranker_data_ingestion.services.semantic_key_normalizer import (
    normalize_answer_key,
    normalize_option_key,
)
from meritranker_data_ingestion.services.semantic_source_span_resolver import resolve_source_spans


def _line(line_id: str, text: str) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
        source_span=SourceSpan(extractor="marker", line_id=line_id),
        role_hints=[],
    )


def _evidence(lines: list[EvidenceLine]) -> DocumentEvidencePackage:
    return DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="exam.pdf",
        primary_extractor="marker",
        extractors_available=["marker"],
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=lines,
    )


def _package(items: list[SemanticBoundQuestion]) -> SemanticBindingPackage:
    return SemanticBindingPackage(
        package_version="1.0",
        source_file_name="exam.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        input_evidence_hash="abc",
        status=SemanticBindingStatus.SUCCEEDED,
        items=items,
    )


def _write_semantic(package_dir: Path, package: SemanticBindingPackage) -> None:
    out = package_dir / SEMANTIC_BINDING_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / SEMANTIC_BOUND_QUESTIONS_NAME).write_text(
        package.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _write_evidence(package_dir: Path, evidence: DocumentEvidencePackage) -> None:
    path = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(a)", "A"),
        ("a.", "A"),
        ("- **A**", "A"),
        ("Option D", "D"),
    ],
)
def test_option_key_normalization(raw: str, expected: str) -> None:
    canonical, _ = normalize_option_key(raw)
    assert canonical == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ans.(d)", "D"),
        ("d", "D"),
        ("(D)", "D"),
    ],
)
def test_answer_key_normalization(raw: str, expected: str) -> None:
    canonical, _ = normalize_answer_key(raw)
    assert canonical == expected


def test_validator_canonical_answer_vs_option_comparison() -> None:
    lines = [
        _line("l1", "**1.** What is 2+2?"),
        _line("l2", "- **A** 3"),
        _line("l3", "- **B** 4"),
        _line("l4", "- **C** 5"),
        _line("l5", "- **D** 6"),
        _line("l6", "1.A 2.B"),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** What is 2+2?",
        raw_text="**1.** What is 2+2?",
        source_spans=[SourceSpan(extractor="marker", line_id="l1")],
        options=[
            SemanticBoundOption(
                key="",
                key_raw="(a)",
                text_raw="3",
                source_spans=[SourceSpan(extractor="marker", line_id="l2")],
            ),
            SemanticBoundOption(
                key="b",
                key_raw="b",
                text_raw="4",
                source_spans=[SourceSpan(extractor="marker", line_id="l3")],
            ),
            SemanticBoundOption(
                key="C",
                key_raw="C",
                text_raw="5",
                source_spans=[SourceSpan(extractor="marker", line_id="l4")],
            ),
            SemanticBoundOption(
                key="D",
                key_raw="D",
                text_raw="6",
                source_spans=[SourceSpan(extractor="marker", line_id="l5")],
            ),
        ],
        answer=SemanticBoundAnswer(
            available=True,
            key="a",
            key_raw="(a)",
            source_spans=[SourceSpan(extractor="marker", line_id="l6")],
        ),
    )
    report = validate_semantic_items(
        [item],
        [],
        evidence,
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    )
    assert report.answer_key_not_in_options_count == 0


def test_option_source_span_resolver_bullet_bold() -> None:
    lines = [
        _line("q1", "**1.** Sample question text here."),
        _line("o1", "- **A** 50"),
        _line("o2", "- **B** 67"),
        _line("o3", "- **C** 52"),
        _line("o4", "- **D** 63"),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** Sample question text here.",
        raw_text="**1.** Sample question text here.",
        options=[
            SemanticBoundOption(key="", key_raw="", text_raw=""),
            SemanticBoundOption(key="", key_raw="", text_raw=""),
            SemanticBoundOption(key="", key_raw="", text_raw=""),
            SemanticBoundOption(key="", key_raw="", text_raw=""),
        ],
        answer=SemanticBoundAnswer(available=False),
    )
    package = _package([item])
    stats = resolve_source_spans(package, evidence)
    assert stats.options_filled_from_evidence_count == 4
    assert package.items[0].options[0].key == "A"
    assert package.items[0].options[0].text_raw == "50"
    assert package.items[0].options[0].source_spans[0].line_id == "o1"


def test_question_source_span_resolver_markdown_question() -> None:
    lines = [
        _line("q1", "**1.** Select the related option."),
        _line("q2", "19 : 34 :: 5 : 6"),
        _line("o1", "- **A** 50"),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** Select the related option.",
        raw_text="**1.** Select the related option.",
        options=[SemanticBoundOption(key="", key_raw="", text_raw="")],
        answer=SemanticBoundAnswer(available=False),
    )
    package = _package([item])
    stats = resolve_source_spans(package, evidence)
    assert stats.question_spans_resolved_count == 1
    assert package.items[0].source_spans[0].line_id == "q1"


def test_answer_source_span_resolver_compact_line() -> None:
    lines = [
        _line("q1", "**1.** Question one"),
        _line("o1", "- **A** x"),
        _line("o2", "- **B** y"),
        _line("o3", "- **C** z"),
        _line("o4", "- **D** w"),
        _line("ak", "1.A 2.B 3.C"),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** Question one",
        raw_text="**1.** Question one",
        options=[
            SemanticBoundOption(key="A", key_raw="A", text_raw="x", source_spans=[]),
            SemanticBoundOption(key="B", key_raw="B", text_raw="y", source_spans=[]),
            SemanticBoundOption(key="C", key_raw="C", text_raw="z", source_spans=[]),
            SemanticBoundOption(key="D", key_raw="D", text_raw="w", source_spans=[]),
        ],
        answer=SemanticBoundAnswer(available=True, key="A", key_raw="A", source_spans=[]),
    )
    package = _package([item])
    stats = resolve_source_spans(package, evidence)
    assert stats.answer_spans_resolved_count == 1
    assert package.items[0].answer.source_spans[0].line_id == "ak"


def test_unresolved_span_stays_unresolved() -> None:
    lines = [_line("x1", "Unrelated content only.")]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=8877,
        question_text_raw="**8877.** Unique missing phrase xyzzyplughall.",
        raw_text="**8877.** Unique missing phrase xyzzyplughall.",
        options=[
            SemanticBoundOption(key="A", key_raw="A", text_raw="ghost-option-xyzzy", source_spans=[]),
        ],
        answer=SemanticBoundAnswer(available=True, key="A", key_raw="A", source_spans=[]),
    )
    package = _package([item])
    stats = resolve_source_spans(package, evidence)
    assert stats.unresolved_question_spans_count == 1
    assert stats.unresolved_option_spans_count == 1
    assert stats.unresolved_answer_spans_count == 1
    assert not package.items[0].source_spans


def test_repair_command_writes_repaired_without_overwrite(tmp_path: Path) -> None:
    lines = [
        _line("q1", "**1.** Repair test question."),
        _line("o1", "- **A** one"),
        _line("o2", "- **B** two"),
        _line("o3", "- **C** three"),
        _line("o4", "- **D** four"),
        _line("ak", "1.A"),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** Repair test question.",
        raw_text="**1.** Repair test question.",
        options=[
            SemanticBoundOption(key="", key_raw="", text_raw=""),
            SemanticBoundOption(key="", key_raw="", text_raw=""),
            SemanticBoundOption(key="", key_raw="", text_raw=""),
            SemanticBoundOption(key="", key_raw="", text_raw=""),
        ],
        answer=SemanticBoundAnswer(available=True, key="A", key_raw="A", source_spans=[]),
    )
    package = _package([item])
    _write_evidence(tmp_path, evidence)
    _write_semantic(tmp_path, package)
    original_mtime = (tmp_path / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_NAME).stat().st_mtime

    result = repair_semantic_binding_package(
        tmp_path,
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        expected_count=1,
    )

    assert result.repaired_path.exists()
    assert result.repair_report_path.exists()
    assert result.validation_path.exists()
    assert result.evaluation_path.exists()
    assert (tmp_path / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME).exists()
    assert (tmp_path / SEMANTIC_BINDING_DIR / SEMANTIC_BINDING_REPAIR_REPORT_NAME).exists()
    assert (tmp_path / SEMANTIC_BINDING_DIR / SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME).exists()
    assert (tmp_path / SEMANTIC_BINDING_DIR / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME).exists()
    new_mtime = (tmp_path / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_NAME).stat().st_mtime
    assert new_mtime == original_mtime


def test_overwrite_semantic_binding_flag(tmp_path: Path) -> None:
    lines = [
        _line("q1", "**1.** Overwrite test."),
        _line("o1", "- **A** one"),
        _line("o2", "- **B** two"),
        _line("o3", "- **C** three"),
        _line("o4", "- **D** four"),
        _line("ak", "1.A"),
    ]
    evidence = _evidence(lines)
    item = SemanticBoundQuestion(
        semantic_question_id="sq_0001",
        question_number=1,
        question_text_raw="**1.** Overwrite test.",
        raw_text="**1.** Overwrite test.",
        options=[SemanticBoundOption(key="", key_raw="", text_raw="") for _ in range(4)],
        answer=SemanticBoundAnswer(available=True, key="A", key_raw="A", source_spans=[]),
    )
    _write_evidence(tmp_path, evidence)
    _write_semantic(tmp_path, _package([item]))

    repair_semantic_binding_package(
        tmp_path,
        overwrite_semantic_binding=True,
    )
    canonical = json.loads(
        (tmp_path / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_NAME).read_text(encoding="utf-8"),
    )
    assert canonical["items"][0]["options"][0]["key"] == "A"


def test_evaluate_use_repaired_reads_repaired_artifacts(tmp_path: Path) -> None:
    lines = [
        _line("q1", "**1.** Eval repaired."),
        _line("o1", "- **A** one"),
        _line("o2", "- **B** two"),
        _line("o3", "- **C** three"),
        _line("o4", "- **D** four"),
        _line("ak", "1.A"),
    ]
    _write_evidence(tmp_path, _evidence(lines))
    _write_semantic(
        tmp_path,
        _package(
            [
                SemanticBoundQuestion(
                    semantic_question_id="sq_0001",
                    question_number=1,
                    question_text_raw="**1.** Eval repaired.",
                    raw_text="**1.** Eval repaired.",
                    options=[SemanticBoundOption(key="", key_raw="", text_raw="") for _ in range(4)],
                    answer=SemanticBoundAnswer(available=True, key="A", key_raw="A", source_spans=[]),
                ),
            ],
        ),
    )
    repair_semantic_binding_package(tmp_path, expected_count=1)

    result = evaluate_semantic_binding_package(tmp_path, expected_count=1, use_repaired=True)
    assert result.output_path.name == SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME
    assert result.evaluation_path.name == SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME
    assert result.package.items[0].options[0].key == "A"


def test_no_provider_called_during_repair(tmp_path: Path) -> None:
    lines = [_line("q1", "**1.** No provider.")]
    _write_evidence(tmp_path, _evidence(lines))
    _write_semantic(
        tmp_path,
        _package(
            [
                SemanticBoundQuestion(
                    semantic_question_id="sq_0001",
                    question_number=1,
                    question_text_raw="**1.** No provider.",
                    raw_text="**1.** No provider.",
                    options=[],
                    answer=SemanticBoundAnswer(available=False),
                ),
            ],
        ),
    )
    with patch(
        "meritranker_data_ingestion.services.semantic_binding_repair.resolve_llm_provider",
        create=True,
    ) as mock_provider:
        repair_semantic_binding_package(tmp_path)
        mock_provider.assert_not_called()
