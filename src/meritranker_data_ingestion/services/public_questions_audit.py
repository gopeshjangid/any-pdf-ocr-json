"""Audit public .questions.json contract compliance (Part 14P)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.services.public_visual_serializer import ALLOWED_PUBLIC_VISUAL_TYPES

ALLOWED_TOP_LEVEL_KEYS = frozenset({"fileMeta", "questions"})
ALLOWED_FILE_META_KEYS = frozenset({
    "sourceName",
    "sourceType",
    "exam",
    "year",
    "set",
    "shift",
    "language",
    "createdBy",
    "notes",
})
ALLOWED_QUESTION_KEYS = frozenset({
    "externalId",
    "questionText",
    "questionType",
    "options",
    "correctAnswer",
    "solutionText",
    "solutionSource",
    "visuals",
    "metadata",
})
ALLOWED_METADATA_KEYS = frozenset({
    "exams",
    "years",
    "section",
    "sourcePaper",
    "questionNumber",
    "status",
    "reviewIssues",
})
ALLOWED_VISUAL_KEYS = frozenset({
    "visualId",
    "type",
    "role",
    "linkedOptionLabel",
    "description",
    "syntax",
    "issues",
})
ALLOWED_STATUSES = frozenset({"ready", "review", "visual_required", "blocked"})
ALLOWED_SOLUTION_SOURCES = frozenset({"pdf", "manual", "unavailable", "unknown"})
FORBIDDEN_LEGACY_TOKENS = frozenset({
    "questionBankReady",
    "patternIngestionReady",
    "manualReviewRequired",
    "isQuestionBankUsable",
    "isPatternIngestionReady",
    "ingestionAction",
    "qualityStatus",
    "questionExtractionStatus",
    "answerStatus",
    "solutionStatus",
    "visualStatus",
    "accepted_safe",
    "extractionStatus",
    "renderTarget",
    "renderSpec",
    "sourcePage",
    "assetPath",
    "debugTrace",
    "source_trace",
    "sourceTrace",
})


@dataclass
class PublicQuestionsAuditResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def assert_passed(self) -> None:
        if not self.passed:
            raise AssertionError("\n".join(self.errors))


def audit_public_questions_json(
    source: Path | dict[str, Any],
    *,
    expected_count: int | None = None,
) -> PublicQuestionsAuditResult:
    """Run all Part 14P/14R public contract checks."""
    payload = _load_payload(source)
    errors: list[str] = []
    if payload is None:
        return PublicQuestionsAuditResult(passed=False, errors=["invalid_or_missing_json"])

    _check_top_level(payload, errors)
    _check_no_legacy_fields(payload, errors)

    questions = payload.get("questions")
    if not isinstance(questions, list):
        errors.append("questions_must_be_array")
        return PublicQuestionsAuditResult(passed=False, errors=errors)

    if expected_count is not None and expected_count > 0:
        if len(questions) != expected_count:
            errors.append(f"question_count_mismatch:{len(questions)}!={expected_count}")

    seen_numbers: set[int] = set()
    for idx, question in enumerate(questions):
        if not isinstance(question, dict):
            errors.append(f"question_{idx}_not_object")
            continue
        _audit_question(question, idx, errors, expected_count=expected_count)
        metadata = question.get("metadata") or {}
        qnum = metadata.get("questionNumber")
        if isinstance(qnum, int):
            if qnum in seen_numbers:
                errors.append(f"duplicate_question_number:{qnum}")
            seen_numbers.add(qnum)
            if expected_count and (qnum < 1 or qnum > expected_count):
                errors.append(f"out_of_range_question_number:{qnum}")

    if expected_count and expected_count > 0:
        for slot in range(1, expected_count + 1):
            expected_id = f"Q{slot:03d}"
            if slot - 1 >= len(questions):
                errors.append(f"missing_external_id_slot:{expected_id}")
                continue
            actual_id = questions[slot - 1].get("externalId")
            if actual_id != expected_id:
                errors.append(f"external_id_mismatch:slot_{slot}:{actual_id}!={expected_id}")

    return PublicQuestionsAuditResult(passed=not errors, errors=errors)


def _load_payload(source: Path | dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(source, dict):
        return source
    if not source.exists():
        return None
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _check_top_level(payload: dict[str, Any], errors: list[str]) -> None:
    keys = set(payload.keys())
    if keys != ALLOWED_TOP_LEVEL_KEYS:
        extra = keys - ALLOWED_TOP_LEVEL_KEYS
        missing = ALLOWED_TOP_LEVEL_KEYS - keys
        if extra:
            errors.append(f"unexpected_top_level_keys:{sorted(extra)}")
        if missing:
            errors.append(f"missing_top_level_keys:{sorted(missing)}")

    file_meta = payload.get("fileMeta")
    if not isinstance(file_meta, dict):
        errors.append("fileMeta_must_be_object")
        return
    meta_keys = set(file_meta.keys())
    if meta_keys != ALLOWED_FILE_META_KEYS:
        extra = meta_keys - ALLOWED_FILE_META_KEYS
        if extra:
            errors.append(f"unexpected_fileMeta_keys:{sorted(extra)}")


def _check_no_legacy_fields(node: Any, errors: list[str], *, path: str = "") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_LEGACY_TOKENS:
                errors.append(f"legacy_field_at:{path}.{key}" if path else f"legacy_field:{key}")
            _check_no_legacy_fields(value, errors, path=f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            _check_no_legacy_fields(item, errors, path=f"{path}[{idx}]")


def _audit_question(
    question: dict[str, Any],
    idx: int,
    errors: list[str],
    *,
    expected_count: int | None = None,
) -> None:
    prefix = f"Q{idx}"
    qkeys = set(question.keys())
    if qkeys != ALLOWED_QUESTION_KEYS:
        extra = qkeys - ALLOWED_QUESTION_KEYS
        if extra:
            errors.append(f"{prefix}:unexpected_keys:{sorted(extra)}")

    metadata = question.get("metadata")
    if not isinstance(metadata, dict):
        errors.append(f"{prefix}:metadata_missing")
        return
    if set(metadata.keys()) != ALLOWED_METADATA_KEYS:
        extra = set(metadata.keys()) - ALLOWED_METADATA_KEYS
        if extra:
            errors.append(f"{prefix}:unexpected_metadata_keys:{sorted(extra)}")

    status = metadata.get("status")
    if status not in ALLOWED_STATUSES:
        errors.append(f"{prefix}:invalid_status:{status}")

    review_issues = metadata.get("reviewIssues")
    if not isinstance(review_issues, list):
        errors.append(f"{prefix}:reviewIssues_must_be_array")
    elif any(not isinstance(issue, str) for issue in review_issues):
        errors.append(f"{prefix}:reviewIssues_must_be_strings")

    solution_source = question.get("solutionSource")
    if solution_source not in ALLOWED_SOLUTION_SOURCES:
        errors.append(f"{prefix}:invalid_solutionSource:{solution_source}")

    if status == "blocked" and "question_missing_from_extraction" not in (review_issues or []):
        if not (question.get("questionText") or "").strip():
            errors.append(f"{prefix}:blocked_without_missing_issue")

    visuals = question.get("visuals")
    if isinstance(visuals, list):
        for vidx, visual in enumerate(visuals):
            if not isinstance(visual, dict):
                errors.append(f"{prefix}:visual_{vidx}_not_object")
                continue
            vkeys = set(visual.keys())
            if vkeys != ALLOWED_VISUAL_KEYS:
                extra = vkeys - ALLOWED_VISUAL_KEYS
                if extra:
                    errors.append(f"{prefix}:visual_{vidx}_unexpected_keys:{sorted(extra)}")
            vtype = visual.get("type")
            if vtype not in ALLOWED_PUBLIC_VISUAL_TYPES:
                errors.append(f"{prefix}:visual_{vidx}_invalid_type:{vtype}")

    if question.get("questionType") == "single_choice" and status == "ready":
        options = question.get("options") or []
        usable = sum(
            1 for opt in options
            if isinstance(opt, dict) and (opt.get("text") or "").strip()
        )
        if usable < 4:
            errors.append(f"{prefix}:ready_with_incomplete_options")
        if isinstance(visuals, list) and any(
            isinstance(v, dict) and v.get("syntax") is None for v in visuals
        ):
            errors.append(f"{prefix}:ready_with_null_visual_syntax")

    if status == "visual_required" and isinstance(visuals, list):
        if visuals and all(isinstance(v, dict) and v.get("syntax") is None for v in visuals):
            if "visual_syntax_missing" not in (review_issues or []):
                errors.append(f"{prefix}:visual_required_missing_syntax_issue")

    correct = question.get("correctAnswer") or {}
    label = (correct.get("label") or "") if isinstance(correct, dict) else ""
    text = (correct.get("text") or "") if isinstance(correct, dict) else ""
    qtext = (question.get("questionText") or "").lower()
    if "chosen option" in qtext and label:
        errors.append(f"{prefix}:chosen_option_used_as_correct_answer")
    if text and "chosen option" in text.lower():
        errors.append(f"{prefix}:chosen_option_text_as_correct_answer")

    options = question.get("options") or []
    option_labels = {
        (opt.get("label") or "").strip().upper()
        for opt in options
        if isinstance(opt, dict) and (opt.get("label") or "").strip()
    }
    option_text_by_label = {
        (opt.get("label") or "").strip().upper(): (opt.get("text") or "").strip()
        for opt in options
        if isinstance(opt, dict) and (opt.get("label") or "").strip()
    }
    if label:
        normalized_label = label.strip().upper()
        if normalized_label and normalized_label not in option_labels:
            errors.append(f"{prefix}:correctAnswer_label_not_in_options:{label}")
    if text and label:
        normalized_label = label.strip().upper()
        expected_text = option_text_by_label.get(normalized_label, "")
        if expected_text and text.strip() != expected_text:
            errors.append(f"{prefix}:correctAnswer_text_mismatch")
