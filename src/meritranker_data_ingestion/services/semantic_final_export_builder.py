"""Build semantic final export packages (Part 13H)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import (
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME,
    SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME,
    SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
    SEMANTIC_FINAL_DIR,
    SEMANTIC_FINAL_GATE_REPORT_NAME,
    SEMANTIC_FINAL_GATE_SUMMARY_MD_NAME,
    SEMANTIC_FINAL_PACKAGE_VERSION,
    SEMANTIC_FINAL_PATCH_APPLIED_NAME,
    SEMANTIC_FINAL_PATCH_TEMPLATE_NAME,
    SEMANTIC_FINAL_QUESTIONS_NAME,
    SEMANTIC_FINAL_REPORT_NAME,
    SEMANTIC_FINAL_REVIEW_ITEMS_JSON_NAME,
    SEMANTIC_FINAL_SUMMARY_MD_NAME,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingPackage,
    SemanticBoundQuestion,
)
from meritranker_data_ingestion.schemas.semantic_final_export import (
    AnswerSourceKind,
    FinalGateStatus,
    ProvenanceKind,
    SemanticAppliedPatchItem,
    SemanticFinalExportMode,
    SemanticFinalExportPackage,
    SemanticFinalExportStatus,
    SemanticFinalGateReport,
    SemanticFinalOption,
    SemanticFinalQuestionItem,
    SemanticFinalSourceTrace,
    SemanticFinalVisualReference,
    SemanticReviewPatchAppliedReport,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.semantic_final_acceptance_gate import (
    FinalGateStatus as GateStatusEnum,
    evaluate_final_acceptance_gate,
)
from meritranker_data_ingestion.services.semantic_final_quality import (
    compute_count_reconciliation,
    derive_final_export_quality,
    is_extra_item,
)
from meritranker_data_ingestion.services.semantic_key_normalizer import (
    canonical_option_keys,
    normalize_answer_key,
)


class SemanticFinalExportError(Exception):
    """Raised when semantic final export cannot proceed."""


def _dedupe_issues(issues: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            out.append(issue)
    return out


@dataclass
class SemanticFinalExportResult:
    package: SemanticFinalExportPackage
    questions_path: Path
    report_path: Path
    summary_path: Path
    gate_report_path: Path | None = None


def build_semantic_final_export(
    package_dir: Path,
    *,
    export_mode: SemanticFinalExportMode = SemanticFinalExportMode.ACCEPTED_ONLY,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
) -> SemanticFinalExportResult:
    """Build final export JSON from repaired semantic binding artifacts."""
    resolved = resolve_path(package_dir)
    sem_dir = resolved / SEMANTIC_BINDING_DIR
    final_dir = resolved / SEMANTIC_FINAL_DIR
    final_dir.mkdir(parents=True, exist_ok=True)

    repaired_path = sem_dir / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME
    if not repaired_path.exists():
        raise SemanticFinalExportError(
            f"Missing repaired semantic package at {repaired_path}. Run repair-semantic-binding first.",
        )

    binding = SemanticBindingPackage.model_validate_json(repaired_path.read_text(encoding="utf-8"))
    evaluation = _load_evaluation(sem_dir / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME)
    expected_count = evaluation.get("expected_count")
    applied_patches = _load_applied_patches(final_dir / SEMANTIC_FINAL_PATCH_APPLIED_NAME)
    reconciliation = compute_count_reconciliation(
        binding,
        expected_count=expected_count,
        evaluation=evaluation,
    )

    gate_counts = {
        GateStatusEnum.ACCEPTED_SAFE: 0,
        GateStatusEnum.REVIEW_VISUAL_REQUIRED: 0,
        GateStatusEnum.REVIEW_EVIDENCE_CORRUPT: 0,
        GateStatusEnum.REVIEW_MANUAL_PATCH_REQUIRED: 0,
        GateStatusEnum.BLOCKED_BAD_ITEM: 0,
    }
    unsafe_previously_accepted: list[int] = []
    export_candidates: list[SemanticFinalQuestionItem] = []
    patched_items: list[SemanticFinalQuestionItem] = []
    all_items: list[SemanticFinalQuestionItem] = []
    excluded_count = 0
    extra_excluded_count = 0
    quarantined_count = 0
    bad_item_count = 0

    patched_by_qnum = {
        item.question_number: item
        for item in applied_patches
        if item.final_item is not None and item.applied
    }

    for bound in binding.items:
        gate = evaluate_final_acceptance_gate(
            bound,
            answer_mode=answer_mode,
            expected_count=expected_count,
        )
        final_item = convert_bound_to_final(bound, manual=False)
        final_item.final_gate_status = gate.status.value
        final_item.final_gate_reasons = list(gate.reasons)
        final_item.issues = _dedupe_issues(final_item.issues + gate.gate_issues)
        gate_counts[gate.status] = gate_counts.get(gate.status, 0) + 1
        all_items.append(final_item)

        if (
            bound.binding_status == SemanticBindingItemStatus.ACCEPTED
            and gate.status != GateStatusEnum.ACCEPTED_SAFE
            and bound.question_number is not None
        ):
            unsafe_previously_accepted.append(bound.question_number)

        if bound.quarantine_status == "quarantined" or bound.excluded_from_export:
            quarantined_count += 1
            if bound.bad_item_classes:
                bad_item_count += 1

        if gate.status == GateStatusEnum.ACCEPTED_SAFE:
            final_item.export_status = SemanticFinalExportStatus.READY_FOR_PATTERN_INPUT
            if export_mode in (
                SemanticFinalExportMode.ACCEPTED_ONLY,
                SemanticFinalExportMode.ACCEPTED_PLUS_PATCHED,
                SemanticFinalExportMode.ALL_WITH_STATUS,
            ):
                export_candidates.append(final_item)
        elif gate.status == GateStatusEnum.BLOCKED_BAD_ITEM:
            final_item.export_status = SemanticFinalExportStatus.BLOCKED
            excluded_count += 1
            if is_extra_item(bound, expected_count=expected_count):
                extra_excluded_count += 1
        else:
            final_item.export_status = SemanticFinalExportStatus.HOLD_FOR_REVIEW
            excluded_count += 1

        qnum = bound.question_number
        if (
            export_mode == SemanticFinalExportMode.ACCEPTED_PLUS_PATCHED
            and qnum is not None
            and qnum in patched_by_qnum
        ):
            patched = patched_by_qnum[qnum]
            patched.final_gate_status = FinalGateStatus.ACCEPTED_SAFE.value
            patched_items.append(patched)

    if export_mode == SemanticFinalExportMode.ACCEPTED_ONLY:
        export_items = export_candidates
    elif export_mode == SemanticFinalExportMode.ACCEPTED_PLUS_PATCHED:
        patched_qnums = {item.question_number for item in patched_items}
        export_items = export_candidates + [
            item for item in patched_items if item.question_number not in {
                a.question_number for a in export_candidates
            }
        ]
    else:
        export_items = all_items

    review_count = int(evaluation.get("review_required_count") or 0)
    rejected_count = int(evaluation.get("rejected_count") or 0)

    if export_mode == SemanticFinalExportMode.ACCEPTED_PLUS_PATCHED:
        patched_exported = sum(
            1 for item in export_items if ProvenanceKind.MANUAL_PATCH.value in item.provenance
        )
        accepted_exported = len(export_items) - patched_exported
    elif export_mode == SemanticFinalExportMode.ACCEPTED_ONLY:
        patched_exported = 0
        accepted_exported = len(export_items)
    else:
        patched_exported = sum(
            1 for item in export_items if ProvenanceKind.MANUAL_PATCH.value in item.provenance
        )
        accepted_exported = sum(
            1 for item in export_items
            if item.export_status == SemanticFinalExportStatus.READY_FOR_PATTERN_INPUT
            and ProvenanceKind.MANUAL_PATCH.value not in item.provenance
        )

    accepted_safe_count = gate_counts.get(GateStatusEnum.ACCEPTED_SAFE, 0)
    quality_result = derive_final_export_quality(
        evaluation,
        reconciliation,
        accepted_exported_count=accepted_exported,
        excluded_count=excluded_count,
        accepted_safe_exported_count=accepted_exported,
    )

    ready_for_full = (
        accepted_safe_count == (expected_count or len(binding.items))
        and reconciliation.count_match
        and excluded_count == 0
        and quality_result.final_export_quality_status == "passed"
    )
    ready_for_partial = accepted_exported > 0 and accepted_exported == accepted_safe_count

    gate_report = SemanticFinalGateReport(
        total_semantic_items=len(binding.items),
        accepted_safe_count=accepted_safe_count,
        review_visual_required_count=gate_counts.get(GateStatusEnum.REVIEW_VISUAL_REQUIRED, 0),
        review_evidence_corrupt_count=gate_counts.get(GateStatusEnum.REVIEW_EVIDENCE_CORRUPT, 0),
        review_manual_patch_required_count=gate_counts.get(
            GateStatusEnum.REVIEW_MANUAL_PATCH_REQUIRED,
            0,
        ),
        blocked_bad_item_count=gate_counts.get(GateStatusEnum.BLOCKED_BAD_ITEM, 0),
        unsafe_previously_accepted_count=len(unsafe_previously_accepted),
        unsafe_previously_accepted_question_numbers=sorted(unsafe_previously_accepted),
        exported_count=len(export_items),
        excluded_count=excluded_count,
        ready_for_full_paper_ingestion=ready_for_full,
        ready_for_partial_accepted_ingestion=ready_for_partial,
    )

    review_items_path = final_dir / SEMANTIC_FINAL_REVIEW_ITEMS_JSON_NAME
    patch_template_path = final_dir / SEMANTIC_FINAL_PATCH_TEMPLATE_NAME
    validation_path = sem_dir / SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME

    export_package = SemanticFinalExportPackage(
        package_version=SEMANTIC_FINAL_PACKAGE_VERSION,
        source_file_name=binding.source_file_name,
        answer_mode=answer_mode,
        export_mode=export_mode,
        expected_count=expected_count,
        total_semantic_items=len(binding.items),
        semantic_item_count=len(binding.items),
        count_match=reconciliation.count_match,
        accepted_count=int(evaluation.get("accepted_count") or 0),
        accepted_safe_count=accepted_safe_count,
        unsafe_previously_accepted_count=len(unsafe_previously_accepted),
        unsafe_previously_accepted_question_numbers=sorted(unsafe_previously_accepted),
        review_visual_required_count=gate_report.review_visual_required_count,
        review_evidence_corrupt_count=gate_report.review_evidence_corrupt_count,
        review_manual_patch_required_count=gate_report.review_manual_patch_required_count,
        blocked_bad_item_count=gate_report.blocked_bad_item_count,
        exported_count=len(export_items),
        accepted_exported_count=accepted_exported,
        patched_exported_count=patched_exported,
        review_required_count=review_count,
        rejected_count=rejected_count,
        excluded_count=excluded_count,
        bad_item_count=bad_item_count,
        quarantined_item_count=quarantined_count,
        extra_excluded_count=extra_excluded_count,
        hallucination_suspected_count=int(evaluation.get("hallucination_suspected_count") or 0),
        source_span_missing_count=int(evaluation.get("source_span_missing_count") or 0),
        answer_key_not_in_options_count=int(
            evaluation.get("answer_key_not_in_options_count") or 0,
        ),
        missing_question_numbers=list(evaluation.get("missing_question_numbers") or []),
        duplicate_question_numbers=list(evaluation.get("duplicate_question_numbers") or []),
        overflow_count=reconciliation.overflow_count,
        extra_item_ids=reconciliation.extra_item_ids,
        extra_question_numbers=reconciliation.extra_question_numbers,
        non_numeric_question_ids=reconciliation.non_numeric_question_ids,
        out_of_range_question_numbers=reconciliation.out_of_range_question_numbers,
        quality_status_from_semantic_evaluation=quality_result.quality_status_from_semantic_evaluation,
        final_export_quality_status=quality_result.final_export_quality_status,
        quality_status=quality_result.final_export_quality_status,
        source_quality_status=quality_result.source_quality_status,
        ready_for_full_paper_ingestion=ready_for_full,
        ready_for_partial_accepted_ingestion=ready_for_partial,
        items=export_items,
        review_items_path=str(review_items_path) if review_items_path.exists() else None,
        patch_template_path=str(patch_template_path) if patch_template_path.exists() else None,
        validation_report_path=str(validation_path) if validation_path.exists() else None,
        warnings=list(quality_result.warnings),
        errors=list(quality_result.errors),
    )

    _validate_counts(export_package)

    questions_path = final_dir / SEMANTIC_FINAL_QUESTIONS_NAME
    report_path = final_dir / SEMANTIC_FINAL_REPORT_NAME
    summary_path = final_dir / SEMANTIC_FINAL_SUMMARY_MD_NAME
    gate_report_path = final_dir / SEMANTIC_FINAL_GATE_REPORT_NAME
    gate_summary_path = final_dir / SEMANTIC_FINAL_GATE_SUMMARY_MD_NAME

    questions_path.write_text(export_package.model_dump_json(indent=2), encoding="utf-8")
    report_payload = _build_report_payload(export_package, export_mode)
    report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    summary_path.write_text(_render_summary_md(export_package), encoding="utf-8")
    gate_report_path.write_text(gate_report.model_dump_json(indent=2), encoding="utf-8")
    gate_summary_path.write_text(_render_gate_summary_md(gate_report, export_package), encoding="utf-8")

    return SemanticFinalExportResult(
        package=export_package,
        questions_path=questions_path,
        report_path=report_path,
        summary_path=summary_path,
        gate_report_path=gate_report_path,
    )


def convert_bound_to_final(
    bound: SemanticBoundQuestion,
    *,
    manual: bool = False,
    reviewer_notes: str | None = None,
    manual_source_reference: str | None = None,
) -> SemanticFinalQuestionItem:
    """Convert a semantic bound question to final export item."""
    options = [
        SemanticFinalOption(
            key=opt.key,
            key_raw=opt.key_raw,
            text_raw=opt.text_raw,
            linked_asset_refs=list(opt.asset_refs),
            source_spans=list(opt.source_spans),
            confidence=opt.confidence,
            issues=list(opt.issues),
        )
        for opt in bound.options
    ]

    answer_key, _ = normalize_answer_key(bound.answer.key or bound.answer.key_raw)
    correct_answer_text = _answer_text_from_options(answer_key, bound)
    issues = list(bound.issues)

    if answer_key and not correct_answer_text:
        issues.append("correct_answer_text_unavailable")

    if manual:
        answer_source = AnswerSourceKind.MANUAL_REVIEW
        provenance = [
            ProvenanceKind.SEMANTIC_BINDING.value,
            ProvenanceKind.MANUAL_PATCH.value,
        ]
    elif bound.answer.available and bound.answer.source_spans:
        answer_source = AnswerSourceKind.SOURCE_GROUNDED
        provenance = [
            ProvenanceKind.SEMANTIC_BINDING.value,
            ProvenanceKind.SEMANTIC_REPAIR.value,
        ]
    else:
        answer_source = AnswerSourceKind.UNAVAILABLE
        provenance = [ProvenanceKind.SEMANTIC_BINDING.value]

    if bound.binding_status == SemanticBindingItemStatus.ACCEPTED:
        export_status = SemanticFinalExportStatus.READY_FOR_PATTERN_INPUT
    elif bound.binding_status == SemanticBindingItemStatus.REJECTED:
        export_status = SemanticFinalExportStatus.BLOCKED
    else:
        export_status = SemanticFinalExportStatus.HOLD_FOR_REVIEW

    metadata: dict[str, str] = {}
    if bound.section:
        metadata["section"] = bound.section
    if bound.subject:
        metadata["subject"] = bound.subject
    if manual_source_reference:
        metadata["manual_source_reference"] = manual_source_reference

    return SemanticFinalQuestionItem(
        final_question_id=f"fq_{bound.semantic_question_id}",
        question_number=bound.question_number,
        question_number_raw=bound.question_number_raw,
        question_text_raw=bound.question_text_raw,
        raw_text=bound.raw_text,
        options=options,
        correct_answer_key=answer_key,
        correct_answer_text=correct_answer_text,
        answer_source=answer_source,
        solution_text_raw=bound.solution.text_raw if bound.solution.available else None,
        solution_available=bound.solution.available,
        visual_references=[
            SemanticFinalVisualReference(
                asset_path=vis.asset_path,
                figure_id=vis.figure_id,
                image_id=vis.image_id,
                role_hint=vis.role_hint.value,
                option_key=vis.option_key,
                source_spans=list(vis.source_spans),
            )
            for vis in bound.visual_references
        ],
        metadata=metadata,
        source_trace=SemanticFinalSourceTrace(
            question_line_ids=[s.line_id for s in bound.source_spans if s.line_id],
            answer_line_ids=[s.line_id for s in bound.answer.source_spans if s.line_id],
            solution_line_ids=[s.line_id for s in bound.solution.source_spans if s.line_id],
            provenance=provenance,
        ),
        semantic_status=bound.binding_status.value,
        export_status=export_status,
        provenance=provenance,
        confidence=bound.confidence,
        issues=issues,
        reviewer_notes=reviewer_notes,
    )


def convert_patch_to_final(
    bound: SemanticBoundQuestion,
    patch_item,
    *,
    reviewer_notes: str,
    manual_source_reference: str,
) -> SemanticFinalQuestionItem:
    """Build final item from manual patch overrides."""
    merged = bound.model_copy(deep=True)
    if patch_item.question_text_raw:
        merged.question_text_raw = patch_item.question_text_raw
        merged.raw_text = patch_item.question_text_raw
    if patch_item.options:
        from meritranker_data_ingestion.schemas.semantic_binding import SemanticBoundOption

        merged.options = [
            SemanticBoundOption(
                key=opt.key,
                key_raw=opt.key_raw or opt.key,
                text_raw=opt.text_raw,
                asset_refs=list(opt.linked_asset_refs),
            )
            for opt in patch_item.options
        ]
    if patch_item.correct_answer_key:
        merged.answer.key = patch_item.correct_answer_key
        merged.answer.key_raw = patch_item.correct_answer_key
        merged.answer.available = True
    if patch_item.solution_text_raw:
        merged.solution.text_raw = patch_item.solution_text_raw
        merged.solution.available = True

    item = convert_bound_to_final(
        merged,
        manual=True,
        reviewer_notes=reviewer_notes,
        manual_source_reference=manual_source_reference,
    )
    item.export_status = SemanticFinalExportStatus.READY_FOR_PATTERN_INPUT
    item.semantic_status = "manual_patch_accepted"
    return item


def _answer_text_from_options(answer_key: str | None, bound: SemanticBoundQuestion) -> str | None:
    if not answer_key:
        return None
    canonical_keys = canonical_option_keys(bound)
    if answer_key not in canonical_keys:
        return None
    for opt in bound.options:
        canon, _ = normalize_answer_key(opt.key or opt.key_raw)
        if canon == answer_key and opt.text_raw.strip():
            return opt.text_raw.strip()
    return None


def _load_evaluation(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _build_report_payload(
    pkg: SemanticFinalExportPackage,
    export_mode: SemanticFinalExportMode,
) -> dict:
    return {
        "export_mode": export_mode.value,
        "expected_count": pkg.expected_count,
        "total_semantic_items": pkg.total_semantic_items,
        "semantic_item_count": pkg.semantic_item_count,
        "count_match": pkg.count_match,
        "accepted_count": pkg.accepted_count,
        "accepted_safe_count": pkg.accepted_safe_count,
        "unsafe_previously_accepted_count": pkg.unsafe_previously_accepted_count,
        "unsafe_previously_accepted_question_numbers": pkg.unsafe_previously_accepted_question_numbers,
        "review_visual_required_count": pkg.review_visual_required_count,
        "review_evidence_corrupt_count": pkg.review_evidence_corrupt_count,
        "review_manual_patch_required_count": pkg.review_manual_patch_required_count,
        "blocked_bad_item_count": pkg.blocked_bad_item_count,
        "exported_count": pkg.exported_count,
        "accepted_exported_count": pkg.accepted_exported_count,
        "patched_exported_count": pkg.patched_exported_count,
        "review_required_count": pkg.review_required_count,
        "rejected_count": pkg.rejected_count,
        "excluded_count": pkg.excluded_count,
        "bad_item_count": pkg.bad_item_count,
        "quarantined_item_count": pkg.quarantined_item_count,
        "extra_excluded_count": pkg.extra_excluded_count,
        "hallucination_suspected_count": pkg.hallucination_suspected_count,
        "source_span_missing_count": pkg.source_span_missing_count,
        "answer_key_not_in_options_count": pkg.answer_key_not_in_options_count,
        "missing_question_numbers": pkg.missing_question_numbers,
        "duplicate_question_numbers": pkg.duplicate_question_numbers,
        "overflow_count": pkg.overflow_count,
        "extra_item_ids": pkg.extra_item_ids,
        "extra_question_numbers": pkg.extra_question_numbers,
        "non_numeric_question_ids": pkg.non_numeric_question_ids,
        "out_of_range_question_numbers": pkg.out_of_range_question_numbers,
        "quality_status_from_semantic_evaluation": pkg.quality_status_from_semantic_evaluation,
        "final_export_quality_status": pkg.final_export_quality_status,
        "quality_status": pkg.quality_status,
        "source_quality_status": pkg.source_quality_status,
        "ready_for_full_paper_ingestion": pkg.ready_for_full_paper_ingestion,
        "ready_for_partial_accepted_ingestion": pkg.ready_for_partial_accepted_ingestion,
        "warnings": pkg.warnings,
        "errors": pkg.errors,
    }


def _load_applied_patches(path: Path) -> list[SemanticAppliedPatchItem]:
    if not path.exists():
        return []
    report = SemanticReviewPatchAppliedReport.model_validate_json(path.read_text(encoding="utf-8"))
    return report.items


def _validate_counts(pkg: SemanticFinalExportPackage) -> None:
    if pkg.export_mode == SemanticFinalExportMode.ALL_WITH_STATUS:
        if pkg.exported_count != pkg.total_semantic_items:
            pkg.warnings.append(
                f"all-with-status count mismatch: exported={pkg.exported_count} total={pkg.total_semantic_items}",
            )
        return

    expected_excluded = pkg.review_required_count + pkg.rejected_count - pkg.patched_exported_count
    if expected_excluded < 0:
        expected_excluded = 0
    if pkg.excluded_count < expected_excluded:
        pkg.warnings.append(
            f"excluded_count={pkg.excluded_count} expected_at_least={expected_excluded}",
        )

    if pkg.exported_count != pkg.accepted_exported_count + pkg.patched_exported_count:
        pkg.errors.append(
            "exported_count must equal accepted_exported_count + patched_exported_count",
        )


def _render_summary_md(pkg: SemanticFinalExportPackage) -> str:
    lines = [
        "# Semantic Final Export Summary",
        "",
        f"- source_file_name: {pkg.source_file_name}",
        f"- export_mode: {pkg.export_mode.value}",
        f"- expected_count: {pkg.expected_count}",
        f"- semantic_item_count: {pkg.semantic_item_count}",
        f"- count_match: {pkg.count_match}",
        f"- accepted_count: {pkg.accepted_count}",
        f"- accepted_safe_count: {pkg.accepted_safe_count}",
        f"- unsafe_previously_accepted_count: {pkg.unsafe_previously_accepted_count}",
        f"- exported_count: {pkg.exported_count}",
        f"- accepted_exported_count: {pkg.accepted_exported_count}",
        f"- excluded_count: {pkg.excluded_count}",
        f"- hallucination_suspected_count: {pkg.hallucination_suspected_count}",
        f"- quality_status_from_semantic_evaluation: {pkg.quality_status_from_semantic_evaluation}",
        f"- final_export_quality_status: {pkg.final_export_quality_status}",
        f"- ready_for_full_paper_ingestion: {pkg.ready_for_full_paper_ingestion}",
        f"- ready_for_partial_accepted_ingestion: {pkg.ready_for_partial_accepted_ingestion}",
        "",
        "## Notes",
        "- Accepted-only export writes accepted_safe items only (strict gate).",
        "- Full paper is not ingestion-safe when quality_status is failed or count_match is false.",
        "- Manual patches require reviewer_notes and confirm_no_guessing.",
    ]
    if pkg.errors:
        lines.extend(["", "## Errors", *[f"- {e}" for e in pkg.errors]])
    if pkg.warnings:
        lines.extend(["", "## Warnings", *[f"- {w}" for w in pkg.warnings]])
    return "\n".join(lines) + "\n"


def _render_gate_summary_md(gate: SemanticFinalGateReport, pkg: SemanticFinalExportPackage) -> str:
    lines = [
        "# Semantic Final Gate Summary",
        "",
        f"- total_semantic_items: {gate.total_semantic_items}",
        f"- accepted_safe_count: {gate.accepted_safe_count}",
        f"- unsafe_previously_accepted_count: {gate.unsafe_previously_accepted_count}",
        f"- review_visual_required_count: {gate.review_visual_required_count}",
        f"- review_evidence_corrupt_count: {gate.review_evidence_corrupt_count}",
        f"- review_manual_patch_required_count: {gate.review_manual_patch_required_count}",
        f"- blocked_bad_item_count: {gate.blocked_bad_item_count}",
        f"- exported_count: {gate.exported_count}",
        f"- excluded_count: {gate.excluded_count}",
        f"- ready_for_full_paper_ingestion: {gate.ready_for_full_paper_ingestion}",
        f"- ready_for_partial_accepted_ingestion: {gate.ready_for_partial_accepted_ingestion}",
        "",
    ]
    if gate.unsafe_previously_accepted_question_numbers:
        nums = ", ".join(str(n) for n in gate.unsafe_previously_accepted_question_numbers[:30])
        lines.append(f"- unsafe_previously_accepted_question_numbers: {nums}")
        lines.append("")
    lines.append(f"- source_file_name: {pkg.source_file_name}")
    return "\n".join(lines) + "\n"
