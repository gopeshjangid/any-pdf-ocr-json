"""Apply human-reviewed semantic patches (Part 13H)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
    SEMANTIC_FINAL_DIR,
    SEMANTIC_FINAL_PATCH_APPLIED_NAME,
    SEMANTIC_FINAL_PATCH_REPORT_NAME,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingPackage,
)
from meritranker_data_ingestion.schemas.semantic_final_export import (
    PatchAction,
    SemanticAppliedPatchItem,
    SemanticReviewPatchAppliedReport,
    SemanticReviewPatchFile,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.semantic_final_export_builder import convert_patch_to_final


class SemanticReviewPatchError(Exception):
    """Raised when patch application fails."""


@dataclass
class SemanticReviewPatchResult:
    report: SemanticReviewPatchAppliedReport
    applied_path: Path
    report_path: Path


def apply_semantic_review_patch(
    package_dir: Path,
    patch_path: Path,
    *,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    allow_accepted_item_patch: bool = False,
) -> SemanticReviewPatchResult:
    """Validate and apply human-reviewed patch file."""
    resolved = resolve_path(package_dir)
    patch_resolved = resolve_path(patch_path)
    if not patch_resolved.is_file():
        raise SemanticReviewPatchError(f"Patch file not found: {patch_resolved}")
    try:
        patch_resolved.relative_to(resolved)
    except ValueError as exc:
        raise SemanticReviewPatchError(
            f"Patch file must be inside package directory: {resolved}",
        ) from exc

    sem_dir = resolved / SEMANTIC_BINDING_DIR
    final_dir = resolved / SEMANTIC_FINAL_DIR
    final_dir.mkdir(parents=True, exist_ok=True)

    repaired_path = sem_dir / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME
    if not repaired_path.exists():
        raise SemanticReviewPatchError(f"Missing repaired package at {repaired_path}.")

    binding = SemanticBindingPackage.model_validate_json(repaired_path.read_text(encoding="utf-8"))
    patch_file = SemanticReviewPatchFile.model_validate_json(patch_resolved.read_text(encoding="utf-8"))

    by_qnum = {item.question_number: item for item in binding.items if item.question_number is not None}
    by_patch_id = {item.semantic_question_id: item for item in binding.items}

    applied_items: list[SemanticAppliedPatchItem] = []
    applied_count = 0
    blocked_count = 0
    hold_count = 0
    rejected_patch_count = 0
    errors: list[str] = []
    warnings: list[str] = []

    for patch_item in patch_file.patch_items:
        bound = None
        if patch_item.question_number is not None:
            bound = by_qnum.get(patch_item.question_number)
        if bound is None:
            bound = by_patch_id.get(patch_item.patch_id)

        result = SemanticAppliedPatchItem(
            patch_id=patch_item.patch_id,
            question_number=patch_item.question_number,
            action=patch_item.action,
        )

        if bound is None:
            result.errors.append("target_question_not_found")
            rejected_patch_count += 1
            applied_items.append(result)
            continue

        if (
            bound.binding_status == SemanticBindingItemStatus.ACCEPTED
            and not allow_accepted_item_patch
        ):
            result.errors.append("cannot_patch_accepted_item_without_flag")
            rejected_patch_count += 1
            applied_items.append(result)
            continue

        if patch_item.action == PatchAction.BLOCK:
            result.applied = True
            blocked_count += 1
            applied_items.append(result)
            continue

        if patch_item.action == PatchAction.HOLD_FOR_REVIEW:
            result.applied = True
            hold_count += 1
            applied_items.append(result)
            continue

        if patch_item.action == PatchAction.ACCEPT_WITH_MANUAL_PATCH:
            validation_errors = _validate_manual_patch(patch_item)
            if validation_errors:
                result.errors.extend(validation_errors)
                rejected_patch_count += 1
                applied_items.append(result)
                continue

            final_item = convert_patch_to_final(
                bound,
                patch_item,
                reviewer_notes=patch_item.reviewer_notes.strip(),
                manual_source_reference=patch_item.manual_source_reference.strip(),
            )
            result.final_item = final_item
            result.applied = True
            result.reviewer_notes = patch_item.reviewer_notes
            result.manual_source_reference = patch_item.manual_source_reference
            applied_count += 1
            applied_items.append(result)
            continue

        result.errors.append(f"unknown_action:{patch_item.action}")
        rejected_patch_count += 1
        applied_items.append(result)

    report = SemanticReviewPatchAppliedReport(
        source_file_name=binding.source_file_name,
        reviewer=patch_file.reviewer,
        total_patch_items=len(patch_file.patch_items),
        applied_count=applied_count,
        blocked_count=blocked_count,
        hold_count=hold_count,
        rejected_patch_count=rejected_patch_count,
        items=applied_items,
        warnings=warnings,
        errors=errors,
    )

    applied_path = final_dir / SEMANTIC_FINAL_PATCH_APPLIED_NAME
    report_path = final_dir / SEMANTIC_FINAL_PATCH_REPORT_NAME
    applied_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    return SemanticReviewPatchResult(
        report=report,
        applied_path=applied_path,
        report_path=report_path,
    )


def _validate_manual_patch(patch_item) -> list[str]:
    errors: list[str] = []
    if not patch_item.confirm_no_guessing:
        errors.append("confirm_no_guessing_required")
    if not patch_item.reviewer_notes.strip():
        errors.append("reviewer_notes_required")
    has_content = bool(
        patch_item.question_text_raw
        or patch_item.options
        or patch_item.correct_answer_key
        or patch_item.solution_text_raw,
    )
    if not has_content:
        errors.append("manual_patch_requires_at_least_one_field")
    if patch_item.correct_answer_key and not patch_item.options:
        errors.append("correct_answer_key_requires_options_for_text_derivation")
    return errors
