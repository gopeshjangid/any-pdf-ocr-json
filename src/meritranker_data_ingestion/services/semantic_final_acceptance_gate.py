"""Strict final acceptance gate and evidence quality router (Part 13J)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBoundQuestion,
)
from meritranker_data_ingestion.services.semantic_final_quality import is_extra_item
from meritranker_data_ingestion.services.semantic_key_normalizer import (
    canonical_option_keys,
    normalize_answer_key,
)

RE_NOISE = re.compile(
    r"(?:Free\s+Mock\s+Test|Download\s+PDF|www\.|http://|https://|"
    r"Subscribe\s+Now|Previous\s+Papers?)",
    re.IGNORECASE,
)

VISUAL_STEM_PATTERNS = (
    "figure",
    "mirror image",
    "embedded",
    "diagram",
    "answer figures",
    "answer figure",
    "paper folding",
    "paper cutting",
    "paper fold",
    "dice",
    "cube",
    "mirror",
    "image",
    "visual",
)


class FinalGateStatus(str, Enum):
    ACCEPTED_SAFE = "accepted_safe"
    ANSWER_UNAVAILABLE_EXPORT = "answer_unavailable_export"
    REVIEW_VISUAL_REQUIRED = "review_visual_required"
    REVIEW_EVIDENCE_CORRUPT = "review_evidence_corrupt"
    REVIEW_MANUAL_PATCH_REQUIRED = "review_manual_patch_required"
    BLOCKED_BAD_ITEM = "blocked_bad_item"


@dataclass
class FinalGateDecision:
    status: FinalGateStatus
    reasons: list[str] = field(default_factory=list)
    gate_issues: list[str] = field(default_factory=list)


def evaluate_final_acceptance_gate(
    item: SemanticBoundQuestion,
    *,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    expected_count: int | None = None,
    manual_patch: bool = False,
) -> FinalGateDecision:
    """Compute strict final gate status for one semantic item."""
    reasons: list[str] = []
    gate_issues: list[str] = []

    usable_options = _usable_text_options(item)
    hard_fails = _collect_hard_acceptance_failures(
        item,
        answer_mode=answer_mode,
        usable_options=usable_options,
        manual_patch=manual_patch,
    )
    if (
        answer_mode == SemanticBinderAnswerMode.QUESTION_ONLY
        and _question_only_exportable(item, usable_options=usable_options)
        and not _has_blocking_hallucination(item, answer_mode=answer_mode)
        and (not hard_fails or _hard_fails_are_answer_only(hard_fails))
    ):
        return _decision(
            FinalGateStatus.ANSWER_UNAVAILABLE_EXPORT,
            hard_fails or ["question_only_answer_unavailable"],
            gate_issues,
        )

    if item.quarantine_status == "quarantined" or item.excluded_from_export:
        return _decision(
            FinalGateStatus.BLOCKED_BAD_ITEM,
            ["quarantined_or_excluded"],
            gate_issues + ["quarantined"],
        )

    if item.bad_item_classes:
        return _decision(
            FinalGateStatus.BLOCKED_BAD_ITEM,
            [f"bad_item_classes:{','.join(item.bad_item_classes)}"],
            gate_issues + list(item.bad_item_classes),
        )

    if is_extra_item(item, expected_count=expected_count):
        return _decision(
            FinalGateStatus.BLOCKED_BAD_ITEM,
            ["extra_question_candidate"],
            gate_issues + ["extra_question_candidate"],
        )

    if _has_blocking_hallucination(item, answer_mode=answer_mode):
        return _decision(
            FinalGateStatus.BLOCKED_BAD_ITEM,
            ["hallucination_suspected"],
            gate_issues + [i for i in item.issues if "hallucinated" in i],
        )

    visual = _is_visual_dependent(item)

    if visual and (not usable_options or _options_image_only_or_empty(item)):
        gate_issues.append("visual_question_requires_vlm_or_manual_patch")
        return _decision(
            FinalGateStatus.REVIEW_VISUAL_REQUIRED,
            ["visual_question_empty_or_image_only_options"],
            gate_issues,
        )

    if not manual_patch and item.binding_status != SemanticBindingItemStatus.ACCEPTED:
        if item.binding_status == SemanticBindingItemStatus.REJECTED:
            return _route_blocked_or_corrupt(item, reasons, gate_issues)
        return _decision(
            FinalGateStatus.REVIEW_MANUAL_PATCH_REQUIRED,
            [f"binding_status:{item.binding_status.value}"],
            gate_issues + list(item.issues),
        )

    if hard_fails:
        gate_issues.extend(hard_fails)
        if _is_corrupt_evidence(item, hard_fails, usable_options):
            return _decision(
                FinalGateStatus.REVIEW_EVIDENCE_CORRUPT,
                hard_fails,
                gate_issues,
            )
        if manual_patch:
            return _decision(
                FinalGateStatus.REVIEW_MANUAL_PATCH_REQUIRED,
                hard_fails,
                gate_issues,
            )
        return _decision(
            FinalGateStatus.REVIEW_EVIDENCE_CORRUPT,
            hard_fails,
            gate_issues,
        )

    return _decision(FinalGateStatus.ACCEPTED_SAFE, [], gate_issues)


def _decision(
    status: FinalGateStatus,
    reasons: list[str],
    gate_issues: list[str],
) -> FinalGateDecision:
    return FinalGateDecision(
        status=status,
        reasons=reasons,
        gate_issues=_dedupe(gate_issues + reasons),
    )


def _route_blocked_or_corrupt(
    item: SemanticBoundQuestion,
    reasons: list[str],
    gate_issues: list[str],
) -> FinalGateDecision:
    if item.bad_item_classes or any("bad_item:" in i for i in item.issues):
        return _decision(FinalGateStatus.BLOCKED_BAD_ITEM, ["rejected_bad_item"], gate_issues)
    return _decision(
        FinalGateStatus.REVIEW_EVIDENCE_CORRUPT,
        ["rejected_needs_review"],
        gate_issues + list(item.issues),
    )


def _is_visual_dependent(item: SemanticBoundQuestion) -> bool:
    text = (item.question_text_raw or "").lower()
    if any(pat in text for pat in VISUAL_STEM_PATTERNS):
        return True
    if item.visual_references and not _usable_text_options(item):
        return True
    if "missing_options" in item.issues and any(
        pat in text for pat in ("figure", "mirror", "image", "diagram")
    ):
        return True
    return False


def _options_image_only_or_empty(item: SemanticBoundQuestion) -> bool:
    if not item.options:
        return True
    for opt in item.options:
        has_key = bool((opt.key or opt.key_raw or "").strip())
        has_text = bool(opt.text_raw.strip())
        if has_key and has_text:
            return False
        if opt.asset_refs and not has_text:
            continue
    return True


def _usable_text_options(item: SemanticBoundQuestion) -> list[str]:
    keys: list[str] = []
    for opt in item.options:
        raw = (opt.key or opt.key_raw or "").strip().rstrip(".")
        key, _ = normalize_answer_key(opt.key or opt.key_raw)
        if not key and raw in {"1", "2", "3", "4"}:
            key = raw
        has_text = bool(opt.text_raw.strip())
        has_span = bool(opt.source_spans)
        if key and has_text and has_span:
            keys.append(key)
    return keys


def _collect_hard_acceptance_failures(
    item: SemanticBoundQuestion,
    *,
    answer_mode: SemanticBinderAnswerMode,
    usable_options: list[str],
    manual_patch: bool,
) -> list[str]:
    failures: list[str] = []

    if item.question_number is None:
        failures.append("missing_question_number")

    if not item.question_text_raw.strip():
        failures.append("missing_question_text")

    if RE_NOISE.search(item.question_text_raw or ""):
        failures.append("noise_in_question_text")

    if not item.source_spans:
        failures.append("missing_question_source_spans")

    for opt in item.options:
        key = (opt.key or opt.key_raw or "").strip()
        text = opt.text_raw.strip()
        if not key or not text:
            failures.append("empty_option_key_or_text")
            break

    if len(usable_options) < 4 and not manual_patch:
        failures.append("incomplete_text_options")

    if (
        answer_mode == SemanticBinderAnswerMode.ANSWER_KEY_ONLY
        and not item.answer.available
    ):
        failures.append("missing_answer_answer_key_only_mode")

    if answer_mode == SemanticBinderAnswerMode.QUESTION_ONLY:
        return _dedupe([f for f in failures if not f.startswith("missing_answer") and f not in {
            "correct_answer_text_unavailable",
            "missing_answer_source_spans",
            "answer_key_not_in_options",
            "answer_key_option_mismatch",
        }])

    answer_key, _ = normalize_answer_key(item.answer.key or item.answer.key_raw)
    option_keys = canonical_option_keys(item)
    if answer_key and option_keys and answer_key not in option_keys:
        failures.append("answer_key_not_in_options")

    if answer_key and answer_key in option_keys:
        answer_text = None
        for opt in item.options:
            canon, _ = normalize_answer_key(opt.key or opt.key_raw)
            if canon == answer_key and opt.text_raw.strip():
                answer_text = opt.text_raw.strip()
                break
        if not answer_text:
            failures.append("correct_answer_text_unavailable")

    if not item.answer.source_spans and item.answer.available:
        failures.append("missing_answer_source_spans")

    for opt in item.options:
        if opt.text_raw.strip() and not opt.source_spans and not opt.asset_refs:
            failures.append("weak_source_grounding")
            break

    if "|" in (item.question_text_raw or "") and len(usable_options) < 4:
        failures.append("table_extraction_corrupt")

    if "answer_key_not_in_options" in item.issues:
        failures.append("answer_key_option_mismatch")

    return _dedupe(failures)


def _is_corrupt_evidence(
    item: SemanticBoundQuestion,
    failures: list[str],
    usable_options: list[str],
) -> bool:
    corrupt_markers = {
        "incomplete_text_options",
        "weak_source_grounding",
        "table_extraction_corrupt",
        "answer_key_not_in_options",
        "answer_key_option_mismatch",
        "empty_option_key_or_text",
        "correct_answer_text_unavailable",
        "missing_question_source_spans",
        "missing_answer_source_spans",
    }
    return bool(corrupt_markers.intersection(failures))


def sanitize_duplicate_warnings(
    warnings: list[str],
    *,
    duplicate_question_numbers: list[int],
    duplicate_count: int = 0,
) -> list[str]:
    """Remove stale duplicate warnings when no duplicates exist."""
    if duplicate_count > 0 or duplicate_question_numbers:
        return warnings
    return [w for w in warnings if not w.startswith("duplicate_question_number:")]


def _has_blocking_hallucination(
    item: SemanticBoundQuestion,
    *,
    answer_mode: SemanticBinderAnswerMode,
) -> bool:
    hallucination_issues = [i for i in item.issues if "hallucinated" in i]
    if not hallucination_issues:
        return False
    if answer_mode == SemanticBinderAnswerMode.QUESTION_ONLY:
        if "hallucinated_question_text" in hallucination_issues and item.source_spans:
            hallucination_issues = [
                i for i in hallucination_issues if i != "hallucinated_question_text"
            ]
        if not hallucination_issues:
            return False
        option_issues = [i for i in hallucination_issues if "hallucinated_option_text" in i]
        if option_issues:
            source_backed = any(
                opt.source_spans and opt.text_raw.strip() for opt in item.options
            )
            if source_backed:
                hallucination_issues = [
                    i for i in hallucination_issues if "hallucinated_option_text" not in i
                ]
        return bool(hallucination_issues)
    return True


def _question_only_exportable(
    item: SemanticBoundQuestion,
    *,
    usable_options: list[str],
) -> bool:
    return bool(
        item.question_number is not None
        and item.question_text_raw.strip()
        and item.source_spans
        and len(usable_options) >= 4
    )


def _hard_fails_are_answer_only(failures: list[str]) -> bool:
    answer_only = {
        "missing_answer_answer_key_only_mode",
        "correct_answer_text_unavailable",
        "missing_answer_source_spans",
        "answer_key_not_in_options",
        "answer_key_option_mismatch",
    }
    return bool(failures) and all(failure in answer_only for failure in failures)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
