"""Evidence-based answer/solution map schema."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class AnswerSolutionMapEntry(BaseModel):
    question_number: int
    answer_label: str | None = None
    answer_label_raw: str | None = None
    solution_text: str = ""
    source: str = "solution_section"
    confidence: float = 0.0
    line_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class AnswerSolutionMapPackage(BaseModel):
    package_version: str = "1.0"
    source_file_name: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    total_mapped: int = 0
    answers_detected: int = 0
    solutions_detected: int = 0
    entries: list[AnswerSolutionMapEntry] = Field(default_factory=list)
    map_usable: bool = True
    answer_solution_map_status: str = "ok"
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
