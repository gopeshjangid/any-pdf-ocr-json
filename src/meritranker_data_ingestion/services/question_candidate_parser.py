"""Deterministic question candidate parser from classified markdown artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    ASSETS_DIR_NAME,
    CLASSIFIED_BLOCKS_NAME,
    CLASSIFIED_CONTENT_LINES_NAME,
    CLASSIFIED_DIR,
    CLASSIFIED_LINES_NAME,
    MARKER_DIR,
    QUESTION_CANDIDATE_REPORT_NAME,
    QUESTION_CANDIDATES_NAME,
    QUESTIONS_DIR,
)
from meritranker_data_ingestion.schemas.classification import ContentLineRecord
from meritranker_data_ingestion.schemas.classification import (
    LineType,
    MarkdownBlockRecord,
    MarkdownLineRecord,
)
from meritranker_data_ingestion.schemas.question_candidates import (
    AssetRole,
    CandidateReviewStatus,
    OptionSourceTrace,
    ParseStatus,
    QuestionAssetReference,
    QuestionCandidate,
    QuestionCandidateParseResult,
    QuestionOptionCandidate,
    QuestionSourceTrace,
)
from meritranker_data_ingestion.services.asset_role_classifier import classify_candidate_assets
from meritranker_data_ingestion.services.candidate_report_metrics import (
    build_status_distribution,
    compute_candidate_report_metrics,
)
from meritranker_data_ingestion.services.candidate_structure_auditor import write_structure_audit
from meritranker_data_ingestion.services.classified_lines_loader import load_lines_for_downstream
from meritranker_data_ingestion.services.line_text_classifier import (
    RE_SOLUTION_HEADING,
    classification_target,
)
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)
from meritranker_data_ingestion.services.option_marker_splitter import split_option_text
from meritranker_data_ingestion.services.visual_intent_detector import (
    has_visual_option_pattern,
    is_image_only_without_labels,
    is_visual_dependent,
    is_visual_text_options_question,
)

RE_OPTION_PAREN = re.compile(r"^\s*\(([A-Ea-e])\)\s*(.*)$", re.DOTALL)
RE_OPTION_DOT = re.compile(r"^([A-E])\.\s*(.*)$", re.DOTALL)
RE_IMAGE_PATH = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
RE_QUESTION_NUM = re.compile(r"^Q\s*\.?\s*(\d+)", re.IGNORECASE)
RE_QUESTION_NUM_WORD = re.compile(r"^Question\s+(?:No\.?\s*)?(\d+)", re.IGNORECASE)
RE_QUESTION_NUM_QUE = re.compile(r"^Que\.?\s*(\d+)", re.IGNORECASE)
RE_QUESTION_NUMERIC = re.compile(r"^(\d+)[\.\)]\s+", re.IGNORECASE)

MIN_LINES_FOR_ZERO_CANDIDATE_GATE = 100
ZERO_CANDIDATE_ERROR = "no_question_candidates_detected_from_non_empty_markdown"
VALID_CANDIDATE_CONFIDENCE_THRESHOLD = 0.85
PROVENANCE_ISSUE_PREFIXES = ("parent_line_", "table_cell_")
EXPECTED_OPTION_KEYS = frozenset({"A", "B", "C", "D"})


def _is_provenance_issue(issue: str) -> bool:
    return issue.startswith(PROVENANCE_ISSUE_PREFIXES)


def _real_issues(issues: list[str]) -> list[str]:
    return [issue for issue in issues if not _is_provenance_issue(issue)]

STRUCTURAL_LINE_TYPES = {
    LineType.PAGE_NUMBER_MARKER,
    LineType.PAGE_BREAK_MARKER,
    LineType.PAGE_FOOTER_MARKER,
}

NOISE_LINE_TYPES = {
    LineType.PAGE_FOOTER_MARKER,
    LineType.NOISE_CANDIDATE,
}

SOLUTION_LINE_TYPES = {
    LineType.SOLUTION_SECTION_HEADING,
    LineType.SOLUTION_ANCHOR,
    LineType.ANSWER_MARKER,
}


class QuestionParseError(Exception):
    """Raised when question candidate parsing cannot proceed."""


@dataclass(frozen=True)
class QuestionParsePaths:
    """Paths for question candidate parse input/output."""

    package_dir: Path
    lines_json: Path
    blocks_json: Path
    content_lines_json: Path
    questions_dir: Path
    candidates_json: Path
    report_json: Path


def build_question_parse_paths(package_dir: Path) -> QuestionParsePaths:
    """Resolve question parse paths for an extraction package."""
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")

    classified_dir = resolved / CLASSIFIED_DIR
    questions_dir = resolved / QUESTIONS_DIR
    return QuestionParsePaths(
        package_dir=resolved,
        lines_json=classified_dir / CLASSIFIED_LINES_NAME,
        blocks_json=classified_dir / CLASSIFIED_BLOCKS_NAME,
        content_lines_json=classified_dir / CLASSIFIED_CONTENT_LINES_NAME,
        questions_dir=questions_dir,
        candidates_json=questions_dir / QUESTION_CANDIDATES_NAME,
        report_json=questions_dir / QUESTION_CANDIDATE_REPORT_NAME,
    )


def load_classified_lines(path: Path) -> list[MarkdownLineRecord]:
    """Load lines.json into MarkdownLineRecord models."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [MarkdownLineRecord.model_validate(item) for item in payload]


