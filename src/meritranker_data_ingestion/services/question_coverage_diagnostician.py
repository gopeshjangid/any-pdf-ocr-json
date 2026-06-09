"""Question coverage diagnostics for extraction packages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    ASSETS_DIR_NAME,
    DIAGNOSTICS_DIR,
    FINAL_QUESTIONS_NAME,
    QUESTION_CANDIDATES_NAME,
    QUESTION_COVERAGE_JSON_NAME,
    QUESTION_COVERAGE_MD_NAME,
    RAW_MARKDOWN_NAME,
)
from meritranker_data_ingestion.schemas.classification import LineType
from meritranker_data_ingestion.schemas.final_question_package import FinalQuestionPackage
from meritranker_data_ingestion.schemas.question_candidates import QuestionCandidate
from meritranker_data_ingestion.services.classified_lines_loader import (
    load_classified_lines_file,
    load_content_lines_file,
    load_lines_for_downstream,
)
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)
from meritranker_data_ingestion.services.line_text_classifier import (
    _match_question_anchor,
    classification_target,
)

RE_IMAGE_REF = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


class CoverageDiagnosticError(Exception):
    """Raised when coverage diagnostics cannot proceed."""


@dataclass(frozen=True)
class CoveragePaths:
    package_dir: Path
    diagnostics_dir: Path
    coverage_json: Path
    coverage_md: Path


def build_coverage_paths(package_dir: Path) -> CoveragePaths:
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")
    diagnostics_dir = resolved / DIAGNOSTICS_DIR
    return CoveragePaths(
        package_dir=resolved,
        diagnostics_dir=diagnostics_dir,
        coverage_json=diagnostics_dir / QUESTION_COVERAGE_JSON_NAME,
        coverage_md=diagnostics_dir / QUESTION_COVERAGE_MD_NAME,
    )


def _continuous_ranges(numbers: list[int]) -> list[list[int]]:
    if not numbers:
        return []
    sorted_nums = sorted(numbers)
    ranges: list[list[int]] = [[sorted_nums[0]]]
    for num in sorted_nums[1:]:
        if num == ranges[-1][-1] + 1:
            ranges[-1].append(num)
        else:
            ranges.append([num])
    return ranges


def _anchor_numbers_from_lines(lines) -> set[int]:
    numbers: set[int] = set()
    for line in lines:
        if line.line_type != LineType.QUESTION_ANCHOR:
            continue
        if line.detected_label:
            match = re.search(r"(\d+)", line.detected_label)
            if match:
                numbers.add(int(match.group(1)))
                continue
        anchor = _match_question_anchor(classification_target(line.raw_text))
        if anchor:
            match = re.search(r"(\d+)", anchor[0])
            if match:
                numbers.add(int(match.group(1)))
    return numbers


def _diagnose_missing_reason(
    qnum: int,
    *,
    raw_text: str,
    content_lines,
    table_row_lines: set[int],
) -> str:
    patterns = [
        rf"\*\*Q{qnum}\.\*\*",
        rf"Q{qnum}\.<br>",
        rf"Q{qnum}\.",
        rf"Q\.{qnum}",
        rf"Q-{qnum}",
        rf"Q {qnum}",
    ]
    for pattern in patterns:
        if re.search(pattern, raw_text, re.IGNORECASE):
            if any(qnum in _anchor_numbers_from_lines([cl]) for cl in content_lines):
                return "merged_inside_previous_candidate"
            for line in content_lines:
                if line.parent_line_number in table_row_lines and re.search(
                    pattern, line.raw_text, re.IGNORECASE,
                ):
                    return "table_cell_split_issue"
            return "anchor_not_detected"

    image_only = re.search(rf"Q{qnum}", raw_text, re.IGNORECASE) is None
    if image_only:
        return "not_present_in_marker_markdown"
    return "unknown"


def diagnose_question_coverage(
    package_dir: Path,
    *,
    expected_count: int | None = None,
) -> dict:
    """Build question coverage diagnostic report."""
    paths = build_coverage_paths(package_dir)
    lines, _, _, line_source, content_used = load_lines_for_downstream(package_dir)

    content_anchor_nums = _anchor_numbers_from_lines(lines)

    candidates: list[QuestionCandidate] = []
    candidates_path = package_dir / "questions" / QUESTION_CANDIDATES_NAME
    if candidates_path.is_file():
        payload = json.loads(candidates_path.read_text(encoding="utf-8"))
        candidates = [QuestionCandidate.model_validate(item) for item in payload]
    candidate_nums = {c.question_number for c in candidates if c.question_number is not None}

    final_nums: set[int] = set()
    final_path = package_dir / "final" / FINAL_QUESTIONS_NAME
    if final_path.is_file():
        package = FinalQuestionPackage.model_validate(
            json.loads(final_path.read_text(encoding="utf-8")),
        )
        final_nums = {i.question_number for i in package.items if i.question_number is not None}

    expected_nums: set[int] = set()
    if expected_count and expected_count > 0:
        expected_nums = set(range(1, expected_count + 1))

    missing_content = sorted(expected_nums - content_anchor_nums) if expected_nums else []
    missing_candidates = sorted(expected_nums - candidate_nums) if expected_nums else []
    missing_final = sorted(expected_nums - final_nums) if expected_nums else []

    raw_text = ""
    raw_path = package_dir / "marker" / RAW_MARKDOWN_NAME
    if raw_path.is_file():
        raw_text = raw_path.read_text(encoding="utf-8")

    classified_lines = []
    lines_path = package_dir / "classified" / "lines.json"
    if lines_path.is_file():
        classified_lines = load_classified_lines_file(lines_path)
    table_row_lines = {
        ln.line_number for ln in classified_lines if ln.line_type == LineType.TABLE_ROW
    }

    content_lines = []
    content_path = package_dir / "classified" / "content-lines.json"
    if content_path.is_file():
        content_lines = load_content_lines_file(content_path)

    missing_reasons: dict[str, str] = {}
    for qnum in missing_candidates:
        missing_reasons[str(qnum)] = _diagnose_missing_reason(
            qnum,
            raw_text=raw_text,
            content_lines=content_lines,
            table_row_lines=table_row_lines,
        )

    image_refs = RE_IMAGE_REF.findall(raw_text)
    assets_dir = package_dir / "marker" / ASSETS_DIR_NAME
    asset_files: set[str] = set()
    if assets_dir.is_dir():
        asset_files = {p.name for p in assets_dir.rglob("*") if p.is_file()}

    unresolved = sorted({ref for ref in image_refs if ref.split("/")[-1] not in asset_files})
    duplicate_assets = len(list(assets_dir.rglob("*"))) - len(asset_files) if assets_dir.is_dir() else 0

    duplicate_nums = sorted(
        n for n in candidate_nums if sum(1 for c in candidates if c.question_number == n) > 1
    )

    return {
        "package_dir": str(package_dir),
        "expected_question_count": expected_count,
        "content_lines_used": content_used,
        "line_source_path": line_source,
        "content_line_anchor_numbers": sorted(content_anchor_nums),
        "content_line_anchor_count": len(content_anchor_nums),
        "candidate_numbers": sorted(candidate_nums),
        "candidate_count": len(candidates),
        "final_numbers": sorted(final_nums),
        "final_count": len(final_nums),
        "missing_at_content_line_stage": missing_content,
        "missing_at_candidate_stage": missing_candidates,
        "missing_at_final_stage": missing_final,
        "continuous_missing_ranges": _continuous_ranges(missing_candidates),
        "duplicate_question_numbers": duplicate_nums,
        "missing_reasons": missing_reasons,
        "image_reference_count": len(image_refs),
        "image_asset_count": len(asset_files),
        "missing_asset_reference_count": len(unresolved),
        "unresolved_image_references_sample": unresolved[:20],
        "duplicate_asset_count": max(0, duplicate_assets),
        "warnings": (
            ["image_references_found_but_assets_missing"] if unresolved else []
        ),
    }


def render_coverage_markdown(report: dict) -> str:
    lines = [
        "# Question Coverage Diagnostics",
        "",
        f"- Expected count: {report.get('expected_question_count', 'n/a')}",
        f"- Content-line anchors: {report['content_line_anchor_count']}",
        f"- Candidates: {report['candidate_count']}",
        f"- Final items: {report['final_count']}",
        f"- Content lines used: {report['content_lines_used']}",
        "",
        "## Missing at Candidate Stage",
        "",
    ]
    missing = report.get("missing_at_candidate_stage", [])
    if missing:
        lines.append(", ".join(str(n) for n in missing))
    else:
        lines.append("None")

    ranges = report.get("continuous_missing_ranges", [])
    if ranges:
        lines.extend(["", "## Continuous Missing Ranges", ""])
        for r in ranges:
            lines.append(f"- {r[0]}–{r[-1]}" if len(r) > 1 else f"- {r[0]}")

    reasons = report.get("missing_reasons", {})
    if reasons:
        lines.extend(["", "## Missing Reasons", ""])
        for qnum, reason in sorted(reasons.items(), key=lambda x: int(x[0])):
            lines.append(f"- Q{qnum}: {reason}")

    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in report["warnings"]:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def write_coverage_outputs(report: dict, paths: CoveragePaths) -> None:
    assert_output_contains(paths.package_dir, paths.diagnostics_dir)
    paths.diagnostics_dir.mkdir(parents=True, exist_ok=True)
    paths.coverage_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    paths.coverage_md.write_text(render_coverage_markdown(report), encoding="utf-8")


def diagnose_question_coverage_package(
    package_dir: Path,
    *,
    expected_question_count: int | None = None,
) -> dict:
    paths = build_coverage_paths(package_dir)
    report = diagnose_question_coverage(package_dir, expected_count=expected_question_count)
    write_coverage_outputs(report, paths)
    return report
