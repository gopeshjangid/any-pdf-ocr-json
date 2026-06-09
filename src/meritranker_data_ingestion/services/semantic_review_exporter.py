"""Export semantic review items and patch templates (Part 13H/13J)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import (
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
    SEMANTIC_FINAL_DIR,
    SEMANTIC_FINAL_PATCH_TEMPLATE_NAME,
    SEMANTIC_FINAL_REVIEW_ITEMS_JSON_NAME,
    SEMANTIC_FINAL_REVIEW_ITEMS_MD_NAME,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingPackage,
)
from meritranker_data_ingestion.schemas.semantic_final_export import (
    PatchAction,
    SemanticPatchItemInput,
    SemanticReviewExportItem,
    SemanticReviewExportReport,
    SemanticReviewPatchFile,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.semantic_final_acceptance_gate import (
    FinalGateStatus,
    evaluate_final_acceptance_gate,
)
from meritranker_data_ingestion.services.semantic_key_normalizer import normalize_answer_key
from meritranker_data_ingestion.services.semantic_remaining_issue_diagnostician import (
    SemanticRemainingIssuesReport as DiagReport,
)


class SemanticReviewExportError(Exception):
    """Raised when semantic review export cannot proceed."""


@dataclass
class SemanticReviewExportResult:
    report: SemanticReviewExportReport
    json_path: Path
    md_path: Path
    patch_template_path: Path | None = None


def export_semantic_review_items(
    package_dir: Path,
    *,
    generate_patch_template: bool = True,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    expected_count: int | None = None,
) -> SemanticReviewExportResult:
    """Export non-accepted-safe items and optional patch template."""
    resolved = resolve_path(package_dir)
    sem_dir = resolved / SEMANTIC_BINDING_DIR
    final_dir = resolved / SEMANTIC_FINAL_DIR
    final_dir.mkdir(parents=True, exist_ok=True)

    repaired_path = sem_dir / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME
    if not repaired_path.exists():
        raise SemanticReviewExportError(
            f"Missing repaired semantic package at {repaired_path}.",
        )

    binding = SemanticBindingPackage.model_validate_json(repaired_path.read_text(encoding="utf-8"))
    if expected_count is None:
        expected_count = _load_expected_count(sem_dir)

    diag = _load_diagnosis(sem_dir / SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME)
    diag_by_qnum = {d.question_number: d for d in diag.items if d.question_number is not None}
    diag_by_id = {d.semantic_question_id: d for d in diag.items}

    items: list[SemanticReviewExportItem] = []
    review_required = 0
    rejected = 0

    for bound in binding.items:
        gate = evaluate_final_acceptance_gate(
            bound,
            answer_mode=answer_mode,
            expected_count=expected_count,
        )
        if gate.status == FinalGateStatus.ACCEPTED_SAFE:
            continue

        if bound.binding_status.value == "review_required":
            review_required += 1
        elif bound.binding_status.value == "rejected":
            rejected += 1
        else:
            review_required += 1

        qnum = bound.question_number
        patch_id = f"q_{qnum:04d}" if qnum is not None else bound.semantic_question_id
        diag_item = diag_by_qnum.get(qnum) or diag_by_id.get(bound.semantic_question_id)
        failure_class = diag_item.failure_class if diag_item else None
        repairability = diag_item.repairability if diag_item else None
        nearby = [line.text_preview for line in diag_item.nearby_evidence_lines[:3]] if diag_item else []
        recommended = _recommended_action(gate.status.value, failure_class, repairability)

        answer_key, _ = normalize_answer_key(bound.answer.key or bound.answer.key_raw)
        preview = _truncate(bound.question_text_raw, 200)
        option_previews = [
            f"{opt.key or opt.key_raw}: {_truncate(opt.text_raw, 80)}"
            for opt in bound.options[:4]
        ]

        items.append(
            SemanticReviewExportItem(
                patch_id=patch_id,
                question_number=qnum,
                semantic_question_id=bound.semantic_question_id,
                current_status=bound.binding_status.value,
                final_gate_status=gate.status.value,
                final_gate_reasons=list(gate.reasons),
                issues=_dedupe(list(bound.issues) + gate.gate_issues),
                failure_class=failure_class,
                repairability=repairability,
                question_text_preview=preview,
                option_keys=[opt.key for opt in bound.options if opt.key],
                answer_key=answer_key,
                nearby_evidence_excerpt=nearby,
                recommended_action=recommended,
                current_options_preview=option_previews,
            ),
        )

    report = SemanticReviewExportReport(
        total_review_items=len(items),
        review_required_count=review_required,
        rejected_count=rejected,
        items=items,
    )

    json_path = final_dir / SEMANTIC_FINAL_REVIEW_ITEMS_JSON_NAME
    md_path = final_dir / SEMANTIC_FINAL_REVIEW_ITEMS_MD_NAME
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(_render_review_md(report), encoding="utf-8")

    patch_template_path: Path | None = None
    if generate_patch_template:
        patch_template_path = final_dir / SEMANTIC_FINAL_PATCH_TEMPLATE_NAME
        template = SemanticReviewPatchFile(
            package_version="1.0.0",
            source_file_name=binding.source_file_name,
            reviewer="",
            created_at=datetime.now(timezone.utc).isoformat(),
            patch_items=[
                SemanticPatchItemInput(
                    patch_id=item.patch_id,
                    question_number=item.question_number,
                    action=PatchAction.HOLD_FOR_REVIEW,
                    reviewer_notes="",
                    manual_source_reference="",
                    confirm_no_guessing=False,
                )
                for item in items
            ],
        )
        patch_template_path.write_text(template.model_dump_json(indent=2), encoding="utf-8")

    return SemanticReviewExportResult(
        report=report,
        json_path=json_path,
        md_path=md_path,
        patch_template_path=patch_template_path,
    )


def _load_expected_count(sem_dir: Path) -> int | None:
    from meritranker_data_ingestion.config import SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME

    path = sem_dir / SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME
    if not path.exists():
        return None
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("expected_count")


def _load_diagnosis(path: Path) -> DiagReport:
    if not path.exists():
        return DiagReport()
    return DiagReport.model_validate_json(path.read_text(encoding="utf-8"))


def _recommended_action(
    gate_status: str,
    failure_class: str | None,
    repairability: str | None,
) -> str:
    if gate_status == FinalGateStatus.REVIEW_VISUAL_REQUIRED.value:
        return "needs_visual_vlm"
    if gate_status == FinalGateStatus.BLOCKED_BAD_ITEM.value:
        return "block"
    if gate_status == FinalGateStatus.REVIEW_MANUAL_PATCH_REQUIRED.value:
        return "manual_fix"
    if failure_class == "visual_only_options":
        return "needs_visual_vlm"
    if repairability == "blocked":
        return "block"
    if repairability == "repairable":
        return "manual_fix"
    if failure_class == "noise_in_question_text":
        return "manual_fix"
    return "accept_if_verified"


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _render_review_md(report: SemanticReviewExportReport) -> str:
    lines = [
        "# Semantic Review Items",
        "",
        f"- total_review_items: {report.total_review_items}",
        f"- review_required_count: {report.review_required_count}",
        f"- rejected_count: {report.rejected_count}",
        "",
    ]
    for item in report.items:
        lines.extend(
            [
                f"## {item.patch_id} (Q{item.question_number})",
                f"- status: {item.current_status}",
                f"- final_gate_status: {item.final_gate_status}",
                f"- final_gate_reasons: {', '.join(item.final_gate_reasons) or '(none)'}",
                f"- failure_class: {item.failure_class}",
                f"- repairability: {item.repairability}",
                f"- recommended_action: {item.recommended_action}",
                f"- answer_key: {item.answer_key}",
                f"- issues: {', '.join(item.issues)}",
                f"- question_preview: {item.question_text_preview}",
                "",
            ],
        )
    return "\n".join(lines) + "\n"
