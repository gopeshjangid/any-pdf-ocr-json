"""Simple process-pdfs workflow: route extractors, export review JSON (Part 14X)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.schemas.semantic_binding import SemanticBinderAnswerMode
from meritranker_data_ingestion.services.batch_pdf_runner import (
    BATCH_RUN_LOG_JSONL_NAME,
    BatchPdfRunnerError,
    find_pdfs,
    _append_jsonl_events,
    _export_questions_json,
)
from meritranker_data_ingestion.services.batch_summary_builder import (
    BatchPdfMetricsRow,
    write_batch_summary,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.pdf_stem import safe_pdf_stem
from meritranker_data_ingestion.services.pdf_summary_builder import write_pdf_summary
from meritranker_data_ingestion.services.pipeline_stage_tracker import PipelineStageRecorder
from meritranker_data_ingestion.services.review_json_exporter import export_review_json
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    SemanticPipelineError,
    SemanticPipelineOptions,
    run_semantic_pipeline,
)
from meritranker_data_ingestion.services.share_log_builder import (
    ShareLogContext,
    build_share_log,
    default_questions_export_path,
)


READY_DIR_NAME = "ready_for_question_bank"


@dataclass(frozen=True)
class ProcessPdfsOptions:
    input_dir: Path = Path("input_pdfs")
    output_dir: Path = Path("batch_outputs")
    ready_dir: Path = Path("ready_for_question_bank")
    expected_count: int = 100
    answer_mode: str = "auto"
    extractor_strategy: str = "auto"
    provider: str | None = None
    model: str | None = None
    timeout_seconds: int = 180
    continue_on_error: bool = True
    allow_auto_fallback: bool = True
    enable_llm_window_repair: bool = False


@dataclass
class ProcessPdfItemResult:
    pdf_path: Path
    pdf_stem: str
    output_folder: Path
    run_status: str
    quality_verdict: str
    main_failure_reason: str | None
    questions_json_path: Path | None
    review_json_path: Path | None
    summary_path: Path | None
    share_log_path: Path | None
    error_message: str | None = None
    metrics: dict = field(default_factory=dict)


@dataclass
class ProcessPdfsResult:
    output_dir: Path
    summary_path: Path
    log_path: Path
    items: list[ProcessPdfItemResult]
    succeeded: int
    failed: int


def process_pdfs(options: ProcessPdfsOptions | None = None) -> ProcessPdfsResult:
    """Run routed extraction for all PDFs in input_pdfs/."""
    opts = options or ProcessPdfsOptions()
    input_dir = resolve_path(opts.input_dir)
    output_dir = resolve_path(opts.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ready_dir = resolve_path(opts.ready_dir)
    ready_dir.mkdir(parents=True, exist_ok=True)

    pdfs = find_pdfs(input_dir)
    if not pdfs:
        raise BatchPdfRunnerError(f"No PDF files found in {input_dir}")

    model = opts.model or os.environ.get("MERITRANKER_BINDER_MODEL")
    log_path = output_dir / BATCH_RUN_LOG_JSONL_NAME
    items: list[ProcessPdfItemResult] = []

    for pdf_path in pdfs:
        item = _process_one(pdf_path, output_dir, opts, model=model, log_path=log_path)
        items.append(item)
        if item.run_status == "failed" and not opts.continue_on_error:
            break

    rows = [
        BatchPdfMetricsRow(
            pdf_name=i.pdf_path.name,
            pdf_stem=i.pdf_stem,
            output_folder=i.output_folder,
            run_status=i.run_status,
            questions_json_path=i.questions_json_path,
            metrics=i.metrics,
            main_issue=i.main_failure_reason,
            layout_type_detected=i.metrics.get("layout_type_detected", "unknown"),
            public_json_audit=i.metrics.get("public_json_audit", "missing"),
            ready_percentage=float(i.metrics.get("ready_percentage", 0.0)),
        )
        for i in items
    ]
    summary_path = write_batch_summary(
        output_dir,
        rows,
        subtitle="Part 14X process-pdfs run.",
    )
    succeeded = sum(1 for i in items if i.run_status in {"passed", "partial_ready"})
    failed = sum(1 for i in items if i.run_status == "failed")
    return ProcessPdfsResult(
        output_dir=output_dir,
        summary_path=summary_path,
        log_path=log_path,
        items=items,
        succeeded=succeeded,
        failed=failed,
    )


def _process_one(
    pdf_path: Path,
    batch_output_dir: Path,
    options: ProcessPdfsOptions,
    *,
    model: str | None,
    log_path: Path,
) -> ProcessPdfItemResult:
    stem = safe_pdf_stem(pdf_path.name)
    output_folder = batch_output_dir / stem
    questions_path = default_questions_export_path(output_folder, stem)
    share_log_path = output_folder / f"{stem}.share-log.md"
    started_at = datetime.now(timezone.utc)
    start_perf = datetime.now(timezone.utc)

    output_folder.mkdir(parents=True, exist_ok=True)
    recorder = PipelineStageRecorder()
    pipeline_error: str | None = None
    pipeline_result = None

    try:
        pipeline_result = run_semantic_pipeline(
            SemanticPipelineOptions(
                input_pdf=pdf_path,
                output_dir=output_folder,
                expected_count=options.expected_count,
                answer_mode=SemanticBinderAnswerMode(options.answer_mode),
                extractor="marker",
                provider=options.provider,
                model=model,
                timeout_seconds=options.timeout_seconds,
                ocr_engine="auto",
                auto_profile=True,
                build_final_questions_export=True,
                allow_ocr_fallback=options.allow_auto_fallback,
                extractor_strategy=options.extractor_strategy,
                allow_auto_fallback=options.allow_auto_fallback,
                enable_llm_window_repair=options.enable_llm_window_repair,
                force=True,
                stage_recorder=recorder,
            ),
        )
    except SemanticPipelineError as exc:
        pipeline_error = str(exc)

    _export_questions_json(output_folder, questions_path)
    review_path = export_review_json(questions_path) if questions_path.exists() else None

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
        model=model,
    )
    share_result = build_share_log(share_ctx)
    _append_jsonl_events(log_path, pdf_path.name, recorder.events, share_result)
    _enrich_routing_metrics(share_result.metrics, output_folder)

    summary_path = write_pdf_summary(
        output_folder,
        stem=stem,
        pdf_file_name=pdf_path.name,
        metrics=share_result.metrics,
        questions_json_path=questions_path if questions_path.exists() else None,
        review_json_path=review_path,
    )

    return ProcessPdfItemResult(
        pdf_path=pdf_path,
        pdf_stem=stem,
        output_folder=output_folder,
        run_status=share_result.run_status,
        quality_verdict=share_result.quality_verdict,
        main_failure_reason=share_result.main_failure_reason,
        questions_json_path=questions_path if questions_path.exists() else None,
        review_json_path=review_path,
        summary_path=summary_path,
        share_log_path=share_result.share_log_path,
        error_message=pipeline_error,
        metrics=share_result.metrics,
    )


def _enrich_routing_metrics(metrics: dict, output_folder: Path) -> None:
    profile_path = output_folder / "extraction_package" / "diagnostics" / "pdf-extractor-profile.json"
    if not profile_path.exists():
        return
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    metrics["extractor_strategy_requested"] = profile.get("extractor_strategy_requested")
    metrics["extractor_strategy_effective"] = profile.get("extractor_strategy_effective")
    metrics["pdf_profile_summary"] = {
        "text_density_score": profile.get("text_density_score"),
        "image_area_ratio": profile.get("image_area_ratio"),
        "scanned_or_screenshot_score": profile.get("scanned_or_screenshot_score"),
        "layout_hint": profile.get("layout_hint"),
    }
    metrics["sampled_pages"] = profile.get("sampled_pages")
    metrics["ignored_profile_pages_count"] = profile.get("ignored_profile_pages_count")
    metrics["marker_used"] = profile.get("marker_used")
    metrics["azure_used"] = profile.get("azure_used")
    metrics["dual_used"] = profile.get("dual_used")
    metrics["fallback_used"] = profile.get("fallback_used", profile.get("fallback_allowed"))
    metrics["fallback_reason"] = profile.get("fallback_reason")
    metrics["marker_quality_summary"] = profile.get("marker_quality_summary")
