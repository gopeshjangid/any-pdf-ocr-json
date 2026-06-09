"""Build source-faithful pattern-ingestion handoff package (Part 12)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    ANSWER_SOLUTION_MAP_NAME,
    ARTIFACT_RECONCILIATION_JSON_NAME,
    AUDIT_DIR,
    BLOCKED_PATTERN_INPUT_NAME,
    BLOCKED_QUESTIONS_NAME,
    DIAGNOSTICS_DIR,
    ELIGIBILITY_DIR,
    ELIGIBLE_PATTERN_INPUT_NAME,
    ELIGIBLE_QUESTIONS_NAME,
    FINAL_DIR,
    FINAL_PACKAGE_AUDIT_JSON_NAME,
    FINAL_QUESTIONS_NAME,
    INGESTION_ELIGIBILITY_REPORT_NAME,
    MAPPINGS_DIR,
    PACKAGE_MANIFEST_NAME,
    PATTERN_INPUT_DIR,
    PATTERN_INPUT_PACKAGE_NAME,
    PATTERN_INPUT_SUMMARY_MD_NAME,
    REVIEW_DIR,
    REVIEW_ITEMS_JSON_NAME,
    REVIEW_PATTERN_INPUT_NAME,
    REVIEW_REQUIRED_QUESTIONS_NAME,
)
from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    QuestionAnswerSolutionMapping,
)
from meritranker_data_ingestion.schemas.artifact_reconciliation import QualityGateStatus
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionItem,
    FinalQuestionPackage,
)
from meritranker_data_ingestion.schemas.ingestion_eligibility import (
    EligibilityBuildStatus,
    EligibilityStatus,
    IngestionEligibilityItem,
    IngestionEligibilityReport,
)
from meritranker_data_ingestion.schemas.pattern_question_input import (
    PatternExportMode,
    PatternIngestionAction,
    PatternInputBuildStatus,
    PatternQuestionInputAnswer,
    PatternQuestionInputAsset,
    PatternQuestionInputBuildResult,
    PatternQuestionInputItem,
    PatternQuestionInputOption,
    PatternQuestionInputPackage,
    PatternQuestionInputSolution,
    PatternQuestionInputSourceTrace,
)
from meritranker_data_ingestion.schemas.review_export import ReviewExportReport
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)

ELIGIBILITY_REQUIRED_ERROR = "eligibility_required_for_pattern_input"
QUALITY_GATE_FAILED_ERROR = "quality_gate_failed_for_pattern_input"

INGESTION_ACTION_BY_STATUS: dict[EligibilityStatus, PatternIngestionAction] = {
    EligibilityStatus.ELIGIBLE_FOR_INGESTION: PatternIngestionAction.READY_FOR_PATTERN_INGESTION,
    EligibilityStatus.REVIEW_REQUIRED: PatternIngestionAction.HOLD_FOR_REVIEW,
    EligibilityStatus.BLOCKED: PatternIngestionAction.BLOCKED_DO_NOT_INGEST,
}

EXPORT_MODE_STATUSES: dict[PatternExportMode, frozenset[EligibilityStatus]] = {
    PatternExportMode.ELIGIBLE_ONLY: frozenset({EligibilityStatus.ELIGIBLE_FOR_INGESTION}),
    PatternExportMode.INCLUDE_REVIEW: frozenset({
        EligibilityStatus.ELIGIBLE_FOR_INGESTION,
        EligibilityStatus.REVIEW_REQUIRED,
    }),
    PatternExportMode.INCLUDE_BLOCKED: frozenset({
        EligibilityStatus.ELIGIBLE_FOR_INGESTION,
        EligibilityStatus.BLOCKED,
    }),
    PatternExportMode.ALL: frozenset({
        EligibilityStatus.ELIGIBLE_FOR_INGESTION,
        EligibilityStatus.REVIEW_REQUIRED,
        EligibilityStatus.BLOCKED,
    }),
}


class PatternInputBuildError(Exception):
    """Raised when pattern input build cannot proceed safely."""


@dataclass(frozen=True)
class PatternInputPaths:
    package_dir: Path
    pattern_input_dir: Path
    package_json: Path
    eligible_json: Path
    review_json: Path
    blocked_json: Path
    summary_md: Path


def build_pattern_input_paths(package_dir: Path) -> PatternInputPaths:
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")
    pattern_dir = resolved / PATTERN_INPUT_DIR
    return PatternInputPaths(
        package_dir=resolved,
        pattern_input_dir=pattern_dir,
        package_json=pattern_dir / PATTERN_INPUT_PACKAGE_NAME,
        eligible_json=pattern_dir / ELIGIBLE_PATTERN_INPUT_NAME,
        review_json=pattern_dir / REVIEW_PATTERN_INPUT_NAME,
        blocked_json=pattern_dir / BLOCKED_PATTERN_INPUT_NAME,
        summary_md=pattern_dir / PATTERN_INPUT_SUMMARY_MD_NAME,
    )


def _load_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_trace(trace) -> PatternQuestionInputSourceTrace:
    return PatternQuestionInputSourceTrace(
        start_line=trace.start_line,
        end_line=trace.end_line,
        page_start=trace.page_start,
        page_end=trace.page_end,
        line_numbers=list(trace.line_numbers),
    )


def _build_item(
    final_item: FinalQuestionItem,
    elig_item: IngestionEligibilityItem,
    mapping: QuestionAnswerSolutionMapping | None,
    *,
    source_order: int,
    source_refs: dict[str, str],
    review_export_ids: set[str],
    audit_flags: list[str],
) -> PatternQuestionInputItem:
    item_audit = list(audit_flags)
    if (
        elig_item.eligibility_status == EligibilityStatus.REVIEW_REQUIRED
        and final_item.question_id not in review_export_ids
    ):
        item_audit.append("review_export_missing_for_item")

    solution_trace = None
    if final_item.solution.available:
        solution_trace = PatternQuestionInputSourceTrace(
            start_line=final_item.solution.start_line or final_item.source_trace.start_line,
            end_line=final_item.solution.end_line or final_item.source_trace.end_line,
            page_start=final_item.source_trace.page_start,
            page_end=final_item.source_trace.page_end,
            line_numbers=[],
        )

    return PatternQuestionInputItem(
        input_id=f"pi_{source_order:04d}",
        question_id=final_item.question_id,
        question_number=final_item.question_number,
        source_order=source_order,
        eligibility_status=elig_item.eligibility_status.value,
        ingestion_action=INGESTION_ACTION_BY_STATUS[elig_item.eligibility_status],
        question_text_raw=final_item.question_text_raw,
        raw_text=final_item.raw_text,
        options=[
            PatternQuestionInputOption(
                key=opt.key,
                key_raw=opt.key_raw,
                text_raw=opt.text_raw,
                linked_asset_paths=list(opt.linked_asset_paths),
                source_trace=_copy_trace(opt.source_trace),
                issues=list(opt.issues),
            )
            for opt in final_item.options
        ],
        answer=PatternQuestionInputAnswer(
            available=final_item.answer.available,
            key=final_item.answer.key,
            key_raw=final_item.answer.key_raw,
            source_text_raw=final_item.answer.source_text_raw,
            source_line=final_item.answer.source_line,
            issues=list(final_item.answer.issues),
        ),
        solution=PatternQuestionInputSolution(
            available=final_item.solution.available,
            text_raw=final_item.solution.text_raw,
            source_trace=solution_trace,
            image_references=list(final_item.solution.image_references),
            issues=list(final_item.solution.issues),
        ),
        visual_assets=[
            PatternQuestionInputAsset(
                asset_path=asset.asset_path,
                role=asset.role,
                option_key=asset.option_key,
                line_number=asset.line_number,
                issues=list(asset.issues),
            )
            for asset in final_item.assets
        ],
        source_trace=_copy_trace(final_item.source_trace),
        mapping_status=mapping.mapping_status.value if mapping else None,
        validation_status=final_item.validation_status.value,
        review_reasons=list(elig_item.review_reasons),
        blocking_reasons=list(elig_item.blocking_reasons),
        eligibility_reasons=list(elig_item.eligibility_reasons),
        audit_flags=sorted(set(item_audit)),
        source_refs=dict(source_refs),
    )


def _should_export(
    status: EligibilityStatus,
    export_mode: PatternExportMode,
) -> bool:
    return status in EXPORT_MODE_STATUSES[export_mode]


def render_pattern_input_summary(
    result: PatternQuestionInputBuildResult,
    *,
    paths: PatternInputPaths,
) -> str:
    pkg = result.package
    visual_exported = sum(1 for item in pkg.items if item.visual_assets)
    blocked_exported = sum(
        1
        for item in pkg.items
        if item.ingestion_action == PatternIngestionAction.BLOCKED_DO_NOT_INGEST
    )
    eligible_in_export = sum(
        1
        for item in pkg.items
        if item.eligibility_status == EligibilityStatus.ELIGIBLE_FOR_INGESTION.value
    )
    review_in_export = sum(
        1
        for item in pkg.items
        if item.eligibility_status == EligibilityStatus.REVIEW_REQUIRED.value
    )

    lines = [
        "# Pattern Question Input Package",
        "",
        "_This package does not perform pattern ingestion._",
        "",
        f"**Source file:** {pkg.source_file_name or 'unknown'}",
        f"**Export mode:** `{pkg.export_mode.value}`",
        f"**Quality gate:** `{pkg.quality_gate_status or 'unknown'}`",
        f"**Answer mode:** `{pkg.answer_mode.value}`",
        "",
        "## Counts",
        "",
        f"- Total source questions: {pkg.total_source_questions}",
        f"- Exported count: {pkg.exported_count}",
        f"- Eligible in export: {eligible_in_export}",
        f"- Review in export: {review_in_export}",
        f"- Blocked in export: {blocked_exported}",
        f"- Visual assets in export: {visual_exported}",
        "",
        "## Output paths",
        "",
        f"- Package: `{paths.package_json.relative_to(paths.package_dir)}`",
        f"- Eligible subset: `{paths.eligible_json.relative_to(paths.package_dir)}`",
        f"- Review subset: `{paths.review_json.relative_to(paths.package_dir)}`",
        f"- Blocked subset: `{paths.blocked_json.relative_to(paths.package_dir)}`",
        "",
    ]
    if pkg.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in pkg.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)


def build_pattern_question_input(
    package_dir: Path,
    *,
    export_mode: PatternExportMode = PatternExportMode.ELIGIBLE_ONLY,
    allow_missing_eligibility: bool = False,
    allow_failed_quality_gate: bool = False,
) -> PatternQuestionInputBuildResult:
    """Build pattern input package from existing extraction artifacts."""
    paths = build_pattern_input_paths(package_dir)
    warnings: list[str] = []
    errors: list[str] = []

    final_data = _load_json(paths.package_dir / FINAL_DIR / FINAL_QUESTIONS_NAME)
    if not isinstance(final_data, dict):
        raise PatternInputBuildError(f"Missing final package: {FINAL_QUESTIONS_NAME}")
    final_package = FinalQuestionPackage.model_validate(final_data)

    eligibility_data = _load_json(
        paths.package_dir / ELIGIBILITY_DIR / INGESTION_ELIGIBILITY_REPORT_NAME,
    )
    if not isinstance(eligibility_data, dict):
        if not allow_missing_eligibility:
            raise PatternInputBuildError(ELIGIBILITY_REQUIRED_ERROR)
        warnings.append(ELIGIBILITY_REQUIRED_ERROR)
        eligibility = IngestionEligibilityReport(
            status=EligibilityBuildStatus.SUCCEEDED,
            package_dir=str(paths.package_dir),
            total_questions=final_package.total_questions,
        )
    else:
        eligibility = IngestionEligibilityReport.model_validate(eligibility_data)

    reconciliation_data = _load_json(
        paths.package_dir / DIAGNOSTICS_DIR / ARTIFACT_RECONCILIATION_JSON_NAME,
    )
    quality_gate_status: str | None = None
    if isinstance(reconciliation_data, dict):
        quality_gate_status = reconciliation_data.get("quality_gate_status")
        if quality_gate_status == QualityGateStatus.FAILED.value and not allow_failed_quality_gate:
            raise PatternInputBuildError(QUALITY_GATE_FAILED_ERROR)
        if quality_gate_status == QualityGateStatus.WARNING.value:
            warnings.append(f"quality_gate_status:{quality_gate_status}")
        recon_checks = reconciliation_data.get("checks", [])
        if any(c.get("check_id") == "flagged_final_items_missing_from_review" for c in recon_checks):
            warnings.append("review_export_incomplete")

    mappings_data = _load_json(paths.package_dir / MAPPINGS_DIR / ANSWER_SOLUTION_MAP_NAME)
    mappings_by_id: dict[str, QuestionAnswerSolutionMapping] = {}
    if isinstance(mappings_data, list):
        for entry in mappings_data:
            mapping = QuestionAnswerSolutionMapping.model_validate(entry)
            mappings_by_id[mapping.question_id] = mapping

    review_data = _load_json(paths.package_dir / REVIEW_DIR / REVIEW_ITEMS_JSON_NAME)
    review_export_ids: set[str] = set()
    if isinstance(review_data, dict):
        review_report = ReviewExportReport.model_validate(review_data)
        review_export_ids = {item.question_id for item in review_report.items}

    manifest = _load_json(paths.package_dir / PACKAGE_MANIFEST_NAME)
    source_file_name = final_package.source_file_name
    if isinstance(manifest, dict) and manifest.get("source_file_name"):
        source_file_name = manifest["source_file_name"]

    audit_data = _load_json(paths.package_dir / AUDIT_DIR / FINAL_PACKAGE_AUDIT_JSON_NAME)
    package_audit_flags: list[str] = []
    if isinstance(audit_data, dict) and audit_data.get("status"):
        package_audit_flags.append(f"audit_status:{audit_data['status']}")

    elig_by_id = {item.question_id: item for item in eligibility.items}

    source_artifact_paths = {
        "final_questions": f"{FINAL_DIR}/{FINAL_QUESTIONS_NAME}",
        "answer_solution_map": f"{MAPPINGS_DIR}/{ANSWER_SOLUTION_MAP_NAME}",
        "eligibility_report": f"{ELIGIBILITY_DIR}/{INGESTION_ELIGIBILITY_REPORT_NAME}",
        "eligible_questions": f"{ELIGIBILITY_DIR}/{ELIGIBLE_QUESTIONS_NAME}",
        "review_required_questions": f"{ELIGIBILITY_DIR}/{REVIEW_REQUIRED_QUESTIONS_NAME}",
        "blocked_questions": f"{ELIGIBILITY_DIR}/{BLOCKED_QUESTIONS_NAME}",
        "review_items": f"{REVIEW_DIR}/{REVIEW_ITEMS_JSON_NAME}",
        "artifact_reconciliation": f"{DIAGNOSTICS_DIR}/{ARTIFACT_RECONCILIATION_JSON_NAME}",
        "final_package_audit": f"{AUDIT_DIR}/{FINAL_PACKAGE_AUDIT_JSON_NAME}",
    }

    item_source_refs = {
        "final_question_item": f"{FINAL_DIR}/{FINAL_QUESTIONS_NAME}",
        "eligibility_item": f"{ELIGIBILITY_DIR}/{INGESTION_ELIGIBILITY_REPORT_NAME}",
        "answer_solution_mapping": f"{MAPPINGS_DIR}/{ANSWER_SOLUTION_MAP_NAME}",
    }

    exported_items: list[PatternQuestionInputItem] = []
    source_order = 0
    for final_item in final_package.items:
        elig_item = elig_by_id.get(final_item.question_id)
        if elig_item is None:
            if allow_missing_eligibility and ELIGIBILITY_REQUIRED_ERROR in warnings:
                elig_item = IngestionEligibilityItem(
                    question_id=final_item.question_id,
                    question_number=final_item.question_number,
                    validation_status=final_item.validation_status.value,
                    eligibility_status=EligibilityStatus.REVIEW_REQUIRED,
                    source_trace=final_item.source_trace,
                    recommended_action="Eligibility missing; hold for review.",
                )
            else:
                errors.append(f"missing_eligibility_for:{final_item.question_id}")
                continue
        if not _should_export(elig_item.eligibility_status, export_mode):
            continue
        source_order += 1
        exported_items.append(
            _build_item(
                final_item,
                elig_item,
                mappings_by_id.get(final_item.question_id),
                source_order=source_order,
                source_refs=item_source_refs,
                review_export_ids=review_export_ids,
                audit_flags=package_audit_flags,
            ),
        )

    eligible_items = [
        item
        for item in exported_items
        if item.eligibility_status == EligibilityStatus.ELIGIBLE_FOR_INGESTION.value
    ]
    review_items = [
        item
        for item in exported_items
        if item.eligibility_status == EligibilityStatus.REVIEW_REQUIRED.value
    ]
    blocked_items = [
        item
        for item in exported_items
        if item.eligibility_status == EligibilityStatus.BLOCKED.value
    ]

    package = PatternQuestionInputPackage(
        source_file_name=source_file_name,
        answer_mode=eligibility.answer_mode,
        quality_gate_status=quality_gate_status,
        total_source_questions=final_package.total_questions,
        exported_count=len(exported_items),
        export_mode=export_mode,
        items=exported_items,
        source_artifact_paths=source_artifact_paths,
        warnings=warnings,
        errors=errors,
    )

    output_paths = {
        "package_json": str(paths.package_json),
        "eligible_json": str(paths.eligible_json),
        "review_json": str(paths.review_json),
        "blocked_json": str(paths.blocked_json),
        "summary_md": str(paths.summary_md),
    }

    return PatternQuestionInputBuildResult(
        status=PatternInputBuildStatus.SUCCEEDED if not errors else PatternInputBuildStatus.FAILED,
        package=package,
        eligible_items=eligible_items,
        review_items=review_items,
        blocked_items=blocked_items,
        output_paths=output_paths,
    )


def write_pattern_input_outputs(
    result: PatternQuestionInputBuildResult,
    paths: PatternInputPaths,
) -> None:
    """Write pattern input JSON and markdown artifacts."""
    paths.pattern_input_dir.mkdir(parents=True, exist_ok=True)

    for out_path, payload in (
        (paths.package_json, result.package.model_dump(mode="json")),
        (paths.eligible_json, [i.model_dump(mode="json") for i in result.eligible_items]),
        (paths.review_json, [i.model_dump(mode="json") for i in result.review_items]),
        (paths.blocked_json, [i.model_dump(mode="json") for i in result.blocked_items]),
    ):
        assert_output_contains(paths.package_dir, out_path)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    assert_output_contains(paths.package_dir, paths.summary_md)
    paths.summary_md.write_text(
        render_pattern_input_summary(result, paths=paths),
        encoding="utf-8",
    )


def build_pattern_question_input_package(
    package_dir: Path,
    *,
    export_mode: PatternExportMode = PatternExportMode.ELIGIBLE_ONLY,
    allow_missing_eligibility: bool = False,
    allow_failed_quality_gate: bool = False,
) -> PatternQuestionInputBuildResult:
    """Build and write pattern input handoff package."""
    paths = build_pattern_input_paths(package_dir)
    result = build_pattern_question_input(
        package_dir,
        export_mode=export_mode,
        allow_missing_eligibility=allow_missing_eligibility,
        allow_failed_quality_gate=allow_failed_quality_gate,
    )
    if result.status == PatternInputBuildStatus.FAILED:
        raise PatternInputBuildError("; ".join(result.package.errors))
    write_pattern_input_outputs(result, paths)
    return result
