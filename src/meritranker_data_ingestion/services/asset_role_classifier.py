"""Deterministic asset role classification for question candidates (Part 10)."""

from __future__ import annotations

import re
from pathlib import Path

from meritranker_data_ingestion.schemas.classification import LineType, MarkdownLineRecord
from meritranker_data_ingestion.schemas.question_candidates import (
    AssetRole,
    QuestionAssetReference,
    QuestionOptionCandidate,
)
from meritranker_data_ingestion.services.visual_intent_detector import (
    has_visual_option_pattern,
    is_visual_dependent,
)

RE_IMAGE_PATH = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def _extract_image_path(raw_markdown: str) -> str | None:
    match = RE_IMAGE_PATH.search(raw_markdown)
    return match.group(1) if match else None


def _asset_exists(assets_dir: Path | None, asset_path: str | None) -> bool:
    if not assets_dir or not asset_path:
        return False
    direct = assets_dir / asset_path
    if direct.is_file():
        return True
    name = Path(asset_path).name
    return any(p.name == name for p in assets_dir.rglob("*") if p.is_file())


def _last_option_line(options: list[QuestionOptionCandidate]) -> int | None:
    if not options:
        return None
    return max(opt.end_line for opt in options)


def _option_for_image_line(
    line_number: int,
    options: list[QuestionOptionCandidate],
) -> QuestionOptionCandidate | None:
    preceding = [opt for opt in options if opt.start_line < line_number]
    if not preceding:
        return None
    closest = max(preceding, key=lambda opt: opt.start_line)
    later = [opt for opt in options if opt.start_line > closest.start_line]
    next_option_line = min((opt.start_line for opt in later), default=10**9)
    if line_number < next_option_line:
        return closest
    return None


def should_link_image_to_option(
    option: QuestionOptionCandidate,
    *,
    visual_dependent: bool,
    visual_option_pattern: bool,
    image_line: int,
    last_option_line: int | None,
) -> bool:
    """Decide whether an image line should bind to a specific option."""
    if last_option_line is not None and image_line > last_option_line:
        if not option.text_raw.strip():
            return True
        return False
    if option.text_raw.strip() and not visual_dependent and not visual_option_pattern:
        return False
    if not option.text_raw.strip():
        return True
    if visual_option_pattern:
        return True
    return False


def classify_candidate_assets(
    candidate_lines: list[MarkdownLineRecord],
    options: list[QuestionOptionCandidate],
    *,
    question_text_raw: str,
    assets_dir: Path | None,
) -> tuple[list[QuestionAssetReference], list[str], int]:
    """
    Classify image assets and return (assets, candidate_issues, false_binding_prevented_count).
    Mutates option.linked_asset_paths in place when binding is appropriate.
    """
    issues: list[str] = []
    false_binding_prevented = 0
    assets: list[QuestionAssetReference] = []

    for opt in options:
        opt.linked_asset_paths = []

    visual_dependent = is_visual_dependent(question_text_raw)
    visual_option_pattern = has_visual_option_pattern(options)
    first_option_line = min((opt.start_line for opt in options), default=None)
    last_option_line = _last_option_line(options)

    pre_option_image_lines = [
        ln.line_number
        for ln in candidate_lines
        if ln.line_type == LineType.IMAGE_REFERENCE
        and (first_option_line is None or ln.line_number < first_option_line)
    ]

    for line in candidate_lines:
        if line.line_type != LineType.IMAGE_REFERENCE:
            continue

        asset_path = _extract_image_path(line.raw_text)
        role = AssetRole.UNKNOWN
        option_key: str | None = None
        asset_issues: list[str] = []

        matched_option = _option_for_image_line(line.line_number, options)

        if first_option_line is not None and line.line_number < first_option_line:
            if visual_dependent:
                if line.line_number == pre_option_image_lines[0]:
                    role = AssetRole.QUESTION_IMAGE
                else:
                    role = AssetRole.QUESTION_SUPPORT_IMAGE
            else:
                role = AssetRole.NOISE_CANDIDATE
                asset_issues.append("unexpected_image_for_text_question")
        elif matched_option is not None and should_link_image_to_option(
            matched_option,
            visual_dependent=visual_dependent,
            visual_option_pattern=visual_option_pattern,
            image_line=line.line_number,
            last_option_line=last_option_line,
        ):
            role = AssetRole.OPTION_IMAGE
            option_key = matched_option.key
            if asset_path:
                matched_option.linked_asset_paths.append(asset_path)
        elif last_option_line is not None and line.line_number > last_option_line:
            role = AssetRole.NOISE_CANDIDATE
            asset_issues.append("possible_noise_asset_after_options")
            false_binding_prevented += 1
        elif matched_option is not None and matched_option.text_raw.strip():
            role = AssetRole.NOISE_CANDIDATE
            asset_issues.append("image_after_text_options_unbound")
            false_binding_prevented += 1
        else:
            asset_issues.append("unlabeled_visual_assets")

        if asset_path and not _asset_exists(assets_dir, asset_path):
            asset_issues.append("linked_asset_missing")

        assets.append(
            QuestionAssetReference(
                raw_markdown=line.raw_text,
                asset_path=asset_path,
                role=role,
                option_key=option_key,
                line_number=line.line_number,
                confidence=line.confidence,
                issues=sorted(set(asset_issues)),
            ),
        )

    if any("possible_noise_asset_after_options" in a.issues for a in assets):
        issues.append("possible_noise_asset_after_options")

    return assets, issues, false_binding_prevented
