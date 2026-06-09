"""Generic visual-dependency detection for final questions (Part 14M)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionQualityStatus,
    FinalQuestionVisual,
)

VISUAL_PHRASES = (
    "given figure",
    "given graph",
    "bar graph",
    "following graph",
    "study the following graph",
    "in the given figure",
    "diagram",
    "figure",
    "shaded region",
    "mirror image",
    "paper folding",
    "paper cutting",
    "answer figures",
    "answer figure",
)

RE_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def detect_visual_dependency(item: FinalQuestionItem) -> tuple[bool, list[str]]:
    text = (item.question_text_raw or "").lower()
    hits = [phrase for phrase in VISUAL_PHRASES if phrase in text]
    if item.visual_assets:
        hits.append("visual_asset_present")
    return bool(hits), hits


def apply_visual_metadata(item: FinalQuestionItem) -> FinalQuestionItem:
    """Attach visuals[] entries and upgrade status when visuals are required."""
    visual_needed, phrases = detect_visual_dependency(item)
    if not visual_needed and not item.visual_assets:
        return item

    issues = list(item.issues)
    visuals: list[FinalQuestionVisual] = list(item.visuals)
    assets = list(item.visual_assets)
    quality = item.quality_status

    for idx, asset in enumerate(assets or [None], start=1):
        visual_id = f"Q{item.question_number or item.global_order:03d}_V{idx}"
        visuals.append(
            FinalQuestionVisual(
                visual_id=visual_id,
                type=_visual_type_from_phrases(phrases),
                role="question",
                description="Figure/diagram required from source PDF.",
                extraction_status="image_required",
                render_target="canva",
                asset_ref=asset,
                issues=["original_visual_required"],
            ),
        )

    if not visuals and visual_needed:
        visuals.append(
            FinalQuestionVisual(
                visual_id=f"Q{item.question_number or item.global_order:03d}_V1",
                type=_visual_type_from_phrases(phrases),
                role="question",
                description="Figure/diagram required from source PDF.",
                extraction_status="image_required",
                render_target="canva",
                issues=["original_visual_required"],
            ),
        )

    if visual_needed or assets:
        if "visual_required" not in issues:
            issues.append("visual_required")
        if not assets:
            quality = FinalQuestionQualityStatus.VISUAL_REQUIRED
        elif quality == FinalQuestionQualityStatus.ACCEPTED_SAFE and not _has_usable_options(item):
            quality = FinalQuestionQualityStatus.VISUAL_REQUIRED

    return item.model_copy(
        update={
            "visuals": visuals,
            "visual_assets": assets,
            "quality_status": quality,
            "issues": _dedupe(issues),
        },
    )


def _visual_type_from_phrases(phrases: list[str]) -> str:
    joined = " ".join(phrases).lower()
    if "mirror image" in joined:
        return "mirror_image"
    if "paper folding" in joined or "paper cutting" in joined:
        return "paper_folding"
    if "bar graph" in joined or "graph" in joined or "chart" in joined:
        return "chart"
    if "table" in joined:
        return "table"
    if "figure" in joined or "diagram" in joined or "geometry" in joined:
        return "geometry"
    return "embedded_figure"


def _has_usable_options(item: FinalQuestionItem) -> bool:
    from meritranker_data_ingestion.services.deterministic_option_parser import count_usable_options

    return count_usable_options(item.options) >= 4


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