def load_classified_blocks(path: Path) -> list[MarkdownBlockRecord]:
    """Load blocks.json into MarkdownBlockRecord models."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [MarkdownBlockRecord.model_validate(item) for item in payload]


def load_content_lines(path: Path) -> list[ContentLineRecord]:
    """Load content-lines.json into ContentLineRecord models."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ContentLineRecord.model_validate(item) for item in payload]


def load_lines_for_parsing(
    paths: QuestionParsePaths,
) -> tuple[list[MarkdownLineRecord], dict[int, ContentLineRecord], int]:
    """Prefer content-lines.json; fallback to lines.json."""
    lines, content_by_num, raw_line_count, _, _ = load_lines_for_downstream(paths.package_dir)
    return lines, content_by_num, raw_line_count


def _extract_question_number_raw(line: MarkdownLineRecord) -> str | None:
    if line.detected_label:
        return line.detected_label
    text = classification_target(line.raw_text)
    for pattern in (RE_QUESTION_NUM, RE_QUESTION_NUM_WORD, RE_QUESTION_NUM_QUE):
        match = pattern.match(text)
        if match:
            return f"Q{match.group(1)}"
    numeric = RE_QUESTION_NUMERIC.match(text)
    if numeric:
        return f"Q{numeric.group(1)}"
    return None


def _extract_question_number(label: str | None) -> int | None:
    if not label:
        return None
    match = re.search(r"(\d+)", label)
    return int(match.group(1)) if match else None


def _parse_option_key(line: MarkdownLineRecord) -> tuple[str | None, str | None, str]:
    """Return (normalized key, key_raw, remainder text)."""
    text = classification_target(line.raw_text)
    paren = RE_OPTION_PAREN.match(text)
    if paren:
        key = paren.group(1).upper()
        remainder = paren.group(2)
        key_raw = f"({paren.group(1)})"
        return key, key_raw, remainder

    dot = RE_OPTION_DOT.match(text.strip())
    if dot:
        key = dot.group(1).upper()
        remainder = dot.group(2)
        key_raw = f"{dot.group(1)}."
        return key, key_raw, remainder

    if line.detected_label:
        return line.detected_label.upper(), line.detected_label, ""
    return None, None, text


def _extract_image_path(raw_markdown: str) -> str | None:
    match = RE_IMAGE_PATH.search(raw_markdown)
    return match.group(1) if match else None


def _is_solution_boundary(line: MarkdownLineRecord) -> bool:
    return line.line_type in SOLUTION_LINE_TYPES


