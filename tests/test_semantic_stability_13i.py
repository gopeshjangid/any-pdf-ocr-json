"""Tests for Part 13I semantic stability guard and chunk diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    EXTRACTION_PACKAGE_DIR,
    SEMANTIC_BINDING_BAD_ITEMS_JSON_NAME,
    SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME,
    SEMANTIC_BINDING_CHUNKS_DIR,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME,
    SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
    SEMANTIC_FINAL_DIR,
    SEMANTIC_FINAL_REPORT_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import SourceSpan
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
    SemanticBindingPackage,
)
from meritranker_data_ingestion.schemas.semantic_final_export import SemanticFinalExportMode
from meritranker_data_ingestion.services.semantic_bad_item_guard import (
    apply_semantic_bad_item_guard,
    classify_bad_item,
)
from meritranker_data_ingestion.services.semantic_chunk_diagnostics_builder import (
    analyze_chunk_items,
    build_chunk_diagnostics_package,
)
from meritranker_data_ingestion.services.semantic_chunk_replay import replay_semantic_chunks
from meritranker_data_ingestion.services.semantic_final_export_builder import build_semantic_final_export
from meritranker_data_ingestion.services.semantic_remaining_issue_diagnostician import (
    diagnose_semantic_remaining_issues,
)


def _bound(
    qnum: int | None,
    status: SemanticBindingItemStatus,
    *,
    semantic_id: str | None = None,
    issues: list[str] | None = None,
    text: str = "Question text",
    chunk_id: str | None = None,
    gate_safe: bool = False,
) -> SemanticBoundQuestion:
    sid = semantic_id or (f"sq_{qnum:04d}" if qnum is not None else "sq_0101")
    span = [SourceSpan(extractor="marker", line_id="l1")]
    opts: list[SemanticBoundOption] = []
    answer_spans: list[SourceSpan] = []
    question_spans: list[SourceSpan] = []
    if gate_safe and qnum is not None:
        opts = [
            SemanticBoundOption(key=k, key_raw=k, text_raw=f"opt {k}", source_spans=span)
            for k in ("A", "B", "C", "D")
        ]
        answer_spans = span
        question_spans = span
    return SemanticBoundQuestion(
        semantic_question_id=sid,
        question_number=qnum,
        question_text_raw=text,
        raw_text=text,
        options=opts,
        answer=SemanticBoundAnswer(
            available=True,
            key="A",
            key_raw="A",
            source_spans=answer_spans,
        ),
        source_spans=question_spans,
        binding_status=status,
        issues=list(issues or []),
        chunk_id=chunk_id,
    )


def _write_binding_pkg(tmp_path: Path, items: list[SemanticBoundQuestion]) -> Path:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True, exist_ok=True)
    package = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="exam.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        input_evidence_hash="abc",
        status=SemanticBindingStatus.SUCCEEDED,
        items=items,
    )
    (sem / SEMANTIC_BOUND_QUESTIONS_NAME).write_text(
        package.model_dump_json(indent=2),
        encoding="utf-8",
    )
    evidence_dir = pkg / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.joinpath("document-evidence.json").write_text(
        json.dumps(
            {
                "package_version": "1.0",
                "source_file_name": "exam.pdf",
                "primary_extractor": "marker",
                "extractors_available": ["marker"],
                "extractors_used": ["marker"],
                "extraction_status": "succeeded",
                "lines": [
                    {
                        "line_id": "l1",
                        "text_raw": "**1.** Question text",
                        "normalized_preview": "**1.** Question text",
                        "source_extractor": "marker",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    return pkg


def _write_eval(pkg: Path, **overrides) -> None:
    payload = {
        "expected_count": 100,
        "semantic_item_count": 2,
        "accepted_count": 1,
        "review_required_count": 0,
        "rejected_count": 1,
        "hallucination_suspected_count": 1,
        "source_span_missing_count": 0,
        "answer_key_not_in_options_count": 0,
        "quality_status": "failed",
        **overrides,
    }
    (pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    repaired = SemanticBindingPackage.model_validate_json(
        (pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_NAME).read_text(),
    )
    (pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME).write_text(
        repaired.model_dump_json(indent=2),
        encoding="utf-8",
    )


def test_chunk_diagnostics_generated(tmp_path: Path) -> None:
    items = [_bound(1, SemanticBindingItemStatus.ACCEPTED, chunk_id="chunk-000")]
    diag = analyze_chunk_items(
        items,
        chunk_id="chunk-000",
        chunk_index=0,
        line_start_id="l1",
        line_end_id="l2",
        evidence_line_count=2,
        expected_count=100,
    )
    package = build_chunk_diagnostics_package(
        [diag],
        source_file_name="exam.pdf",
        provider="mock",
        model="mock",
        expected_count=100,
        merged_items=items,
    )
    assert package.total_chunks == 1
    assert package.chunks[0].chunk_id == "chunk-000"


def test_non_numeric_sq_0101_quarantined(tmp_path: Path) -> None:
    pkg = _write_binding_pkg(
        tmp_path,
        [
            _bound(1, SemanticBindingItemStatus.ACCEPTED),
            _bound(None, SemanticBindingItemStatus.ACCEPTED, semantic_id="sq_0101", text=""),
        ],
    )
    result = apply_semantic_bad_item_guard(pkg, expected_count=100)
    assert result.quarantined_count >= 1
    bad = json.loads((pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BINDING_BAD_ITEMS_JSON_NAME).read_text())
    assert any(i["semantic_question_id"] == "sq_0101" for i in bad["items"])
    classes = classify_bad_item(
        _bound(None, SemanticBindingItemStatus.ACCEPTED, semantic_id="sq_0101", text=""),
        expected_count=100,
        overflow_ids={"sq_0101"},
    )
    assert "non_numeric_question_number" in classes


def test_out_of_range_quarantined(tmp_path: Path) -> None:
    classes = classify_bad_item(
        _bound(101, SemanticBindingItemStatus.ACCEPTED),
        expected_count=100,
        overflow_ids=set(),
    )
    assert "question_number_out_of_range" in classes


def test_hallucinated_item_classified(tmp_path: Path) -> None:
    classes = classify_bad_item(
        _bound(95, SemanticBindingItemStatus.REJECTED, issues=["hallucinated_question_text"]),
        expected_count=100,
        overflow_ids=set(),
    )
    assert "hallucinated_question_text" in classes


def test_expected_count_overflow_detected(tmp_path: Path) -> None:
    pkg = _write_binding_pkg(
        tmp_path,
        [_bound(i, SemanticBindingItemStatus.ACCEPTED) for i in range(1, 4)]
        + [_bound(None, SemanticBindingItemStatus.ACCEPTED, semantic_id="sq_0101")],
    )
    result = apply_semantic_bad_item_guard(pkg, expected_count=3)
    assert result.report.bad_item_count >= 1
    assert any("expected_count_mismatch" in w for w in result.report.warnings)


def test_duplicate_question_classified() -> None:
    classes = classify_bad_item(
        _bound(5, SemanticBindingItemStatus.REVIEW_REQUIRED, issues=["duplicate_question_number"]),
        expected_count=100,
        overflow_ids=set(),
    )
    assert "duplicate_question_number" in classes


def test_final_export_excludes_quarantined(tmp_path: Path) -> None:
    extra = _bound(None, SemanticBindingItemStatus.REJECTED, semantic_id="sq_0101")
    extra.quarantine_status = "quarantined"
    extra.excluded_from_export = True
    extra.bad_item_classes = ["non_numeric_question_number"]
    pkg = _write_binding_pkg(
        tmp_path,
        [_bound(1, SemanticBindingItemStatus.ACCEPTED, gate_safe=True), extra],
    )
    _write_eval(pkg, semantic_item_count=2, accepted_count=1, rejected_count=1)
    result = build_semantic_final_export(pkg, export_mode=SemanticFinalExportMode.ACCEPTED_ONLY)
    assert result.package.exported_count == 1
    assert result.package.accepted_safe_count == 1
    assert result.package.quarantined_item_count >= 1
    report = json.loads((pkg / SEMANTIC_FINAL_DIR / SEMANTIC_FINAL_REPORT_NAME).read_text())
    assert report["quarantined_item_count"] >= 1


def test_replay_plan_dry_run_no_provider(tmp_path: Path) -> None:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR / SEMANTIC_BINDING_DIR
    pkg.mkdir(parents=True)
    diag = {
        "source_file_name": "exam.pdf",
        "total_chunks": 1,
        "chunks": [
            {
                "chunk_id": "chunk-000",
                "chunk_index": 0,
                "returned_item_count": 1,
                "non_numeric_question_items": ["sq_0101"],
                "status": "warning",
            },
        ],
        "aggregate": {
            "chunks_requiring_replay": ["chunk-000"],
        },
    }
    (pkg / SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME).write_text(
        json.dumps(diag),
        encoding="utf-8",
    )
    with patch(
        "meritranker_data_ingestion.services.llm_provider.resolve_llm_provider",
        create=True,
    ) as mock_provider:
        result = replay_semantic_chunks(
            tmp_path / EXTRACTION_PACKAGE_DIR,
            only_suspicious=True,
            execute=False,
        )
        mock_provider.assert_not_called()
    assert result.plan.dry_run is True
    assert result.plan.suspicious_chunk_count >= 1


def test_remaining_issues_includes_bad_classes_and_chunk_id(tmp_path: Path) -> None:
    item = _bound(2, SemanticBindingItemStatus.REVIEW_REQUIRED, chunk_id="chunk-001")
    item.bad_item_classes = ["missing_source_anchor"]
    pkg = _write_binding_pkg(tmp_path, [_bound(1, SemanticBindingItemStatus.ACCEPTED), item])
    _write_eval(pkg)
    (pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME).write_text(
        (pkg / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_NAME).read_text(),
        encoding="utf-8",
    )
    result = diagnose_semantic_remaining_issues(pkg, use_repaired=True)
    payload = json.loads(result.json_path.read_text())
    assert payload["items"][0]["chunk_id"] == "chunk-001"
    assert "missing_source_anchor" in payload["items"][0]["bad_item_classes"]


def test_no_prompt_saved_without_debug_flag(tmp_path: Path) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    assert not (chunks_dir / "raw").exists()
