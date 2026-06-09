"""Build Part 14T batch-summary.md with per-PDF stabilization metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.config import EXTRACTION_PACKAGE_DIR
from meritranker_data_ingestion.services.layout_type_classifier import classify_layout_type
from meritranker_data_ingestion.services.public_questions_audit import audit_public_questions_json
from meritranker_data_ingestion.services.share_log_builder import (
    ShareLogContext,
    _collect_metrics,
    _load_internal_final_questions,
    _quality_verdict,
    build_share_log,
    default_questions_export_path,
    internal_final_questions_path,
)

BATCH_SUMMARY_MD_NAME = "batch-summary.md"

PART_14T_TABLE_HEADER = (
    "| PDF | layout_type_detected | expected_count | public_detected | ready | review | "
    "visual_required | blocked | ready_percentage | answer_available | solution_available | "
    "incomplete_options | missing_question_count | raw_candidate_count | extra_candidate_count | "
    "duplicate_candidate_count | question_window_count | solution_window_count | "
    "answer_solution_map_count | main_issue | public_json_audit | questions_json |"
)


@dataclass
class BatchPdfMetricsRow:
    pdf_name: str
    pdf_stem: str
    output_folder: Path
    run_status: str = "unknown"
    questions_json_path: Path | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    main_issue: str | None = None
    layout_type_detected: str = "unknown"
    public_json_audit: str = "missing"
    ready_percentage: float = 0.0


def collect_batch_pdf_metrics(
    output_folder: Path,
    *,
    pdf_file_name: str | None = None,
    expected_count: int | None = None,
) -> BatchPdfMetricsRow:
    """Collect Part 14T metrics for one PDF output folder."""
    stem = output_folder.name
    pdf_name = pdf_file_name or f"{stem}.pdf"
    package_dir = output_folder / EXTRACTION_PACKAGE_DIR
    questions_path = default_questions_export_path(output_folder, stem)
    fq = _load_internal_final_questions(output_folder)

    ctx = ShareLogContext(
        pdf_file_name=pdf_name,
        input_path=output_folder.parent.parent / "input_pdfs" / pdf_name,
        output_folder=output_folder,
        started_at=datetime.now(timezone.utc),
        duration_seconds=0.0,
        questions_json_path=questions_path if questions_path.exists() else None,
    )
    metrics = _collect_metrics(ctx, fq, package_dir)
    if expected_count is not None:
        metrics["expected_count"] = expected_count

    verdict, main_issue, _ = _quality_verdict(metrics, fq, None, package_dir=package_dir)
    layout = classify_layout_type(metrics, package_dir=package_dir, main_issue=main_issue)
    audit = _audit_label(questions_path, metrics.get("expected_count"))

    expected = int(metrics.get("expected_count") or 0)
    ready = int(metrics.get("ready_count") or 0)
    ready_pct = round((ready / expected) * 100, 1) if expected else 0.0

    return BatchPdfMetricsRow(
        pdf_name=pdf_name,
        pdf_stem=stem,
        output_folder=output_folder,
        run_status=verdict.lower() if verdict else "unknown",
        questions_json_path=questions_path if questions_path.exists() else None,
        metrics=metrics,
        main_issue=main_issue,
        layout_type_detected=layout,
        public_json_audit=audit,
        ready_percentage=ready_pct,
    )


def refresh_batch_summary_from_outputs(
    batch_output_dir: Path,
    *,
    expected_count: int | None = None,
    subtitle: str | None = None,
) -> Path:
    """Rebuild batch-summary.md by scanning existing per-PDF output folders."""
    rows: list[BatchPdfMetricsRow] = []
    for child in sorted(batch_output_dir.iterdir()):
        if not child.is_dir():
            continue
        internal = internal_final_questions_path(child)
        questions = list(child.glob("*.questions.json"))
        if not internal.exists() and not questions:
            continue
        rows.append(collect_batch_pdf_metrics(child, expected_count=expected_count))
    return write_batch_summary(batch_output_dir, rows, subtitle=subtitle)


def write_batch_summary(
    output_dir: Path,
    rows: list[BatchPdfMetricsRow],
    *,
    subtitle: str | None = None,
) -> Path:
    """Write Part 14T batch summary markdown."""
    path = output_dir / BATCH_SUMMARY_MD_NAME
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "# Batch Summary",
        "",
        subtitle or f"Part 14T multi-layout stabilization — {stamp}.",
        "",
        PART_14T_TABLE_HEADER,
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        m = row.metrics
        qjson = str(row.questions_json_path) if row.questions_json_path else "missing"
        lines.append(
            "| {pdf} | {layout} | {expected} | {detected} | {ready} | {review} | {visual} | "
            "{blocked} | {ready_pct}% | {answers} | {solutions} | {incomplete} | {missing} | "
            "{raw} | {extra} | {dup} | {qw} | {sol} | {map} | {issue} | {audit} | {qjson} |".format(
                pdf=row.pdf_name,
                layout=row.layout_type_detected,
                expected=m.get("expected_count", 0),
                detected=m.get("total_questions_detected", 0),
                ready=m.get("ready_count", 0),
                review=m.get("review_count", 0),
                visual=m.get("visual_required_count", 0),
                blocked=m.get("blocked_count", 0),
                ready_pct=row.ready_percentage,
                answers=m.get("answer_available_count", 0),
                solutions=m.get("solution_available_count", 0),
                incomplete=m.get("incomplete_options_count", 0),
                missing=m.get("missing_question_count", 0),
                raw=m.get("raw_candidate_count", 0),
                extra=m.get("extra_candidate_count", 0),
                dup=m.get("duplicate_candidate_count", 0),
                qw=m.get("question_window_count", 0),
                sol=m.get("solution_window_count", 0),
                map=m.get("answer_solution_map_count", 0),
                issue=row.main_issue or "none",
                audit=row.public_json_audit,
                qjson=qjson,
            ),
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def refresh_share_logs_in_batch(batch_output_dir: Path) -> list[Path]:
    """Rebuild share logs for all PDF output folders with final artifacts."""
    refreshed: list[Path] = []
    for child in sorted(batch_output_dir.iterdir()):
        if not child.is_dir():
            continue
        if not internal_final_questions_path(child).exists():
            continue
        stem = child.name
        questions_path = default_questions_export_path(child, stem)
        ctx = ShareLogContext(
            pdf_file_name=f"{stem}.pdf",
            input_path=child,
            output_folder=child,
            started_at=datetime.now(timezone.utc),
            duration_seconds=0.0,
            questions_json_path=questions_path if questions_path.exists() else None,
        )
        result = build_share_log(ctx)
        refreshed.append(result.share_log_path)
    return refreshed


def _audit_label(questions_path: Path | None, expected_count: object) -> str:
    if questions_path is None or not questions_path.exists():
        return "missing"
    expected = int(expected_count) if expected_count else None
    result = audit_public_questions_json(questions_path, expected_count=expected)
    return "PASS" if result.passed else "FAIL"