def _find_solution_section_line(lines: list[MarkdownLineRecord]) -> int | None:
    for line in lines:
        if line.line_type == LineType.SOLUTION_SECTION_HEADING:
            return line.line_number
        if RE_SOLUTION_HEADING.match(classification_target(line.raw_text)):
            return line.line_number
    return None


def _find_question_anchor_indices(
    lines: list[MarkdownLineRecord],
    solution_start: int | None,
) -> list[int]:
    indices: list[int] = []
    for idx, line in enumerate(lines):
        if line.line_type != LineType.QUESTION_ANCHOR:
            continue
        if solution_start is not None and line.line_number >= solution_start:
            continue
        indices.append(idx)
    return indices


def _collect_candidate_lines(
    lines: list[MarkdownLineRecord],
    start_idx: int,
    end_idx: int,
) -> list[MarkdownLineRecord]:
    return lines[start_idx:end_idx]


def _build_question_text_raw(candidate_lines: list[MarkdownLineRecord]) -> str:
    """Build question stem text excluding options, images, and structural noise."""
    parts: list[str] = []
    for line in candidate_lines:
        if line.line_type in {
            LineType.OPTION_CANDIDATE,
            LineType.IMAGE_REFERENCE,
            *STRUCTURAL_LINE_TYPES,
            *NOISE_LINE_TYPES,
        }:
            if line.line_type in {LineType.OPTION_CANDIDATE, LineType.IMAGE_REFERENCE}:
                break
            continue
        parts.append(line.raw_text)
    return "\n".join(parts)


def _option_source_trace(
    line: MarkdownLineRecord,
    content_by_num: dict[int, ContentLineRecord],
    *,
    end_line: int,
) -> OptionSourceTrace | None:
    content = content_by_num.get(line.line_number)
    if content is None:
        return OptionSourceTrace(start_line=line.line_number, end_line=end_line)
    return OptionSourceTrace(
        start_line=line.line_number,
        end_line=end_line,
        parent_line_number=content.parent_line_number,
        source_kind=content.source_kind.value,
        table_cell_index=content.table_cell_index,
        table_segment_index=content.table_segment_index,
    )


def _resolve_assets_dir(package_dir: Path | None) -> Path | None:
    if package_dir is None:
        return None
    assets_dir = package_dir / MARKER_DIR / ASSETS_DIR_NAME
    return assets_dir if assets_dir.is_dir() else None


def _asset_exists(assets_dir: Path | None, asset_path: str | None) -> bool:
    if not assets_dir or not asset_path:
        return False
    direct = assets_dir / asset_path
    if direct.is_file():
        return True
    name = Path(asset_path).name
    return any(p.name == name for p in assets_dir.rglob("*") if p.is_file())


def _effective_option_issues(opt: QuestionOptionCandidate) -> list[str]:
    issues = _real_issues(opt.issues)
    if opt.linked_asset_paths and "empty_option_text" in issues:
        return [issue for issue in issues if issue != "empty_option_text"]
    return issues


def _parse_options(
    candidate_lines: list[MarkdownLineRecord],
    content_by_num: dict[int, ContentLineRecord] | None = None,
) -> tuple[list[QuestionOptionCandidate], bool]:
    options: list[QuestionOptionCandidate] = []
    idx = 0
    while idx < len(candidate_lines):
        line = candidate_lines[idx]
        if line.line_type != LineType.OPTION_CANDIDATE:
            idx += 1
            continue

        key, key_raw, remainder = _parse_option_key(line)
        text_parts = [remainder] if remainder else []
        linked_asset_paths: list[str] = []
        issues = _real_issues(list(line.issues))
        start_line = line.line_number
        end_line = line.line_number
        confidence = line.confidence
        idx += 1

        while idx < len(candidate_lines):
            next_line = candidate_lines[idx]
            if next_line.line_type == LineType.OPTION_CANDIDATE:
                break
            if next_line.line_type == LineType.IMAGE_REFERENCE:
                if text_parts:
                    break
                end_line = next_line.line_number
                idx += 1
                continue
            if _is_solution_boundary(next_line):
                break
            if next_line.line_type in STRUCTURAL_LINE_TYPES:
                idx += 1
                continue
            if next_line.line_type in {LineType.TEXT, LineType.MATH_BLOCK, LineType.TABLE_ROW}:
                text_parts.append(next_line.raw_text)
                end_line = next_line.line_number
                confidence = min(confidence, next_line.confidence)
                idx += 1
                continue
            break

        text_raw = "\n".join(text_parts)
        if not text_raw.strip() and not linked_asset_paths:
            issues.append("empty_option_text")

        trace = _option_source_trace(
            line,
            content_by_num or {},
            end_line=end_line,
        )
        options.append(
            QuestionOptionCandidate(
                key=key,
                key_raw=key_raw,
                text_raw=text_raw,
                start_line=start_line,
                end_line=end_line,
                confidence=confidence,
                source_trace=trace,
                linked_asset_paths=linked_asset_paths,
                issues=issues,
            ),
        )

    return _apply_option_line_splits(options)


