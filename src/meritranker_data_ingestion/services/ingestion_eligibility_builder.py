"""Read-only ingestion eligibility builder and duplicate solution diagnostics (Part 9)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    ANSWER_SOLUTION_MAP_NAME,
    ANSWER_SOLUTION_REPORT_NAME,
    AUDIT_DIR,
    BLOCKED_QUESTIONS_NAME,
    DIAGNOSTICS_DIR,
    DUPLICATE_SOLUTION_DIAGNOSTICS_NAME,
    ELIGIBILITY_DIR,
    ELIGIBLE_QUESTIONS_NAME,
    FINAL_DIR,
    FINAL_PACKAGE_AUDIT_JSON_NAME,
    FINAL_QUESTIONS_NAME,
    FINAL_VALIDATION_REPORT_NAME,
    INGESTION_ELIGIBILITY_MD_NAME,
    INGESTION_ELIGIBILITY_REPORT_NAME,
    MAPPINGS_DIR,
    QUESTION_COVERAGE_JSON_NAME,
    REVIEW_DIR,
    REVIEW_ITEMS_JSON_NAME,
    REVIEW_REQUIRED_QUESTIONS_NAME,
)
from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    QuestionAnswerSolutionMapping,
)
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionItem,
    FinalQuestionPackage,
    FinalQuestionValidationReport,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.ingestion_eligibility import (
    AnswerMode,
    DuplicateSafetyDecision,
    DuplicateSolutionDiagnostic,
    EligibilityBuildStatus,
    EligibilityOutputPaths,
    EligibilityStatus,
    IngestionEligibilityItem,
    IngestionEligibilityReport,
    SolutionSourceSummary,
)
from meritranker_data_ingestion.schemas.question_candidates import AssetRole
from meritranker_data_ingestion.services.answer_solution_mapper import (
    _collect_solution_blocks,
    _extract_answer_from_line,
    _find_solution_section_line,
)
from meritranker_data_ingestion.services.classified_lines_loader import load_lines_for_downstream
from meritranker_data_ingestion.services.visual_intent_detector import is_visual_dependent
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)

PREVIEW_LIMIT = 120
EXPECTED_OPTION_KEYS = frozenset({"A", "B", "C", "D"})

BLOCKING_ISSUES = frozenset({
    "missing_option_labels_for_visual_question",
    "unlabeled_option_images",
    "unlabeled_visual_assets",
    "source_backed_option_labels_missing",
    "invalid_option_count",
    "linked_asset_missing",
    "answer_option_mismatch",
    "solution_contains_next_anchor",
    "missing_options",
    "missing_question_text",
    "conflicting_answer_keys",
    "duplicate_question_number",
})

VISUAL_REVIEW_ISSUES = frozenset({
    "visual_question_requires_review",
    "visual_question_requires_diagram_syntax",
})

VISUAL_BLOCKING_ISSUES = frozenset({
    "missing_option_labels_for_visual_question",
    "unlabeled_option_images",
    "unlabeled_visual_assets",
    "source_backed_option_labels_missing",
    "invalid_option_count",
    "linked_asset_missing",
    "empty_option_text_without_asset",
})


class EligibilityBuildError(Exception):
    """Raised when eligibility build cannot proceed."""


@dataclass(frozen=True)
class EligibilityPaths:
    package_dir: Path
    eligibility_dir: Path
    report_json: Path
    eligible_json: Path
    review_required_json: Path
    blocked_json: Path
    duplicate_json: Path
    markdown: Path


def build_eligibility_paths(package_dir: Path) -> EligibilityPaths:
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")
    eligibility_dir = resolved / ELIGIBILITY_DIR
    return EligibilityPaths(
        package_dir=resolved,
        eligibility_dir=eligibility_dir,
        report_json=eligibility_dir / INGESTION_ELIGIBILITY_REPORT_NAME,
        eligible_json=eligibility_dir / ELIGIBLE_QUESTIONS_NAME,
        review_required_json=eligibility_dir / REVIEW_REQUIRED_QUESTIONS_NAME,
        blocked_json=eligibility_dir / BLOCKED_QUESTIONS_NAME,
        duplicate_json=eligibility_dir / DUPLICATE_SOLUTION_DIAGNOSTICS_NAME,
        markdown=eligibility_dir / INGESTION_ELIGIBILITY_MD_NAME,
    )


def _truncate(text: str, limit: int = PREVIEW_LIMIT) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _has_visual_assets(item: FinalQuestionItem) -> bool:
    return bool(item.assets)


def _has_linked_option_assets(item: FinalQuestionItem) -> bool:
    if any(opt.linked_asset_paths for opt in item.options):
        return True
    return any(asset.role == AssetRole.OPTION_IMAGE for asset in item.assets)


def _answer_matches_options(item: FinalQuestionItem) -> bool:
    if not item.answer.available or not item.answer.key:
        return True
    if not item.options:
        return False
    keys = {opt.key for opt in item.options if opt.key}
    return item.answer.key in keys


def _build_duplicate_diagnostics(
    package_dir: Path,
    mappings: list[QuestionAnswerSolutionMapping],
    duplicate_numbers: list[int],
) -> list[DuplicateSolutionDiagnostic]:
    if not duplicate_numbers:
        return []

    lines, _, _, _, _ = load_lines_for_downstream(package_dir)
    solution_start = _find_solution_section_line(lines)
    solution_blocks, _ = _collect_solution_blocks(lines, solution_start)

    mapping_by_qnum = {
        m.question_number: m
        for m in mappings
        if m.question_number is not None
    }

    diagnostics: list[DuplicateSolutionDiagnostic] = []
    for q_num in sorted(duplicate_numbers):
        sources_raw = solution_blocks.get(q_num, [])
        summaries: list[SolutionSourceSummary] = []
        answer_keys: list[str | None] = []
        raw_texts: list[str] = []

        for sol in sources_raw:
            qn, key, _, _, _ = _extract_answer_from_line(sol.raw_text)
            answer_keys.append(key if qn == q_num else None)
            raw_texts.append(sol.raw_text)
            summaries.append(
                SolutionSourceSummary(
                    start_line=sol.start_line,
                    end_line=sol.end_line,
                    line_numbers=list(sol.line_numbers),
                    answer_key=key if qn == q_num else None,
                    raw_text_preview=_truncate(sol.raw_text),
                ),
            )

        answers_identical = len(set(answer_keys)) <= 1
        solution_texts_identical = len(set(raw_texts)) <= 1

        mapping = mapping_by_qnum.get(q_num)
        mapped_ids = [mapping.question_id] if mapping else []
        chosen_line = mapping.solution.start_line if mapping and mapping.solution else None

        if answers_identical and solution_texts_identical:
            decision = DuplicateSafetyDecision.HARMLESS_DUPLICATE_SAME_TEXT
            action = "Duplicate sources are identical; manual confirm before ingestion."
        elif not answers_identical or not solution_texts_identical:
            decision = DuplicateSafetyDecision.DUPLICATE_CONFLICT
            action = "Conflicting duplicate solution sources; resolve against source PDF before ingestion."
        else:
            decision = DuplicateSafetyDecision.NEEDS_REVIEW
            action = "Duplicate solution sources need manual review."

        diagnostics.append(
            DuplicateSolutionDiagnostic(
                solution_number=q_num,
                source_count=len(summaries),
                sources=summaries,
                answers_identical=answers_identical,
                solution_texts_identical=solution_texts_identical,
                mapped_question_ids=mapped_ids,
                chosen_source_start_line=chosen_line,
                safety_decision=decision,
                recommended_action=action,
            ),
        )

    return diagnostics


def _duplicate_blocks_question(
    q_num: int | None,
    duplicate_diag: dict[int, DuplicateSolutionDiagnostic],
) -> tuple[bool, str | None]:
    if q_num is None or q_num not in duplicate_diag:
        return False, None
    diag = duplicate_diag[q_num]
    if diag.safety_decision == DuplicateSafetyDecision.DUPLICATE_CONFLICT:
        return True, "duplicate_solution_conflict"
    if diag.safety_decision == DuplicateSafetyDecision.NEEDS_REVIEW:
        return False, "duplicate_solution_needs_review"
    return False, "duplicate_solution_harmless"


def _classify_item(
    item: FinalQuestionItem,
    *,
    answer_mode: AnswerMode,
    duplicate_diag: dict[int, DuplicateSolutionDiagnostic],
    review_ids: set[str],
) -> IngestionEligibilityItem:
    blocking: list[str] = []
    review: list[str] = []
    reasons: list[str] = []

    if not item.question_text_raw.strip():
        blocking.append("missing_question_text")
    if not item.raw_text.strip():
        blocking.append("missing_raw_text")
    if not item.source_trace.line_numbers:
        blocking.append("missing_source_trace")

    for issue in item.issues:
        if issue in BLOCKING_ISSUES or issue in VISUAL_BLOCKING_ISSUES:
            blocking.append(issue)
        elif issue in VISUAL_REVIEW_ISSUES:
            review.append(issue)
        elif issue == "low_confidence_answer":
            review.append(issue)

    if item.validation_status == ValidationStatus.INCOMPLETE:
        blocking.append("validation_incomplete")
    if item.validation_status == ValidationStatus.DUPLICATE_CONFLICT:
        blocking.append("validation_duplicate_conflict")
    if item.validation_status == ValidationStatus.REJECTED:
        blocking.append("validation_rejected")
    if item.validation_status == ValidationStatus.NEEDS_REVIEW:
        review.append("validation_needs_review")

    if "answer_option_mismatch" in item.issues:
        blocking.append("answer_option_mismatch")
    elif item.options and item.answer.available and not _answer_matches_options(item):
        blocking.append("answer_option_mismatch")

    if "solution_contains_next_anchor" in item.issues:
        blocking.append("solution_contains_next_anchor")

    dup_blocks, dup_reason = _duplicate_blocks_question(item.question_number, duplicate_diag)
    if dup_blocks:
        blocking.append(dup_reason or "duplicate_solution_conflict")
    elif dup_reason:
        review.append(dup_reason)

    has_visual = _has_visual_assets(item)
    has_linked = _has_linked_option_assets(item)
    visual_dependent = is_visual_dependent(item.question_text_raw) or has_visual

    if visual_dependent:
        if any(issue in item.issues for issue in VISUAL_BLOCKING_ISSUES):
            pass
        elif any(asset.issues for asset in item.assets if "linked_asset_missing" in asset.issues):
            blocking.append("linked_asset_missing")
        elif "visual_question_requires_diagram_syntax" in item.issues:
            review.append("visual_question_requires_diagram_syntax")
        elif "visual_question_requires_review" in item.issues or has_linked:
            review.append("visual_question_requires_review")
        elif item.validation_status != ValidationStatus.VALIDATED:
            review.append("visual_question_uncertain")

    if answer_mode == AnswerMode.REQUIRED:
        if not item.answer.available:
            blocking.append("missing_answer_required_mode")
        if not item.solution.available:
            review.append("missing_solution")
    elif answer_mode == AnswerMode.OPTIONAL:
        if not item.answer.available:
            review.append("question_only_no_answer")
    elif answer_mode == AnswerMode.QUESTION_ONLY:
        if not item.answer.available:
            reasons.append("question_only_mode")

    if item.question_id in review_ids:
        review.append("review_export_flagged")

    if item.confidence < 0.8:
        review.append("low_confidence")

    blocking = sorted(set(blocking))
    review = sorted(set(review))

    if blocking:
        status = EligibilityStatus.BLOCKED
        action = "Blocked from pattern ingestion; fix or manually approve against source PDF."
    elif review:
        status = EligibilityStatus.REVIEW_REQUIRED
        action = "Manual educator review required before pattern ingestion."
    elif item.validation_status in {
        ValidationStatus.VALIDATED,
        ValidationStatus.QUESTION_ONLY_VALIDATED,
    }:
        status = EligibilityStatus.ELIGIBLE_FOR_INGESTION
        action = "Eligible for future pattern ingestion gate (not ingested by this tool)."
        reasons.append("clean_validated_item")
    else:
        status = EligibilityStatus.REVIEW_REQUIRED
        review.append("non_validated_status")
        action = "Manual review required before pattern ingestion."

    all_reasons = sorted(set(reasons + blocking + review))

    return IngestionEligibilityItem(
        question_id=item.question_id,
        question_number=item.question_number,
        validation_status=item.validation_status.value,
        eligibility_status=status,
        eligibility_reasons=all_reasons,
        blocking_reasons=blocking,
        review_reasons=review,
        answer_available=item.answer.available,
        solution_available=item.solution.available,
        has_visual_assets=has_visual,
        has_linked_option_assets=has_linked,
        duplicate_solution_issue=dup_blocks or dup_reason is not None,
        source_trace=item.source_trace,
        recommended_action=action,
        question_text_preview=_truncate(item.question_text_raw),
    )


def build_ingestion_eligibility(
    package: FinalQuestionPackage,
    mappings: list[QuestionAnswerSolutionMapping],
    *,
    package_dir: Path,
    answer_mode: AnswerMode = AnswerMode.REQUIRED,
    validation_report: FinalQuestionValidationReport | None = None,
    mapping_report: dict | None = None,
    review_ids: set[str] | None = None,
) -> IngestionEligibilityReport:
    """Build eligibility report from final package and mapping artifacts."""
    warnings: list[str] = []
    errors: list[str] = []

    duplicate_numbers = []
    if mapping_report:
        duplicate_numbers = list(mapping_report.get("duplicate_solution_numbers", []))

    duplicate_diagnostics = _build_duplicate_diagnostics(
        package_dir,
        mappings,
        duplicate_numbers,
    )
    duplicate_diag_by_num = {d.solution_number: d for d in duplicate_diagnostics}

    items = [
        _classify_item(
            item,
            answer_mode=answer_mode,
            duplicate_diag=duplicate_diag_by_num,
            review_ids=review_ids or set(),
        )
        for item in package.items
    ]

    eligible = [i for i in items if i.eligibility_status == EligibilityStatus.ELIGIBLE_FOR_INGESTION]
    review_req = [i for i in items if i.eligibility_status == EligibilityStatus.REVIEW_REQUIRED]
    blocked = [i for i in items if i.eligibility_status == EligibilityStatus.BLOCKED]

    duplicate_conflict_count = sum(
        1 for d in duplicate_diagnostics
        if d.safety_decision == DuplicateSafetyDecision.DUPLICATE_CONFLICT
    )

    missing_asset_count = sum(
        1 for item in package.items
        if any("linked_asset_missing" in asset.issues for asset in item.assets)
    )

    visual_review_count = sum(
        1 for i in items
        if i.has_visual_assets and i.eligibility_status == EligibilityStatus.REVIEW_REQUIRED
    )

    total = len(items)
    if len(eligible) + len(review_req) + len(blocked) != total:
        errors.append(
            f"count_reconciliation_failed: {len(eligible)}+{len(review_req)}+{len(blocked)} != {total}",
        )

    status = EligibilityBuildStatus.FAILED if errors else EligibilityBuildStatus.SUCCEEDED

    return IngestionEligibilityReport(
        status=status,
        package_dir=str(package_dir),
        answer_mode=answer_mode,
        total_questions=total,
        eligible_count=len(eligible),
        review_required_count=len(review_req),
        blocked_count=len(blocked),
        visual_question_count=sum(1 for i in package.items if i.assets),
        visual_review_count=visual_review_count,
        duplicate_solution_count=len(duplicate_diagnostics),
        duplicate_solution_conflict_count=duplicate_conflict_count,
        answer_option_mismatch_count=sum(
            1 for i in items if "answer_option_mismatch" in i.blocking_reasons
        ),
        incomplete_count=sum(
            1 for i in items if "validation_incomplete" in i.blocking_reasons
        ),
        missing_asset_count=missing_asset_count,
        items=items,
        duplicate_diagnostics=duplicate_diagnostics,
        warnings=warnings,
        errors=errors,
    )


def render_eligibility_markdown(report: IngestionEligibilityReport) -> str:
    lines = [
        "# Ingestion Eligibility Report",
        "",
        "> **Warning:** This report does not perform ingestion.",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total questions | {report.total_questions} |",
        f"| Eligible for ingestion | {report.eligible_count} |",
        f"| Review required | {report.review_required_count} |",
        f"| Blocked | {report.blocked_count} |",
        f"| Answer mode | {report.answer_mode.value} |",
        f"| Visual questions | {report.visual_question_count} |",
        f"| Duplicate solution numbers | {report.duplicate_solution_count} |",
        f"| Duplicate conflicts | {report.duplicate_solution_conflict_count} |",
        "",
        "## Duplicate Solution Diagnostics",
        "",
    ]

    if not report.duplicate_diagnostics:
        lines.append("No duplicate solution numbers detected.")
    else:
        for diag in report.duplicate_diagnostics:
            lines.append(
                f"- **S{diag.solution_number}** ({diag.source_count} sources) — "
                f"{diag.safety_decision.value} — {diag.recommended_action}",
            )

    lines.extend(["", "## Blocked Questions", ""])
    blocked = [i for i in report.items if i.eligibility_status == EligibilityStatus.BLOCKED]
    if not blocked:
        lines.append("None.")
    else:
        for item in blocked[:50]:
            lines.append(
                f"- **Q{item.question_number}** (`{item.question_id}`) — "
                f"{', '.join(item.blocking_reasons[:3])}",
            )

    lines.extend(["", "## Review Required", ""])
    review = [i for i in report.items if i.eligibility_status == EligibilityStatus.REVIEW_REQUIRED]
    if not review:
        lines.append("None.")
    else:
        for item in review[:50]:
            lines.append(
                f"- **Q{item.question_number}** (`{item.question_id}`) — "
                f"{', '.join(item.review_reasons[:3])}",
            )

    lines.extend([
        "",
        "## Recommended Next Action",
        "",
        "Use `eligible-questions.json` only as a future pattern-ingestion gate input. "
        "Review blocked and review-required lists against the source PDF before any ingestion.",
        "",
    ])
    return "\n".join(lines)


def write_eligibility_outputs(
    report: IngestionEligibilityReport,
    paths: EligibilityPaths,
) -> EligibilityOutputPaths:
    assert_output_contains(paths.package_dir, paths.eligibility_dir)
    paths.eligibility_dir.mkdir(parents=True, exist_ok=True)

    eligible = [i for i in report.items if i.eligibility_status == EligibilityStatus.ELIGIBLE_FOR_INGESTION]
    review_req = [i for i in report.items if i.eligibility_status == EligibilityStatus.REVIEW_REQUIRED]
    blocked = [i for i in report.items if i.eligibility_status == EligibilityStatus.BLOCKED]

    output_paths = EligibilityOutputPaths(
        report_json=str(paths.report_json),
        eligible_json=str(paths.eligible_json),
        review_required_json=str(paths.review_required_json),
        blocked_json=str(paths.blocked_json),
        duplicate_diagnostics_json=str(paths.duplicate_json),
        markdown=str(paths.markdown),
    )

    report_payload = report.model_dump(mode="json")
    report_payload["output_paths"] = output_paths.model_dump(mode="json")

    for target in (
        paths.report_json,
        paths.eligible_json,
        paths.review_required_json,
        paths.blocked_json,
        paths.duplicate_json,
    ):
        assert_output_contains(paths.package_dir, target)

    paths.report_json.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    paths.eligible_json.write_text(
        json.dumps([i.model_dump(mode="json") for i in eligible], indent=2),
        encoding="utf-8",
    )
    paths.review_required_json.write_text(
        json.dumps([i.model_dump(mode="json") for i in review_req], indent=2),
        encoding="utf-8",
    )
    paths.blocked_json.write_text(
        json.dumps([i.model_dump(mode="json") for i in blocked], indent=2),
        encoding="utf-8",
    )
    paths.duplicate_json.write_text(
        json.dumps([d.model_dump(mode="json") for d in report.duplicate_diagnostics], indent=2),
        encoding="utf-8",
    )
    paths.markdown.write_text(render_eligibility_markdown(report), encoding="utf-8")

    return output_paths


def _load_review_ids(package_dir: Path) -> set[str]:
    review_path = package_dir / REVIEW_DIR / REVIEW_ITEMS_JSON_NAME
    if not review_path.is_file():
        return set()
    data = json.loads(review_path.read_text(encoding="utf-8"))
    return {item["question_id"] for item in data.get("items", [])}


def build_ingestion_eligibility_package(
    package_dir: Path,
    *,
    answer_mode: AnswerMode = AnswerMode.REQUIRED,
) -> IngestionEligibilityReport:
    """Load package artifacts and write eligibility outputs."""
    paths = build_eligibility_paths(package_dir)
    questions_path = package_dir / FINAL_DIR / FINAL_QUESTIONS_NAME
    map_path = package_dir / MAPPINGS_DIR / ANSWER_SOLUTION_MAP_NAME

    if not questions_path.is_file():
        raise EligibilityBuildError(f"Missing final package: {questions_path}")
    if not map_path.is_file():
        raise EligibilityBuildError(f"Missing answer/solution map: {map_path}")

    package = FinalQuestionPackage.model_validate(
        json.loads(questions_path.read_text(encoding="utf-8")),
    )
    mappings = [
        QuestionAnswerSolutionMapping.model_validate(item)
        for item in json.loads(map_path.read_text(encoding="utf-8"))
    ]

    mapping_report: dict | None = None
    report_path = package_dir / MAPPINGS_DIR / ANSWER_SOLUTION_REPORT_NAME
    if report_path.is_file():
        mapping_report = json.loads(report_path.read_text(encoding="utf-8"))

    validation_report = None
    val_path = package_dir / FINAL_DIR / FINAL_VALIDATION_REPORT_NAME
    if val_path.is_file():
        validation_report = FinalQuestionValidationReport.model_validate(
            json.loads(val_path.read_text(encoding="utf-8")),
        )

    review_ids = _load_review_ids(package_dir)

    report = build_ingestion_eligibility(
        package,
        mappings,
        package_dir=package_dir,
        answer_mode=answer_mode,
        validation_report=validation_report,
        mapping_report=mapping_report,
        review_ids=review_ids,
    )

    output_paths = write_eligibility_outputs(report, paths)
    return report.model_copy(update={"output_paths": output_paths})
