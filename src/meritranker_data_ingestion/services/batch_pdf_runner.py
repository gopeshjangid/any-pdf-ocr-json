"""Batch PDF folder runner with share logs (PDF → final questions JSON)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.schemas.semantic_binding import SemanticBinderAnswerMode
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.output_path_guard import OutputPathGuardError, clean_output_directory
from meritranker_data_ingestion.services.pdf_stem import safe_pdf_stem
from meritranker_data_ingestion.services.pipeline_stage_tracker import PipelineStageRecorder
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    SemanticPipelineError,
    SemanticPipelineOptions,
    SemanticPipelineResult,
    run_semantic_pipeline,
)
from meritranker_data_ingestion.services.final_questions_public_serializer import (
    write_public_questions_json,
)
from meritranker_data_ingestion.services.batch_summary_builder import (
    BatchPdfMetricsRow,
    write_batch_summary,
)
from meritranker_data_ingestion.services.share_log_builder import (
    ShareLogContext,
    build_share_log,
    default_questions_export_path,
    internal_final_questions_path,
)
from meritranker_data_ingestion.schemas.final_questions_export import FinalQuestionsPackage


BATCH_SUMMARY_MD_NAME = "batch-summary.md"
BATCH_RUN_LOG_JSONL_NAME = "batch-run.log.jsonl"


class BatchPdfRunnerError(Exception):
    """Raised when batch runner cannot start."""


@dataclass(frozen=True)
class BatchPdfRunnerOptions:
    input_dir: Path
    output_dir: Path
    answer_mode: str = "auto"
    extractor: str = "marker"
    ocr_engine: str = "auto"
    provider: str | None = None
    model: str | None = None
    timeout_seconds: int = 180
    expected_count: int | None = None
    continue_on_error: bool = True
    clean_output: bool = False
    max_files: int | None = None
    max_concurrency: int = 1
    allow_ocr_fallback: bool = False
    allow_unsupported_layout: bool = False


@dataclass
class BatchPdfItemResult:
    pdf_path: Path
    pdf_stem: str
    output_folder: Path
    run_status: str
    quality_verdict: str
    main_failure_reason: str | None
    questions_json_path: Path | None
    share_log_path: Path | None
    error_message: str | None = None
    metrics: dict = field(default_factory=dict)


@dataclass
class BatchPdfRunResult:
    output_dir: Path
    summary_path: Path
    log_path: Path
    items: list[BatchPdfItemResult]
    succeeded: int
    failed: int


def find_pdfs(input_dir: Path) -> list[Path]:
    """Return sorted PDF paths from input directory (non-recursive)."""
    resolved = resolve_path(input_dir)
    if not resolved.is_dir():
        raise BatchPdfRunnerError(f"Input directory does not exist: {resolved}")
    pdfs = sorted(resolved.glob("*.pdf")) + sorted(resolved.glob("*.PDF"))
    seen: set[str] = set()
    unique: list[Path] = []
    for pdf in pdfs:
        key = str(pdf.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(pdf)
    return unique


def run_pdf_folder(options: BatchPdfRunnerOptions) -> BatchPdfRunResult:
    """Process each PDF in input_dir sequentially."""
    if options.max_concurrency != 1:
        raise BatchPdfRunnerError("Only max_concurrency=1 is supported.")

    input_dir = resolve_path(options.input_dir)
    output_dir = resolve_path(options.output_dir)
    pdfs = find_pdfs(input_dir)
    if options.max_files is not None:
        pdfs = pdfs[: options.max_files]
    if not pdfs:
        raise BatchPdfRunnerError(f"No PDF files found in {input_dir}")

    if options.clean_output:
        try:
            clean_output_directory(output_dir)
        except OutputPathGuardError as exc:
            raise BatchPdfRunnerError(str(exc)) from exc
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / BATCH_RUN_LOG_JSONL_NAME
    if options.clean_output and log_path.exists():
        log_path.unlink()

    items: list[BatchPdfItemResult] = []
    for pdf_path in pdfs:
        item = _process_single_pdf(pdf_path, output_dir, options, log_path)
        items.append(item)
        if item.run_status == "failed" and not options.continue_on_error:
            break

    summary_path = _write_batch_summary(output_dir, items)
    succeeded = sum(1 for i in items if i.run_status == "passed")
    failed = sum(1 for i in items if i.run_status == "failed")
    return BatchPdfRunResult(
        output_dir=output_dir,
        summary_path=summary_path,
        log_path=log_path,
        items=items,
        succeeded=succeeded,
        failed=failed,
    )


def _process_single_pdf(
    pdf_path: Path,
    batch_output_dir: Path,
    options: BatchPdfRunnerOptions,
    log_path: Path,
) -> BatchPdfItemResult:
    stem = safe_pdf_stem(pdf_path.name)
    output_folder = batch_output_dir / stem
    questions_path = default_questions_export_path(output_folder, stem)
    share_log_path = output_folder / f"{stem}.share-log.md"
    started_at = datetime.now(timezone.utc)
    start_perf = datetime.now(timezone.utc)

    if output_folder.exists() and any(output_folder.iterdir()) and not options.clean_output:
        return _skip_existing(pdf_path, stem, output_folder, questions_path, share_log_path)

    output_folder.mkdir(parents=True, exist_ok=True)
    recorder = PipelineStageRecorder()
    pipeline_result: SemanticPipelineResult | None = None
    pipeline_error: str | None = None

    try:
        pipeline_result = run_semantic_pipeline(
            SemanticPipelineOptions(
                input_pdf=pdf_path,
                output_dir=output_folder,
                expected_count=options.expected_count,
                answer_mode=SemanticBinderAnswerMode(options.answer_mode),
                extractor=options.extractor,
                provider=options.provider,
                model=options.model,
                timeout_seconds=options.timeout_seconds,
                ocr_engine=options.ocr_engine,
                auto_profile=True,
                build_final_questions_export=True,
                allow_ocr_fallback=options.allow_ocr_fallback,
                allow_unsupported_layout=options.allow_unsupported_layout,
                stage_recorder=recorder,
            ),
        )
    except SemanticPipelineError as exc:
        pipeline_error = str(exc)

    _export_questions_json(output_folder, questions_path)
    duration = (datetime.now(timezone.utc) - start_perf).total_seconds()

    share_ctx = ShareLogContext(
        pdf_file_name=pdf_path.name,
        input_path=pdf_path.resolve(),
        output_folder=output_folder,
        started_at=started_at,
        duration_seconds=duration,
        pipeline_error=pipeline_error,
        pipeline_result=pipeline_result,
        questions_json_path=questions_path if questions_path.exists() else None,
        stage_events=recorder.events,
        provider=options.provider,
        model=options.model,
    )
    share_result = build_share_log(share_ctx)
    _append_jsonl_events(log_path, pdf_path.name, recorder.events, share_result)

    return BatchPdfItemResult(
        pdf_path=pdf_path,
        pdf_stem=stem,
        output_folder=output_folder,
        run_status=share_result.run_status,
        quality_verdict=share_result.quality_verdict,
        main_failure_reason=share_result.main_failure_reason,
        questions_json_path=questions_path if questions_path.exists() else None,
        share_log_path=share_result.share_log_path,
        error_message=pipeline_error,
        metrics=share_result.metrics,
    )


def _skip_existing(
    pdf_path: Path,
    stem: str,
    output_folder: Path,
    questions_path: Path,
    share_log_path: Path,
) -> BatchPdfItemResult:
    share_log_path.write_text(
        "\n".join(
            [
                f"# Share Log — {pdf_path.name}",
                "",
                "## Skipped",
                "",
                f"Output folder already exists: {output_folder}",
                "Pass --clean-output to rerun.",
                "",
            ],
        ),
        encoding="utf-8",
    )
    return BatchPdfItemResult(
        pdf_path=pdf_path,
        pdf_stem=stem,
        output_folder=output_folder,
        run_status="failed",
        quality_verdict="FAILED",
        main_failure_reason="output_exists",
        questions_json_path=questions_path if questions_path.exists() else None,
        share_log_path=share_log_path,
        error_message="skipped_existing_output",
    )


def _export_questions_json(output_folder: Path, export_path: Path) -> None:
    internal = internal_final_questions_path(output_folder)
    if not internal.exists():
        return
    package = FinalQuestionsPackage.model_validate_json(internal.read_text(encoding="utf-8"))
    write_public_questions_json(package, export_path)


def _append_jsonl_events(
    log_path: Path,
    pdf_name: str,
    events,
    share_result,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for event in events:
            payload = {
                "pdf_name": pdf_name,
                "stage": event.stage,
                "status": event.status,
                "duration_ms": event.duration_ms,
                "metrics": event.metrics,
                "warnings": event.warnings,
                "errors": event.errors,
                "recommended_next_action": share_result.recommended_next_action,
            }
            handle.write(json.dumps(payload, default=str) + "\n")
        if not events:
            payload = {
                "pdf_name": pdf_name,
                "stage": "pipeline",
                "status": share_result.run_status,
                "duration_ms": 0,
                "metrics": share_result.metrics,
                "warnings": [],
                "errors": [share_result.main_failure_reason] if share_result.main_failure_reason else [],
                "recommended_next_action": share_result.recommended_next_action,
            }
            handle.write(json.dumps(payload, default=str) + "\n")


def _write_batch_summary(output_dir: Path, items: list[BatchPdfItemResult]) -> Path:
    rows = [
        BatchPdfMetricsRow(
            pdf_name=item.pdf_path.name,
            pdf_stem=item.pdf_stem,
            output_folder=item.output_folder,
            run_status=item.run_status,
            questions_json_path=item.questions_json_path,
            metrics=item.metrics,
            main_issue=item.main_failure_reason,
            layout_type_detected=item.metrics.get("layout_type_detected", "unknown"),
            public_json_audit=item.metrics.get("public_json_audit", "missing"),
            ready_percentage=float(item.metrics.get("ready_percentage", 0.0)),
        )
        for item in items
    ]
    return write_batch_summary(output_dir, rows)
