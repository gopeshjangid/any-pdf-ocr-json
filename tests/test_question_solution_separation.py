"""Tests for question/solution section separation and answer-solution mapping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.config import (
    EVIDENCE_DIR,
    EXTRACTION_PACKAGE_DIR,
    FINAL_QUESTIONS_DIR,
    FINAL_QUESTIONS_JSON_NAME,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
)
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionQualityStatus,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingPackage,
    SemanticBindingStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
    SemanticBoundSolution,
)
from meritranker_data_ingestion.services.document_section_splitter import split_document_sections
from meritranker_data_ingestion.services.evidence_answer_solution_mapper import (
    build_evidence_answer_solution_map,
)
from meritranker_data_ingestion.services.final_questions_export_builder import build_final_questions_export
from meritranker_data_ingestion.services.question_window_builder import build_question_windows
from meritranker_data_ingestion.services.share_log_builder import ShareLogContext, build_share_log
from meritranker_data_ingestion.services.solution_window_builder import build_solution_windows
from datetime import datetime, timezone


def test_detect_explanations_heading() -> None:
    lines = [
        _line("l1", "1. Question text"),
        _line("l2", "### **explanatIons**"),
        _line("l3", "**1.** (*c*) Let x be"),
    ]
    split = split_document_sections(lines)
    assert split.solution_section_detected is True
    assert len(split.question_lines) == 1
    assert len(split.solution_lines) == 1


def test_question_windows_ignore_solution_section(tmp_path: Path) -> None:
    pkg = _write_evidence_package(
        tmp_path,
        [
            _line("q1", "**1.** What is 2+2?"),
            _line("o1", "1. 3"),
            _line("o2", "2. 4"),
            _line("o3", "3. 5"),
            _line("o4", "4. 6"),
            _line("h1", "### explanations"),
            _line("s1", "**1.** (*b*) Because 2+2=4"),
            _line("s2", "**2.** (*a*) Next solution"),
        ],
        expected_count=1,
    )
    result = build_question_windows(pkg, expected_count=1)
    assert result.package.total_windows == 1
    assert result.package.solution_section_detected is True
    assert result.package.question_solution_section_mixed is False


def test_solution_windows_parse_numbered_answer(tmp_path: Path) -> None:
    pkg = _write_evidence_package(
        tmp_path,
        [
            _line("q1", "**1.** Question"),
            _line("h1", "Explanations"),
            _line("s1", "**1.** (*c*) Let the present age"),
            _line("s2", "More solution text"),
            _line("s3", "**2.** (*d*) Next answer"),
        ],
    )
    result = build_solution_windows(pkg)
    assert result.package.total_windows == 2
    first = result.package.windows[0]
    assert first.source_question_number == 1
    assert first.answer_label == "C"
    assert "Let the present age" in first.solution_text_raw
    second = result.package.windows[1]
    assert second.source_question_number == 2
    assert second.answer_label == "D"


def test_solution_text_stops_at_next_numbered_solution(tmp_path: Path) -> None:
    pkg = _write_evidence_package(
        tmp_path,
        [
            _line("h1", "Solutions"),
            _line("s1", "**1.** (a) First only"),
            _line("s2", "detail one"),
            _line("s3", "**2.** (b) Second"),
        ],
    )
    result = build_solution_windows(pkg)
    assert "detail one" in result.package.windows[0].solution_text_raw
    assert "Second" not in result.package.windows[0].solution_text_raw


def test_answer_solution_map_maps_question_one_to_c(tmp_path: Path) -> None:
    pkg = _write_evidence_package(
        tmp_path,
        [
            _line("h1", "Explanations"),
            _line("s1", "**1.** (*c*) Answer text"),
        ],
    )
    build_solution_windows(pkg)
    result = build_evidence_answer_solution_map(pkg)
    assert result.package.total_mapped == 1
    assert result.package.entries[0].question_number == 1
    assert result.package.entries[0].answer_label == "C"


def test_final_export_fills_answer_from_solution_map(tmp_path: Path) -> None:
    pkg = _write_evidence_package(
        tmp_path,
        [
            _line("q1", "**1.** Pick one"),
            _line("o1", "(a) Alpha"),
            _line("o2", "(b) Beta"),
            _line("o3", "(c) Gamma"),
            _line("o4", "(d) Delta"),
            _line("h1", "Explanations"),
            _line("s1", "**1.** (*c*) Gamma is correct because"),
        ],
    )
    _write_binding(
        pkg,
        SemanticBoundQuestion(
            semantic_question_id="sq_0001",
            question_number=1,
            question_text_raw="Pick one",
            raw_text="Pick one",
            options=[
                SemanticBoundOption(key="a", key_raw="(a)", text_raw="Alpha"),
                SemanticBoundOption(key="b", key_raw="(b)", text_raw="Beta"),
                SemanticBoundOption(key="c", key_raw="(c)", text_raw="Gamma"),
                SemanticBoundOption(key="d", key_raw="(d)", text_raw="Delta"),
            ],
            answer=SemanticBoundAnswer(available=False),
            solution=SemanticBoundSolution(available=False),
            binding_status=SemanticBindingItemStatus.ACCEPTED,
        ),
    )
    build_solution_windows(pkg)
    build_evidence_answer_solution_map(pkg)
    export = build_final_questions_export(pkg, answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY)
    item = export.package.items[0]
    assert item.correct_answer_key == "C"
    assert item.correct_answer_text == "Gamma"
    assert item.solution_text_raw is not None
    assert "Gamma is correct" in item.solution_text_raw
    assert item.quality_status != FinalQuestionQualityStatus.ANSWER_UNAVAILABLE


def test_question_window_count_not_double_solution_section(tmp_path: Path) -> None:
    lines = []
    for i in range(1, 4):
        lines.extend(
            [
                _line(f"q{i}", f"**{i}.** Question {i}?"),
                _line(f"o{i}a", "1. A"),
                _line(f"o{i}b", "2. B"),
                _line(f"o{i}c", "3. C"),
                _line(f"o{i}d", "4. D"),
            ],
        )
    lines.append(_line("h1", "### Explanations"))
    for i in range(1, 4):
        lines.append(_line(f"s{i}", f"**{i}.** (*c*) Solution {i}"))
    pkg = _write_evidence_package(tmp_path, lines, expected_count=3)
    qw = build_question_windows(pkg, expected_count=3)
    sol = build_solution_windows(pkg)
    assert qw.package.total_windows == 3
    assert sol.package.total_windows == 3


def test_share_log_main_failure_not_none_for_poor_quality(tmp_path: Path) -> None:
    out = tmp_path / "paper"
    out.mkdir()
    questions = out / "paper.questions.json"
    questions.write_text(
        FinalQuestionsPackage(
            source_file_name="paper.pdf",
            total_questions_detected=100,
            accepted_safe_count=0,
            answer_unavailable_count=88,
            items=[],
        ).model_dump_json(),
        encoding="utf-8",
    )
    ev = out / EXTRACTION_PACKAGE_DIR / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "question-windows.json").write_text(
        json.dumps({"total_windows": 222, "question_solution_section_mixed": True}),
        encoding="utf-8",
    )
    (ev / "solution-windows.json").write_text(
        json.dumps({"total_windows": 100}),
        encoding="utf-8",
    )
    (ev / "answer-solution-map.json").write_text(
        json.dumps({"total_mapped": 5}),
        encoding="utf-8",
    )
    ctx = ShareLogContext(
        pdf_file_name="paper.pdf",
        input_path=tmp_path / "paper.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
        questions_json_path=questions,
    )
    result = build_share_log(ctx)
    assert result.main_failure_reason == "question_solution_section_mixed"
    assert "question_solution_section_mixed" in result.share_log_path.read_text(encoding="utf-8")


def test_final_gate_report_path_points_to_evaluation_artifact(tmp_path: Path) -> None:
    out = tmp_path / "paper"
    out.mkdir()
    pkg = out / EXTRACTION_PACKAGE_DIR
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True, exist_ok=True)
    eval_path = sem / "semantic-binding-evaluation.repaired.json"
    eval_path.write_text("{}", encoding="utf-8")
    ctx = ShareLogContext(
        pdf_file_name="paper.pdf",
        input_path=tmp_path / "paper.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
    )
    result = build_share_log(ctx)
    text = result.share_log_path.read_text(encoding="utf-8")
    assert "semantic-binding-evaluation.repaired.json" in text
    assert "(exists)" in text


def _line(line_id: str, text: str) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
    )


def _write_evidence_package(
    tmp_path: Path,
    lines: list[EvidenceLine],
    *,
    expected_count: int | None = None,
) -> Path:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    ev = pkg / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "document-evidence.json").write_text(
        DocumentEvidencePackage(
            package_version="1.0",
            source_file_name="t.pdf",
            primary_extractor="marker",
            extractors_used=["marker"],
            extraction_status=EvidenceExtractionStatus.SUCCEEDED,
            lines=lines,
        ).model_dump_json(),
        encoding="utf-8",
    )
    return pkg


def _write_binding(pkg: Path, *items: SemanticBoundQuestion) -> None:
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True, exist_ok=True)
    binding = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="t.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        status=SemanticBindingStatus.SUCCEEDED,
        input_evidence_hash="hash",
        items=list(items),
    )
    (sem / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME).write_text(
        binding.model_dump_json(),
        encoding="utf-8",
    )
