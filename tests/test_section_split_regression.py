"""Regression tests for Part 14 section split sanity and window guards."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import EVIDENCE_DIR, EXTRACTION_PACKAGE_DIR
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
)
from meritranker_data_ingestion.schemas.final_questions_export import FinalQuestionsPackage
from meritranker_data_ingestion.services.evidence_answer_solution_mapper import (
    build_evidence_answer_solution_map,
)
from meritranker_data_ingestion.services.question_window_builder import build_question_windows
from meritranker_data_ingestion.services.section_split_sanity import evaluate_section_split_sanity
from meritranker_data_ingestion.services.share_log_builder import ShareLogContext, build_share_log
from meritranker_data_ingestion.services.solution_window_builder import build_solution_windows
from meritranker_data_ingestion.schemas.question_window import QuestionWindowsPackage
from meritranker_data_ingestion.schemas.solution_window import SolutionWindowsPackage
from meritranker_data_ingestion.schemas.evidence_answer_solution_map import AnswerSolutionMapPackage


def _line(line_id: str, text: str) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
    )


def _write_pkg(tmp_path: Path, lines: list[EvidenceLine]) -> Path:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    ev = pkg / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "document-evidence.json").write_text(
        DocumentEvidencePackage(
            package_version="1.0",
            source_file_name="100q.pdf",
            primary_extractor="marker",
            extractors_used=["marker"],
            extraction_status=EvidenceExtractionStatus.SUCCEEDED,
            lines=lines,
        ).model_dump_json(),
        encoding="utf-8",
    )
    return pkg


def _hundred_question_lines() -> list[EvidenceLine]:
    lines: list[EvidenceLine] = [
        _line("title", "# 100 Quantitative Aptitude Questions"),
    ]
    for i in range(1, 101):
        lines.extend(
            [
                _line(f"q{i}", f"- **{i}.** Question number {i} about ratios?"),
                _line(f"o{i}a", "(a) Alpha"),
                _line(f"o{i}b", "(b) Beta"),
                _line(f"o{i}c", "(c) Gamma"),
                _line(f"o{i}d", "(d) Delta"),
            ],
        )
    lines.append(_line("h1", "## Solutions"))
    for i in range(1, 101):
        lines.append(_line(f"s{i}", f"**{i}. (c):** Explanation for question {i}."))
    return lines


def test_hundred_question_doc_produces_near_hundred_question_windows(tmp_path: Path) -> None:
    pkg = _write_pkg(tmp_path, _hundred_question_lines())
    qw = build_question_windows(pkg, expected_count=100)
    sol = build_solution_windows(pkg, expected_count=100)
    assert qw.package.total_windows >= 70
    assert qw.package.total_windows <= 130
    assert sol.package.total_windows >= 70
    assert sol.package.total_windows <= 130


def test_solution_lines_after_explanations_heading(tmp_path: Path) -> None:
    pkg = _write_pkg(
        tmp_path,
        [
            _line("q1", "**1.** Question"),
            _line("h1", "Explanations"),
            _line("s1", "**1.** (c) Because"),
            _line("s2", "**2.** (d) Next"),
        ],
    )
    sol = build_solution_windows(pkg)
    assert sol.package.total_windows == 2
    assert sol.package.windows[0].answer_label == "C"


def test_option_numbers_not_solution_windows(tmp_path: Path) -> None:
    pkg = _write_pkg(
        tmp_path,
        [
            _line("h1", "Solutions"),
            _line("o1", "1. 3"),
            _line("o2", "2. 4"),
            _line("s1", "**1.** (a) Real solution"),
        ],
    )
    sol = build_solution_windows(pkg)
    assert sol.package.total_windows == 1


def test_formula_steps_not_solution_windows(tmp_path: Path) -> None:
    pkg = _write_pkg(
        tmp_path,
        [
            _line("h1", "Solutions"),
            _line("f1", "1. x = 3"),
            _line("f2", "2. y = 5"),
            _line("s1", "**1.** (b) Final answer"),
        ],
    )
    sol = build_solution_windows(pkg)
    assert sol.package.total_windows == 1


def test_over_detected_solution_windows_trigger_sanity_failure() -> None:
    sanity = evaluate_section_split_sanity(
        expected_count=100,
        question_windows=QuestionWindowsPackage(
            source_file_name="t.pdf",
            total_windows=14,
            question_window_build_status="failed",
        ),
        solution_windows=SolutionWindowsPackage(
            source_file_name="t.pdf",
            total_windows=226,
            solution_window_detection_status="over_detected",
            warnings=["solution_over_detection"],
        ),
        answer_map=AnswerSolutionMapPackage(
            source_file_name="t.pdf",
            total_mapped=226,
            map_usable=False,
            answer_solution_map_status="over_detected",
            warnings=["answer_solution_map_over_detected"],
        ),
    )
    assert not sanity.passed
    assert sanity.failure_reason in {
        "question_window_build_failed",
        "question_solution_split_failed",
        "solution_over_detection",
    }


def test_too_few_question_windows_triggers_fallback(tmp_path: Path) -> None:
    lines = [
        _line("q1", "- **1.** First question here?"),
        _line("o1", "(a) A"),
        _line("h1", "## Solutions"),
        _line("s1", "**1.** (c) Sol"),
    ]
    pkg = _write_pkg(tmp_path, lines)
    qw = build_question_windows(pkg, expected_count=100)
    assert qw.package.total_windows >= 1


def test_final_questions_empty_marks_failed_not_partial(tmp_path: Path) -> None:
    out = tmp_path / "paper"
    out.mkdir()
    questions = out / "paper.questions.json"
    questions.write_text(
        FinalQuestionsPackage(
            source_file_name="paper.pdf",
            total_questions_detected=0,
            accepted_safe_count=0,
            items=[],
        ).model_dump_json(),
        encoding="utf-8",
    )
    ev = out / EXTRACTION_PACKAGE_DIR / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "question-windows.json").write_text(
        json.dumps(
            {
                "total_windows": 14,
                "question_window_build_status": "ok",
                "section_split_status": "ok",
            },
        ),
        encoding="utf-8",
    )
    (ev / "solution-windows.json").write_text(
        json.dumps(
            {
                "total_windows": 226,
                "solution_window_detection_status": "over_detected",
            },
        ),
        encoding="utf-8",
    )
    (ev / "answer-solution-map.json").write_text(
        json.dumps(
            {
                "total_mapped": 226,
                "map_usable": False,
                "answer_solution_map_status": "over_detected",
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
        questions_json_path=questions,
        pipeline_result=MagicMock(expected_count=100),
    )
    result = build_share_log(ctx)
    assert result.quality_verdict == "failed"
    assert result.main_failure_reason == "final_questions_empty"
    assert result.run_status == "failed"


def test_batch_runner_passes_expected_count() -> None:
    from meritranker_data_ingestion.services.batch_pdf_runner import BatchPdfRunnerOptions

    opts = BatchPdfRunnerOptions(
        input_dir=Path("input_pdfs"),
        output_dir=Path("batch_outputs"),
        expected_count=100,
    )
    assert opts.expected_count == 100


def test_share_log_section_split_pipeline_error(tmp_path: Path) -> None:
    out = tmp_path / "paper"
    out.mkdir()
    ev = out / EXTRACTION_PACKAGE_DIR / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "question-windows.json").write_text(
        json.dumps({"total_windows": 14, "question_window_build_status": "failed"}),
        encoding="utf-8",
    )
    (ev / "solution-windows.json").write_text(
        json.dumps({"total_windows": 226, "solution_window_detection_status": "over_detected"}),
        encoding="utf-8",
    )
    ctx = ShareLogContext(
        pdf_file_name="paper.pdf",
        input_path=tmp_path / "paper.pdf",
        output_folder=out,
        started_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
        pipeline_error="Section split sanity failed: question_window_build_failed",
        pipeline_result=MagicMock(expected_count=100),
    )
    result = build_share_log(ctx)
    assert result.quality_verdict == "failed"
    assert result.main_failure_reason == "question_window_build_failed"


def test_answer_map_caps_over_detection(tmp_path: Path) -> None:
    lines = [_line("h1", "Solutions")]
    for i in range(1, 150):
        lines.append(_line(f"s{i}", f"**{i}. (a):** Solution {i}"))
    pkg = _write_pkg(tmp_path, lines)
    build_solution_windows(pkg, expected_count=100)
    mp = build_evidence_answer_solution_map(pkg, expected_count=100)
    assert mp.package.total_mapped <= 149
    assert not mp.package.map_usable
    assert "answer_solution_map_over_detected" in mp.package.warnings
