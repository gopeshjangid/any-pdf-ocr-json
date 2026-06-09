"""Replay finalization stages without OCR/Marker/LLM (Part 14L)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import (
    EXTRACTION_PACKAGE_DIR,
    FINAL_QUESTIONS_DIR,
    FINAL_REVIEW_ITEMS_JSON_NAME,
)
from meritranker_data_ingestion.schemas.semantic_binding import SemanticBinderAnswerMode
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.final_questions_export_builder import (
    FinalQuestionsExportError,
    build_final_questions_export,
)
from meritranker_data_ingestion.services.question_window_builder import build_question_windows
from meritranker_data_ingestion.services.final_questions_public_serializer import (
    write_public_questions_json,
)
from meritranker_data_ingestion.services.share_log_builder import (
    ShareLogContext,
    build_share_log,
    default_questions_export_path,
)


class FinalizationReplayError(Exception):
    """Raised when replay-finalization cannot proceed."""


@dataclass(frozen=True)
class FinalizationReplayResult:
    package_dir: Path
    output_dir: Path | None
    question_window_count: int
    total_questions_detected: int
    questions_with_4_options_count: int
    accepted_safe_count: int
    accepted_safe_with_incomplete_options_count: int
    ready_count: int
    review_count: int
    review_items_count: int
    answer_solution_join_gap_count: int
    answers_mapped_from_solution_count: int
    json_path: Path
    report_path: Path
    review_items_path: Path | None
    share_log_path: Path | None


def replay_finalization(
    package_dir: Path,
    *,
    expected_count: int | None = None,
    refresh_share_log: bool = True,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
) -> FinalizationReplayResult:
    """Rerun question windows, option parsing, join, gate, and final export only."""
    resolved = resolve_path(package_dir)
    manifest = _load_manifest(resolved)
    output_dir = Path(manifest["output_dir"]) if manifest.get("output_dir") else resolved.parent

    qw_result = build_question_windows(resolved, expected_count=expected_count)

    try:
        export_result = build_final_questions_export(
            resolved,
            answer_mode=answer_mode,
            expected_count=expected_count,
        )
    except FinalQuestionsExportError as exc:
        raise FinalizationReplayError(str(exc)) from exc

    report_data = json.loads(export_result.report_path.read_text(encoding="utf-8"))
    if expected_count is not None:
        report_data["expected_count"] = expected_count
        export_result.report_path.write_text(
            json.dumps(report_data, indent=2),
            encoding="utf-8",
        )
    share_log_path: Path | None = None

    if refresh_share_log and output_dir.exists():
        stem = output_dir.name
        questions_path = default_questions_export_path(output_dir, stem)
        write_public_questions_json(export_result.package, questions_path)
        share_ctx = ShareLogContext(
            pdf_file_name=manifest.get("source_file_name", f"{stem}.pdf"),
            input_path=Path(manifest.get("input_pdf_path", output_dir / f"{stem}.pdf")),
            output_folder=output_dir,
            started_at=datetime.now(timezone.utc),
            duration_seconds=0.0,
            questions_json_path=questions_path,
        )
        share_result = build_share_log(share_ctx)
        share_log_path = share_result.share_log_path

    return FinalizationReplayResult(
        package_dir=resolved,
        output_dir=output_dir if output_dir.exists() else None,
        question_window_count=qw_result.package.total_windows,
        total_questions_detected=export_result.package.total_questions_detected,
        questions_with_4_options_count=int(
            report_data.get("questions_with_4_options_count", 0),
        ),
        accepted_safe_count=export_result.package.accepted_safe_count,
        accepted_safe_with_incomplete_options_count=int(
            report_data.get("accepted_safe_with_incomplete_options_count", 0),
        ),
        ready_count=int(report_data.get("ready_count", export_result.package.ready_count)),
        review_count=int(report_data.get("review_count", export_result.package.review_count)),
        review_items_count=int(report_data.get("review_items_count", 0)),
        answer_solution_join_gap_count=int(report_data.get("answer_solution_join_gap_count", 0)),
        answers_mapped_from_solution_count=int(
            report_data.get("answers_mapped_from_solution_count", 0),
        ),
        json_path=export_result.json_path,
        report_path=export_result.report_path,
        review_items_path=resolved / FINAL_QUESTIONS_DIR / FINAL_REVIEW_ITEMS_JSON_NAME,
        share_log_path=share_log_path,
    )


def _load_manifest(package_dir: Path) -> dict:
    path = package_dir / "manifest.json"
    if not path.exists() and package_dir.name == EXTRACTION_PACKAGE_DIR:
        path = package_dir.parent / EXTRACTION_PACKAGE_DIR / "manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