def _apply_option_line_splits(
    options: list[QuestionOptionCandidate],
) -> tuple[list[QuestionOptionCandidate], bool]:
    """Split options whose text_raw contains embedded option markers."""
    expanded: list[QuestionOptionCandidate] = []
    split_applied = False
    for opt in options:
        segments, was_split = split_option_text(opt.text_raw, opt.key)
        if was_split:
            split_applied = True
            for seg_key, seg_key_raw, seg_text in segments:
                expanded.append(
                    opt.model_copy(
                        update={
                            "key": seg_key,
                            "key_raw": seg_key_raw,
                            "text_raw": seg_text,
                        },
                    ),
                )
        else:
            expanded.append(opt)
    return expanded, split_applied


def _assess_visual_issues(
    options: list[QuestionOptionCandidate],
    assets: list[QuestionAssetReference],
    *,
    question_text_raw: str,
) -> list[str]:
    visual_issues: list[str] = []
    option_keys = {opt.key for opt in options if opt.key}
    visual_dependent = is_visual_dependent(question_text_raw)

    if is_image_only_without_labels(options, assets):
        visual_issues.extend([
            "missing_options",
            "source_backed_option_labels_missing",
            "unlabeled_visual_assets",
        ])
        return sorted(set(visual_issues))

    if is_visual_text_options_question(question_text_raw, options):
        visual_issues.append("visual_question_requires_diagram_syntax")
        return sorted(set(visual_issues))

    if has_visual_option_pattern(options):
        visual_issues.append("visual_question_requires_review")
        return sorted(set(visual_issues))

    if not assets and not visual_dependent:
        return []

    if visual_dependent and option_keys and option_keys != EXPECTED_OPTION_KEYS:
        visual_issues.append("missing_option_labels_for_visual_question")

    unlabeled = [
        asset
        for asset in assets
        if asset.role == AssetRole.UNKNOWN
        or (
            asset.role not in {
                AssetRole.QUESTION_IMAGE,
                AssetRole.QUESTION_SUPPORT_IMAGE,
                AssetRole.OPTION_IMAGE,
                AssetRole.NOISE_CANDIDATE,
            }
            and "unlabeled_visual_assets" in asset.issues
        )
    ]
    if unlabeled:
        visual_issues.append("unlabeled_visual_assets")

    for opt in options:
        if not opt.text_raw.strip() and not opt.linked_asset_paths:
            visual_issues.append("empty_option_text_without_asset")

    if options and option_keys != EXPECTED_OPTION_KEYS:
        visual_issues.append("invalid_option_count")

    return sorted(set(visual_issues))


