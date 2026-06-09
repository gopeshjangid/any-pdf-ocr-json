"""Diagnostics inspector for raw markdown extraction packages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    ASSETS_DIR_NAME,
    DIAGNOSTICS_DIR,
    RAW_MARKDOWN_INSPECTION_JSON_NAME,
    RAW_MARKDOWN_INSPECTION_MD_NAME,
    RAW_MARKDOWN_NAME,
)
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)
from meritranker_data_ingestion.services.line_text_classifier import _match_question_anchor

RE_IMAGE_REF = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
RE_OPTION_LIKE = re.compile(r"\([A-Da-d]\)|^[A-Da-d]\)", re.MULTILINE)
RE_SOLUTION_LIKE = re.compile(r"^S\s*\d+|^Ans(?:wer)?\s*[\.\:]", re.IGNORECASE | re.MULTILINE)
RE_TABLE_ROW = re.compile(r"^\s*\|")


class InspectionError(Exception):
    """Raised when inspection cannot proceed."""


@dataclass(frozen=True)
class InspectionPaths:
    package_dir: Path
    raw_markdown: Path
    assets_dir: Path
    diagnostics_dir: Path
    inspection_json: Path
    inspection_md: Path


def build_inspection_paths(package_dir: Path) -> InspectionPaths:
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")

    marker_dir = resolved / "marker"
    diagnostics_dir = resolved / DIAGNOSTICS_DIR
    return InspectionPaths(
        package_dir=resolved,
        raw_markdown=marker_dir / RAW_MARKDOWN_NAME,
        assets_dir=marker_dir / ASSETS_DIR_NAME,
        diagnostics_dir=diagnostics_dir,
        inspection_json=diagnostics_dir / RAW_MARKDOWN_INSPECTION_JSON_NAME,
        inspection_md=diagnostics_dir / RAW_MARKDOWN_INSPECTION_MD_NAME,
    )


def _truncate_sample(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def inspect_raw_markdown(package_dir: Path) -> dict:
    """Build diagnostics report for raw markdown without printing full content."""
    paths = build_inspection_paths(package_dir)
    if not paths.raw_markdown.is_file():
        raise InspectionError(f"Missing markdown file: {paths.raw_markdown}")

    text = paths.raw_markdown.read_text(encoding="utf-8")
    lines = text.splitlines()

    table_rows = [ln for ln in lines if RE_TABLE_ROW.match(ln)]
    table_rows_with_q = []
    for ln in table_rows:
        if re.search(r"Q\s*\d+", ln, re.IGNORECASE):
            table_rows_with_q.append(_truncate_sample(ln))

    image_refs = RE_IMAGE_REF.findall(text)
    asset_files: list[str] = []
    if paths.assets_dir.is_dir():
        asset_files = sorted(
            str(p.relative_to(paths.assets_dir))
            for p in paths.assets_dir.rglob("*")
            if p.is_file()
        )

    question_samples: list[str] = []
    option_samples: list[str] = []
    solution_samples: list[str] = []

    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            continue
        if _match_question_anchor(stripped):
            question_samples.append(_truncate_sample(stripped))
        if RE_OPTION_LIKE.search(stripped):
            option_samples.append(_truncate_sample(stripped))
        if RE_SOLUTION_LIKE.search(stripped):
            solution_samples.append(_truncate_sample(stripped))

    warnings: list[str] = []
    if image_refs and not asset_files:
        warnings.append("image_references_found_but_assets_missing")
    if len(lines) > 100 and not question_samples and table_rows_with_q:
        warnings.append("questions_may_be_inside_table_rows")

    return {
        "package_dir": str(paths.package_dir),
        "source_markdown": str(paths.raw_markdown),
        "total_raw_lines": len(lines),
        "table_row_count": len(table_rows),
        "table_rows_with_question_patterns": table_rows_with_q[:10],
        "image_reference_count": len(image_refs),
        "image_asset_files": asset_files[:50],
        "image_asset_count": len(asset_files),
        "question_like_samples": question_samples[:10],
        "option_like_samples": option_samples[:10],
        "solution_like_samples": solution_samples[:10],
        "warnings": warnings,
    }


def render_inspection_markdown(report: dict) -> str:
    lines = [
        "# Raw Markdown Inspection",
        "",
        "> Read-only diagnostics. Does not modify source or final JSON.",
        "",
        "## Summary",
        "",
        f"- Total raw lines: {report['total_raw_lines']}",
        f"- Table rows: {report['table_row_count']}",
        f"- Image references: {report['image_reference_count']}",
        f"- Image asset files: {report['image_asset_count']}",
        "",
        "## Table Rows With Question Patterns",
        "",
    ]
    if report["table_rows_with_question_patterns"]:
        for sample in report["table_rows_with_question_patterns"]:
            lines.append(f"- `{sample}`")
    else:
        lines.append("- None detected in raw lines")

    lines.extend(["", "## Question-Like Samples", ""])
    for sample in report.get("question_like_samples", [])[:5]:
        lines.append(f"- `{sample}`")

    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in report["warnings"]:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)


def write_inspection_outputs(report: dict, paths: InspectionPaths) -> None:
    assert_output_contains(paths.package_dir, paths.diagnostics_dir)
    paths.diagnostics_dir.mkdir(parents=True, exist_ok=True)

    assert_output_contains(paths.package_dir, paths.inspection_json)
    paths.inspection_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    assert_output_contains(paths.package_dir, paths.inspection_md)
    paths.inspection_md.write_text(render_inspection_markdown(report), encoding="utf-8")


def inspect_raw_markdown_package(package_dir: Path) -> dict:
    paths = build_inspection_paths(package_dir)
    report = inspect_raw_markdown(package_dir)
    write_inspection_outputs(report, paths)
    return report
