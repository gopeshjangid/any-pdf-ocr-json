"""Local question window schema (Part 14C)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class QuestionWindowStatus(str, Enum):
    READY = "ready"
    INCOMPLETE_OPTIONS = "incomplete_options"
    NO_ANCHOR = "no_anchor"
    REVIEW_REQUIRED = "review_required"


class QuestionWindow(BaseModel):
    window_id: str
    source_question_number_raw: str | None = None
    parsed_question_number: int | None = None
    global_order: int
    start_line_id: str | None = None
    end_line_id: str | None = None
    line_ids: list[str] = Field(default_factory=list)
    question_anchor_line_ids: list[str] = Field(default_factory=list)
    option_candidate_line_ids: list[str] = Field(default_factory=list)
    chosen_option_line_ids: list[str] = Field(default_factory=list)
    status_line_ids: list[str] = Field(default_factory=list)
    answer_candidate_line_ids: list[str] = Field(default_factory=list)
    solution_candidate_line_ids: list[str] = Field(default_factory=list)
    visual_asset_refs: list[str] = Field(default_factory=list)
    source_engines: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    issues: list[str] = Field(default_factory=list)
    status: QuestionWindowStatus = QuestionWindowStatus.READY


class QuestionWindowsPackage(BaseModel):
    package_version: str = "1.0"
    source_file_name: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    total_windows: int = 0
    windows_with_4_options: int = 0
    windows_with_chosen_option: int = 0
    repeated_question_numbers: list[int] = Field(default_factory=list)
    unsupported_layout_detected: bool = False
    line_reuse_warnings: list[str] = Field(default_factory=list)
    solution_section_detected: bool = False
    question_solution_section_mixed: bool = False
    question_window_build_status: str = "ok"
    section_split_status: str = "ok"
    section_split_fallback_used: bool = False
    windows: list[QuestionWindow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
