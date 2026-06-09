"""Serialize internal final export to public Part 14O/14P JSON contract."""

from __future__ import annotations

import json
from pathlib import Path

from meritranker_data_ingestion.schemas.final_questions_export import (
    ExtractionProfileSummary,
    FinalQuestionItem,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.schemas.final_questions_public import (
    FinalQuestionsPublicPackage,
    PublicCorrectAnswer,
    PublicFileMeta,
    PublicQuestionItem,
    PublicQuestionMetadata,
    PublicQuestionOption,
)
from meritranker_data_ingestion.services.deterministic_option_parser import count_usable_options
from meritranker_data_ingestion.services.public_visual_serializer import (
    serialize_public_visual,
    visual_has_syntax,
)
from meritranker_data_ingestion.services.review_issue_normalizer import normalize_review_issues

ALLOWED_PUBLIC_STATUSES = frozenset({"ready", "review", "visual_required", "blocked"})
ALLOWED_SOLUTION_SOURCES = frozenset({"pdf", "manual", "unavailable", "unknown"})


def serialize_public_package(
    package: FinalQuestionsPackage,
    *,
    extraction_profile: ExtractionProfileSummary | None = None,
) -> dict:
    """Return camelCase public JSON dict with only fileMeta and questions."""
    profile = extraction_profile or package.extraction_profile_summary
    public = FinalQuestionsPublicPackage(
        file_meta=_build_file_meta(package.source_file_name, profile),
        questions=[serialize_public_question(item, source_name=package.source_file_name) for item in package.items],
    )
    return public.model_dump(mode="json", by_alias=True, exclude_none=False)


def serialize_public_question(
    item: FinalQuestionItem,
    *,
    source_name: str | None = None,
) -> PublicQuestionItem:
    qnum = item.question_number
    external_id = f"Q{qnum:03d}" if qnum is not None else item.final_question_id
    options = [
        PublicQuestionOption(
            label=(opt.canonical_key or opt.key or "").upper()[:1] or "?",
            text=opt.text_raw or "",
        )
        for opt in item.options
        if (opt.text_raw or "").strip() or opt.canonical_key
    ]
    answer_label = None
    answer_text = None
    if item.correct_answer_key:
        answer_label = (item.correct_answer_key or "").upper()[:1] or item.correct_answer_key
    if item.correct_answer_text:
        answer_text = item.correct_answer_text

    review_issues = normalize_review_issues(
        item.issues + item.metadata.review_issues,
        question_text=item.question_text_raw or "",
    )
    status = _normalize_status(item.metadata.status)

    if item.chosen_option_canonical_key and answer_label == item.chosen_option_canonical_key:
        answer_label = None
        answer_text = None
        status = "review"
        if "chosen_option_not_correct_answer_source" not in review_issues:
            review_issues.append("chosen_option_not_correct_answer_source")

    visuals = [serialize_public_visual(v) for v in item.visuals]
    if visuals and any(v.syntax is None for v in visuals):
        if status == "ready":
            status = "visual_required"
        if "visual_syntax_missing" not in review_issues:
            review_issues.append("visual_syntax_missing")
    elif item.visuals and not any(visual_has_syntax(v) for v in item.visuals):
        if _item_needs_visual(item) and status == "ready":
            status = "visual_required"
            if "visual_syntax_missing" not in review_issues:
                review_issues.append("visual_syntax_missing")

    return PublicQuestionItem(
        external_id=external_id,
        question_text=item.question_text_raw or None,
        question_type="single_choice",
        options=options,
        correct_answer=PublicCorrectAnswer(label=answer_label, text=answer_text),
        solution_text=item.solution_text_raw,
        solution_source=_normalize_solution_source(
            item.solution_source,
            solution_text=item.solution_text_raw,
        ),
        visuals=visuals,
        metadata=PublicQuestionMetadata(
            exams=[],
            years=[],
            section=None,
            source_paper=source_name,
            question_number=qnum,
            status=status,
            review_issues=review_issues,
        ),
    )


def write_public_questions_json(
    package: FinalQuestionsPackage,
    export_path: Path,
    *,
    extraction_profile: ExtractionProfileSummary | None = None,
) -> Path:
    """Write simplified public .questions.json."""
    export_path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_public_package(package, extraction_profile=extraction_profile)
    export_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return export_path


def compute_status_counts_from_package(package: FinalQuestionsPackage) -> dict[str, int]:
    """Derive simplified status counts from internal package items."""
    counts = {
        "ready_count": 0,
        "review_count": 0,
        "visual_required_count": 0,
        "blocked_count": 0,
        "answer_available_count": 0,
        "solution_available_count": 0,
        "incomplete_options_count": 0,
        "missing_question_count": 0,
    }
    status_keys = {
        "ready": "ready_count",
        "review": "review_count",
        "visual_required": "visual_required_count",
        "blocked": "blocked_count",
    }
    for item in package.items:
        status = _normalize_status(item.metadata.status)
        key = status_keys.get(status)
        if key:
            counts[key] += 1
        if item.correct_answer_key and item.correct_answer_text:
            counts["answer_available_count"] += 1
        if item.solution_text_raw and item.solution_text_raw.strip():
            counts["solution_available_count"] += 1
        if count_usable_options(item.options) < 4:
            counts["incomplete_options_count"] += 1
        if "question_missing_from_extraction" in item.metadata.review_issues:
            counts["missing_question_count"] += 1
    return counts


def _normalize_status(status: str) -> str:
    if status in ALLOWED_PUBLIC_STATUSES:
        return status
    mapping = {
        "ready_for_pattern_ingestion": "ready",
        "question_bank_ready": "ready",
        "review_required": "review",
        "answer_unavailable": "ready",
        "accepted_safe": "ready",
    }
    return mapping.get(status, "review")


def _normalize_solution_source(value: str | None, *, solution_text: str | None = None) -> str:
    if solution_text and solution_text.strip():
        return "pdf"
    if not value:
        return "unavailable"
    lower = value.lower()
    if lower in ALLOWED_SOLUTION_SOURCES:
        return lower
    if lower in {"separate_solution_section", "inline", "pdf_extraction"}:
        return "pdf"
    return "unavailable"


def _item_needs_visual(item: FinalQuestionItem) -> bool:
    from meritranker_data_ingestion.services.visual_detection import detect_visual_dependency

    needed, _ = detect_visual_dependency(item)
    return needed or bool(item.visuals)


def _build_file_meta(source_name: str, profile: ExtractionProfileSummary) -> PublicFileMeta:
    raw_lang = (profile.language or "en").lower()
    language = "en" if raw_lang in {"en", "english", "eng"} else raw_lang
    return PublicFileMeta(
        source_name=source_name,
        source_type="pdf_extraction",
        exam=None,
        year=None,
        set=None,
        shift=None,
        language=language,
        created_by="ai_extraction",
        notes="",
    )
