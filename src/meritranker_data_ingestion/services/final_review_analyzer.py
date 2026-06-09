"""Remaining review analyzer for final questions export (Part 14O)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    FINAL_QUESTIONS_DIR,
    FINAL_REVIEW_ITEMS_JSON_NAME,
    FINAL_REVIEW_ITEMS_MD_NAME,
)
from meritranker_data_ingestion.schemas.final_questions_export import FinalQuestionItem, FinalQuestionsPackage
from meritranker_data_ingestion.services.answer_solution_join_diagnostics import (
    AnswerSolutionJoinDiagnostics,
    AnswerSolutionJoinGap,
)
from meritranker_data_ingestion.services.deterministic_option_parser import count_usable_options
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.visual_detection import detect_visual_dependency


@dataclass(frozen=True)
class FinalReviewAnalyzerResult:
    json_path: Path
    md_path: Path
    items: list[dict]
    total_review_items: int


def build_final_review_analyzer(
    package_dir: Path,
    *,
    package: FinalQuestionsPackage,
    join_diagnostics: AnswerSolutionJoinDiagnostics | None = None,
) -> FinalReviewAnalyzerResult:
    resolved = resolve_path(package_dir)
    gap_by_qnum = {
        gap.question_number: gap
        for gap in (join_diagnostics.answer_solution_join_gap_items if join_diagnostics else [])
    }

    review_items: list[dict] = []
    for item in package.items:
        if item.metadata.status == "ready" and not _needs_suspicion_review(item):
            continue
        review_items.append(_to_review_record(item, gap_by_qnum.get(item.question_number or -1)))

    out_dir = resolved / FINAL_QUESTIONS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / FINAL_REVIEW_ITEMS_JSON_NAME
    md_path = out_dir / FINAL_REVIEW_ITEMS_MD_NAME

    payload = {
        "source_file_name": package.source_file_name,
        "total_questions_detected": package.total_questions_detected,
        "total_review_items": len(review_items),
        "items": review_items,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_render_md(payload), encoding="utf-8")

    return FinalReviewAnalyzerResult(
        json_path=json_path,
        md_path=md_path,
        items=review_items,
        total_review_items=len(review_items),
    )


def _needs_suspicion_review(item: FinalQuestionItem) -> bool:
    return any(
        marker in issue
        for issue in item.issues + item.metadata.review_issues
        for marker in ("hallucinated", "cross_window", "answer_key_not_in_options")
    )


def _to_review_record(
    item: FinalQuestionItem,
    gap: AnswerSolutionJoinGap | None,
) -> dict:
    review_class = _classify_review(item, gap)
    return {
        "externalId": f"Q{item.question_number:03d}" if item.question_number else item.final_question_id,
        "questionNumber": item.question_number,
        "status": item.metadata.status,
        "reviewClass": review_class,
        "reviewIssues": _dedupe(item.issues + item.metadata.review_issues),
        "questionPreview": (item.question_text_raw or "").replace("\n", " ")[:120],
        "optionsCount": count_usable_options(item.options),
        "answerAvailable": bool(item.correct_answer_key and item.correct_answer_text),
        "solutionAvailable": bool(item.solution_text_raw and item.solution_text_raw.strip()),
        "visualsCount": len(item.visuals),
        "recommendedAction": _recommended_action(review_class, item),
        "joinGapReason": gap.reason if gap else None,
    }


def _classify_review(item: FinalQuestionItem, gap: AnswerSolutionJoinGap | None) -> str:
    if item.metadata.status == "blocked":
        return "blocked"
    if gap is not None:
        return "answer_solution_join_gap"
    if item.metadata.status == "visual_required" or "visual_syntax_missing" in item.metadata.review_issues:
        visual_needed, phrases = detect_visual_dependency(item)
        if any("graph" in p or "chart" in p for p in phrases):
            return "table_or_chart_visual"
        return "visual_required"
    if "incomplete_options" in item.metadata.review_issues or count_usable_options(item.options) < 4:
        return "incomplete_options"
    if "expected_answer_missing" in item.metadata.review_issues:
        return "answer_unavailable"
    if any("hallucinated" in i or "cross_window" in i for i in item.issues):
        return "source_text_uncertain"
    if _looks_formula_uncertain(item):
        return "formula_uncertain"
    return "review_required"


def _looks_formula_uncertain(item: FinalQuestionItem) -> bool:
    text = item.question_text_raw or ""
    return ("\\frac" in text or "\\left" in text) and count_usable_options(item.options) < 4


def _recommended_action(review_class: str, item: FinalQuestionItem) -> str:
    actions = {
        "visual_required": "Attach or render required figure/diagram before question-bank use.",
        "table_or_chart_visual": "Convert chart/table visual or preserve source image reference.",
        "incomplete_options": "Recover options from local window evidence or mark for manual review.",
        "answer_unavailable": "Obtain missing answer for solved-PDF question or hold from pattern ingestion.",
        "answer_solution_join_gap": "Inspect answer-solution map join and option completeness.",
        "formula_uncertain": "Verify formula OCR and option text locally.",
        "source_text_uncertain": "Verify source spans against evidence lines.",
        "blocked": "Quarantine item; do not export to ingestion.",
        "review_required": "Manual review in final questions JSON.",
    }
    if item.metadata.status == "ready" and review_class == "answer_unavailable":
        return "Question is ready for question bank; answer optional for PYQ export."
    return actions.get(review_class, actions["review_required"])


def _render_md(payload: dict) -> str:
    lines = [
        "# Final Review Items",
        "",
        f"- source: {payload.get('source_file_name', 'unknown')}",
        f"- total_questions: {payload.get('total_questions_detected', 0)}",
        f"- review_items: {payload.get('total_review_items', 0)}",
        "",
    ]
    for item in payload.get("items", []):
        lines.append(
            f"## {item.get('externalId')} — {item.get('reviewClass')} ({item.get('status')})",
        )
        lines.append(f"- status: {item.get('status')}")
        lines.append(f"- options: {item.get('optionsCount')}")
        lines.append(f"- answerAvailable: {item.get('answerAvailable')}")
        lines.append(f"- solutionAvailable: {item.get('solutionAvailable')}")
        lines.append(f"- reviewIssues: {', '.join(item.get('reviewIssues', [])[:6]) or 'none'}")
        if item.get("joinGapReason"):
            lines.append(f"- joinGapReason: {item.get('joinGapReason')}")
        lines.append(f"- action: {item.get('recommendedAction')}")
        lines.append(f"- preview: {item.get('questionPreview', '')[:100]}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
