"""Source-grounded semantic binder service (Part 13C)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.config import (
    ARTIFACT_RECONCILIATION_JSON_NAME,
    BINDER_PROVIDER_MOCK,
    DEFAULT_BINDER_MAX_LINES_PER_CHUNK,
    SEMANTIC_BINDING_PROMPT_VERSION,
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    FINAL_VALIDATION_REPORT_NAME,
    INGESTION_ELIGIBILITY_REPORT_NAME,
    QUESTION_CANDIDATE_REPORT_NAME,
    SEMANTIC_BINDING_CACHE_NAME,
    SEMANTIC_BINDING_EVALUATION_JSON_NAME,
    SEMANTIC_BINDING_EVALUATION_MD_NAME,
    SEMANTIC_BINDING_PACKAGE_VERSION,
    SEMANTIC_BINDING_PROMPTS_DIR,
    SEMANTIC_BINDING_REPORT_NAME,
    SEMANTIC_BINDING_SUMMARY_MD_NAME,
    SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME,
    SEMANTIC_BINDING_VALIDATION_NAME,
    SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
    SEMANTIC_BINDING_CHUNKS_DIR,
    SEMANTIC_BINDING_CHUNKS_RAW_DIR,
    SEMANTIC_BINDING_DIR,
)
from meritranker_data_ingestion.schemas.semantic_binding import AnswerKeyEvidenceCandidate
from meritranker_data_ingestion.services.answer_key_evidence_extractor import (
    answer_key_candidates_to_prompt_json,
    extract_answer_key_candidates,
)
from meritranker_data_ingestion.services.semantic_binding_evaluation import (
    build_semantic_binding_evaluation,
    render_evaluation_md,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    RoleHint,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingPackage,
    SemanticBindingQualityThresholds,
    SemanticBindingStatus,
    SemanticBindingValidationReport,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
    SemanticBoundSolution,
    SemanticMetadataCandidate,
    SemanticVisualReference,
    SemanticVisualRoleHint,
)
from meritranker_data_ingestion.services.file_service import PathValidationError, resolve_path
from meritranker_data_ingestion.services.llm_provider import (
    LlmProvider,
    LlmProviderError,
    LlmProviderPreflightError,
    resolve_llm_provider,
)
from meritranker_data_ingestion.services.semantic_binding_validator import validate_semantic_items
from meritranker_data_ingestion.services.semantic_chunk_diagnostics_builder import (
    analyze_chunk_items,
    build_chunk_diagnostics_package,
    chunk_output_record_from_diagnostic,
    write_chunk_diagnostics_package,
    write_chunk_output,
)


class SemanticBindingError(Exception):
    """Raised when semantic binding cannot complete."""


@dataclass(frozen=True)
class SemanticBindingResult:
    package: SemanticBindingPackage
    output_path: Path
    validation_path: Path
    report_path: Path
    evaluation_path: Path
    from_cache: bool = False


@dataclass(frozen=True)
class BinderTriggerEvaluation:
    should_run: bool
    reasons: list[str]


def compute_evidence_hash(
    evidence: DocumentEvidencePackage,
    *,
    provider: str,
    model: str,
    answer_mode: SemanticBinderAnswerMode,
    include_answer_key_evidence: bool = True,
    max_lines_per_chunk: int = DEFAULT_BINDER_MAX_LINES_PER_CHUNK,
) -> str:
    payload = evidence.model_dump_json()
    digest = hashlib.sha256(
        (
            f"{payload}|{provider}|{model}|{answer_mode.value}|"
            f"{include_answer_key_evidence}|{max_lines_per_chunk}|"
            f"{SEMANTIC_BINDING_PROMPT_VERSION}"
        ).encode("utf-8"),
    )
    return digest.hexdigest()[:24]


def evaluate_binder_trigger(
    package_dir: Path,
    evidence: DocumentEvidencePackage,
    *,
    force: bool = False,
) -> BinderTriggerEvaluation:
    if force:
        return BinderTriggerEvaluation(should_run=True, reasons=["force_semantic_binder"])

    reasons: list[str] = []
    q_report = _load_json(package_dir / "questions" / QUESTION_CANDIDATE_REPORT_NAME)
    if q_report:
        total = int(q_report.get("total_candidates") or 0)
        valid = int(q_report.get("valid_candidates") or 0)
        no_opts = int(q_report.get("candidates_with_no_options") or 0)
        if total > 0 and valid / total < 0.70:
            reasons.append("valid_candidates_ratio_below_0_70")
        if no_opts > 20:
            reasons.append("candidates_with_no_options_above_20")

    recon = _load_json(package_dir / "diagnostics" / ARTIFACT_RECONCILIATION_JSON_NAME)
    if recon:
        gate = str(recon.get("quality_gate_status") or "")
        if gate in {"failed", "warning"}:
            reasons.append(f"artifact_reconciliation_{gate}")

    elig = _load_json(package_dir / "eligibility" / INGESTION_ELIGIBILITY_REPORT_NAME)
    option_hints = evidence.lines and sum(
        1 for line in evidence.lines if RoleHint.OPTION_LABEL_CANDIDATE in line.role_hints
    )
    if elig:
        eligible = int(elig.get("eligible_count") or 0)
        if eligible == 0 and option_hints > 0:
            reasons.append("eligible_zero_with_option_hints")

    val_report = _load_json(package_dir / "final" / FINAL_VALIDATION_REPORT_NAME)
    if val_report:
        missing = val_report.get("missing_question_numbers") or []
        if missing:
            reasons.append("missing_question_numbers_in_final_validation")

    return BinderTriggerEvaluation(should_run=bool(reasons), reasons=reasons)


def bind_semantically_package(
    package_dir: Path,
    *,
    provider: str | None = None,
    model: str | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
    api_version: str | None = None,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    expected_count: int | None = None,
    force: bool = False,
    debug_prompts: bool = False,
    max_lines_per_chunk: int = DEFAULT_BINDER_MAX_LINES_PER_CHUNK,
    include_answer_key_evidence: bool = True,
    timeout_seconds: int | None = None,
    eval_only: bool = False,
    llm_provider: LlmProvider | None = None,
) -> SemanticBindingResult:
    """Run semantic binding on normalized document evidence."""
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")

    from meritranker_data_ingestion.services.evidence_resolver import (
        load_document_evidence,
        select_binding_lines,
    )

    try:
        evidence, evidence_path = load_document_evidence(resolved)
    except FileNotFoundError as exc:
        raise SemanticBindingError(
            f"{exc}. Run normalize-evidence first.",
        ) from exc
    out_dir = resolved / SEMANTIC_BINDING_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / SEMANTIC_BOUND_QUESTIONS_NAME
    evaluation_path = out_dir / SEMANTIC_BINDING_EVALUATION_JSON_NAME

    if eval_only:
        return _run_eval_only(
            package_dir=resolved,
            evidence=evidence,
            evidence_path=evidence_path,
            output_path=output_path,
            out_dir=out_dir,
            evaluation_path=evaluation_path,
            expected_count=expected_count,
            answer_mode=answer_mode,
        )

    provider_impl = llm_provider or resolve_llm_provider(
        provider=provider,
        model=model,
        endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
        timeout_seconds=timeout_seconds or 120,
    )

    evidence_hash = compute_evidence_hash(
        evidence,
        provider=provider_impl.provider_name,
        model=provider_impl.model_name,
        answer_mode=answer_mode,
        include_answer_key_evidence=include_answer_key_evidence,
        max_lines_per_chunk=max_lines_per_chunk,
    )

    cache_path = out_dir / SEMANTIC_BINDING_CACHE_NAME

    if not force and _cache_matches(cache_path, evidence_hash, output_path):
        cached = SemanticBindingPackage.model_validate_json(output_path.read_text(encoding="utf-8"))
        validation = cached.validation_summary
        evaluation = build_semantic_binding_evaluation(
            cached,
            validation,
            expected_count=expected_count,
            chunk_count=0,
            cache_hit=True,
            answer_mode=answer_mode,
            package_dir=resolved,
        )
        _write_evaluation(out_dir, evaluation)
        return SemanticBindingResult(
            package=cached,
            output_path=output_path,
            validation_path=out_dir / SEMANTIC_BINDING_VALIDATION_NAME,
            report_path=out_dir / SEMANTIC_BINDING_REPORT_NAME,
            evaluation_path=evaluation_path,
            from_cache=True,
        )

    primary_lines = select_binding_lines(evidence, evidence_path)
    line_by_id = {line.line_id: line for line in primary_lines}

    from meritranker_data_ingestion.services.question_window_builder import load_question_windows

    windows_pkg = load_question_windows(resolved)
    answer_key_candidates = extract_answer_key_candidates(primary_lines)
    if windows_pkg and windows_pkg.windows:
        chunks, chunk_meta_list = _chunk_lines_from_windows(
            line_by_id,
            windows_pkg,
            max_lines_per_chunk,
            answer_key_candidates=answer_key_candidates if include_answer_key_evidence else [],
        )
    else:
        chunks, chunk_meta_list = _chunk_lines_smart(
            primary_lines,
            max_lines_per_chunk,
            answer_key_candidates=answer_key_candidates if include_answer_key_evidence else [],
        )
    prompts_dir = out_dir / SEMANTIC_BINDING_PROMPTS_DIR if debug_prompts else None
    if prompts_dir is not None:
        prompts_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = out_dir / SEMANTIC_BINDING_CHUNKS_DIR
    chunks_dir.mkdir(parents=True, exist_ok=True)
    raw_chunks_dir = chunks_dir / SEMANTIC_BINDING_CHUNKS_RAW_DIR if debug_prompts else None
    if raw_chunks_dir is not None:
        raw_chunks_dir.mkdir(parents=True, exist_ok=True)

    raw_items: list[dict[str, Any]] = []
    metadata_raw: list[dict[str, Any]] = []
    chunk_diagnostics_list = []
    errors: list[str] = []
    warnings: list[str] = list(evidence.warnings)
    estimated_prompt_chars = 0

    answer_key_json = (
        answer_key_candidates_to_prompt_json(answer_key_candidates)
        if include_answer_key_evidence
        else []
    )

    planned_chunk_count = len(chunks)
    executed_chunk_count = 0

    if provider_impl.provider_name != BINDER_PROVIDER_MOCK:
        try:
            provider_impl.preflight()
        except LlmProviderPreflightError as exc:
            return _fail_provider_preflight(
                resolved=resolved,
                out_dir=out_dir,
                output_path=output_path,
                evaluation_path=evaluation_path,
                evidence=evidence,
                evidence_path=evidence_path,
                evidence_hash=evidence_hash,
                provider_impl=provider_impl,
                answer_mode=answer_mode,
                expected_count=expected_count,
                planned_chunk_count=planned_chunk_count,
                error_message=str(exc),
                diagnostics=exc.diagnostics,
            )

    for chunk_idx, chunk in enumerate(chunks):
        chunk_id = f"chunk-{chunk_idx:03d}"
        meta = chunk_meta_list[chunk_idx] if chunk_idx < len(chunk_meta_list) else {}
        line_ids = [line.line_id for line in chunk]
        line_start_id = line_ids[0] if line_ids else None
        line_end_id = line_ids[-1] if line_ids else None
        prompt = _build_prompt(
            chunk,
            evidence,
            answer_mode=answer_mode,
            answer_key_evidence=answer_key_json if include_answer_key_evidence else None,
            chunk_meta=meta,
        )
        estimated_prompt_chars += len(prompt)
        prompt_saved = False
        if prompts_dir is not None:
            (prompts_dir / f"chunk_{chunk_idx:03d}.txt").write_text(prompt, encoding="utf-8")
            prompt_saved = True

        chunk_error: str | None = None
        chunk_parsed: list[SemanticBoundQuestion] = []
        response: dict[str, Any] | None = None
        try:
            response = provider_impl.generate_json(prompt, schema_name="semantic_binding_chunk")
            executed_chunk_count += 1
        except LlmProviderError as exc:
            msg = str(exc)
            if any(token in msg for token in ("HTTP 401", "HTTP 403", "HTTP 404")):
                raise SemanticBindingError(f"provider_request_failed: {msg}") from exc
            chunk_error = str(exc)
            errors.append(f"chunk_{chunk_idx}: {exc}")
        except json.JSONDecodeError as exc:
            chunk_error = f"invalid_json:{exc}"
            errors.append(f"chunk_{chunk_idx}: invalid_json:{exc}")
            response = None
        else:
            if not isinstance(response, dict):
                chunk_error = "response_not_object"
                errors.append(f"chunk_{chunk_idx}: response_not_object")
                response = None

        raw_response_saved = False
        if response is not None and raw_chunks_dir is not None:
            (raw_chunks_dir / f"{chunk_id}.json").write_text(
                json.dumps(response, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raw_response_saved = True

        if isinstance(response, dict):
            chunk_items = response.get("items") or []
            if isinstance(chunk_items, list):
                for item in chunk_items:
                    if not isinstance(item, dict):
                        continue
                    item = dict(item)
                    item["chunk_id"] = chunk_id
                    raw_items.append(item)
                    parsed = _parse_question_item(item)
                    parsed.chunk_id = chunk_id
                    _assign_window_id(parsed, meta, windows_pkg)
                    chunk_parsed.append(parsed)
            response_meta = response.get("metadata_candidates") or []
            if isinstance(response_meta, list):
                metadata_raw.extend(item for item in response_meta if isinstance(item, dict))

        diag = analyze_chunk_items(
            chunk_parsed,
            chunk_id=chunk_id,
            chunk_index=chunk_idx,
            line_start_id=line_start_id,
            line_end_id=line_end_id,
            evidence_line_count=len(chunk),
            expected_count=expected_count,
            error=chunk_error,
            prompt_saved=prompt_saved,
            raw_response_saved=raw_response_saved,
        )
        chunk_diagnostics_list.append(diag)
        write_chunk_output(
            chunks_dir,
            chunk_output_record_from_diagnostic(
                diag,
                provider=provider_impl.provider_name,
                model=provider_impl.model_name,
            ),
        )

    items = _merge_chunk_items(raw_items, warnings=warnings)
    if include_answer_key_evidence:
        _enrich_answers_from_key_evidence(items, answer_key_candidates, warnings=warnings)
    metadata_candidates = _parse_metadata(metadata_raw)

    binding_package = SemanticBindingPackage(
        package_version=SEMANTIC_BINDING_PACKAGE_VERSION,
        source_file_name=evidence.source_file_name,
        binder_provider=provider_impl.provider_name,
        binder_model=provider_impl.model_name,
        answer_mode=answer_mode,
        input_evidence_hash=evidence_hash,
        metadata_candidates=metadata_candidates,
        items=items,
        warnings=warnings,
        errors=errors,
        source_artifact_paths={
            "document_evidence": str(evidence_path),
            "semantic_bound_questions": str(output_path),
        },
    )

    validation = validate_semantic_items(
        binding_package.items,
        binding_package.metadata_candidates,
        evidence,
        answer_mode=answer_mode,
        expected_count=expected_count,
    )
    binding_package.validation_summary = validation

    chunk_diag_package = build_chunk_diagnostics_package(
        chunk_diagnostics_list,
        source_file_name=evidence.source_file_name,
        provider=provider_impl.provider_name,
        model=provider_impl.model_name,
        expected_count=expected_count,
        merged_items=binding_package.items,
        validation_hallucination_count=validation.hallucination_suspected_count,
        missing_question_numbers=list(validation.missing_question_numbers),
    )
    write_chunk_diagnostics_package(out_dir, chunk_diag_package)

    used_question_windows = bool(windows_pkg and windows_pkg.windows)
    provider_called = (
        executed_chunk_count > 0
        or provider_impl.provider_name == BINDER_PROVIDER_MOCK
    )
    returned_item_count = len(items)

    binding_package.warnings.extend(validation.warnings)
    binding_package.warnings.extend(
        [
            f"planned_chunk_count:{planned_chunk_count}",
            f"executed_chunk_count:{executed_chunk_count}",
            f"returned_item_count:{returned_item_count}",
            f"used_question_windows:{used_question_windows}",
            f"provider_called:{provider_called}",
        ],
    )
    if returned_item_count == 0:
        if planned_chunk_count == 0:
            binding_package.warnings.append("skipped_reason:no_chunks_planned")
        elif not provider_called:
            binding_package.warnings.append("skipped_reason:provider_not_called")
        else:
            binding_package.warnings.append("skipped_reason:zero_items_returned")

    if errors and not items:
        binding_package.status = SemanticBindingStatus.FAILED
    elif not items:
        binding_package.status = SemanticBindingStatus.WARNING
    elif validation.rejected_count > 0 or validation.review_required_count > 0:
        binding_package.status = SemanticBindingStatus.WARNING
    else:
        binding_package.status = SemanticBindingStatus.SUCCEEDED

    evaluation = build_semantic_binding_evaluation(
        binding_package,
        validation,
        expected_count=expected_count,
        chunk_count=planned_chunk_count,
        estimated_prompt_chars=estimated_prompt_chars,
        cache_hit=False,
        answer_mode=answer_mode,
        package_dir=resolved,
    )
    _write_artifacts(out_dir, binding_package, validation, evidence_hash, evaluation)
    return SemanticBindingResult(
        package=binding_package,
        output_path=output_path,
        validation_path=out_dir / SEMANTIC_BINDING_VALIDATION_NAME,
        report_path=out_dir / SEMANTIC_BINDING_REPORT_NAME,
        evaluation_path=evaluation_path,
        from_cache=False,
    )


def _fail_provider_preflight(
    *,
    resolved: Path,
    out_dir: Path,
    output_path: Path,
    evaluation_path: Path,
    evidence: DocumentEvidencePackage,
    evidence_path: Path,
    evidence_hash: str,
    provider_impl: LlmProvider,
    answer_mode: SemanticBinderAnswerMode,
    expected_count: int | None,
    planned_chunk_count: int,
    error_message: str,
    diagnostics: dict[str, Any],
) -> SemanticBindingResult:
    """Write failed binding artifacts when provider preflight fails."""
    validation = SemanticBindingValidationReport(errors=[error_message])
    binding_package = SemanticBindingPackage(
        package_version=SEMANTIC_BINDING_PACKAGE_VERSION,
        source_file_name=evidence.source_file_name,
        binder_provider=provider_impl.provider_name,
        binder_model=provider_impl.model_name,
        answer_mode=answer_mode,
        input_evidence_hash=evidence_hash,
        items=[],
        status=SemanticBindingStatus.FAILED,
        validation_summary=validation,
        errors=[f"provider_preflight_failed: {error_message}"],
        warnings=[
            f"planned_chunk_count:{planned_chunk_count}",
            "executed_chunk_count:0",
            f"provider_diagnostics:{json.dumps(diagnostics, ensure_ascii=False)}",
        ],
        source_artifact_paths={
            "document_evidence": str(evidence_path),
            "semantic_bound_questions": str(output_path),
        },
    )
    evaluation = build_semantic_binding_evaluation(
        binding_package,
        validation,
        expected_count=expected_count,
        chunk_count=planned_chunk_count,
        cache_hit=False,
        answer_mode=answer_mode,
        package_dir=resolved,
    )
    _write_artifacts(out_dir, binding_package, validation, evidence_hash, evaluation)
    return SemanticBindingResult(
        package=binding_package,
        output_path=output_path,
        validation_path=out_dir / SEMANTIC_BINDING_VALIDATION_NAME,
        report_path=out_dir / SEMANTIC_BINDING_REPORT_NAME,
        evaluation_path=evaluation_path,
        from_cache=False,
    )


def _cache_matches(cache_path: Path, evidence_hash: str, output_path: Path) -> bool:
    if not cache_path.exists() or not output_path.exists():
        return False
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return cache.get("input_evidence_hash") == evidence_hash


def _run_eval_only(
    *,
    package_dir: Path,
    evidence: DocumentEvidencePackage,
    evidence_path: Path,
    output_path: Path,
    out_dir: Path,
    evaluation_path: Path,
    validation_path: Path | None = None,
    expected_count: int | None,
    answer_mode: SemanticBinderAnswerMode,
    thresholds: SemanticBindingQualityThresholds | None = None,
    write_evaluation_to_canonical: bool = True,
) -> SemanticBindingResult:
    if not output_path.exists():
        raise SemanticBindingError(
            f"No semantic binding output at {output_path}. Run bind-semantically first.",
        )
    package = SemanticBindingPackage.model_validate_json(output_path.read_text(encoding="utf-8"))
    resolved_validation_path = validation_path or (out_dir / SEMANTIC_BINDING_VALIDATION_NAME)
    if resolved_validation_path.exists():
        validation = SemanticBindingValidationReport.model_validate_json(
            resolved_validation_path.read_text(encoding="utf-8"),
        )
    else:
        validation = validate_semantic_items(
            package.items,
            package.metadata_candidates,
            evidence,
            answer_mode=answer_mode,
            expected_count=expected_count,
        )
        package.validation_summary = validation

    evaluation = build_semantic_binding_evaluation(
        package,
        validation,
        expected_count=expected_count,
        chunk_count=0,
        cache_hit=True,
        answer_mode=answer_mode,
        package_dir=package_dir,
        thresholds=thresholds,
    )
    if write_evaluation_to_canonical:
        _write_evaluation(out_dir, evaluation)
    else:
        evaluation_path.write_text(evaluation.model_dump_json(indent=2), encoding="utf-8")
    return SemanticBindingResult(
        package=package,
        output_path=output_path,
        validation_path=resolved_validation_path,
        report_path=out_dir / SEMANTIC_BINDING_REPORT_NAME,
        evaluation_path=evaluation_path,
        from_cache=True,
    )


def evaluate_semantic_binding_package(
    package_dir: Path,
    *,
    expected_count: int | None = None,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    thresholds: SemanticBindingQualityThresholds | None = None,
    bind_if_missing: bool = False,
    use_repaired: bool = False,
    provider: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
    include_answer_key_evidence: bool = True,
) -> SemanticBindingResult:
    """Evaluate semantic binding from existing artifacts (no provider call by default)."""
    resolved = resolve_path(package_dir)
    out_dir = resolved / SEMANTIC_BINDING_DIR
    if use_repaired:
        output_path = out_dir / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME
        evaluation_path = out_dir / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME
        validation_path = out_dir / SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME
    else:
        output_path = out_dir / SEMANTIC_BOUND_QUESTIONS_NAME
        evaluation_path = out_dir / SEMANTIC_BINDING_EVALUATION_JSON_NAME
        validation_path = out_dir / SEMANTIC_BINDING_VALIDATION_NAME

    if not output_path.exists():
        if bind_if_missing:
            return bind_semantically_package(
                resolved,
                provider=provider,
                model=model,
                answer_mode=answer_mode,
                expected_count=expected_count,
                force=True,
                timeout_seconds=timeout_seconds,
                include_answer_key_evidence=include_answer_key_evidence,
            )
        hint = (
            "Run repair-semantic-binding first."
            if use_repaired
            else "Run bind-semantically first or pass --bind-if-missing."
        )
        raise SemanticBindingError(f"No semantic binding output at {output_path}. {hint}")

    from meritranker_data_ingestion.services.evidence_resolver import load_document_evidence

    try:
        evidence, evidence_path = load_document_evidence(resolved)
    except FileNotFoundError as exc:
        raise SemanticBindingError(
            f"{exc}. Run normalize-evidence first.",
        ) from exc

    return _run_eval_only(
        package_dir=resolved,
        evidence=evidence,
        evidence_path=evidence_path,
        output_path=output_path,
        out_dir=out_dir,
        evaluation_path=evaluation_path,
        validation_path=validation_path,
        expected_count=expected_count,
        answer_mode=answer_mode,
        thresholds=thresholds,
        write_evaluation_to_canonical=not use_repaired,
    )


def _write_evaluation(out_dir: Path, evaluation) -> None:
    (out_dir / SEMANTIC_BINDING_EVALUATION_JSON_NAME).write_text(
        evaluation.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (out_dir / SEMANTIC_BINDING_EVALUATION_MD_NAME).write_text(
        render_evaluation_md(evaluation),
        encoding="utf-8",
    )


def _write_artifacts(
    out_dir: Path,
    package: SemanticBindingPackage,
    validation: SemanticBindingValidationReport,
    evidence_hash: str,
    evaluation=None,
) -> None:
    output_path = out_dir / SEMANTIC_BOUND_QUESTIONS_NAME
    output_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")

    report = {
        "status": package.status.value,
        "binder_provider": package.binder_provider,
        "binder_model": package.binder_model,
        "answer_mode": package.answer_mode.value,
        "item_count": len(package.items),
        "validation_summary": validation.model_dump(),
        "warnings": package.warnings,
        "errors": package.errors,
    }
    (out_dir / SEMANTIC_BINDING_REPORT_NAME).write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    (out_dir / SEMANTIC_BINDING_VALIDATION_NAME).write_text(
        validation.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (out_dir / SEMANTIC_BINDING_SUMMARY_MD_NAME).write_text(
        _render_summary_md(package, validation),
        encoding="utf-8",
    )
    (out_dir / SEMANTIC_BINDING_CACHE_NAME).write_text(
        json.dumps(
            {
                "input_evidence_hash": evidence_hash,
                "binder_provider": package.binder_provider,
                "binder_model": package.binder_model,
                "answer_mode": package.answer_mode.value,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if evaluation is not None:
        _write_evaluation(out_dir, evaluation)


def _is_question_anchor(line) -> bool:
    if RoleHint.QUESTION_ANCHOR_CANDIDATE in line.role_hints:
        return True
    text = line.text_raw.strip()
    return bool(re.match(r"^\*\*\d+\.\*\*", text) or re.match(r"^\d+\.\s+\S", text))


def _chunk_lines_smart(
    lines: list,
    max_lines: int,
    *,
    answer_key_candidates: list[AnswerKeyEvidenceCandidate],
) -> tuple[list[list], list[dict[str, Any]]]:
    """Anchor-aware chunking that keeps question blocks intact when possible."""
    if max_lines <= 0 or not lines:
        return [lines], [{"chunk_type": "full", "line_start": 0, "line_end": len(lines)}]

    anchor_indices = [i for i, line in enumerate(lines) if _is_question_anchor(line)]
    if not anchor_indices:
        return _chunk_lines_fixed(lines, max_lines)

    blocks: list[list] = []
    block_meta: list[dict[str, Any]] = []
    for idx, start in enumerate(anchor_indices):
        end = anchor_indices[idx + 1] if idx + 1 < len(anchor_indices) else len(lines)
        block = lines[start:end]
        blocks.append(block)
        block_meta.append(
            {
                "chunk_type": "question_block",
                "line_start": start,
                "line_end": end - 1,
                "anchor_line_id": lines[start].line_id,
            },
        )

    chunks: list[list] = []
    chunk_meta: list[dict[str, Any]] = []
    current: list = []
    current_meta: dict[str, Any] = {"chunk_type": "merged_blocks", "block_count": 0}

    for block, meta in zip(blocks, block_meta, strict=True):
        if current and len(current) + len(block) > max_lines:
            chunks.append(current)
            chunk_meta.append({**current_meta, "line_count": len(current)})
            current = []
            current_meta = {"chunk_type": "merged_blocks", "block_count": 0}
        current.extend(block)
        current_meta["block_count"] = int(current_meta.get("block_count", 0)) + 1

    if current:
        chunks.append(current)
        chunk_meta.append({**current_meta, "line_count": len(current)})

    if answer_key_candidates:
        ak_line_ids = {c.source_line_id for c in answer_key_candidates}
        ak_lines = [line for line in lines if line.line_id in ak_line_ids]
        if ak_lines:
            chunks.append(ak_lines)
            chunk_meta.append(
                {
                    "chunk_type": "answer_key_evidence",
                    "line_count": len(ak_lines),
                    "answer_key_count": len(answer_key_candidates),
                },
            )

    return chunks, chunk_meta


def _assign_window_id(parsed, meta: dict[str, Any], windows_pkg) -> None:
    window_ids = meta.get("window_ids") or []
    if len(window_ids) == 1:
        parsed.window_id = window_ids[0]
        return
    if windows_pkg and parsed.question_number is not None:
        for window in windows_pkg.windows:
            if window.parsed_question_number == parsed.question_number:
                parsed.window_id = window.window_id
                return
    if window_ids and parsed.window_id is None:
        parsed.issues.append("window_id_unresolved")


def _chunk_lines_from_windows(
    line_by_id: dict,
    windows_pkg,
    max_lines: int,
    *,
    answer_key_candidates: list[AnswerKeyEvidenceCandidate],
) -> tuple[list[list], list[dict[str, Any]]]:
    """Build binder chunks from local question windows (Part 14C)."""
    chunks: list[list] = []
    chunk_meta: list[dict[str, Any]] = []
    batch: list = []
    batch_meta: dict[str, Any] = {"chunk_type": "question_windows", "window_ids": []}

    for window in windows_pkg.windows:
        window_lines = [line_by_id[lid] for lid in window.line_ids if lid in line_by_id]
        if not window_lines:
            continue
        if batch and len(batch) + len(window_lines) > max_lines:
            chunks.append(batch)
            chunk_meta.append({**batch_meta, "line_count": len(batch)})
            batch = []
            batch_meta = {"chunk_type": "question_windows", "window_ids": []}
        batch.extend(window_lines)
        batch_meta["window_ids"].append(window.window_id)

    if batch:
        chunks.append(batch)
        chunk_meta.append({**batch_meta, "line_count": len(batch)})

    if not chunks:
        all_lines = list(line_by_id.values())
        return _chunk_lines_smart(all_lines, max_lines, answer_key_candidates=answer_key_candidates)

    if answer_key_candidates:
        ak_line_ids = {c.source_line_id for c in answer_key_candidates}
        ak_lines = [line_by_id[lid] for lid in ak_line_ids if lid in line_by_id]
        if ak_lines:
            chunks.append(ak_lines)
            chunk_meta.append(
                {
                    "chunk_type": "answer_key_evidence",
                    "line_count": len(ak_lines),
                    "answer_key_count": len(answer_key_candidates),
                },
            )
    return chunks, chunk_meta


def _chunk_lines_fixed(lines: list, max_lines: int) -> tuple[list[list], list[dict[str, Any]]]:
    chunks = [lines[i : i + max_lines] for i in range(0, len(lines), max_lines)]
    meta = [
        {"chunk_type": "fixed_window", "line_start": i, "line_end": min(i + max_lines, len(lines)) - 1}
        for i in range(0, len(lines), max_lines)
    ]
    return chunks, meta


def _build_prompt(
    chunk_lines: list,
    evidence: DocumentEvidencePackage,
    *,
    answer_mode: SemanticBinderAnswerMode,
    answer_key_evidence: list[dict] | None = None,
    chunk_meta: dict[str, Any] | None = None,
) -> str:
    compact_lines = [
        {
            "line_id": line.line_id,
            "page_number": line.page_number,
            "text_raw": line.text_raw,
            "role_hints": [hint.value for hint in line.role_hints],
        }
        for line in chunk_lines
    ]
    page_numbers = [line.page_number for line in chunk_lines if line.page_number is not None]
    line_ids = [line.line_id for line in chunk_lines]
    nearby_images = [
        {
            "image_id": img.image_id,
            "asset_path": img.asset_path,
            "page_number": img.page_number,
        }
        for img in evidence.images[:30]
    ]
    instructions = (
        "Extract source-backed exam questions from EVIDENCE_LINES_JSON only.\n"
        "STRICT RULES:\n"
        "- Preserve source text exactly (question_text_raw, option text_raw, answer, solution).\n"
        "- Do NOT solve questions, guess answers, or invent options.\n"
        "- Do NOT rewrite or paraphrase source text.\n"
        "- Ignore ads/noise: Free Mock Test, Download PDF, Previous Papers, Telegram, YouTube, www links.\n"
        "- Extract metadata (exam, year, shift, section) only when explicitly in source lines.\n"
        "OPTION STYLES to recognize:\n"
        "- (a) text, a. text, - **A** text, **A** text, A text, multiline options under same question.\n"
        "ANSWER KEY STYLES (answer-key-only mode — solution may be absent):\n"
        "- 1.A, 1. A, 1 - A, 1) A, Q1 A, Answer Key: 1.A 2.B 3.C\n"
        "- Use ANSWER_KEY_EVIDENCE_JSON when provided; include source_spans with line_id.\n"
        "VISUAL: reference image_id/asset_path only if line indicates association. Do NOT interpret images.\n"
        "- Include source_spans with line_id for EVERY question, option, answer, solution, metadata field.\n"
        "- If uncertain: binding_status=review_required with issues.\n"
        f"- Answer mode: {answer_mode.value}.\n"
        "- Return JSON: {\"items\": [...], \"metadata_candidates\": [...]}.\n"
    )
    parts = [
        instructions,
        f"CHUNK_META_JSON:{json.dumps(chunk_meta or {}, ensure_ascii=False)}",
        f"CHUNK_LINE_RANGE:{line_ids[0] if line_ids else ''}..{line_ids[-1] if line_ids else ''}",
        f"CHUNK_PAGE_RANGE:{min(page_numbers) if page_numbers else ''}..{max(page_numbers) if page_numbers else ''}",
        f"EVIDENCE_LINES_JSON:{json.dumps(compact_lines, ensure_ascii=False)}",
        f"NEARBY_IMAGES_JSON:{json.dumps(nearby_images, ensure_ascii=False)}",
    ]
    if answer_key_evidence:
        parts.append(
            f"ANSWER_KEY_EVIDENCE_JSON:{json.dumps(answer_key_evidence, ensure_ascii=False)}",
        )
    return "\n".join(parts) + "\n"


def _enrich_answers_from_key_evidence(
    items: list[SemanticBoundQuestion],
    candidates: list[AnswerKeyEvidenceCandidate],
    *,
    warnings: list[str],
) -> None:
    """Post-bind enrichment from deterministic answer-key evidence (does not overwrite LLM answers)."""
    by_qnum = {c.question_number: c for c in candidates}
    enriched = 0
    for item in items:
        if item.question_number is None or item.answer.available:
            continue
        cand = by_qnum.get(item.question_number)
        if cand is None:
            continue
        item.answer = SemanticBoundAnswer(
            available=True,
            key=cand.answer_key,
            key_raw=cand.answer_key,
            answer_text_raw=cand.source_text_raw,
            source_spans=[
                SourceSpan(
                    extractor="marker",
                    line_id=cand.source_line_id,
                ),
            ],
            confidence=cand.confidence,
        )
        enriched += 1
    if enriched:
        warnings.append(f"answer_key_evidence_enriched_count={enriched}")


def _merge_chunk_items(
    raw_items: list[dict[str, Any]],
    *,
    warnings: list[str],
) -> list[SemanticBoundQuestion]:
    parsed = [_parse_question_item(raw) for raw in raw_items]
    by_number: dict[int, list[SemanticBoundQuestion]] = {}
    unnumbered: list[SemanticBoundQuestion] = []

    for item in parsed:
        if item.question_number is None:
            unnumbered.append(item)
            continue
        by_number.setdefault(item.question_number, []).append(item)

    merged: list[SemanticBoundQuestion] = []
    for qnum, group in sorted(by_number.items()):
        if len(group) == 1:
            if group[0].chunk_id is None and group[0].semantic_question_id:
                pass
            merged.append(group[0])
            continue
        primary = group[0]
        if primary.chunk_id is None:
            for other in group[1:]:
                if other.chunk_id:
                    primary.chunk_id = other.chunk_id
                    break
        for other in group[1:]:
            if _items_consistent(primary, other):
                primary.options.extend(other.options)
                primary.source_spans.extend(other.source_spans)
            else:
                primary.issues.append("duplicate_question_number")
                primary.binding_status = SemanticBindingItemStatus.REVIEW_REQUIRED
                if f"duplicate_question_number:{qnum}" not in warnings:
                    warnings.append(f"duplicate_question_number:{qnum}")
        merged.append(primary)

    merged.extend(unnumbered)
    for idx, item in enumerate(merged, start=1):
        if not item.semantic_question_id:
            item.semantic_question_id = f"sq_{idx:04d}"
    return merged


def _items_consistent(a: SemanticBoundQuestion, b: SemanticBoundQuestion) -> bool:
    return normalize_text(a.question_text_raw) == normalize_text(b.question_text_raw)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _parse_question_item(raw: dict[str, Any]) -> SemanticBoundQuestion:
    options = [
        SemanticBoundOption(
            key=str(opt.get("key") or "").upper(),
            key_raw=str(opt.get("key_raw") or opt.get("key") or ""),
            text_raw=str(opt.get("text_raw") or ""),
            asset_refs=list(opt.get("asset_refs") or []),
            source_spans=_parse_spans(opt.get("source_spans")),
            confidence=float(opt.get("confidence") or 0.0),
            issues=list(opt.get("issues") or []),
        )
        for opt in raw.get("options") or []
        if isinstance(opt, dict)
    ]
    answer_raw = raw.get("answer") if isinstance(raw.get("answer"), dict) else {}
    solution_raw = raw.get("solution") if isinstance(raw.get("solution"), dict) else {}
    visuals = [
        SemanticVisualReference(
            asset_path=v.get("asset_path"),
            figure_id=v.get("figure_id"),
            image_id=v.get("image_id"),
            role_hint=_parse_visual_role(v.get("role_hint")),
            option_key=v.get("option_key"),
            source_spans=_parse_spans(v.get("source_spans")),
            confidence=float(v.get("confidence") or 0.0),
            issues=list(v.get("issues") or []),
        )
        for v in raw.get("visual_references") or []
        if isinstance(v, dict)
    ]

    return SemanticBoundQuestion(
        semantic_question_id=str(raw.get("semantic_question_id") or ""),
        question_number=_as_int(raw.get("question_number")),
        question_number_raw=raw.get("question_number_raw"),
        question_text_raw=str(raw.get("question_text_raw") or ""),
        raw_text=str(raw.get("raw_text") or raw.get("question_text_raw") or ""),
        options=options,
        answer=SemanticBoundAnswer(
            available=bool(answer_raw.get("available")),
            key=(str(answer_raw["key"]).upper() if answer_raw.get("key") else None),
            key_raw=answer_raw.get("key_raw"),
            answer_text_raw=answer_raw.get("answer_text_raw"),
            source_spans=_parse_spans(answer_raw.get("source_spans")),
            confidence=float(answer_raw.get("confidence") or 0.0),
            issues=list(answer_raw.get("issues") or []),
        ),
        solution=SemanticBoundSolution(
            available=bool(solution_raw.get("available")),
            text_raw=solution_raw.get("text_raw"),
            source_spans=_parse_spans(solution_raw.get("source_spans")),
            confidence=float(solution_raw.get("confidence") or 0.0),
            issues=list(solution_raw.get("issues") or []),
        ),
        visual_references=visuals,
        section=raw.get("section"),
        subject=raw.get("subject"),
        metadata_refs=list(raw.get("metadata_refs") or []),
        source_spans=_parse_spans(raw.get("source_spans")),
        confidence=float(raw.get("confidence") or 0.0),
        binding_status=_parse_binding_status(raw.get("binding_status")),
        issues=list(raw.get("issues") or []),
        chunk_id=raw.get("chunk_id"),
    )


def _parse_metadata(raw_items: list[dict[str, Any]]) -> list[SemanticMetadataCandidate]:
    out: list[SemanticMetadataCandidate] = []
    for raw in raw_items:
        out.append(
            SemanticMetadataCandidate(
                key_hint=str(raw.get("key_hint") or "metadata"),
                value_raw=str(raw.get("value_raw") or ""),
                source_spans=_parse_spans(raw.get("source_spans")),
                confidence=float(raw.get("confidence") or 0.0),
                issues=list(raw.get("issues") or []),
            ),
        )
    return out


def _parse_spans(raw: Any) -> list[SourceSpan]:
    if not isinstance(raw, list):
        return []
    spans: list[SourceSpan] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        spans.append(
            SourceSpan(
                extractor=str(item.get("extractor") or "marker"),
                page_number=item.get("page_number"),
                line_id=item.get("line_id"),
                source_artifact_path=item.get("source_artifact_path"),
                raw_index=item.get("raw_index"),
                bbox=item.get("bbox"),
            ),
        )
    return spans


def _parse_binding_status(value: Any) -> SemanticBindingItemStatus:
    try:
        return SemanticBindingItemStatus(value)
    except (ValueError, TypeError):
        return SemanticBindingItemStatus.REVIEW_REQUIRED


def _parse_visual_role(value: Any) -> SemanticVisualRoleHint:
    try:
        return SemanticVisualRoleHint(value)
    except (ValueError, TypeError):
        return SemanticVisualRoleHint.UNKNOWN


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _render_summary_md(
    package: SemanticBindingPackage,
    validation: SemanticBindingValidationReport,
) -> str:
    lines = [
        "# Semantic Binding Summary",
        "",
        f"- Status: `{package.status.value}`",
        f"- Provider: `{package.binder_provider}` / `{package.binder_model}`",
        f"- Answer mode: `{package.answer_mode.value}`",
        f"- Total items: {validation.total_items}",
        f"- Accepted: {validation.accepted_count}",
        f"- Review required: {validation.review_required_count}",
        f"- Rejected: {validation.rejected_count}",
        f"- Hallucination suspected: {validation.hallucination_suspected_count}",
        "",
    ]
    if package.warnings:
        lines.append("## Warnings")
        lines.extend(f"- {w}" for w in package.warnings)
        lines.append("")
    if package.errors:
        lines.append("## Errors")
        lines.extend(f"- {e}" for e in package.errors)
        lines.append("")
    return "\n".join(lines)
