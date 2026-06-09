"""Derive truthful candidate report counters from parsed candidates (Part 11)."""

from __future__ import annotations

from meritranker_data_ingestion.schemas.question_candidates import (
    AssetRole,
    CandidateReviewStatus,
    QuestionCandidate,
)
from meritranker_data_ingestion.services.visual_intent_detector import (
    has_visual_option_pattern,
    is_visual_dependent,
    is_visual_text_options_question,
)

EXPECTED_OPTION_KEYS = frozenset({"A", "B", "C", "D"})

NOISE_ISSUE_MARKERS = frozenset({
    "possible_noise_asset_after_options",
    "image_after_text_options_unbound",
})

ISSUE_CANONICAL_ALIASES: dict[str, str] = {
    "unlabeled_option_images": "unlabeled_visual_assets",
}


def normalize_issue_name(issue: str) -> str:
    """Map legacy issue names to canonical taxonomy."""
    return ISSUE_CANONICAL_ALIASES.get(issue, issue)


def candidate_has_noise(candidate: QuestionCandidate) -> bool:
    """True when candidate has noise assets or noise-related issues."""
    if any(issue in NOISE_ISSUE_MARKERS for issue in candidate.issues):
        return True
    if any("noise_inside" in issue for issue in candidate.issues):
        return True
    if any(asset.role == AssetRole.NOISE_CANDIDATE for asset in candidate.assets):
        return True
    if any(
        "image_after_text_options_unbound" in asset.issues for asset in candidate.assets
    ):
        return True
    return False


def count_noise_assets(candidates: list[QuestionCandidate]) -> int:
    """Count individual noise-classified assets across candidates."""
    total = 0
    for candidate in candidates:
        for asset in candidate.assets:
            if asset.role == AssetRole.NOISE_CANDIDATE:
                total += 1
            elif any(
                marker in asset.issues for marker in NOISE_ISSUE_MARKERS
            ):
                total += 1
    return total


def _has_partial_options(candidate: QuestionCandidate) -> bool:
    if not candidate.options:
        return False
    keys = {opt.key for opt in candidate.options if opt.key}
    return keys != EXPECTED_OPTION_KEYS


def compute_candidate_report_metrics(
    candidates: list[QuestionCandidate],
) -> dict[str, int]:
    """Compute all candidate report counters directly from candidate shells."""
    status_distribution: dict[str, int] = {}
    for candidate in candidates:
        key = candidate.review_status.value
        status_distribution[key] = status_distribution.get(key, 0) + 1

    metrics: dict[str, int] = {
        "total_candidates": len(candidates),
        "valid_candidates": status_distribution.get(
            CandidateReviewStatus.CANDIDATE_VALID.value,
            0,
        ),
        "needs_review_candidates": status_distribution.get(
            CandidateReviewStatus.CANDIDATE_NEEDS_REVIEW.value,
            0,
        ),
        "incomplete_candidates": status_distribution.get(
            CandidateReviewStatus.CANDIDATE_INCOMPLETE.value,
            0,
        ),
        "duplicate_candidates": status_distribution.get(
            CandidateReviewStatus.CANDIDATE_DUPLICATE.value,
            0,
        ),
        "rejected_candidates": status_distribution.get(
            CandidateReviewStatus.CANDIDATE_REJECTED.value,
            0,
        ),
        "candidates_with_images": sum(1 for c in candidates if c.assets),
        "candidates_with_question_images": sum(
            1
            for c in candidates
            if any(a.role == AssetRole.QUESTION_IMAGE for a in c.assets)
        ),
        "candidates_with_question_support_images": sum(
            1
            for c in candidates
            if any(a.role == AssetRole.QUESTION_SUPPORT_IMAGE for a in c.assets)
        ),
        "candidates_with_option_images": sum(
            1
            for c in candidates
            if any(a.role == AssetRole.OPTION_IMAGE for a in c.assets)
        ),
        "candidates_with_linked_option_assets": sum(
            1 for c in candidates if any(opt.linked_asset_paths for opt in c.options)
        ),
        "candidates_with_noise": sum(1 for c in candidates if candidate_has_noise(c)),
        "noise_asset_count": count_noise_assets(candidates),
        "candidates_with_no_options": sum(1 for c in candidates if not c.options),
        "candidates_with_partial_options": sum(
            1 for c in candidates if _has_partial_options(c)
        ),
        "candidates_with_invalid_option_count": sum(
            1
            for c in candidates
            if c.options
            and {opt.key for opt in c.options if opt.key} != EXPECTED_OPTION_KEYS
        ),
        "visual_dependent_count": sum(
            1 for c in candidates if is_visual_dependent(c.question_text_raw)
        ),
        "visual_text_option_count": sum(
            1
            for c in candidates
            if is_visual_text_options_question(c.question_text_raw, c.options)
        ),
        "visual_image_option_count": sum(
            1 for c in candidates if has_visual_option_pattern(c.options)
        ),
        "visual_missing_option_labels_count": sum(
            1
            for c in candidates
            if "missing_option_labels_for_visual_question" in c.issues
            or "source_backed_option_labels_missing" in c.issues
        ),
        "same_line_option_split_count": sum(
            1 for c in candidates if "same_line_option_split_applied" in c.issues
        ),
        "source_backed_option_labels_missing_count": sum(
            1 for c in candidates if "source_backed_option_labels_missing" in c.issues
        ),
        "unlabeled_visual_assets_count": sum(
            1 for c in candidates if "unlabeled_visual_assets" in c.issues
        ),
        "possible_noise_asset_after_options_count": sum(
            1 for c in candidates if "possible_noise_asset_after_options" in c.issues
        ),
    }
    return metrics


def build_status_distribution(candidates: list[QuestionCandidate]) -> dict[str, int]:
    """Build review status distribution from candidates."""
    distribution: dict[str, int] = {}
    for candidate in candidates:
        key = candidate.review_status.value
        distribution[key] = distribution.get(key, 0) + 1
    return distribution
