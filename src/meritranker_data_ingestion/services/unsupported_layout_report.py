"""Write unsupported layout diagnostics (Part 14C)."""

from __future__ import annotations

import json
from pathlib import Path

from meritranker_data_ingestion.config import (
    DIAGNOSTICS_DIR,
    UNSUPPORTED_LAYOUT_REPORT_JSON_NAME,
    UNSUPPORTED_LAYOUT_REPORT_MD_NAME,
)
from meritranker_data_ingestion.services.unsupported_layout_detector import UnsupportedLayoutResult


def write_unsupported_layout_report(
    package_dir: Path,
    layout: UnsupportedLayoutResult,
    *,
    stopped: bool = False,
) -> tuple[Path, Path]:
    diag = package_dir / DIAGNOSTICS_DIR
    diag.mkdir(parents=True, exist_ok=True)
    json_path = diag / UNSUPPORTED_LAYOUT_REPORT_JSON_NAME
    md_path = diag / UNSUPPORTED_LAYOUT_REPORT_MD_NAME
    payload = {
        "unsupported_layout_detected": layout.unsupported_layout_detected,
        "unsupported_reason": layout.unsupported_reason,
        "recommended_pipeline_action": layout.recommended_pipeline_action,
        "recommended_next_pipeline": layout.recommended_next_pipeline,
        "repeated_question_numbers": layout.repeated_question_numbers,
        "response_sheet_markers_detected": layout.response_sheet_markers_detected,
        "chosen_option_detected": layout.chosen_option_detected,
        "pipeline_stopped": stopped,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Unsupported Layout Report",
                "",
                f"- unsupported_layout_detected: {layout.unsupported_layout_detected}",
                f"- unsupported_reason: {layout.unsupported_reason}",
                f"- recommended_pipeline_action: {layout.recommended_pipeline_action}",
                f"- recommended_next_pipeline: {layout.recommended_next_pipeline}",
                f"- repeated_question_numbers: {layout.repeated_question_numbers}",
                f"- pipeline_stopped: {stopped}",
                "",
            ],
        ),
        encoding="utf-8",
    )
    return json_path, md_path
