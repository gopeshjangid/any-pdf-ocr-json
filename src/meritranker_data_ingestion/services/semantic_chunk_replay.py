"""Plan and optionally replay suspicious semantic binding chunks (Part 13I)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    SEMANTIC_BINDING_CHUNKS_DIR,
    SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_REPLAY_PLAN_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingPackage,
)
from meritranker_data_ingestion.schemas.semantic_chunk_diagnostics import (
    ChunkDiagnosticStatus,
    SemanticChunkDiagnosticPackage,
    SemanticChunkOutputRecord,
    SemanticChunkReplayPlan,
    SemanticChunkReplayPlanItem,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.semantic_bad_item_guard import apply_semantic_bad_item_guard
from meritranker_data_ingestion.services.semantic_binding_repair import repair_semantic_binding_package
from meritranker_data_ingestion.services.semantic_binder import (
    _build_prompt,
    _chunk_lines_smart,
    _merge_chunk_items,
    _parse_question_item,
)
from meritranker_data_ingestion.services.semantic_binder import (
    evaluate_semantic_binding_package,
)
from meritranker_data_ingestion.services.answer_key_evidence_extractor import (
    answer_key_candidates_to_prompt_json,
    extract_answer_key_candidates,
)


class SemanticChunkReplayError(Exception):
    """Raised when chunk replay cannot proceed."""


@dataclass
class SemanticChunkReplayResult:
    plan: SemanticChunkReplayPlan
    plan_path: Path
    executed: bool
    replayed_chunk_count: int = 0
    warnings: list[str] | None = None


def replay_semantic_chunks(
    package_dir: Path,
    *,
    only_suspicious: bool = True,
    execute: bool = False,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    expected_count: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
    include_answer_key_evidence: bool = True,
) -> SemanticChunkReplayResult:
    """Build replay plan; optionally re-call LLM for suspicious chunks only."""
    resolved = resolve_path(package_dir)
    sem_dir = resolved / SEMANTIC_BINDING_DIR
    diag_path = sem_dir / SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME
    if not diag_path.exists():
        raise SemanticChunkReplayError(
            f"Missing chunk diagnostics at {diag_path}. Run bind-semantically first.",
        )

    diagnostics = SemanticChunkDiagnosticPackage.model_validate_json(
        diag_path.read_text(encoding="utf-8"),
    )
    plan_items = _build_plan_items(diagnostics, only_suspicious=only_suspicious)
    plan = SemanticChunkReplayPlan(
        source_file_name=diagnostics.source_file_name,
        dry_run=not execute,
        suspicious_chunk_count=len(plan_items),
        chunks=plan_items,
    )
    plan_path = sem_dir / SEMANTIC_BINDING_REPLAY_PLAN_NAME
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    if not execute:
        return SemanticChunkReplayResult(
            plan=plan,
            plan_path=plan_path,
            executed=False,
        )

    if not plan_items:
        return SemanticChunkReplayResult(
            plan=plan,
            plan_path=plan_path,
            executed=True,
            replayed_chunk_count=0,
            warnings=["no_suspicious_chunks_to_replay"],
        )

    replayed = _execute_chunk_replay(
        resolved,
        plan_items=plan_items,
        diagnostics=diagnostics,
        answer_mode=answer_mode,
        expected_count=expected_count,
        provider=provider,
        model=model,
        timeout_seconds=timeout_seconds,
        include_answer_key_evidence=include_answer_key_evidence,
    )

    apply_semantic_bad_item_guard(
        resolved,
        expected_count=expected_count,
        answer_mode=answer_mode,
    )
    repair_semantic_binding_package(
        resolved,
        answer_mode=answer_mode,
        expected_count=expected_count,
    )
    evaluate_semantic_binding_package(
        resolved,
        expected_count=expected_count,
        answer_mode=answer_mode,
        use_repaired=True,
    )

    return SemanticChunkReplayResult(
        plan=plan,
        plan_path=plan_path,
        executed=True,
        replayed_chunk_count=replayed,
    )


def _build_plan_items(
    diagnostics: SemanticChunkDiagnosticPackage,
    *,
    only_suspicious: bool,
) -> list[SemanticChunkReplayPlanItem]:
    items: list[SemanticChunkReplayPlanItem] = []
    for chunk in diagnostics.chunks:
        reasons: list[str] = []
        if chunk.status == ChunkDiagnosticStatus.FAILED:
            reasons.append("failed_provider_response")
        if chunk.non_numeric_question_items:
            reasons.append("non_numeric_item")
        if chunk.out_of_range_question_numbers:
            reasons.append("out_of_range_question")
        if chunk.duplicate_question_numbers:
            reasons.append("duplicate_question")
        if chunk.suspected_noise_items:
            reasons.append("suspected_noise")
        if any(k.startswith("hallucinated") for k in chunk.validation_issue_counts):
            reasons.append("hallucination")
        span_missing = chunk.validation_issue_counts.get("missing_question_source_spans", 0)
        if span_missing > 2:
            reasons.append("high_missing_spans")

        if only_suspicious and not reasons:
            continue
        if chunk.chunk_id in diagnostics.aggregate.chunks_requiring_replay and not reasons:
            reasons.append("aggregate_replay_candidate")

        items.append(
            SemanticChunkReplayPlanItem(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                reasons=reasons or ["manual_replay"],
                will_execute=True,
            ),
        )
    return items


def _execute_chunk_replay(
    package_dir: Path,
    *,
    plan_items: list[SemanticChunkReplayPlanItem],
    diagnostics: SemanticChunkDiagnosticPackage,
    answer_mode: SemanticBinderAnswerMode,
    expected_count: int | None,
    provider: str | None,
    model: str | None,
    timeout_seconds: int | None,
    include_answer_key_evidence: bool,
) -> int:
    from meritranker_data_ingestion.services.llm_provider import resolve_llm_provider

    evidence_path = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    evidence = DocumentEvidencePackage.model_validate_json(
        evidence_path.read_text(encoding="utf-8"),
    )
    binding_path = package_dir / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_NAME
    if not binding_path.exists():
        raise SemanticChunkReplayError("Missing semantic-bound-questions.json")

    binding = SemanticBindingPackage.model_validate_json(binding_path.read_text(encoding="utf-8"))
    primary_lines = [
        line for line in evidence.lines if line.source_extractor == evidence.primary_extractor
    ] or list(evidence.lines)

    answer_key_candidates = extract_answer_key_candidates(primary_lines)
    chunks, chunk_meta_list = _chunk_lines_smart(
        primary_lines,
        120,
        answer_key_candidates=answer_key_candidates if include_answer_key_evidence else [],
    )
    answer_key_json = (
        answer_key_candidates_to_prompt_json(answer_key_candidates)
        if include_answer_key_evidence
        else []
    )

    provider_impl = resolve_llm_provider(
        provider=provider,
        model=model,
        timeout_seconds=timeout_seconds or 120,
    )

    replay_chunk_ids = {item.chunk_id for item in plan_items}
    kept_raw: list[dict[str, Any]] = [
        item.model_dump()
        for item in binding.items
        if (item.chunk_id or "") not in replay_chunk_ids
    ]
    replay_raw: list[dict[str, Any]] = []
    warnings: list[str] = []
    replayed_count = 0

    for chunk_idx, chunk in enumerate(chunks):
        chunk_id = f"chunk-{chunk_idx:03d}"
        if chunk_id not in replay_chunk_ids:
            continue

        meta = chunk_meta_list[chunk_idx] if chunk_idx < len(chunk_meta_list) else {}
        prompt = _build_prompt(
            chunk,
            evidence,
            answer_mode=answer_mode,
            answer_key_evidence=answer_key_json if include_answer_key_evidence else None,
            chunk_meta=meta,
        )
        try:
            response = provider_impl.generate_json(prompt, schema_name="semantic_binding_chunk")
        except Exception as exc:
            raise SemanticChunkReplayError(f"replay failed for {chunk_id}: {exc}") from exc

        if not isinstance(response, dict):
            raise SemanticChunkReplayError(f"replay invalid response for {chunk_id}")

        chunk_items = response.get("items") or []
        for raw in chunk_items:
            if isinstance(raw, dict):
                raw = dict(raw)
                raw["chunk_id"] = chunk_id
                replay_raw.append(raw)
        replayed_count += 1

    if not replay_raw:
        return 0

    merged = _merge_chunk_items(kept_raw + replay_raw, warnings=warnings)
    chunk_by_qnum: dict[int, str] = {}
    for raw in replay_raw:
        qn = raw.get("question_number")
        cid = raw.get("chunk_id")
        if qn is not None and cid:
            chunk_by_qnum[int(qn)] = str(cid)
    for item in merged:
        if item.question_number is not None and item.question_number in chunk_by_qnum:
            item.chunk_id = chunk_by_qnum[item.question_number]
        elif item.chunk_id is None:
            for kept in binding.items:
                if kept.semantic_question_id == item.semantic_question_id and kept.chunk_id:
                    item.chunk_id = kept.chunk_id
                    break

    binding.items = merged
    binding.warnings.extend(warnings)
    binding.warnings.append(f"chunk_replay_executed:{replayed_count}")
    binding_path.write_text(binding.model_dump_json(indent=2), encoding="utf-8")
    return replayed_count
