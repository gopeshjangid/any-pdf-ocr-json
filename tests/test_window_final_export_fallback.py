"""Tests for Part 14K deterministic window-to-final export fallback."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import (
    EVIDENCE_DIR,
    EXTRACTION_PACKAGE_DIR,
    FINAL_QUESTIONS_DIR,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
)
from meritranker_data_ingestion.schemas.evidence_answer_solution_map import (
    AnswerSolutionMapEntry,
    AnswerSolutionMapPackage,
)
from meritranker_data_ingestion.schemas.final_questions_export import FinalAnswerSource
from meritranker_data_ingestion.schemas.question_window import QuestionWindow, QuestionWindowsPackage
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingPackage,
    SemanticBindingStatus,
)
from meritranker_data_ingestion.services.final_questions_export_builder import build_final_questions_export
from meritranker_data_ingestion.services.semantic_pipeline_runner import _binding_diagnostics_from_warnings
from meritranker_data_ingestion.services.share_log_builder import ShareLogContext, build_share_log
from meritranker_data_ingestion.services.window_final_question_builder import (
    build_window_final_questions,
    merge_semantic_and_window_exports,
)


def _line(line_id: str, text: str) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
    )


def _write_package(tmp_path: Path, lines: list[EvidenceLine], windows: list[QuestionWindow]) -> Path:
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
    (ev / "question-windows.json").write_text(
        QuestionWindowsPackage(
            source_file_name="t.pdf",
            total_windows=len(windows),
            windows=windows,
        ).model_dump_json(),
        encoding="utf-8",
    )
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True, exist_ok=True)
    (sem / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME).write_text(
        SemanticBindingPackage(
            package_version="1.0",
            source_file_name="t.pdf",
            binder_provider="mock",
            binder_model="mock",
            answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
            status=SemanticBindingStatus.WARNING,
            input_evidence_hash="hash",
            items=[],
            warnings=[
                "planned_chunk_count:5",
                "executed_chunk_count:0",
                "returned_item_count:0",
                "used_question_windows:True",
                "provider_called:False",
                "skipped_reason:provider_not_called",
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )
    (ev / "extraction-capability-profile.json").write_text(
        json.dumps(
            {
                "language": "english",
                "text_availability": "high",
                "option_availability": "high",
                "answer_source": "separate_solution_section",
                "recommended_answer_mode": "answer-key-only",
            },
        ),
        encoding="utf-8",
    )
    return pkg


def test_hundred_windows_zero_semantic_builds_hundred_questions(tmp_path: Path) -> None:
    lines: list[EvidenceLine] = []
    windows: list[QuestionWindow] = []
    for i in range(1, 101):
        lines.extend(
            [
                _line(f"q{i}", f"- **{i}.** Question {i} text here?"),
                _line(f"o{i}a", "(a) Alpha"),
                _line(f"o{i}b", "(b) Beta"),
                _line(f"o{i}c", "(c) Gamma"),
                _line(f"o{i}d", "(d) Delta"),
            ],
        )
        windows.append(
            QuestionWindow(
                window_id=f"qw_{i:04d}",
                parsed_question_number=i,
                global_order=i,
                start_line_id=f"q{i}",
                line_ids=[f"q{i}", f"o{i}a", f"o{i}b", f"o{i}c", f"o{i}d"],
                question_anchor_line_ids=[f"q{i}"],
                option_candidate_line_ids=[f"o{i}a", f"o{i}b", f"o{i}c", f"o{i}d"],
            ),
        )
    pkg = _write_package(tmp_path, lines, windows)
    result = build_final_questions_export(pkg)
    assert result.package.total_questions_detected == 100


def test_paren_options_produce_abcd(tmp_path: Path) -> None:
    lines = [
        _line("q1", "**1.** Pick"),
        _line("o1", "(a) Alpha (b) Beta (c) Gamma (d) Delta"),
    ]
    windows = [
        QuestionWindow(
            window_id="qw_0001",
            parsed_question_number=1,
            global_order=1,
            line_ids=["q1", "o1"],
            question_anchor_line_ids=["q1"],
            option_candidate_line_ids=["o1"],
        ),
    ]
    pkg = _write_package(tmp_path, lines, windows)
    evidence = DocumentEvidencePackage.model_validate_json(
        (pkg / EVIDENCE_DIR / "document-evidence.json").read_text(),
    )
    qw = QuestionWindowsPackage.model_validate_json(
        (pkg / EVIDENCE_DIR / "question-windows.json").read_text(),
    )
    built = build_window_final_questions(
        windows_pkg=qw,
        evidence=evidence,
        answer_by_qnum={},
    )
    keys = {opt.canonical_key for opt in built.items[0].options}
    assert keys == {"A", "B", "C", "D"}


def test_two_column_option_lines(tmp_path: Path) -> None:
    lines = [
        _line("q1", "**1.** Pick"),
        _line("o1", "| (a) | Alpha | (b) | Beta |"),
        _line("o2", "| (c) | Gamma | (d) | Delta |"),
    ]
    windows = [
        QuestionWindow(
            window_id="qw_0001",
            parsed_question_number=1,
            global_order=1,
            line_ids=["q1", "o1", "o2"],
            question_anchor_line_ids=["q1"],
            option_candidate_line_ids=["o1", "o2"],
        ),
    ]
    pkg = _write_package(tmp_path, lines, windows)
    evidence = DocumentEvidencePackage.model_validate_json(
        (pkg / EVIDENCE_DIR / "document-evidence.json").read_text(),
    )
    qw = QuestionWindowsPackage.model_validate_json(
        (pkg / EVIDENCE_DIR / "question-windows.json").read_text(),
    )
    built = build_window_final_questions(
        windows_pkg=qw,
        evidence=evidence,
        answer_by_qnum={},
    )
    assert len(built.items[0].options) == 4


def test_answer_solution_map_joins_answer_and_solution(tmp_path: Path) -> None:
    lines = [
        _line("q1", "**1.** Pick"),
        _line("oa", "(a) Alpha"),
        _line("ob", "(b) Beta"),
        _line("oc", "(c) Gamma"),
        _line("od", "(d) Delta"),
    ]
    windows = [
        QuestionWindow(
            window_id="qw_0001",
            parsed_question_number=1,
            global_order=1,
            line_ids=["q1", "oa", "ob", "oc", "od"],
            question_anchor_line_ids=["q1"],
            option_candidate_line_ids=["oa", "ob", "oc", "od"],
        ),
    ]
    pkg = _write_package(tmp_path, lines, windows)
    (pkg / EVIDENCE_DIR / "answer-solution-map.json").write_text(
        AnswerSolutionMapPackage(
            source_file_name="t.pdf",
            total_mapped=1,
            map_usable=True,
            entries=[
                AnswerSolutionMapEntry(
                    question_number=1,
                    answer_label="C",
                    solution_text="Gamma is correct",
                    line_ids=["s1"],
                ),
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )
    result = build_final_questions_export(pkg)
    item = result.package.items[0]
    assert item.correct_answer_key == "C"
    assert item.correct_answer_text == "Gamma"
    assert item.solution_text_raw == "Gamma is correct"
    assert item.answer_source == FinalAnswerSource.SEPARATE_SOLUTION_SECTION


def test_missing_answer_map_is_answer_unavailable(tmp_path: Path) -> None:
    lines = [
        _line("q1", "**1.** Pick"),
        _line("oa", "(a) Alpha"),
        _line("ob", "(b) Beta"),
        _line("oc", "(c) Gamma"),
        _line("od", "(d) Delta"),
    ]
    windows = [
        QuestionWindow(
            window_id="qw_0001",
            parsed_question_number=1,
            global_order=1,
            line_ids=["q1", "oa", "ob", "oc", "od"],
            question_anchor_line_ids=["q1"],
            option_candidate_line_ids=["oa", "ob", "oc", "od"],
        ),
    ]
    pkg = _write_package(tmp_path, lines, windows)
    (pkg / EVIDENCE_DIR / "answer-solution-map.json").write_text(
        AnswerSolutionMapPackage(
            source_file_name="t.pdf",
            total_mapped=0,
            map_usable=True,
            entries=[],
        ).model_dump_json(),
        encoding="utf-8",
    )
    result = build_final_questions_export(pkg)
    assert result.package.items[0].quality_status.value == "answer_unavailable"


def test_zero_semantic_triggers_deterministic_fallback() -> None:
    window_result = type(
        "R",
        (),
        {
            "items": [],
            "answers_mapped_count": 0,
            "solutions_mapped_count": 0,
            "deterministic_window_questions_built": 3,
            "warnings": [],
        },
    )()
    merge = merge_semantic_and_window_exports(
        semantic_items=[],
        window_result=window_result,  # type: ignore[arg-type]
        question_window_count=3,
    )
    assert "deterministic_window_export_fallback_used" in merge.warnings


def test_semantic_underbound_triggers_fill() -> None:
    from meritranker_data_ingestion.schemas.final_questions_export import (
        FinalQuestionItem,
        FinalQuestionQualityStatus,
    )

    semantic = [
        FinalQuestionItem(
            final_question_id="fq_1",
            global_order=1,
            question_number=1,
            question_text_raw="Q1",
            quality_status=FinalQuestionQualityStatus.ACCEPTED_SAFE,
        ),
    ]
    window_result = build_window_final_questions(
        windows_pkg=QuestionWindowsPackage(
            source_file_name="t.pdf",
            total_windows=2,
            windows=[
                QuestionWindow(
                    window_id="qw_0002",
                    parsed_question_number=2,
                    global_order=2,
                    line_ids=["q2"],
                    question_anchor_line_ids=["q2"],
                ),
            ],
        ),
        evidence=DocumentEvidencePackage(
            package_version="1.0",
            source_file_name="t.pdf",
            primary_extractor="marker",
            extractors_used=["marker"],
            extraction_status=EvidenceExtractionStatus.SUCCEEDED,
            lines=[_line("q2", "**2.** Second question here?")],
        ),
        answer_by_qnum={},
    )
    merge = merge_semantic_and_window_exports(
        semantic_items=semantic,
        window_result=window_result,
        question_window_count=10,
    )
    assert merge.semantic_underbound_window_fallback_used is True
    assert len(merge.items) == 2


def test_share_log_includes_deterministic_fallback_metrics(tmp_path: Path) -> None:
    out = tmp_path / "paper"
    out.mkdir()
    pkg = out / EXTRACTION_PACKAGE_DIR
    fq_dir = pkg / FINAL_QUESTIONS_DIR
    fq_dir.mkdir(parents=True)
    (fq_dir / "final-questions.json").write_text(
        json.dumps(
            {
                "source_file_name": "paper.pdf",
                "total_questions_detected": 100,
                "accepted_safe_count": 80,
                "items": [],
            },
        ),
        encoding="utf-8",
    )
    (fq_dir / "final-questions-report.json").write_text(
        json.dumps(
            {
                "deterministic_window_export_used": True,
                "deterministic_window_questions_built": 100,
                "answers_mapped_from_solution_count": 87,
                "solutions_mapped_count": 87,
                "semantic_underbound_window_fallback_used": False,
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
    assert result.metrics["deterministic_window_export_used"] is True
    assert result.metrics["deterministic_window_questions_built"] == 100
    assert result.metrics["answers_mapped_from_solution_count"] == 87


def test_semantic_binding_zero_items_diagnostics() -> None:
    diag = _binding_diagnostics_from_warnings(
        [
            "planned_chunk_count:12",
            "executed_chunk_count:0",
            "returned_item_count:0",
            "used_question_windows:True",
            "provider_called:False",
            "skipped_reason:provider_not_called",
        ],
    )
    assert diag["planned_chunk_count"] == 12
    assert diag["returned_item_count"] == 0
    assert diag["provider_called"] is False
    assert diag["skipped_reason"] == "provider_not_called"