def _compute_review_status(
    issues: list[str],
    options: list[QuestionOptionCandidate],
    confidence: float,
    is_duplicate: bool,
    *,
    question_text_raw: str,
    assets: list[QuestionAssetReference] | None = None,
) -> CandidateReviewStatus:
    if is_duplicate:
        return CandidateReviewStatus.CANDIDATE_DUPLICATE
    if not question_text_raw.strip():
        return CandidateReviewStatus.CANDIDATE_INCOMPLETE
    if not options:
        return CandidateReviewStatus.CANDIDATE_INCOMPLETE

    visual_issues = _assess_visual_issues(
        options,
        assets or [],
        question_text_raw=question_text_raw,
    )
    all_issues = list(issues) + visual_issues

    incomplete_issues = {
        "missing_options",
        "source_backed_option_labels_missing",
        "missing_option_labels_for_visual_question",
        "unlabeled_visual_assets",
        "invalid_option_count",
        "empty_option_text_without_asset",
    }
    if any(issue in incomplete_issues for issue in all_issues):
        return CandidateReviewStatus.CANDIDATE_INCOMPLETE

    if "visual_question_requires_review" in all_issues:
        return CandidateReviewStatus.CANDIDATE_NEEDS_REVIEW

    non_blocking_issues = {
        "visual_question_requires_diagram_syntax",
        "possible_noise_asset_after_options",
        "same_line_option_split_applied",
    }
    real_candidate_issues = [
        issue
        for issue in _real_issues(all_issues)
        if issue not in non_blocking_issues
    ]
    if real_candidate_issues:
        return CandidateReviewStatus.CANDIDATE_NEEDS_REVIEW

    for opt in options:
        if _effective_option_issues(opt):
            return CandidateReviewStatus.CANDIDATE_NEEDS_REVIEW

    if confidence < VALID_CANDIDATE_CONFIDENCE_THRESHOLD:
        return CandidateReviewStatus.CANDIDATE_NEEDS_REVIEW

    return CandidateReviewStatus.CANDIDATE_VALID


def _build_candidate(
    candidate_id: int,
    candidate_lines: list[MarkdownLineRecord],
    is_duplicate: bool,
    content_by_num: dict[int, ContentLineRecord] | None = None,
    *,
    package_dir: Path | None = None,
) -> QuestionCandidate:
    anchor = candidate_lines[0]
    issues: list[str] = []
    if is_duplicate:
        issues.append("duplicate_question_number")

    for line in candidate_lines:
        if line.line_type in NOISE_LINE_TYPES:
            issues.append(f"noise_inside_candidate_line_{line.line_number}")

    raw_text = "\n".join(line.raw_text for line in candidate_lines)
    question_text_raw = _build_question_text_raw(candidate_lines)
    options, split_applied = _parse_options(candidate_lines, content_by_num)
    if split_applied:
        issues.append("same_line_option_split_applied")

    assets_dir = _resolve_assets_dir(package_dir)
    assets, asset_issues, _false_prevented = classify_candidate_assets(
        candidate_lines,
        options,
        question_text_raw=question_text_raw,
        assets_dir=assets_dir,
    )
    issues.extend(asset_issues)

    for opt in options:
        if opt.linked_asset_paths and "empty_option_text" in opt.issues:
            opt.issues = [issue for issue in opt.issues if issue != "empty_option_text"]

    visual_issues = _assess_visual_issues(
        options,
        assets,
        question_text_raw=question_text_raw,
    )
    issues.extend(visual_issues)

    if not options:
        issues.append("missing_options")

    line_numbers = [line.line_number for line in candidate_lines]
    page_numbers = [line.page_number for line in candidate_lines if line.page_number is not None]

    confidences = [line.confidence for line in candidate_lines]
    candidate_confidence = sum(confidences) / len(confidences) if confidences else 0.5

    question_number_raw = _extract_question_number_raw(anchor)
    question_number = _extract_question_number(question_number_raw)

    review_status = _compute_review_status(
        issues,
        options,
        candidate_confidence,
        is_duplicate,
        question_text_raw=question_text_raw,
        assets=assets,
    )

    return QuestionCandidate(
        question_id=f"q_{candidate_id:04d}",
        question_number=question_number,
        question_number_raw=question_number_raw,
        raw_text=raw_text,
        question_text_raw=question_text_raw,
        options=options,
        assets=assets,
        source_trace=QuestionSourceTrace(
            start_line=line_numbers[0],
            end_line=line_numbers[-1],
            page_start=min(page_numbers) if page_numbers else None,
            page_end=max(page_numbers) if page_numbers else None,
            line_numbers=line_numbers,
        ),
        confidence=candidate_confidence,
        review_status=review_status,
        issues=sorted(set(issues)),
    )


