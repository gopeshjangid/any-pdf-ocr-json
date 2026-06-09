"""Cross-artifact reconciliation and quality gate (Part 11)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    ANSWER_SOLUTION_MAP_NAME,
    ANSWER_SOLUTION_REPORT_NAME,
    ARTIFACT_RECONCILIATION_JSON_NAME,
    ARTIFACT_RECONCILIATION_MD_NAME,
    AUDIT_DIR,
    BLOCKED_QUESTIONS_NAME,
    DIAGNOSTICS_DIR,
    ELIGIBILITY_DIR,
    ELIGIBLE_QUESTIONS_NAME,
    FINAL_DIR,
    FINAL_PACKAGE_AUDIT_JSON_NAME,
    FINAL_QUESTIONS_NAME,
    FINAL_VALIDATION_REPORT_NAME,
    INGESTION_ELIGIBILITY_REPORT_NAME,
    MAPPINGS_DIR,
    PACKAGE_MANIFEST_NAME,
    QUESTION_CANDIDATE_REPORT_NAME,
    QUESTION_CANDIDATES_NAME,
    QUESTION_STRUCTURE_AUDIT_NAME,
    QUESTIONS_DIR,
    REVIEW_DIR,
    REVIEW_ITEMS_JSON_NAME,
    REVIEW_REQUIRED_QUESTIONS_NAME,
)
from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    MappingStatus,
    QuestionAnswerSolutionMapping,
)
from meritranker_data_ingestion.schemas.artifact_reconciliation import (
    ArtifactReconciliationReport,
    ArtifactReconciliationSummary,
    QualityGateStatus,
    ReconciliationCheck,
    ReconciliationSeverity,
)
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionPackage,
    FinalQuestionValidationReport,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.ingestion_eligibility import (
    EligibilityStatus,
    IngestionEligibilityReport,
)
from meritranker_data_ingestion.schemas.question_candidates import QuestionCandidate
from meritranker_data_ingestion.schemas.review_export import ReviewExportReport
from meritranker_data_ingestion.services.candidate_report_metrics import (
    compute_candidate_report_metrics,
    normalize_issue_name,
)
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)
from meritranker_data_ingestion.services.visual_intent_detector import is_visual_dependent

REPORT_COUNTER_FIELDS = (
    "total_candidates",
    "valid_candidates",
    "needs_review_candidates",
    "incomplete_candidates",
    "duplicate_candidates",
    "rejected_candidates",
    "candidates_with_images",
    "candidates_with_question_images",
    "candidates_with_question_support_images",
    "candidates_with_option_images",
    "candidates_with_linked_option_assets",
    "candidates_with_noise",
    "noise_asset_count",
    "candidates_with_no_options",
    "candidates_with_partial_options",
    "candidates_with_invalid_option_count",
    "visual_dependent_count",
    "visual_text_option_count",
    "visual_image_option_count",
    "visual_missing_option_labels_count",
    "same_line_option_split_count",
    "source_backed_option_labels_missing_count",
    "unlabeled_visual_assets_count",
    "possible_noise_asset_after_options_count",
)

REVIEW_STATUSES = frozenset({
    ValidationStatus.NEEDS_REVIEW,
    ValidationStatus.INCOMPLETE,
    ValidationStatus.DUPLICATE_CONFLICT,
    ValidationStatus.REJECTED,
})


class ArtifactReconciliationError(Exception):
    """Raised when reconciliation cannot proceed."""


@dataclass(frozen=True)
class ReconciliationPaths:
    package_dir: Path
    diagnostics_dir: Path
    report_json: Path
    report_md: Path


def build_reconciliation_paths(package_dir: Path) -> ReconciliationPaths:
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")
    diagnostics_dir = resolved / DIAGNOSTICS_DIR
    return ReconciliationPaths(
        package_dir=resolved,
        diagnostics_dir=diagnostics_dir,
        report_json=diagnostics_dir / ARTIFACT_RECONCILIATION_JSON_NAME,
        report_md=diagnostics_dir / ARTIFACT_RECONCILIATION_MD_NAME,
    )


def _load_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _add_check(
    checks: list[ReconciliationCheck],
    *,
    check_id: str,
    category: str,
    severity: ReconciliationSeverity,
    message: str,
    expected=None,
    actual=None,
) -> None:
    checks.append(
        ReconciliationCheck(
            check_id=check_id,
            category=category,
            severity=severity,
            message=message,
            expected=expected,
            actual=actual,
        ),
    )


def _reconcile_candidate_report(
    candidates: list[QuestionCandidate],
    report: dict | None,
    checks: list[ReconciliationCheck],
) -> dict[str, int]:
    """Reconcile candidate report counters against actual candidates."""
    actual = compute_candidate_report_metrics(candidates)
    actual_status = {
        key: actual.get(key, 0)
        for key in (
            "valid_candidates",
            "needs_review_candidates",
            "incomplete_candidates",
            "duplicate_candidates",
            "rejected_candidates",
        )
    }
    actual_distribution = {}
    for candidate in candidates:
        key = candidate.review_status.value
        actual_distribution[key] = actual_distribution.get(key, 0) + 1

    if report is None:
        _add_check(
            checks,
            check_id="candidate_report_missing",
            category="candidate",
            severity=ReconciliationSeverity.FAILED,
            message="question-candidate-report.json is missing",
        )
        return actual

    reported_total = report.get("total_candidates", -1)
    if reported_total != actual["total_candidates"]:
        _add_check(
            checks,
            check_id="candidate_total_mismatch",
            category="candidate",
            severity=ReconciliationSeverity.FAILED,
            message="Reported total_candidates does not match actual candidate count",
            expected=actual["total_candidates"],
            actual=reported_total,
        )
    else:
        _add_check(
            checks,
            check_id="candidate_total_match",
            category="candidate",
            severity=ReconciliationSeverity.PASSED,
            message="total_candidates matches actual count",
            expected=actual["total_candidates"],
            actual=reported_total,
        )

    reported_dist = report.get("status_distribution", {})
    if reported_dist != actual_distribution:
        _add_check(
            checks,
            check_id="candidate_status_distribution_mismatch",
            category="candidate",
            severity=ReconciliationSeverity.FAILED,
            message="Reported status_distribution does not match actual statuses",
            expected=actual_distribution,
            actual=reported_dist,
        )
    else:
        _add_check(
            checks,
            check_id="candidate_status_distribution_match",
            category="candidate",
            severity=ReconciliationSeverity.PASSED,
            message="status_distribution matches actual statuses",
        )

    if sum(reported_dist.values()) != actual["total_candidates"] and reported_dist:
        _add_check(
            checks,
            check_id="candidate_status_distribution_sum_mismatch",
            category="candidate",
            severity=ReconciliationSeverity.FAILED,
            message="status_distribution sum does not equal total_candidates",
            expected=actual["total_candidates"],
            actual=sum(reported_dist.values()),
        )

    for field in REPORT_COUNTER_FIELDS:
        if field == "total_candidates":
            continue
        reported_val = report.get(field)
        if reported_val is None:
            continue
        actual_val = actual.get(field, 0)
        if reported_val != actual_val:
            _add_check(
                checks,
                check_id=f"candidate_{field}_mismatch",
                category="candidate",
                severity=ReconciliationSeverity.FAILED,
                message=f"Reported {field} does not match derived count",
                expected=actual_val,
                actual=reported_val,
            )

    reported_missing = set(report.get("missing_question_numbers", []))
    actual_numbers = {c.question_number for c in candidates if c.question_number is not None}
    if actual_numbers and min(actual_numbers) == 1:
        expected_max = max(actual_numbers)
        expected_missing = set(range(1, expected_max + 1)) - actual_numbers
        if reported_missing != expected_missing:
            _add_check(
                checks,
                check_id="candidate_missing_numbers_mismatch",
                category="candidate",
                severity=ReconciliationSeverity.WARNING,
                message="Reported missing_question_numbers differs from derived set",
                expected=sorted(expected_missing),
                actual=sorted(reported_missing),
            )

    if actual["candidates_with_noise"] > 0 and report.get("candidates_with_noise", 0) == 0:
        _add_check(
            checks,
            check_id="candidate_noise_zero_when_actual_nonzero",
            category="candidate",
            severity=ReconciliationSeverity.FAILED,
            message="Report claims zero noise candidates but actual candidates have noise assets/issues",
            expected=actual["candidates_with_noise"],
            actual=report.get("candidates_with_noise", 0),
        )

    return actual


def _reconcile_mapping(
    candidates: list[QuestionCandidate],
    mappings: list[QuestionAnswerSolutionMapping],
    mapping_report: dict | None,
    checks: list[ReconciliationCheck],
) -> dict[str, int]:
    """Reconcile mapping artifacts against candidates."""
    stats = {
        "mapping_count": len(mappings),
        "mapped_count": sum(
            1 for m in mappings if m.mapping_status == MappingStatus.MAPPED
        ),
        "answer_available_count": sum(1 for m in mappings if m.answer_available),
        "solution_available_count": sum(1 for m in mappings if m.solution_available),
        "answer_key_not_in_candidate_options_count": 0,
    }

    candidate_by_id = {c.question_id: c for c in candidates}
    candidate_ids = set(candidate_by_id)
    mapping_ids = {m.question_id for m in mappings}

    if len(mappings) != len(candidates):
        _add_check(
            checks,
            check_id="mapping_count_mismatch",
            category="mapping",
            severity=ReconciliationSeverity.FAILED,
            message="Mapping count does not match candidate count",
            expected=len(candidates),
            actual=len(mappings),
        )
    else:
        _add_check(
            checks,
            check_id="mapping_count_match",
            category="mapping",
            severity=ReconciliationSeverity.PASSED,
            message="Mapping count matches candidate count",
        )

    missing_mappings = sorted(candidate_ids - mapping_ids)
    if missing_mappings:
        _add_check(
            checks,
            check_id="candidate_missing_mapping",
            category="mapping",
            severity=ReconciliationSeverity.FAILED,
            message="Some candidate question_ids have no mapping record",
            expected=[],
            actual=missing_mappings[:10],
        )

    extra_mappings = sorted(mapping_ids - candidate_ids)
    if extra_mappings:
        _add_check(
            checks,
            check_id="mapping_orphan_records",
            category="mapping",
            severity=ReconciliationSeverity.FAILED,
            message="Mapping records exist without matching candidates",
            expected=[],
            actual=extra_mappings[:10],
        )

    if mapping_report is not None:
        reported_mapped = mapping_report.get("mapped_count")
        if reported_mapped is not None and reported_mapped != stats["mapped_count"]:
            _add_check(
                checks,
                check_id="mapping_report_mapped_count_mismatch",
                category="mapping",
                severity=ReconciliationSeverity.FAILED,
                message="answer-solution-report mapped_count does not match actual",
                expected=stats["mapped_count"],
                actual=reported_mapped,
            )

    for mapping in mappings:
        candidate = candidate_by_id.get(mapping.question_id)
        if candidate is None or not mapping.answer_available or not mapping.answer:
            continue
        answer_key = mapping.answer.answer_key
        if not answer_key:
            continue
        option_keys = {opt.key for opt in candidate.options if opt.key}
        if option_keys and answer_key not in option_keys:
            stats["answer_key_not_in_candidate_options_count"] += 1

    if stats["answer_key_not_in_candidate_options_count"] > 0:
        _add_check(
            checks,
            check_id="answer_key_not_in_candidate_options",
            category="mapping",
            severity=ReconciliationSeverity.WARNING,
            message="Some mapped answer keys are not present in candidate options",
            actual=stats["answer_key_not_in_candidate_options_count"],
        )

    candidate_numbers = {
        c.question_number for c in candidates if c.question_number is not None
    }
    mapped_numbers = {
        m.question_number for m in mappings if m.question_number is not None
    }
    not_in_candidates = sorted(mapped_numbers - candidate_numbers)
    not_in_mappings = sorted(candidate_numbers - mapped_numbers)
    if not_in_candidates:
        _add_check(
            checks,
            check_id="mapped_question_numbers_not_in_candidates",
            category="mapping",
            severity=ReconciliationSeverity.FAILED,
            message="Mapped question numbers missing from candidates",
            actual=not_in_candidates[:10],
        )
    if not_in_mappings:
        _add_check(
            checks,
            check_id="candidate_question_numbers_not_in_mappings",
            category="mapping",
            severity=ReconciliationSeverity.FAILED,
            message="Candidate question numbers missing from mappings",
            actual=not_in_mappings[:10],
        )

    return stats


def _reconcile_final(
    candidates: list[QuestionCandidate],
    final_package: FinalQuestionPackage | None,
    validation_report: FinalQuestionValidationReport | None,
    checks: list[ReconciliationCheck],
) -> dict[str, int]:
    """Reconcile final package against candidates and validation report."""
    stats = {"visual_question_count": 0}
    if final_package is None:
        _add_check(
            checks,
            check_id="final_package_missing",
            category="final",
            severity=ReconciliationSeverity.FAILED,
            message="final/questions.json is missing",
        )
        return stats

    items = final_package.items
    candidate_ids = {c.question_id for c in candidates}
    final_ids = {item.question_id for item in items}

    if len(items) != len(candidates):
        _add_check(
            checks,
            check_id="final_total_mismatch",
            category="final",
            severity=ReconciliationSeverity.FAILED,
            message="Final item count does not match candidate count",
            expected=len(candidates),
            actual=len(items),
        )
    else:
        _add_check(
            checks,
            check_id="final_total_match",
            category="final",
            severity=ReconciliationSeverity.PASSED,
            message="Final item count matches candidate count",
        )

    missing_final = sorted(candidate_ids - final_ids)
    if missing_final:
        _add_check(
            checks,
            check_id="candidate_missing_final_item",
            category="final",
            severity=ReconciliationSeverity.FAILED,
            message="Some candidates have no final item",
            actual=missing_final[:10],
        )

    if validation_report is not None:
        actual_dist: dict[str, int] = {}
        for item in items:
            key = item.validation_status.value
            actual_dist[key] = actual_dist.get(key, 0) + 1
        reported_dist = validation_report.status_distribution
        if reported_dist != actual_dist:
            _add_check(
                checks,
                check_id="final_status_distribution_mismatch",
                category="final",
                severity=ReconciliationSeverity.FAILED,
                message="validation-report status_distribution does not match final items",
                expected=actual_dist,
                actual=reported_dist,
            )

    stats["visual_question_count"] = sum(
        1
        for item in items
        if item.assets
        or is_visual_dependent(item.question_text_raw)
        or any(
            normalize_issue_name(issue)
            in {
                "visual_question_requires_review",
                "visual_question_requires_diagram_syntax",
            }
            for issue in item.issues
        )
    )

    if validation_report is not None and validation_report.total_questions != len(items):
        _add_check(
            checks,
            check_id="validation_report_total_mismatch",
            category="final",
            severity=ReconciliationSeverity.FAILED,
            message="validation-report total_questions does not match final items",
            expected=len(items),
            actual=validation_report.total_questions,
        )

    return stats


def _reconcile_review(
    final_package: FinalQuestionPackage | None,
    review_report: ReviewExportReport | None,
    checks: list[ReconciliationCheck],
) -> None:
    """Reconcile review export against final package."""
    if final_package is None:
        return

    if review_report is None:
        _add_check(
            checks,
            check_id="review_export_missing",
            category="review",
            severity=ReconciliationSeverity.WARNING,
            message="review-items.json is missing",
        )
        return

    if review_report.review_item_count != len(review_report.items):
        _add_check(
            checks,
            check_id="review_item_count_mismatch",
            category="review",
            severity=ReconciliationSeverity.FAILED,
            message="review_item_count does not match actual review items",
            expected=len(review_report.items),
            actual=review_report.review_item_count,
        )

    final_by_id = {item.question_id: item for item in final_package.items}
    for review_item in review_report.items:
        if review_item.question_id not in final_by_id:
            _add_check(
                checks,
                check_id="review_item_missing_final",
                category="review",
                severity=ReconciliationSeverity.FAILED,
                message=f"Review item {review_item.question_id} not found in final package",
            )

    flagged_ids = {
        item.question_id
        for item in final_package.items
        if item.validation_status in REVIEW_STATUSES
        or item.issues
    }
    review_ids = {item.question_id for item in review_report.items}
    missing_from_review = sorted(flagged_ids - review_ids)
    if missing_from_review:
        _add_check(
            checks,
            check_id="flagged_final_items_missing_from_review",
            category="review",
            severity=ReconciliationSeverity.WARNING,
            message="Some flagged final items are not in review export",
            actual=missing_from_review[:10],
        )


def _reconcile_eligibility(
    final_package: FinalQuestionPackage | None,
    eligibility: IngestionEligibilityReport | None,
    eligible_items: list | None,
    review_items: list | None,
    blocked_items: list | None,
    mapping_stats: dict[str, int],
    checks: list[ReconciliationCheck],
    warnings: list[str],
) -> bool:
    """Reconcile eligibility artifacts. Returns whether eligibility was built."""
    if eligibility is None:
        warnings.append("eligibility_not_built")
        _add_check(
            checks,
            check_id="eligibility_not_built",
            category="eligibility",
            severity=ReconciliationSeverity.WARNING,
            message="Eligibility artifacts were not built; skipping eligibility reconciliation",
        )
        return False

    total = eligibility.total_questions
    sum_counts = (
        eligibility.eligible_count
        + eligibility.review_required_count
        + eligibility.blocked_count
    )
    if sum_counts != total:
        _add_check(
            checks,
            check_id="eligibility_sum_mismatch",
            category="eligibility",
            severity=ReconciliationSeverity.FAILED,
            message="eligible + review_required + blocked does not equal total_questions",
            expected=total,
            actual=sum_counts,
        )

    if eligible_items is not None and len(eligible_items) != eligibility.eligible_count:
        _add_check(
            checks,
            check_id="eligible_file_count_mismatch",
            category="eligibility",
            severity=ReconciliationSeverity.FAILED,
            message="eligible-questions.json count does not match eligible_count",
            expected=eligibility.eligible_count,
            actual=len(eligible_items),
        )

    if review_items is not None and len(review_items) != eligibility.review_required_count:
        _add_check(
            checks,
            check_id="review_required_file_count_mismatch",
            category="eligibility",
            severity=ReconciliationSeverity.FAILED,
            message="review-required-questions.json count does not match review_required_count",
            expected=eligibility.review_required_count,
            actual=len(review_items),
        )

    if blocked_items is not None and len(blocked_items) != eligibility.blocked_count:
        _add_check(
            checks,
            check_id="blocked_file_count_mismatch",
            category="eligibility",
            severity=ReconciliationSeverity.FAILED,
            message="blocked-questions.json count does not match blocked_count",
            expected=eligibility.blocked_count,
            actual=len(blocked_items),
        )

    eligible_ids = {item.get("question_id") for item in (eligible_items or [])}
    blocked_ids = {item.get("question_id") for item in (blocked_items or [])}
    overlap = eligible_ids & blocked_ids
    if overlap:
        _add_check(
            checks,
            check_id="blocked_in_eligible",
            category="eligibility",
            severity=ReconciliationSeverity.FAILED,
            message="Blocked questions appear in eligible-questions.json",
            actual=sorted(overlap)[:10],
        )

    for item in eligibility.items:
        if item.eligibility_status != EligibilityStatus.ELIGIBLE_FOR_INGESTION:
            continue
        if item.duplicate_solution_issue:
            _add_check(
                checks,
                check_id="duplicate_conflict_eligible",
                category="eligibility",
                severity=ReconciliationSeverity.FAILED,
                message=f"Question {item.question_id} has duplicate solution conflict but is eligible",
            )
        visual_reasons = {
            "visual_question_requires_review",
            "visual_question_requires_diagram_syntax",
        }
        is_visual_eligible = (
            item.has_linked_option_assets
            or any(
                reason in visual_reasons
                for reason in item.review_reasons + item.eligibility_reasons
            )
        )
        if final_package:
            final_item = next(
                (i for i in final_package.items if i.question_id == item.question_id),
                None,
            )
            if final_item and is_visual_dependent(final_item.question_text_raw):
                is_visual_eligible = True
        if is_visual_eligible:
            _add_check(
                checks,
                check_id="visual_dependent_eligible",
                category="eligibility",
                severity=ReconciliationSeverity.FAILED,
                message=f"Visual-dependent question {item.question_id} is eligible without approval",
            )
        if "answer_option_mismatch" in item.blocking_reasons:
            _add_check(
                checks,
                check_id="answer_mismatch_eligible",
                category="eligibility",
                severity=ReconciliationSeverity.FAILED,
                message=f"Question {item.question_id} has answer_option_mismatch but is eligible",
            )

    if final_package and mapping_stats["answer_key_not_in_candidate_options_count"] > 0:
        for item in eligibility.items:
            if item.eligibility_status != EligibilityStatus.ELIGIBLE_FOR_INGESTION:
                continue
            final_item = next(
                (i for i in final_package.items if i.question_id == item.question_id),
                None,
            )
            if final_item and final_item.answer.available and final_item.answer.key:
                option_keys = {opt.key for opt in final_item.options if opt.key}
                if option_keys and final_item.answer.key not in option_keys:
                    _add_check(
                        checks,
                        check_id="answer_key_not_in_options_eligible",
                        category="eligibility",
                        severity=ReconciliationSeverity.FAILED,
                        message=f"Question {item.question_id} answer key not in options but eligible",
                    )

    return True


def _compute_quality_gate(
    checks: list[ReconciliationCheck],
    *,
    candidate_metrics: dict[str, int],
    eligibility_built: bool,
    expected_count_match: bool | None,
    incomplete_count: int,
    review_item_count: int | None,
) -> QualityGateStatus:
    """Determine overall quality gate status from checks and package facts."""
    if any(c.severity == ReconciliationSeverity.FAILED for c in checks):
        return QualityGateStatus.FAILED

    warning_triggers = any(c.severity == ReconciliationSeverity.WARNING for c in checks)
    if not eligibility_built:
        warning_triggers = True
    if expected_count_match is False:
        warning_triggers = True
    if incomplete_count > 0:
        warning_triggers = True
    if review_item_count and review_item_count > 0:
        warning_triggers = True
    if candidate_metrics.get("visual_dependent_count", 0) > 0:
        warning_triggers = True
    if candidate_metrics.get("candidates_with_noise", 0) > 0:
        warning_triggers = True
    if candidate_metrics.get("candidates_with_no_options", 0) > 0:
        warning_triggers = True

    if warning_triggers:
        return QualityGateStatus.WARNING
    return QualityGateStatus.PASSED


def _top_issue_counts(candidates: list[QuestionCandidate]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for candidate in candidates:
        for issue in candidate.issues:
            counter[normalize_issue_name(issue)] += 1
        for asset in candidate.assets:
            for issue in asset.issues:
                counter[normalize_issue_name(issue)] += 1
    return dict(counter.most_common(15))


def reconcile_artifacts(package_dir: Path) -> ArtifactReconciliationReport:
    """Run deterministic reconciliation across package artifacts."""
    paths = build_reconciliation_paths(package_dir)
    checks: list[ReconciliationCheck] = []
    warnings: list[str] = []

    candidates_data = _load_json(paths.package_dir / QUESTIONS_DIR / QUESTION_CANDIDATES_NAME)
    if not isinstance(candidates_data, list):
        raise ArtifactReconciliationError(
            f"Missing or invalid {QUESTION_CANDIDATES_NAME}",
        )
    candidates = [QuestionCandidate.model_validate(item) for item in candidates_data]

    candidate_report = _load_json(
        paths.package_dir / QUESTIONS_DIR / QUESTION_CANDIDATE_REPORT_NAME,
    )
    if isinstance(candidate_report, dict):
        pass
    else:
        candidate_report = None

    candidate_metrics = _reconcile_candidate_report(candidates, candidate_report, checks)

    mappings_data = _load_json(paths.package_dir / MAPPINGS_DIR / ANSWER_SOLUTION_MAP_NAME)
    mappings = (
        [QuestionAnswerSolutionMapping.model_validate(m) for m in mappings_data]
        if isinstance(mappings_data, list)
        else []
    )
    mapping_report = _load_json(
        paths.package_dir / MAPPINGS_DIR / ANSWER_SOLUTION_REPORT_NAME,
    )
    if not isinstance(mapping_report, dict):
        mapping_report = None
    mapping_stats = _reconcile_mapping(candidates, mappings, mapping_report, checks)

    final_data = _load_json(paths.package_dir / FINAL_DIR / FINAL_QUESTIONS_NAME)
    final_package = (
        FinalQuestionPackage.model_validate(final_data)
        if isinstance(final_data, dict)
        else None
    )

    validation_data = _load_json(
        paths.package_dir / FINAL_DIR / FINAL_VALIDATION_REPORT_NAME,
    )
    validation_report = (
        FinalQuestionValidationReport.model_validate(validation_data)
        if isinstance(validation_data, dict)
        else None
    )
    final_stats = _reconcile_final(
        candidates,
        final_package,
        validation_report,
        checks,
    )

    review_data = _load_json(paths.package_dir / REVIEW_DIR / REVIEW_ITEMS_JSON_NAME)
    review_report = None
    if isinstance(review_data, dict):
        review_report = ReviewExportReport.model_validate(review_data)
    _reconcile_review(final_package, review_report, checks)

    eligibility_data = _load_json(
        paths.package_dir / ELIGIBILITY_DIR / INGESTION_ELIGIBILITY_REPORT_NAME,
    )
    eligibility = (
        IngestionEligibilityReport.model_validate(eligibility_data)
        if isinstance(eligibility_data, dict)
        else None
    )
    eligible_items = _load_json(
        paths.package_dir / ELIGIBILITY_DIR / ELIGIBLE_QUESTIONS_NAME,
    )
    review_required_items = _load_json(
        paths.package_dir / ELIGIBILITY_DIR / REVIEW_REQUIRED_QUESTIONS_NAME,
    )
    blocked_items = _load_json(
        paths.package_dir / ELIGIBILITY_DIR / BLOCKED_QUESTIONS_NAME,
    )
    eligibility_built = _reconcile_eligibility(
        final_package,
        eligibility,
        eligible_items if isinstance(eligible_items, list) else None,
        review_required_items if isinstance(review_required_items, list) else None,
        blocked_items if isinstance(blocked_items, list) else None,
        mapping_stats,
        checks,
        warnings,
    )

    audit_data = _load_json(
        paths.package_dir / AUDIT_DIR / FINAL_PACKAGE_AUDIT_JSON_NAME,
    )
    expected_count_match = None
    if isinstance(audit_data, dict):
        expected_count_match = audit_data.get("expected_count_match")

    manifest = _load_json(paths.package_dir / PACKAGE_MANIFEST_NAME)
    source_file_name = None
    if isinstance(manifest, dict):
        source_file_name = manifest.get("source_file_name")

    failed_count = sum(1 for c in checks if c.severity == ReconciliationSeverity.FAILED)
    warning_count = sum(1 for c in checks if c.severity == ReconciliationSeverity.WARNING)
    passed_count = sum(1 for c in checks if c.severity == ReconciliationSeverity.PASSED)

    incomplete_count = candidate_metrics.get("incomplete_candidates", 0)
    review_item_count = review_report.review_item_count if review_report else None

    quality_gate = _compute_quality_gate(
        checks,
        candidate_metrics=candidate_metrics,
        eligibility_built=eligibility_built,
        expected_count_match=expected_count_match,
        incomplete_count=incomplete_count,
        review_item_count=review_item_count,
    )

    summary = ArtifactReconciliationSummary(
        source_file_name=source_file_name,
        total_questions=len(candidates),
        expected_count_match=expected_count_match,
        mapped_count=mapping_stats.get("mapped_count"),
        eligible_count=eligibility.eligible_count if eligibility else None,
        review_required_count=eligibility.review_required_count if eligibility else None,
        blocked_count=eligibility.blocked_count if eligibility else None,
        quality_gate_status=quality_gate,
        failed_check_count=failed_count,
        warning_count=warning_count,
        top_issue_counts=_top_issue_counts(candidates),
    )

    return ArtifactReconciliationReport(
        package_dir=str(paths.package_dir),
        quality_gate_status=quality_gate,
        failed_check_count=failed_count,
        warning_count=warning_count,
        passed_check_count=passed_count,
        checks=checks,
        summary=summary,
        eligibility_built=eligibility_built,
        warnings=warnings,
    )


def render_reconciliation_markdown(report: ArtifactReconciliationReport) -> str:
    """Render human-readable reconciliation summary."""
    lines = [
        "# Artifact Reconciliation",
        "",
        f"**Package:** `{report.package_dir}`",
        f"**Quality gate:** `{report.quality_gate_status.value}`",
        f"**Checks:** {report.passed_check_count} passed, "
        f"{report.warning_count} warnings, {report.failed_check_count} failed",
        "",
        "## Summary",
        "",
        f"- Total questions: {report.summary.total_questions}",
        f"- Mapped: {report.summary.mapped_count}",
        f"- Eligible: {report.summary.eligible_count}",
        f"- Review required: {report.summary.review_required_count}",
        f"- Blocked: {report.summary.blocked_count}",
        "",
    ]
    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    failed = [c for c in report.checks if c.severity == ReconciliationSeverity.FAILED]
    if failed:
        lines.append("## Failed Checks")
        lines.append("")
        for check in failed:
            lines.append(f"- `{check.check_id}`: {check.message}")
        lines.append("")

    warn_checks = [c for c in report.checks if c.severity == ReconciliationSeverity.WARNING]
    if warn_checks:
        lines.append("## Warning Checks")
        lines.append("")
        for check in warn_checks:
            lines.append(f"- `{check.check_id}`: {check.message}")
        lines.append("")

    if report.summary.top_issue_counts:
        lines.append("## Top Issue Counts")
        lines.append("")
        for issue, count in report.summary.top_issue_counts.items():
            lines.append(f"- {issue}: {count}")
        lines.append("")

    lines.append(
        "_Reconciliation is read-only. It does not modify source artifacts or perform ingestion._",
    )
    return "\n".join(lines)


def write_reconciliation_outputs(
    report: ArtifactReconciliationReport,
    paths: ReconciliationPaths,
) -> None:
    """Write reconciliation JSON and markdown."""
    paths.diagnostics_dir.mkdir(parents=True, exist_ok=True)
    assert_output_contains(paths.package_dir, paths.report_json)
    paths.report_json.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    assert_output_contains(paths.package_dir, paths.report_md)
    paths.report_md.write_text(
        render_reconciliation_markdown(report),
        encoding="utf-8",
    )


def reconcile_artifacts_package(package_dir: Path) -> ArtifactReconciliationReport:
    """Run reconciliation and write diagnostics artifacts."""
    paths = build_reconciliation_paths(package_dir)
    report = reconcile_artifacts(package_dir)
    write_reconciliation_outputs(report, paths)
    return report
