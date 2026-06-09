"""Deterministic structural audit for question candidates (Part 10)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from meritranker_data_ingestion.config import QUESTION_STRUCTURE_AUDIT_NAME, QUESTIONS_DIR
from meritranker_data_ingestion.schemas.question_candidates import (
    AssetRole,
    QuestionCandidate,
)
from meritranker_data_ingestion.services.file_service import assert_output_contains
from meritranker_data_ingestion.services.visual_intent_detector import (
    has_visual_option_pattern,
    is_visual_dependent,
    is_visual_text_options_question,
)

EXPECTED_OPTION_KEYS = frozenset({"A", "B", "C", "D"})


def build_structure_audit(candidates: list[QuestionCandidate]) -> dict:
    """Build structural audit summary from parsed candidates."""
    by_issue: dict[str, list[int]] = defaultdict(list)

    same_line_split = 0
    visual_dependent_count = 0
    visual_text_options = 0
    visual_image_options = 0
    visual_missing_labels = 0
    noise_asset = 0
    support_images = 0
    false_binding_prevented = 0
    visual_review = 0
    invalid_option_count = 0

    for candidate in candidates:
        qnum = candidate.question_number
        issues = set(candidate.issues)
        option_keys = {opt.key for opt in candidate.options if opt.key}

        if "same_line_option_split_applied" in issues:
            same_line_split += 1
            if qnum is not None:
                by_issue["same_line_option_split_applied"].append(qnum)

        if is_visual_dependent(candidate.question_text_raw):
            visual_dependent_count += 1
            if qnum is not None:
                by_issue["visual_dependent"].append(qnum)

        if is_visual_text_options_question(candidate.question_text_raw, candidate.options):
            visual_text_options += 1
            if qnum is not None:
                by_issue["visual_question_requires_diagram_syntax"].append(qnum)

        if has_visual_option_pattern(candidate.options):
            visual_image_options += 1
            if qnum is not None:
                by_issue["visual_with_image_options"].append(qnum)

        if "missing_option_labels_for_visual_question" in issues or (
            "source_backed_option_labels_missing" in issues
        ):
            visual_missing_labels += 1
            if qnum is not None:
                by_issue["missing_option_labels_for_visual_question"].append(qnum)

        if "possible_noise_asset_after_options" in issues:
            noise_asset += 1
            if qnum is not None:
                by_issue["possible_noise_asset_after_options"].append(qnum)

        if any(a.role == AssetRole.QUESTION_SUPPORT_IMAGE for a in candidate.assets):
            support_images += 1
            if qnum is not None:
                by_issue["question_support_image"].append(qnum)

        if any("image_after_text_options_unbound" in a.issues for a in candidate.assets):
            false_binding_prevented += 1
            if qnum is not None:
                by_issue["image_after_text_options_unbound"].append(qnum)

        if "visual_question_requires_review" in issues or (
            "visual_question_requires_diagram_syntax" in issues
        ):
            visual_review += 1
            if qnum is not None:
                by_issue["candidates_requiring_visual_review"].append(qnum)

        if option_keys and option_keys != EXPECTED_OPTION_KEYS:
            invalid_option_count += 1
            if qnum is not None:
                by_issue["invalid_option_count"].append(qnum)

        for issue in issues:
            if qnum is not None and issue not in by_issue:
                by_issue[issue].append(qnum)

    sorted_by_issue = {
        key: sorted(set(nums)) for key, nums in sorted(by_issue.items())
    }

    return {
        "total_candidates": len(candidates),
        "invalid_option_count_questions": invalid_option_count,
        "same_line_option_split_count": same_line_split,
        "visual_dependent_count": visual_dependent_count,
        "visual_with_text_options_count": visual_text_options,
        "visual_with_image_options_count": visual_image_options,
        "visual_missing_option_labels_count": visual_missing_labels,
        "noise_asset_candidate_count": noise_asset,
        "question_support_image_count": support_images,
        "false_binding_prevented_count": false_binding_prevented,
        "candidates_requiring_visual_review": visual_review,
        "candidate_numbers_by_issue": sorted_by_issue,
    }


def write_structure_audit(
    candidates: list[QuestionCandidate],
    package_dir: Path,
) -> dict:
    """Write questions/question-structure-audit.json."""
    audit = build_structure_audit(candidates)
    questions_dir = package_dir / QUESTIONS_DIR
    questions_dir.mkdir(parents=True, exist_ok=True)
    out_path = questions_dir / QUESTION_STRUCTURE_AUDIT_NAME
    assert_output_contains(package_dir, out_path)
    out_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit
