"""Build per-chunk semantic binding diagnostics (Part 13I)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from meritranker_data_ingestion.schemas.semantic_binding import SemanticBoundQuestion
from meritranker_data_ingestion.schemas.semantic_chunk_diagnostics import (
    ChunkDiagnosticStatus,
    SemanticChunkDiagnostic,
    SemanticChunkDiagnosticAggregate,
    SemanticChunkDiagnosticPackage,
    SemanticChunkOutputRecord,
)
from meritranker_data_ingestion.services.semantic_final_quality import classify_extra_item


def analyze_chunk_items(
    items: list[SemanticBoundQuestion],
    *,
    chunk_id: str,
    chunk_index: int,
    line_start_id: str | None,
    line_end_id: str | None,
    evidence_line_count: int,
    expected_count: int | None,
    error: str | None = None,
    cache_hit: bool = False,
    prompt_saved: bool = False,
    raw_response_saved: bool = False,
) -> SemanticChunkDiagnostic:
    """Summarize one chunk's returned items."""
    qnums: list[int] = []
    non_numeric: list[str] = []
    out_of_range: list[int] = []
    duplicates: list[int] = []
    noise_items: list[str] = []
    issue_counts: Counter[str] = Counter()

    seen_qnums: dict[int, int] = {}
    for item in items:
        for issue in item.issues:
            issue_counts[issue.split(":")[0]] += 1
        if "noise_in_question_text" in item.issues:
            noise_items.append(item.semantic_question_id)
        if item.question_number is None:
            non_numeric.append(item.semantic_question_id)
        else:
            qnums.append(item.question_number)
            seen_qnums[item.question_number] = seen_qnums.get(item.question_number, 0) + 1
            if expected_count is not None and (
                item.question_number < 1 or item.question_number > expected_count
            ):
                out_of_range.append(item.question_number)
        extra_reason = classify_extra_item(item, expected_count=expected_count)
        if extra_reason == "non_numeric_question_number" and item.semantic_question_id not in non_numeric:
            non_numeric.append(item.semantic_question_id)

    for qnum, count in seen_qnums.items():
        if count > 1:
            duplicates.append(qnum)
        q_issues = " ".join(
            issue
            for i in items
            if i.question_number == qnum
            for issue in i.issues
        )
        if "duplicate_question_number" in q_issues and qnum not in duplicates:
            duplicates.append(qnum)

    status = ChunkDiagnosticStatus.OK
    if error:
        status = ChunkDiagnosticStatus.FAILED
    elif non_numeric or out_of_range or duplicates or noise_items:
        status = ChunkDiagnosticStatus.WARNING
    if any("hallucinated" in issue for item in items for issue in item.issues):
        status = ChunkDiagnosticStatus.WARNING

    q_range = None
    if qnums:
        q_range = f"{min(qnums)}..{max(qnums)}"

    return SemanticChunkDiagnostic(
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        line_start_id=line_start_id,
        line_end_id=line_end_id,
        evidence_line_count=evidence_line_count,
        requested_question_range=q_range,
        returned_item_count=len(items),
        returned_question_numbers=sorted(set(qnums)),
        returned_semantic_question_ids=[i.semantic_question_id for i in items],
        non_numeric_question_items=non_numeric,
        out_of_range_question_numbers=sorted(set(out_of_range)),
        duplicate_question_numbers=sorted(set(duplicates)),
        suspected_noise_items=noise_items,
        validation_issue_counts=dict(issue_counts),
        raw_response_saved=raw_response_saved,
        prompt_saved=prompt_saved,
        cache_hit=cache_hit,
        status=status,
        error=error,
    )


