"""Deterministic quality audit for final question packages (Part 7)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    AUDIT_DIR,
    FINAL_DIR,
    FINAL_PACKAGE_AUDIT_JSON_NAME,
    FINAL_PACKAGE_AUDIT_MD_NAME,
    FINAL_QUESTIONS_NAME,
    FINAL_VALIDATION_REPORT_NAME,
)
from meritranker_data_ingestion.schemas.final_package_audit import (
    AuditIssueSeverity,
    AuditStatus,
    FinalPackageAuditIssue,
    FinalPackageAuditReport,
)
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionItem,
    FinalQuestionPackage,
    FinalQuestionValidationReport,
    ValidationStatus,
)
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)

IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]+\)")

HIGH_RISK_STATUSES = frozenset({
    ValidationStatus.NEEDS_REVIEW,
    ValidationStatus.INCOMPLETE,
    ValidationStatus.DUPLICATE_CONFLICT,
    ValidationStatus.REJECTED,
})


class AuditError(Exception):
    """Raised when audit cannot proceed."""


@dataclass(frozen=True)
class AuditPaths:
    """Paths for final package audit input/output."""

    package_dir: Path
    final_dir: Path
    questions_json: Path
    validation_report_json: Path
    audit_dir: Path
    audit_json: Path
    audit_md: Path


def build_audit_paths(package_dir: Path) -> AuditPaths:
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")

    final_dir = resolved / FINAL_DIR
    audit_dir = resolved / AUDIT_DIR
    return AuditPaths(
        package_dir=resolved,
        final_dir=final_dir,
        questions_json=final_dir / FINAL_QUESTIONS_NAME,
        validation_report_json=final_dir / FINAL_VALIDATION_REPORT_NAME,
        audit_dir=audit_dir,
        audit_json=audit_dir / FINAL_PACKAGE_AUDIT_JSON_NAME,
        audit_md=audit_dir / FINAL_PACKAGE_AUDIT_MD_NAME,
    )


def _expected_count_is_very_different(detected: int, expected: int) -> bool:
    if expected <= 0:
        return detected != expected
    tolerance = max(5, int(expected * 0.1))
    return abs(detected - expected) > tolerance


def _has_image_markdown(text: str) -> bool:
    return bool(IMAGE_MARKDOWN_PATTERN.search(text))


def _is_high_risk(item: FinalQuestionItem) -> bool:
    if item.validation_status in HIGH_RISK_STATUSES:
        return True
    if "answer_option_mismatch" in item.issues:
        return True
    if _has_image_markdown(item.raw_text) and not item.assets:
        return True
    if item.solution.available and item.solution.image_references and not item.assets:
        return True
    return False


def audit_final_package(
    package: FinalQuestionPackage,
    validation_report: FinalQuestionValidationReport | None = None,
    *,
    expected_question_count: int | None = None,
) -> FinalPackageAuditReport:
    """Run deterministic quality audit on a final question package."""
    issues: list[FinalPackageAuditIssue] = []
    warnings: list[str] = []
    errors: list[str] = []

    total_questions = package.total_questions
    item_count = len(package.items)

    if item_count == 0:
        errors.append("total_questions is zero")
        return FinalPackageAuditReport(
            status=AuditStatus.FAILED,
            source_file_name=package.source_file_name,
            total_questions=0,
            expected_question_count=expected_question_count,
            expected_count_match=False if expected_question_count is not None else None,
            errors=errors,
            issues=[
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.ERROR,
                    issue_type="zero_questions",
                    message="Final package contains zero questions",
                ),
            ],
        )

    if total_questions != item_count:
        errors.append(
            f"schema_inconsistency: total_questions ({total_questions}) != items ({item_count})",
        )
        issues.append(
            FinalPackageAuditIssue(
                severity=AuditIssueSeverity.ERROR,
                issue_type="schema_inconsistency",
                message=f"Package total_questions ({total_questions}) does not match item count ({item_count})",
            ),
        )

    if validation_report and validation_report.total_questions != item_count:
        errors.append(
            f"validation_report_mismatch: report total ({validation_report.total_questions}) "
            f"!= items ({item_count})",
        )
        issues.append(
            FinalPackageAuditIssue(
                severity=AuditIssueSeverity.ERROR,
                issue_type="validation_report_mismatch",
                message=(
                    f"Validation report total_questions ({validation_report.total_questions}) "
                    f"does not match package items ({item_count})"
                ),
            ),
        )

    validated_count = sum(
        1 for i in package.items if i.validation_status == ValidationStatus.VALIDATED
    )
    question_only_count = sum(
        1 for i in package.items
        if i.validation_status == ValidationStatus.QUESTION_ONLY_VALIDATED
    )
    needs_review_count = sum(
        1 for i in package.items if i.validation_status == ValidationStatus.NEEDS_REVIEW
    )
    incomplete_count = sum(
        1 for i in package.items if i.validation_status == ValidationStatus.INCOMPLETE
    )
    duplicate_conflict_count = sum(
        1 for i in package.items if i.validation_status == ValidationStatus.DUPLICATE_CONFLICT
    )
    candidates_without_options = sum(1 for i in package.items if not i.options)
    answer_mismatch_count = sum(
        1 for i in package.items if "answer_option_mismatch" in i.issues
    )
    visual_count = sum(1 for i in package.items if i.assets)
    answered_count = sum(1 for i in package.items if i.answer.available)
    solved_count = sum(1 for i in package.items if i.solution.available)

    missing_numbers = (
        list(validation_report.missing_question_numbers)
        if validation_report
        else []
    )
    duplicate_numbers = (
        list(validation_report.duplicate_question_numbers)
        if validation_report
        else []
    )

    if validation_report:
        answer_mismatch_count = max(answer_mismatch_count, validation_report.answer_option_mismatch_count)

    expected_count_match: bool | None = None
    if expected_question_count is not None:
        expected_count_match = total_questions == expected_question_count
        if _expected_count_is_very_different(total_questions, expected_question_count):
            errors.append(
                f"expected_count_mismatch: detected {total_questions}, expected {expected_question_count}",
            )
            issues.append(
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.ERROR,
                    issue_type="expected_count_mismatch",
                    message=(
                        f"Detected {total_questions} questions, expected {expected_question_count} "
                        f"(tolerance exceeded)"
                    ),
                ),
            )
        elif not expected_count_match:
            warnings.append(
                f"Expected {expected_question_count} questions, detected {total_questions}",
            )
            issues.append(
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.WARNING,
                    issue_type="expected_count_difference",
                    message=(
                        f"Detected {total_questions} questions, expected {expected_question_count}"
                    ),
                ),
            )

    if missing_numbers:
        warnings.append(f"Missing question numbers: {missing_numbers}")
        for num in missing_numbers:
            issues.append(
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.WARNING,
                    issue_type="missing_question_number",
                    question_number=num,
                    message=f"Question number {num} is missing from sequence",
                ),
            )

    if duplicate_numbers:
        warnings.append(f"Duplicate question numbers: {duplicate_numbers}")
        for num in duplicate_numbers:
            issues.append(
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.WARNING,
                    issue_type="duplicate_question_number",
                    question_number=num,
                    message=f"Question number {num} appears more than once",
                ),
            )

    if needs_review_count > 0:
        warnings.append(f"{needs_review_count} item(s) need review")
        issues.append(
            FinalPackageAuditIssue(
                severity=AuditIssueSeverity.WARNING,
                issue_type="needs_review_items",
                message=f"{needs_review_count} question(s) marked needs_review",
            ),
        )

    if incomplete_count > 0:
        warnings.append(f"{incomplete_count} incomplete item(s)")
        issues.append(
            FinalPackageAuditIssue(
                severity=AuditIssueSeverity.WARNING,
                issue_type="incomplete_items",
                message=f"{incomplete_count} question(s) marked incomplete",
            ),
        )

    if answer_mismatch_count > 0:
        warnings.append(f"{answer_mismatch_count} answer-option mismatch(es)")
        issues.append(
            FinalPackageAuditIssue(
                severity=AuditIssueSeverity.WARNING,
                issue_type="answer_option_mismatch",
                message=f"{answer_mismatch_count} question(s) have answer key not matching options",
            ),
        )

    high_risk_items: list[str] = []
    for item in package.items:
        if _is_high_risk(item):
            high_risk_items.append(item.question_id)
            trace = None
            if item.source_trace:
                trace = item.source_trace.model_dump(mode="json")
            issues.append(
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.WARNING,
                    issue_type="high_risk_item",
                    question_id=item.question_id,
                    question_number=item.question_number,
                    message=(
                        f"Question {item.question_id} status={item.validation_status.value} "
                        f"issues={item.issues}"
                    ),
                    source_trace=trace,
                ),
            )

        if _has_image_markdown(item.raw_text) and not item.assets:
            warnings.append(f"Visual reference without assets: {item.question_id}")
            issues.append(
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.WARNING,
                    issue_type="visual_without_assets",
                    question_id=item.question_id,
                    question_number=item.question_number,
                    message="Image markdown detected but no assets preserved",
                    source_trace=item.source_trace.model_dump(mode="json"),
                ),
            )

        if not item.options:
            issues.append(
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.INFO,
                    issue_type="no_options",
                    question_id=item.question_id,
                    question_number=item.question_number,
                    message="Question has no options",
                ),
            )

    status = _determine_status(errors, warnings)
    return FinalPackageAuditReport(
        status=status,
        source_file_name=package.source_file_name,
        total_questions=total_questions,
        expected_question_count=expected_question_count,
        expected_count_match=expected_count_match,
        validated_count=validated_count,
        question_only_validated_count=question_only_count,
        needs_review_count=needs_review_count,
        incomplete_count=incomplete_count,
        duplicate_conflict_count=duplicate_conflict_count,
        visual_question_count=visual_count,
        answered_count=answered_count,
        solved_count=solved_count,
        candidates_without_options=candidates_without_options,
        answer_option_mismatch_count=answer_mismatch_count,
        missing_question_numbers=missing_numbers,
        duplicate_question_numbers=duplicate_numbers,
        high_risk_items=high_risk_items,
        issues=issues,
        warnings=warnings,
        errors=errors,
    )


def _determine_status(errors: list[str], warnings: list[str]) -> AuditStatus:
    if errors:
        return AuditStatus.FAILED
    if warnings:
        return AuditStatus.WARNING
    return AuditStatus.PASSED


def render_audit_markdown(report: FinalPackageAuditReport) -> str:
    """Render human-readable markdown audit report."""
    lines = [
        "# Final Package Quality Audit",
        "",
        "> **Note:** This audit is read-only. It does not modify `final/questions.json` "
        "or any source artifacts.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Status | **{report.status.value}** |",
        f"| Source file | {report.source_file_name or 'unknown'} |",
        f"| Total questions | {report.total_questions} |",
    ]

    if report.expected_question_count is not None:
        lines.append(f"| Expected count | {report.expected_question_count} |")
        match_label = (
            "yes" if report.expected_count_match else "no"
            if report.expected_count_match is not None
            else "n/a"
        )
        lines.append(f"| Expected count match | {match_label} |")

    lines.extend([
        f"| Validated | {report.validated_count} |",
        f"| Question-only validated | {report.question_only_validated_count} |",
        f"| Needs review | {report.needs_review_count} |",
        f"| Incomplete | {report.incomplete_count} |",
        f"| Duplicate conflict | {report.duplicate_conflict_count} |",
        f"| Visual questions | {report.visual_question_count} |",
        f"| Answered | {report.answered_count} |",
        f"| Solved | {report.solved_count} |",
        f"| Without options | {report.candidates_without_options} |",
        f"| Answer-option mismatches | {report.answer_option_mismatch_count} |",
        f"| High-risk items | {len(report.high_risk_items)} |",
        "",
        "## Quality Findings",
        "",
    ])

    if not report.issues:
        lines.append("No issues detected.")
    else:
        for issue in report.issues:
            qref = ""
            if issue.question_id:
                qref = f" (`{issue.question_id}`)"
            lines.append(
                f"- **[{issue.severity.value}]** {issue.issue_type}{qref}: {issue.message}",
            )

    lines.extend(["", "## High-Risk Questions", ""])
    if report.high_risk_items:
        for qid in report.high_risk_items:
            lines.append(f"- `{qid}`")
    else:
        lines.append("None identified.")

    if report.missing_question_numbers:
        lines.extend([
            "",
            "## Missing Question Numbers",
            "",
            ", ".join(str(n) for n in report.missing_question_numbers),
        ])

    if report.duplicate_question_numbers:
        lines.extend([
            "",
            "## Duplicate Question Numbers",
            "",
            ", ".join(str(n) for n in report.duplicate_question_numbers),
        ])

    lines.extend([
        "",
        "## Recommended Next Action",
        "",
    ])

    if report.status == AuditStatus.FAILED:
        lines.append(
            "1. Resolve critical errors before pattern ingestion.\n"
            "2. Re-run full pipeline if final package is missing or empty.\n"
            "3. Review extraction/classification if expected count is far off.",
        )
    elif report.status == AuditStatus.WARNING:
        lines.append(
            "1. Review high-risk questions and items flagged needs_review/incomplete.\n"
            "2. Verify answer-option mismatches against source PDF.\n"
            "3. Proceed to manual review; do not start pattern ingestion until resolved.",
        )
    else:
        lines.append(
            "1. Spot-check a sample of validated questions against source PDF.\n"
            "2. Audit 2–3 more real PDFs before deciding Part 8 scope.\n"
            "3. Do not start pattern ingestion until multiple samples pass audit.",
        )

    if report.warnings:
        lines.extend(["", "## Warnings", ""])
        for w in report.warnings:
            lines.append(f"- {w}")

    if report.errors:
        lines.extend(["", "## Errors", ""])
        for e in report.errors:
            lines.append(f"- {e}")

    lines.append("")
    return "\n".join(lines)


def write_audit_outputs(report: FinalPackageAuditReport, paths: AuditPaths) -> None:
    """Write audit JSON and markdown under extraction_package/audit/."""
    assert_output_contains(paths.package_dir, paths.audit_dir)
    paths.audit_dir.mkdir(parents=True, exist_ok=True)

    assert_output_contains(paths.package_dir, paths.audit_json)
    paths.audit_json.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )

    assert_output_contains(paths.package_dir, paths.audit_md)
    paths.audit_md.write_text(render_audit_markdown(report), encoding="utf-8")


def _load_package(path: Path) -> FinalQuestionPackage:
    data = json.loads(path.read_text(encoding="utf-8"))
    return FinalQuestionPackage.model_validate(data)


def _load_validation_report(path: Path) -> FinalQuestionValidationReport | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return FinalQuestionValidationReport.model_validate(data)


def audit_final_package_from_directory(
    package_dir: Path,
    *,
    expected_question_count: int | None = None,
) -> FinalPackageAuditReport:
    """Load final artifacts, run audit, write outputs."""
    paths = build_audit_paths(package_dir)

    if not paths.questions_json.is_file():
        report = FinalPackageAuditReport(
            status=AuditStatus.FAILED,
            expected_question_count=expected_question_count,
            errors=[f"Missing final package: {paths.questions_json}"],
            issues=[
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.ERROR,
                    issue_type="missing_final_package",
                    message=f"Missing final package: {paths.questions_json}",
                ),
            ],
        )
        _try_write_audit_outputs(report, paths)
        raise AuditError(report.errors[0])

    try:
        package = _load_package(paths.questions_json)
    except (json.JSONDecodeError, ValueError) as exc:
        report = FinalPackageAuditReport(
            status=AuditStatus.FAILED,
            expected_question_count=expected_question_count,
            errors=[f"Unreadable final package: {exc}"],
            issues=[
                FinalPackageAuditIssue(
                    severity=AuditIssueSeverity.ERROR,
                    issue_type="unreadable_final_package",
                    message=f"Cannot parse final package: {exc}",
                ),
            ],
        )
        _try_write_audit_outputs(report, paths)
        raise AuditError(report.errors[0]) from exc

    validation_report = _load_validation_report(paths.validation_report_json)
    report = audit_final_package(
        package,
        validation_report,
        expected_question_count=expected_question_count,
    )
    write_audit_outputs(report, paths)
    return report


def _try_write_audit_outputs(report: FinalPackageAuditReport, paths: AuditPaths) -> None:
    try:
        write_audit_outputs(report, paths)
    except (PathValidationError, OSError):
        pass