def _detect_sequence_issues(
    candidates: list[QuestionCandidate],
) -> tuple[list[int], list[int], list[str]]:
    """Return duplicate numbers, missing numbers, and warnings."""
    warnings: list[str] = []
    numbers = [c.question_number for c in candidates if c.question_number is not None]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})

    missing: list[int] = []
    if numbers:
        expected_max = max(numbers)
        full_range = set(range(1, expected_max + 1))
        present = set(numbers)
        missing = sorted(full_range - present)
        if missing:
            warnings.append(f"missing_question_numbers:{missing}")

    if duplicates:
        warnings.append(f"duplicate_question_numbers:{duplicates}")

    return duplicates, missing, warnings


def parse_question_candidates(
    lines: list[MarkdownLineRecord],
    blocks: list[MarkdownBlockRecord],  # noqa: ARG001 — reserved for Part 5
    package_dir: Path,
    *,
    raw_line_count: int = 0,
    content_by_num: dict[int, ContentLineRecord] | None = None,
) -> QuestionCandidateParseResult:
    """Parse question candidates from classified line records."""
    solution_start = _find_solution_section_line(lines)
    anchor_indices = _find_question_anchor_indices(lines, solution_start)

    warnings: list[str] = []
    if solution_start is not None:
        warnings.append(f"solution_section_starts_at_line_{solution_start}")

    candidates: list[QuestionCandidate] = []
    seen_numbers: dict[int, int] = {}

    for i, start_idx in enumerate(anchor_indices):
        seq = i + 1
        if i + 1 < len(anchor_indices):
            end_idx = anchor_indices[i + 1]
        else:
            end_idx = len(lines)

        if solution_start is not None:
            while end_idx > start_idx and lines[end_idx - 1].line_number >= solution_start:
                end_idx -= 1

        candidate_lines = _collect_candidate_lines(lines, start_idx, end_idx)
        if not candidate_lines:
            continue

        q_num = _extract_question_number(_extract_question_number_raw(candidate_lines[0]))
        is_duplicate = q_num is not None and q_num in seen_numbers
        if q_num is not None:
            seen_numbers[q_num] = seen_numbers.get(q_num, 0) + 1

        candidates.append(
            _build_candidate(
                seq,
                candidate_lines,
                is_duplicate,
                content_by_num,
                package_dir=package_dir,
            ),
        )

    duplicates, missing, seq_warnings = _detect_sequence_issues(candidates)
    warnings.extend(seq_warnings)

    metrics = compute_candidate_report_metrics(candidates)
    status_distribution = build_status_distribution(candidates)

    status = ParseStatus.SUCCEEDED
    errors: list[str] = []
    effective_raw_count = raw_line_count or len(lines)
    if len(candidates) == 0 and effective_raw_count > MIN_LINES_FOR_ZERO_CANDIDATE_GATE:
        status = ParseStatus.FAILED
        errors.append(ZERO_CANDIDATE_ERROR)

    if sum(status_distribution.values()) != len(candidates):
        errors.append("status_distribution_mismatch")
        status = ParseStatus.FAILED

    return QuestionCandidateParseResult(
        package_dir=str(package_dir),
        status=status,
        candidates=candidates,
        status_distribution=status_distribution,
        duplicate_question_numbers=duplicates,
        missing_question_numbers=missing,
        warnings=warnings,
        errors=errors,
        **metrics,
    )


