"""Read-only review export for flagged final question items (Part 8)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    AUDIT_DIR,
    DIAGNOSTICS_DIR,
    FINAL_DIR,
    FINAL_PACKAGE_AUDIT_JSON_NAME,
    FINAL_QUESTIONS_NAME,
    FINAL_VALIDATION_REPORT_NAME,
    QUESTION_COVERAGE_JSON_NAME,
    REVIEW_DIR,
    REVIEW_ITEMS_JSON_NAME,
    REVIEW_ITEMS_MD_NAME,
)
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionItem,
    FinalQuestionPackage,
    FinalQuestionValidationReport,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.review_export import (
    ReviewExportItem,
    ReviewExportReport,
)
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)

DEFAULT_REVIEW_STATUSES = frozenset({
    ValidationStatus.NEEDS_REVIEW,
    ValidationStatus.INCOMPLETE,
    ValidationStatus.DUPLICATE_CONFLICT,
    ValidationStatus.REJECTED,
})

PREVIEW_LIMIT = 200


class ReviewExportError(Exception):
    """Raised when review export cannot proceed."""


@dataclass(frozen=True)
class ReviewExportPaths:
    package_dir: Path
    review_dir: Path
    review_json: Path
    review_md: Path


def build_review_export_paths(package_dir: Path) -> ReviewExportPaths:
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")
    review_dir = resolved / REVIEW_DIR
    return ReviewExportPaths(
        package_dir=resolved,
        review_dir=review_dir,
        review_json=review_dir / REVIEW_ITEMS_JSON_NAME,
        review_md=review_dir / REVIEW_ITEMS_MD_NAME,
    )


def _truncate_preview(text: str, limit: int = PREVIEW_LIMIT) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _load_audit_high_risk(package_dir: Path) -> set[str]:
    audit_path = package_dir / AUDIT_DIR / FINAL_PACKAGE_AUDIT_JSON_NAME
    if not audit_path.is_file():
        return set()
    data = json.loads(audit_path.read_text(encoding="utf-8"))
    return set(data.get("high_risk_items", []))


REVIEW_REASON_PRIORITY = (
    "solution_contains_next_anchor",
    "missing_option_labels_for_visual_question",
    "source_backed_option_labels_missing",
    "unlabeled_visual_assets",
    "unlabeled_option_images",
    "invalid_option_count",
    "linked_asset_missing",
    "empty_option_text_without_asset",
    "option_image_unlinked",
    "visual_question_requires_diagram_syntax",
    "visual_question_requires_review",
    "possible_noise_asset_after_options",
    "same_line_option_split_applied",
    "answer_option_mismatch",
    "answer_solution_unmapped",
    "incomplete_options",
    "low_confidence",
)


def _determine_review_reason(item: FinalQuestionItem, *, high_risk: bool) -> str:
    for reason in REVIEW_REASON_PRIORITY:
        if reason in item.issues:
            return reason

    if item.validation_status == ValidationStatus.INCOMPLETE:
        if "missing_options" in item.issues:
            return "incomplete_options"
        return "incomplete"
    if item.validation_status == ValidationStatus.DUPLICATE_CONFLICT:
        return "duplicate_conflict"
    if item.validation_status == ValidationStatus.REJECTED:
        return "rejected"
    if item.assets and any(
        "linked_asset_missing" in asset.issues for asset in item.assets
    ):
        return "linked_asset_missing"
    if "unlabeled_visual_assets" in item.issues:
        return "unlabeled_visual_assets"
    if item.assets and any(asset.role.value == "unknown" for asset in item.assets):
        return "unlabeled_visual_assets"
    if item.assets and "visual_question_requires_review" in item.issues:
        return "visual_question_requires_review"
    if not item.answer.available and not item.solution.available:
        return "answer_solution_unmapped"
    if not item.solution.available and item.answer.available:
        return "partial_mapping"
    if high_risk:
        return "audit_high_risk"
    if item.validation_status == ValidationStatus.NEEDS_REVIEW:
        return "needs_review"
    return item.validation_status.value


def _recommended_action(reason: str) -> str:
    actions = {
        "incomplete": "Verify source PDF; check options or question text extraction.",
        "incomplete_options": "Verify missing options against source PDF.",
        "duplicate_conflict": "Resolve duplicate question number against source PDF.",
        "rejected": "Manual review required; candidate may be unusable.",
        "answer_option_mismatch": "Verify answer key against source options in PDF.",
        "visual_question_requires_review": "Visual question bound; manual check before pattern ingestion.",
        "missing_option_labels_for_visual_question": "Missing option labels in source; do not guess A-D.",
        "unlabeled_option_images": "Assign unlabeled images to options manually against source PDF.",
        "option_image_unlinked": "Link option images to A-D keys against source PDF.",
        "empty_option_text_without_asset": "Option has no text and no linked image.",
        "linked_asset_missing": "Check marker/assets; verify image copied from PDF.",
        "solution_contains_next_anchor": "Solution block merged; verify split against source PDF.",
        "answer_solution_unmapped": "Check solution section mapping in source PDF.",
        "partial_mapping": "Verify solution text exists in source PDF.",
        "low_confidence": "Low-confidence mapping; spot-check against source PDF.",
        "audit_high_risk": "Priority manual review per audit findings.",
        "needs_review": "Spot-check raw text against source PDF.",
    }
    return actions.get(reason, "Manual review against source PDF.")


def _should_include_item(
    item: FinalQuestionItem,
    *,
    include_validated: bool,
    high_risk_ids: set[str],
) -> bool:
    if include_validated:
        return True
    if item.validation_status in DEFAULT_REVIEW_STATUSES:
        return True
    if "answer_option_mismatch" in item.issues:
        return True
    if item.question_id in high_risk_ids:
        return True
    if item.assets and item.validation_status != ValidationStatus.VALIDATED:
        return True
    if (
        item.validation_status == ValidationStatus.QUESTION_ONLY_VALIDATED
        and item.issues
    ):
        return True
    return False


def build_review_export(
    package: FinalQuestionPackage,
    *,
    include_validated: bool = False,
    high_risk_ids: set[str] | None = None,
    package_dir: str | None = None,
) -> ReviewExportReport:
    """Build review export from final question package (read-only)."""
    high_risk = high_risk_ids or set()
    items: list[ReviewExportItem] = []
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}

    for item in package.items:
        status_key = item.validation_status.value
        status_counts[status_key] = status_counts.get(status_key, 0) + 1

        if not _should_include_item(item, include_validated=include_validated, high_risk_ids=high_risk):
            continue

        reason = _determine_review_reason(item, high_risk=item.question_id in high_risk)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

        items.append(
            ReviewExportItem(
                question_id=item.question_id,
                question_number=item.question_number,
                validation_status=item.validation_status,
                confidence=item.confidence,
                issues=list(item.issues),
                source_trace=item.source_trace,
                has_answer=item.answer.available,
                has_solution=item.solution.available,
                has_assets=bool(item.assets),
                answer_key=item.answer.key if item.answer.available else None,
                raw_text_preview=_truncate_preview(item.raw_text),
                question_text_preview=_truncate_preview(item.question_text_raw),
                option_count=len(item.options),
                asset_count=len(item.assets),
                review_reason=reason,
                recommended_action=_recommended_action(reason),
            ),
        )

    return ReviewExportReport(
        package_dir=package_dir or "",
        total_final_questions=len(package.items),
        review_item_count=len(items),
        include_validated=include_validated,
        status_counts=status_counts,
        reason_counts=reason_counts,
        items=items,
    )


def render_review_markdown(report: ReviewExportReport) -> str:
    lines = [
        "# Review Items Export",
        "",
        "> **Note:** Read-only export. `final/questions.json` is not modified.",
        "",
        "## Summary",
        "",
        f"- Total final questions: {report.total_final_questions}",
        f"- Review items exported: {report.review_item_count}",
        f"- Include validated: {report.include_validated}",
        "",
        "### Status counts (all final items)",
        "",
    ]
    for status, count in sorted(report.status_counts.items()):
        lines.append(f"- {status}: {count}")

    if report.reason_counts:
        lines.extend(["", "### Review reasons (exported items)", ""])
        for reason, count in sorted(report.reason_counts.items()):
            lines.append(f"- {reason}: {count}")

    grouped: dict[str, list[ReviewExportItem]] = {}
    for item in report.items:
        grouped.setdefault(item.review_reason, []).append(item)

    lines.extend(["", "## Review Items by Reason", ""])
    for reason, group in sorted(grouped.items()):
        lines.append(f"### {reason} ({len(group)})")
        lines.append("")
        for item in group:
            qnum = item.question_number if item.question_number is not None else "?"
            lines.append(
                f"- **Q{qnum}** (`{item.question_id}`) — {item.validation_status.value} "
                f"| reason={item.review_reason} | opts={item.option_count} | "
                f"answer={item.has_answer} | solution={item.has_solution}",
            )
            lines.append(f"  - Preview: `{item.question_text_preview}`")
            if item.issues:
                lines.append(f"  - Issues: {', '.join(item.issues)}")
            lines.append(f"  - Action: {item.recommended_action}")
        lines.append("")

    lines.append("## Recommended Next Step")
    lines.append("")
    lines.append(
        "Review flagged items against the source PDF. "
        "Do not auto-correct JSON; update extraction rules only after manual confirmation.",
    )
    lines.append("")
    return "\n".join(lines)


def write_review_outputs(report: ReviewExportReport, paths: ReviewExportPaths) -> None:
    assert_output_contains(paths.package_dir, paths.review_dir)
    paths.review_dir.mkdir(parents=True, exist_ok=True)

    assert_output_contains(paths.package_dir, paths.review_json)
    paths.review_json.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )

    assert_output_contains(paths.package_dir, paths.review_md)
    paths.review_md.write_text(render_review_markdown(report), encoding="utf-8")


def export_review_items_package(
    package_dir: Path,
    *,
    include_validated: bool = False,
) -> ReviewExportReport:
    """Load final package and write review export artifacts."""
    paths = build_review_export_paths(package_dir)
    questions_path = package_dir / FINAL_DIR / FINAL_QUESTIONS_NAME

    if not questions_path.is_file():
        raise ReviewExportError(f"Missing final package: {questions_path}")

    package = FinalQuestionPackage.model_validate(
        json.loads(questions_path.read_text(encoding="utf-8")),
    )
    high_risk = _load_audit_high_risk(package_dir)
    report = build_review_export(
        package,
        include_validated=include_validated,
        high_risk_ids=high_risk,
        package_dir=str(package_dir),
    )
    write_review_outputs(report, paths)
    return report
