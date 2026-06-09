"""Public final questions JSON contract (Part 14O/14P)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PublicFileMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_name: str = Field(serialization_alias="sourceName")
    source_type: str = Field(default="pdf_extraction", serialization_alias="sourceType")
    exam: str | None = None
    year: int | None = None
    set: str | None = None
    shift: str | None = None
    language: str = "en"
    created_by: str = Field(default="ai_extraction", serialization_alias="createdBy")
    notes: str = ""


class PublicQuestionOption(BaseModel):
    label: str
    text: str


class PublicCorrectAnswer(BaseModel):
    label: str | None = None
    text: str | None = None


class PublicVisualSyntax(BaseModel):
    format: str = "merit_visual_v1"
    kind: str = "geometry"
    canvas: dict = Field(default_factory=lambda: {"width": 800, "height": 500})
    objects: list = Field(default_factory=list)
    constraints: list = Field(default_factory=list)


class PublicQuestionVisual(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    visual_id: str = Field(serialization_alias="visualId")
    type: str = "geometry"
    role: str = "question"
    linked_option_label: str | None = Field(default=None, serialization_alias="linkedOptionLabel")
    description: str = ""
    syntax: PublicVisualSyntax | None = None
    issues: list[str] = Field(default_factory=list)


class PublicQuestionMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    exams: list[str] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    section: str | None = None
    source_paper: str | None = Field(default=None, serialization_alias="sourcePaper")
    question_number: int | None = Field(default=None, serialization_alias="questionNumber")
    status: str = "review"
    review_issues: list[str] = Field(default_factory=list, serialization_alias="reviewIssues")


class PublicQuestionItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    external_id: str = Field(serialization_alias="externalId")
    question_text: str | None = Field(serialization_alias="questionText")
    question_type: str = Field(default="single_choice", serialization_alias="questionType")
    options: list[PublicQuestionOption] = Field(default_factory=list)
    correct_answer: PublicCorrectAnswer = Field(
        default_factory=PublicCorrectAnswer,
        serialization_alias="correctAnswer",
    )
    solution_text: str | None = Field(default=None, serialization_alias="solutionText")
    solution_source: str = Field(default="unavailable", serialization_alias="solutionSource")
    visuals: list[PublicQuestionVisual] = Field(default_factory=list)
    metadata: PublicQuestionMetadata = Field(default_factory=PublicQuestionMetadata)


class FinalQuestionsPublicPackage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_meta: PublicFileMeta = Field(serialization_alias="fileMeta")
    questions: list[PublicQuestionItem] = Field(default_factory=list)
