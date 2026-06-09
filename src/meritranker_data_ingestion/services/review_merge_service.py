"""Merge manually edited review JSON back into public questions (Part 14X/14Y)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.public_questions_audit import (
    PublicQuestionsAuditResult,
    audit_public_questions_json,
)

READY_DIR_NAME = "ready_for_question_bank"
NON_READY_STATUSES = frozenset({"review", "visual_required", "blocked"})
REASON_NON_READY_REMAINING = "non_ready_questions_remaining"
REASON_VALIDATION_FAILED = "validation_failed"


@dataclass
class ReviewMergeResult:
    stem: str
    output_folder: Path
    final_questions_path: Path | None
    ready_copy_path: Path | None
    passed: bool
    errors: list[str] = field(default_factory=list)
    merged_count: int = 0
    unchanged_count: int = 0
    report_json_path: Path | None = None
    report_md_path: Path | None = None
    copied_to_ready_dir: bool = False
    reason_if_not_copied: str | None = None
    validation_passed: bool = False
    ready_count: int = 0
    review_count: int = 0
    visual_required_count: int = 0
    blocked_count: int = 0


class ReviewMergeError(Exception):
    """Raised when merge cannot proceed."""


def merge_reviewed_questions_folder(
    batch_output_dir: Path,
    *,
    ready_dir: Path | None = None,
    expected_count: int | None = 100,
    allow_partial: bool = False,
) -> list[ReviewMergeResult]:
    """Merge all PDF folders that have both questions.json and review.json."""
    root = resolve_path(batch_output_dir)
    ready_root = resolve_path(ready_dir or root.parent / READY_DIR_NAME)
    ready_root.mkdir(parents=True, exist_ok=True)

    results: list[ReviewMergeResult] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        stem = child.name
        questions_path = child / f"{stem}.questions.json"
        review_path = child / f"{stem}.review.json"
        if not questions_path.exists() or not review_path.exists():
            continue
        results.append(
            merge_reviewed_questions(
                questions_path,
                review_path,
                ready_dir=ready_root,
                expected_count=expected_count,
                allow_partial=allow_partial,
            ),
        )
    return results


def merge_reviewed_questions(
    questions_path: Path,
    review_path: Path,
    *,
    ready_dir: Path | None = None,
    expected_count: int | None = 100,
    allow_partial: bool = False,
) -> ReviewMergeResult:
    """Merge one edited review file into questions and write final output."""
    questions_path = resolve_path(questions_path)
    review_path = resolve_path(review_path)
    output_folder = questions_path.parent
    stem = output_folder.name
    final_path = output_folder / f"{stem}.final.questions.json"
    report_json = output_folder / f"{stem}.merge-report.json"
    report_md = output_folder / f"{stem}.merge-report.md"
    ready_root = resolve_path(ready_dir) if ready_dir else output_folder.parent / READY_DIR_NAME

    errors: list[str] = []
    try:
        base = _load_json(questions_path, errors, "questions_json")
        review = _load_json(review_path, errors, "review_json")
    except ReviewMergeError:
        report = _build_report_context(
            stem=stem,
            questions_path=questions_path,
            review_path=review_path,
            final_path=None,
            ready_copy_path=None,
            validation_passed=False,
            validation_errors=errors,
            status_counts=_empty_status_counts(),
            patched_count=0,
            unchanged_count=0,
            copied_to_ready_dir=False,
            reason_if_not_copied=REASON_VALIDATION_FAILED,
        )
        _write_reports(report_json, report_md, report)
        return ReviewMergeResult(
            stem=stem,
            output_folder=output_folder,
            final_questions_path=None,
            ready_copy_path=None,
            passed=False,
            errors=errors,
            report_json_path=report_json,
            report_md_path=report_md,
            reason_if_not_copied=REASON_VALIDATION_FAILED,
        )

    if errors:
        report = _build_report_context(
            stem=stem,
            questions_path=questions_path,
            review_path=review_path,
            final_path=None,
            ready_copy_path=None,
            validation_passed=False,
            validation_errors=errors,
            status_counts=_empty_status_counts(),
            patched_count=0,
            unchanged_count=0,
            copied_to_ready_dir=False,
            reason_if_not_copied=REASON_VALIDATION_FAILED,
        )
        _write_reports(report_json, report_md, report)
        return ReviewMergeResult(
            stem=stem,
            output_folder=output_folder,
            final_questions_path=None,
            ready_copy_path=None,
            passed=False,
            errors=errors,
            report_json_path=report_json,
            report_md_path=report_md,
            reason_if_not_copied=REASON_VALIDATION_FAILED,
        )

    merged, patched_count, unchanged_count, merge_errors = _merge_payloads(
        base,
        review,
        expected_count=expected_count,
    )
    errors.extend(merge_errors)

    audit = audit_public_questions_json(merged, expected_count=expected_count)
    errors.extend(audit.errors)
    validation_passed = not errors

    status_counts = _count_statuses(merged.get("questions") or [])
    all_ready = status_counts["non_ready_count"] == 0
    copied = False
    reason: str | None = None
    final_written: Path | None = None
    ready_copy: Path | None = None
    passed = False

    if validation_passed:
        final_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        final_written = final_path

        if all_ready or allow_partial:
            ready_root.mkdir(parents=True, exist_ok=True)
            ready_copy = ready_root / f"{stem}.final.questions.json"
            shutil.copy2(final_path, ready_copy)
            copied = True
            passed = True
        else:
            reason = REASON_NON_READY_REMAINING
            passed = False
    else:
        reason = REASON_VALIDATION_FAILED
        passed = False

    report = _build_report_context(
        stem=stem,
        questions_path=questions_path,
        review_path=review_path,
        final_path=final_written,
        ready_copy_path=ready_copy,
        validation_passed=validation_passed,
        validation_errors=errors,
        status_counts=status_counts,
        patched_count=patched_count,
        unchanged_count=unchanged_count,
        copied_to_ready_dir=copied,
        reason_if_not_copied=reason,
        merged_questions=merged.get("questions") or [],
        ready_dir=ready_root,
    )
    _write_reports(report_json, report_md, report)

    return ReviewMergeResult(
        stem=stem,
        output_folder=output_folder,
        final_questions_path=final_written,
        ready_copy_path=ready_copy,
        passed=passed,
        errors=errors,
        merged_count=patched_count,
        unchanged_count=unchanged_count,
        report_json_path=report_json,
        report_md_path=report_md,
        copied_to_ready_dir=copied,
        reason_if_not_copied=reason,
        validation_passed=validation_passed,
        ready_count=status_counts["ready_count"],
        review_count=status_counts["review_count"],
        visual_required_count=status_counts["visual_required_count"],
        blocked_count=status_counts["blocked_count"],
    )


def _empty_status_counts() -> dict[str, int]:
    return {
        "total_questions": 0,
        "ready_count": 0,
        "review_count": 0,
        "visual_required_count": 0,
        "blocked_count": 0,
        "non_ready_count": 0,
    }


def _count_statuses(questions: list[Any]) -> dict[str, int]:
    counts = _empty_status_counts()
    counts["total_questions"] = len(questions)
    for question in questions:
        if not isinstance(question, dict):
            continue
        status = (question.get("metadata") or {}).get("status")
        if status == "ready":
            counts["ready_count"] += 1
        elif status == "review":
            counts["review_count"] += 1
        elif status == "visual_required":
            counts["visual_required_count"] += 1
        elif status == "blocked":
            counts["blocked_count"] += 1
    counts["non_ready_count"] = (
        counts["review_count"] + counts["visual_required_count"] + counts["blocked_count"]
    )
    return counts


def _load_json(path: Path, errors: list[str], label: str) -> dict[str, Any]:
    if not path.exists():
        errors.append(f"missing_{label}")
        raise ReviewMergeError(label)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        errors.append(f"invalid_json:{label}")
        raise ReviewMergeError(label) from None
    if not isinstance(data, dict):
        errors.append(f"invalid_shape:{label}")
        raise ReviewMergeError(label)
    if "questions" not in data or not isinstance(data["questions"], list):
        errors.append(f"missing_questions_array:{label}")
    if "fileMeta" not in data or not isinstance(data.get("fileMeta"), dict):
        errors.append(f"missing_fileMeta:{label}")
    return data


def _merge_payloads(
    base: dict[str, Any],
    review: dict[str, Any],
    *,
    expected_count: int | None,
) -> tuple[dict[str, Any], int, int, list[str]]:
    errors: list[str] = []
    base_questions = list(base.get("questions") or [])
    review_questions = list(review.get("questions") or [])

    by_external: dict[str, dict[str, Any]] = {}
    by_number: dict[int, dict[str, Any]] = {}
    for q in review_questions:
        if not isinstance(q, dict):
            continue
        eid = q.get("externalId")
        if isinstance(eid, str):
            by_external[eid] = q
        meta = q.get("metadata") or {}
        qnum = meta.get("questionNumber")
        if isinstance(qnum, int):
            by_number[qnum] = q

    patched_count = 0
    unchanged_count = 0
    result_questions: list[dict[str, Any]] = []
    for q in base_questions:
        if not isinstance(q, dict):
            continue
        eid = q.get("externalId")
        qnum = (q.get("metadata") or {}).get("questionNumber")
        replacement = None
        if isinstance(eid, str) and eid in by_external:
            replacement = by_external[eid]
        elif isinstance(qnum, int) and qnum in by_number:
            replacement = by_number[qnum]
        if replacement is not None:
            result_questions.append(replacement)
            patched_count += 1
        else:
            result_questions.append(q)
            unchanged_count += 1

    result_questions.sort(
        key=lambda item: (item.get("metadata") or {}).get("questionNumber") or 0,
    )

    if expected_count and len(result_questions) != expected_count:
        errors.append(f"question_count_mismatch:{len(result_questions)}!={expected_count}")

    return {
        "fileMeta": dict(base.get("fileMeta") or {}),
        "questions": result_questions,
    }, patched_count, unchanged_count, errors


def _build_report_context(
    *,
    stem: str,
    questions_path: Path,
    review_path: Path,
    final_path: Path | None,
    ready_copy_path: Path | None,
    validation_passed: bool,
    validation_errors: list[str],
    status_counts: dict[str, int],
    patched_count: int,
    unchanged_count: int,
    copied_to_ready_dir: bool,
    reason_if_not_copied: str | None,
    merged_questions: list[Any] | None = None,
    ready_dir: Path | None = None,
) -> dict[str, Any]:
    ready_dir_path = ready_dir
    if ready_dir_path is None and ready_copy_path is not None:
        ready_dir_path = ready_copy_path.parent
    expected_ready_path = (
        ready_dir_path / f"{stem}.final.questions.json" if ready_dir_path else None
    )
    return {
        "pdf_stem": stem,
        "questions_json": str(questions_path),
        "review_json": str(review_path),
        "final_questions_json": str(final_path) if final_path else None,
        "ready_dir_output_path": str(expected_ready_path) if expected_ready_path else None,
        "total_questions": status_counts["total_questions"],
        "ready_count": status_counts["ready_count"],
        "review_count": status_counts["review_count"],
        "visual_required_count": status_counts["visual_required_count"],
        "blocked_count": status_counts["blocked_count"],
        "copied_to_ready_dir": copied_to_ready_dir,
        "reason_if_not_copied": reason_if_not_copied,
        "validation_status": "PASS" if validation_passed else "FAIL",
        "validation_errors": validation_errors,
        "patched_question_count": patched_count,
        "unchanged_question_count": unchanged_count,
        "non_ready_questions": _non_ready_summary(merged_questions or []),
    }


def _non_ready_summary(questions: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        metadata = question.get("metadata") or {}
        status = metadata.get("status")
        if status not in NON_READY_STATUSES:
            continue
        issues = metadata.get("reviewIssues") or []
        issue_text = ", ".join(issues) if issues else "none"
        items.append(
            {
                "externalId": question.get("externalId"),
                "questionNumber": metadata.get("questionNumber"),
                "status": status,
                "reviewIssues": issue_text,
            },
        )
    return items


def _write_reports(report_json: Path, report_md: Path, report: dict[str, Any]) -> None:
    report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report_md.write_text(_render_merge_report_md(report), encoding="utf-8")


def _render_merge_report_md(report: dict[str, Any]) -> str:
    stem = report.get("pdf_stem", "unknown")
    lines = [
        f"# Merge Report — {stem}",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| total_questions | {report.get('total_questions', 0)} |",
        f"| ready_count | {report.get('ready_count', 0)} |",
        f"| review_count | {report.get('review_count', 0)} |",
        f"| visual_required_count | {report.get('visual_required_count', 0)} |",
        f"| blocked_count | {report.get('blocked_count', 0)} |",
        f"| copied_to_ready_dir | {str(report.get('copied_to_ready_dir', False)).lower()} |",
    ]
    reason = report.get("reason_if_not_copied")
    if reason:
        lines.append(f"| reason | {reason} |")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            report.get("validation_status", "FAIL"),
            "",
        ],
    )
    errors = report.get("validation_errors") or []
    if errors:
        lines.append("### Errors")
        lines.extend(f"- {err}" for err in errors)
        lines.append("")

    non_ready = report.get("non_ready_questions") or []
    if non_ready:
        lines.extend(["## Remaining non-ready questions", ""])
        for item in non_ready:
            eid = item.get("externalId") or "?"
            status = item.get("status") or "unknown"
            issues = item.get("reviewIssues") or "none"
            lines.append(f"- {eid}: {status} — {issues}")
        lines.append("")

    return "\n".join(lines)