def write_question_parse_outputs(
    result: QuestionCandidateParseResult,
    paths: QuestionParsePaths,
) -> None:
    """Write question-candidates.json and question-candidate-report.json."""
    assert_output_contains(paths.package_dir, paths.questions_dir)
    paths.questions_dir.mkdir(parents=True, exist_ok=True)

    candidates_payload = [c.model_dump(mode="json") for c in result.candidates]
    assert_output_contains(paths.package_dir, paths.candidates_json)
    paths.candidates_json.write_text(
        json.dumps(candidates_payload, indent=2),
        encoding="utf-8",
    )

    report = {
        "package_dir": result.package_dir,
        "status": result.status.value,
        "created_at": result.created_at.isoformat(),
        "total_candidates": result.total_candidates,
        "valid_candidates": result.valid_candidates,
        "needs_review_candidates": result.needs_review_candidates,
        "incomplete_candidates": result.incomplete_candidates,
        "duplicate_candidates": result.duplicate_candidates,
        "rejected_candidates": result.rejected_candidates,
        "status_distribution": result.status_distribution,
        "duplicate_question_numbers": result.duplicate_question_numbers,
        "missing_question_numbers": result.missing_question_numbers,
        "candidates_with_images": result.candidates_with_images,
        "candidates_with_question_images": result.candidates_with_question_images,
        "candidates_with_question_support_images": result.candidates_with_question_support_images,
        "candidates_with_option_images": result.candidates_with_option_images,
        "candidates_with_linked_option_assets": result.candidates_with_linked_option_assets,
        "candidates_with_noise": result.candidates_with_noise,
        "noise_asset_count": result.noise_asset_count,
        "candidates_with_no_options": result.candidates_with_no_options,
        "candidates_with_partial_options": result.candidates_with_partial_options,
        "candidates_with_invalid_option_count": result.candidates_with_invalid_option_count,
        "visual_dependent_count": result.visual_dependent_count,
        "visual_text_option_count": result.visual_text_option_count,
        "visual_image_option_count": result.visual_image_option_count,
        "visual_missing_option_labels_count": result.visual_missing_option_labels_count,
        "same_line_option_split_count": result.same_line_option_split_count,
        "source_backed_option_labels_missing_count": result.source_backed_option_labels_missing_count,
        "unlabeled_visual_assets_count": result.unlabeled_visual_assets_count,
        "possible_noise_asset_after_options_count": result.possible_noise_asset_after_options_count,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    assert_output_contains(paths.package_dir, paths.report_json)
    paths.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")


def parse_question_candidates_package(package_dir: Path) -> QuestionCandidateParseResult:
    """Load classified artifacts, parse candidates, and write outputs."""
    paths = build_question_parse_paths(package_dir)

    if not paths.lines_json.is_file():
        result = QuestionCandidateParseResult(
            package_dir=str(paths.package_dir),
            status=ParseStatus.FAILED,
            errors=[f"Missing classified lines file: {paths.lines_json}"],
        )
        _try_write_failed_report(result, paths)
        raise QuestionParseError(result.errors[0])

    if not paths.blocks_json.is_file():
        result = QuestionCandidateParseResult(
            package_dir=str(paths.package_dir),
            status=ParseStatus.FAILED,
            errors=[f"Missing classified blocks file: {paths.blocks_json}"],
        )
        _try_write_failed_report(result, paths)
        raise QuestionParseError(result.errors[0])

    lines, content_by_num, raw_line_count = load_lines_for_parsing(paths)
    blocks = load_classified_blocks(paths.blocks_json)
    result = parse_question_candidates(
        lines,
        blocks,
        paths.package_dir,
        raw_line_count=raw_line_count,
        content_by_num=content_by_num,
    )
    write_question_parse_outputs(result, paths)
    write_structure_audit(result.candidates, paths.package_dir)
    if result.status == ParseStatus.FAILED and result.errors:
        raise QuestionParseError(result.errors[0])
    return result


def _try_write_failed_report(
    result: QuestionCandidateParseResult,
    paths: QuestionParsePaths,
) -> None:
    try:
        paths.questions_dir.mkdir(parents=True, exist_ok=True)
        write_question_parse_outputs(result, paths)
    except PathValidationError:
        pass