def chunk_output_record_from_diagnostic(
    diagnostic: SemanticChunkDiagnostic,
    *,
    provider: str,
    model: str,
) -> SemanticChunkOutputRecord:
    return SemanticChunkOutputRecord(
        chunk_id=diagnostic.chunk_id,
        chunk_index=diagnostic.chunk_index,
        line_start_id=diagnostic.line_start_id,
        line_end_id=diagnostic.line_end_id,
        evidence_line_count=diagnostic.evidence_line_count,
        returned_item_count=diagnostic.returned_item_count,
        returned_question_numbers=diagnostic.returned_question_numbers,
        returned_semantic_question_ids=diagnostic.returned_semantic_question_ids,
        provider=provider,
        model=model,
        status=diagnostic.status,
        validation_summary=diagnostic.validation_issue_counts,
        error=diagnostic.error,
        cache_hit=diagnostic.cache_hit,
    )


def build_chunk_diagnostics_package(
    chunk_diagnostics: list[SemanticChunkDiagnostic],
    *,
    source_file_name: str,
    provider: str,
    model: str,
    expected_count: int | None,
    merged_items: list[SemanticBoundQuestion],
    validation_hallucination_count: int = 0,
    missing_question_numbers: list[int] | None = None,
) -> SemanticChunkDiagnosticPackage:
    """Aggregate chunk diagnostics into package-level report."""
    all_qnums = [i.question_number for i in merged_items if i.question_number is not None]
    duplicate_global = sorted(
        q for q in set(all_qnums) if all_qnums.count(q) > 1
    )
    non_numeric_count = sum(1 for i in merged_items if i.question_number is None)
    extra_count = sum(
        1 for i in merged_items if classify_extra_item(i, expected_count=expected_count)
    )
    count_match = expected_count is None or len(merged_items) == expected_count

    replay_candidates: list[str] = []
    for diag in chunk_diagnostics:
        reasons: list[str] = []
        if diag.status == ChunkDiagnosticStatus.FAILED:
            reasons.append("failed_provider_response")
        if diag.non_numeric_question_items:
            reasons.append("non_numeric_item")
        if diag.out_of_range_question_numbers:
            reasons.append("out_of_range_question")
        if diag.duplicate_question_numbers:
            reasons.append("duplicate_question")
        if diag.suspected_noise_items:
            reasons.append("suspected_noise")
        if any(k.startswith("hallucinated") for k in diag.validation_issue_counts):
            reasons.append("hallucination")
        if diag.validation_issue_counts.get("missing_question_source_spans", 0) > 2:
            reasons.append("high_missing_spans")
        if reasons:
            replay_candidates.append(diag.chunk_id)

    aggregate = SemanticChunkDiagnosticAggregate(
        total_returned_items=len(merged_items),
        expected_count=expected_count,
        count_match=count_match,
        extra_item_count=extra_count,
        missing_question_numbers=list(missing_question_numbers or []),
        duplicate_question_numbers=duplicate_global,
        non_numeric_item_count=non_numeric_count,
        hallucination_suspected_count=validation_hallucination_count,
        chunks_requiring_replay=replay_candidates,
    )

    warnings: list[str] = []
    if expected_count is not None and not count_match:
        warnings.append(
            f"expected_count_mismatch: expected={expected_count} actual={len(merged_items)}",
        )
    if extra_count:
        warnings.append(f"extra_item_count:{extra_count}")

    return SemanticChunkDiagnosticPackage(
        source_file_name=source_file_name,
        provider=provider,
        model=model,
        expected_count=expected_count,
        total_chunks=len(chunk_diagnostics),
        chunks=chunk_diagnostics,
        aggregate=aggregate,
        warnings=warnings,
    )


def write_chunk_output(chunks_dir: Path, record: SemanticChunkOutputRecord) -> Path:
    chunks_dir.mkdir(parents=True, exist_ok=True)
    path = chunks_dir / f"{record.chunk_id}.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path


def write_chunk_diagnostics_package(out_dir: Path, package: SemanticChunkDiagnosticPackage) -> Path:
    from meritranker_data_ingestion.config import SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME

    path = out_dir / SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME
    path.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    return path
