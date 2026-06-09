"""Solution window schema for explanation/answer sections."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SolutionWindow(BaseModel):
    solution_window_id: str
    source_question_number: int
    answer_label_raw: str | None = None
    answer_label: str | None = None
    solution_text_raw: str = ""
    source_pages: list[int] = Field(default_factory=list)
    line_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)


class SolutionWindowsPackage(BaseModel):
    package_version: str = "1.0"
    source_file_name: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    total_windows: int = 0
    solution_section_detected: bool = False
    solution_section_confidence: float = 0.0
    solution_window_detection_status: str = "ok"
    windows: list[SolutionWindow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
