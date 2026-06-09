"""Source-grounded validation for semantic binding output (Part 13C)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    RoleHint,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingValidationReport,
    SemanticBoundQuestion,
    SemanticMetadataCandidate,
)
from meritranker_data_ingestion.services.option_key_normalizer import is_valid_option_key
from meritranker_data_ingestion.services.semantic_key_normalizer import (
    canonical_option_keys,
    normalize_answer_key,
)

VALID_OPTION_KEYS = {"A", "B", "C", "D", "E", "1", "2", "3", "4"}

RE_NOISE = re.compile(
    r"(?:Free\s+Mock\s+Test|Download\s+PDF|www\.|http://|https://|"
    r"Subscribe\s+Now|Previous\s+Papers?)",
    re.IGNORECASE,
)


def normalize_for_match(text: str) -> str:
    """Tolerant normalization for source containment checks."""
    target = text.strip()
    target = re.sub(r"\*\*([^*]+)\*\*", r"\1", target)
    target = re.sub(r"^[-*+]\s+", "", target)
    target = re.sub(r"^\(([A-Da-d])\)\s*", r"\1 ", target)
    target = re.sub(r"\s+", " ", target)
    return target.strip().lower()


def _line_text_by_id(evidence: DocumentEvidencePackage) -> dict[str, str]:
    return {line.line_id: line.text_raw for line in evidence.lines}


def _image_ids(evidence: DocumentEvidencePackage) -> set[str]:
    return {img.image_id for img in evidence.images}


def _figure_ids(evidence: DocumentEvidencePackage) -> set[str]:
    return {fig.figure_id for fig in evidence.figures}


def text_in_evidence(text: str, line_ids: list[str], line_map: dict[str, str]) -> bool:
    """Check whether text is contained in referenced evidence lines."""
    if not text.strip():
        return False
    candidates = _evidence_match_candidates(text)
    combined = " ".join(
        normalize_for_match(line_map.get(line_id, ""))
        for line_id in line_ids
        if line_id in line_map
    )
    for candidate in candidates:
        if not candidate:
            continue
        if candidate in combined:
            return True
        for line_id in line_ids:
            line_text = normalize_for_match(line_map.get(line_id, ""))
            if candidate in line_text or line_text in candidate:
                return True
    return False


def _evidence_match_candidates(text: str) -> list[str]:
    normalized = normalize_for_match(text)
    candidates = [normalized]
    stripped = re.sub(r"^\d+\s*[\.\)]\s*", "", normalized).strip()
    if stripped and stripped != normalized:
        candidates.append(stripped)
    return candidates


def validate_semantic_items(
    items: list[SemanticBoundQuestion],
    metadata_candidates: list[SemanticMetadataCandidate],
    evidence: DocumentEvidencePackage,
    *,
    answer_mode: SemanticBinderAnswerMode,
    expected_count: int | None = None,
) -> SemanticBindingValidationReport:
    """Validate and set binding_status on each semantic item."""
    if answer_mode == SemanticBinderAnswerMode.AUTO:
        answer_mode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY
    line_map = _line_text_by_id(evidence)
    image_ids = _image_ids(evidence)
    figure_ids = _figure_ids(evidence)

    report = SemanticBindingValidationReport()
    report.total_items = len(items)

    seen_numbers: dict[int, list[SemanticBoundQuestion]] = {}
    for item in items:
        if item.question_number is not None:
            seen_numbers.setdefault(item.question_number, []).append(item)

    for qnum, group in seen_numbers.items():
        if len(group) > 1:
            report.duplicate_question_number_count += len(group) - 1

    option_hint_count = sum(
        1
        for line in evidence.lines
        if RoleHint.OPTION_LABEL_CANDIDATE in line.role_hints
    )

    for item in items:
        issues: list[str] = []
        status = SemanticBindingItemStatus.ACCEPTED

        if not item.source_spans:
            issues.append("missing_question_source_spans")
            report.source_span_missing_count += 1
            status = SemanticBindingItemStatus.REJECTED

        q_line_ids = [span.line_id for span in item.source_spans if span.line_id]
        if item.question_text_raw and q_line_ids:
            if not text_in_evidence(item.question_text_raw, q_line_ids, line_map):
                issues.append("hallucinated_question_text")
                report.hallucination_suspected_count += 1
                status = SemanticBindingItemStatus.REJECTED

        if RE_NOISE.search(item.question_text_raw or ""):
            issues.append("noise_in_question_text")
            status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)

        for option in item.options:
            has_option_content = bool(
                option.text_raw.strip() or (option.key or option.key_raw or "").strip(),
            )
            if not has_option_content:
                continue

            opt_line_ids = [span.line_id for span in option.source_spans if span.line_id]
            if not opt_line_ids and not option.asset_refs:
                issues.append(f"missing_option_source_spans:{option.key or option.key_raw}")
                report.source_span_missing_count += 1
                status = _escalate(status, SemanticBindingItemStatus.REJECTED)
            elif option.text_raw and opt_line_ids:
                if not text_in_evidence(option.text_raw, opt_line_ids, line_map):
                    issues.append(f"hallucinated_option_text:{option.key}")
                    report.hallucination_suspected_count += 1
                    status = SemanticBindingItemStatus.REJECTED

            key = (option.key or option.key_raw or "").strip().rstrip(".")
            if key and not is_valid_option_key(key):
                issues.append(f"invalid_option_key:{option.key}")
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)

        if not item.options:
            issues.append("missing_options")
            report.option_key_missing_count += 1
            if option_hint_count > 0:
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)
            else:
                status = _escalate(status, SemanticBindingItemStatus.REJECTED)

        if item.answer.available:
            ans_line_ids = [span.line_id for span in item.answer.source_spans if span.line_id]
            if not ans_line_ids:
                issues.append("missing_answer_source_spans")
                report.source_span_missing_count += 1
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)
            elif item.answer.answer_text_raw and not text_in_evidence(
                item.answer.answer_text_raw,
                ans_line_ids,
                line_map,
            ):
                issues.append("hallucinated_answer_text")
                report.hallucination_suspected_count += 1
                status = SemanticBindingItemStatus.REJECTED

            canonical_answer, _ = normalize_answer_key(item.answer.key or item.answer.key_raw)
            option_keys = canonical_option_keys(item)
            if canonical_answer and option_keys and canonical_answer not in option_keys:
                issues.append("answer_key_not_in_options")
                report.answer_key_not_in_options_count += 1
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)

        if item.solution.available:
            sol_line_ids = [span.line_id for span in item.solution.source_spans if span.line_id]
            if not sol_line_ids:
                issues.append("missing_solution_source_spans")
                report.source_span_missing_count += 1
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)
            elif item.solution.text_raw and not text_in_evidence(
                item.solution.text_raw,
                sol_line_ids,
                line_map,
            ):
                issues.append("hallucinated_solution_text")
                report.hallucination_suspected_count += 1
                status = SemanticBindingItemStatus.REJECTED

        if answer_mode == SemanticBinderAnswerMode.REQUIRED:
            if not item.answer.available:
                issues.append("missing_answer_required_mode")
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)
            if not item.solution.available:
                issues.append("missing_solution_required_mode")
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)
        elif answer_mode == SemanticBinderAnswerMode.ANSWER_KEY_ONLY:
            if not item.answer.available:
                issues.append("missing_answer_answer_key_only_mode")
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)
        elif answer_mode == SemanticBinderAnswerMode.QUESTION_ONLY:
            if item.answer.available and item.answer.key:
                issues.append("answer_present_question_only_mode_ignored")
        elif answer_mode == SemanticBinderAnswerMode.OPTIONAL:
            if not item.answer.available:
                issues.append("missing_answer_optional_mode")
            if not item.solution.available:
                issues.append("missing_solution_optional_mode")

        for visual in item.visual_references:
            if visual.image_id and visual.image_id not in image_ids:
                issues.append(f"unknown_image_id:{visual.image_id}")
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)
            if visual.figure_id and visual.figure_id not in figure_ids:
                issues.append(f"unknown_figure_id:{visual.figure_id}")
                status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)

        if item.question_number is not None and len(seen_numbers.get(item.question_number, [])) > 1:
            issues.append("duplicate_question_number")
            status = _escalate(status, SemanticBindingItemStatus.REVIEW_REQUIRED)

        item.issues = _dedupe(issues)
        item.binding_status = status

        if status == SemanticBindingItemStatus.ACCEPTED:
            report.accepted_count += 1
        elif status == SemanticBindingItemStatus.REVIEW_REQUIRED:
            report.review_required_count += 1
        else:
            report.rejected_count += 1

    if expected_count is not None:
        found = {item.question_number for item in items if item.question_number is not None}
        report.missing_question_numbers = [
            n for n in range(1, expected_count + 1) if n not in found
        ]
        if report.missing_question_numbers:
            report.warnings.append(
                f"missing_question_numbers_count={len(report.missing_question_numbers)}",
            )

    for _meta in metadata_candidates:
        if not _meta.source_spans:
            report.source_span_missing_count += 1

    return report


def _escalate(
    current: SemanticBindingItemStatus,
    target: SemanticBindingItemStatus,
) -> SemanticBindingItemStatus:
    order = {
        SemanticBindingItemStatus.ACCEPTED: 0,
        SemanticBindingItemStatus.REVIEW_REQUIRED: 1,
        SemanticBindingItemStatus.REJECTED: 2,
    }
    return current if order[current] >= order[target] else target


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
