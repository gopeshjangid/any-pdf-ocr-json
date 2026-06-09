"""Per-PDF human-readable summary markdown (Part 14X)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def summary_md_path(output_folder: Path, stem: str) -> Path:
    return output_folder / f"{stem}.summary.md"


def write_pdf_summary(
    output_folder: Path,
    *,
    stem: str,
    pdf_file_name: str,
    metrics: dict[str, Any],
    questions_json_path: Path | None,
    review_json_path: Path | None,
) -> Path:
    """Write compact per-PDF summary for day-to-day review."""
    path = summary_md_path(output_folder, stem)
    audit = metrics.get("public_json_audit", "missing")
    ready = metrics.get("ready_count", 0)
    review = metrics.get("review_count", 0)
    visual = metrics.get("visual_required_count", 0)
    blocked = metrics.get("blocked_count", 0)
    layout = metrics.get("layout_type_detected", "unknown")
    strategy = metrics.get("extractor_strategy_effective", "unknown")

    lines = [
        f"# Summary — {pdf_file_name}",
        "",
        "## Quality",
        "",
        f"- layout_type: {layout}",
        f"- ready: {ready}",
        f"- review: {review}",
        f"- visual_required: {visual}",
        f"- blocked: {blocked}",
        f"- public_json_audit: {audit}",
        "",
        "## Extraction",
        "",
        f"- extractor_strategy_effective: {strategy}",
        f"- marker_used: {metrics.get('marker_used', False)}",
        f"- azure_used: {metrics.get('azure_used', False)}",
        f"- dual_used: {metrics.get('dual_used', False)}",
        f"- fallback_used: {metrics.get('fallback_used', False)}",
        "",
        "## Artifacts",
        "",
        f"- questions_json: {questions_json_path if questions_json_path and questions_json_path.exists() else 'missing'}",
        f"- review_json: {review_json_path if review_json_path and review_json_path.exists() else 'missing'}",
        "",
        "## Next steps",
        "",
        "1. Edit `<stem>.review.json` for non-ready questions.",
        "2. Run `make merge` to produce final JSON.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
