"""Deterministic final-question items from question windows (Part 14K)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage, EvidenceLine, SourceSpan
from meritranker_data_ingestion.schemas.evidence_answer_solution_map import AnswerSolutionMapEntry
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalAnswerSource,
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
    FinalQuestionSourceTrace,
)
from meritranker_data_ingestion.schemas.question_window import QuestionWindow, QuestionWindowsPackage
from meritranker_data_ingestion.services.deterministic_option_parser import (
    ParsedOption,
    count_usable_options,
    parse_options_from_window_lines,
)
from meritranker_data_ingestion.services.final_item_acceptance_gate import strip_stale_window_issues
from meritranker_data_ingestion.services.semantic_key_normalizer import normalize_answer_key

RE_Q_ANCHOR_STRIP = re.compile(
    r"^(?:[-•*]\s*)?(?:\*\*)?(?:Q\.?\s*)?\d{1,3}\s*[\.\)]\s*(?:\*\*)?\s*",
    re.IGNORECASE,
)
RE_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
from meritranker_data_ingestion.config import (
    SEMANTIC_EXPECTED_COVERAGE_RATIO,
    SEMANTIC_UNDERBOUND_WINDOW_RATIO,
    WINDOW_EXPECTED_COVERAGE_RATIO,
)


@dataclass(frozen=True)
class WindowFinalBuildResult:
    items: list[FinalQuestionItem]
    answers_mapped_count: int
    solutions_mapped_count: int
    deterministic_window_questions_built: int
    warnings: list[str]


@dataclass(frozen=True)
class WindowExportMergeResult:
    items: list[FinalQuestionItem]
    answers_mapped_count: int
    solutions_mapped_count: int
    deterministic_window_export_used: bool
    deterministic_window_questions_built: int
    semantic_underbound_window_fallback_used: bool
    warnings: list[str]


def build_window_final_questions(
    *,
    windows_pkg: QuestionWindowsPackage,
    evidence: DocumentEvidencePackage,
    answer_by_qnum: dict[int, AnswerSolutionMapEntry],
) -> WindowFinalBuildResult:
    """Build final question items deterministically from local question windows."""
    line_by_id = {line.line_id: line for line in evidence.lines}
    items: list[FinalQuestionItem] = []
    answers_mapped = 0
    solutions_mapped = 0

    for window in windows_pkg.windows:
        item, ans_inc, sol_inc = _build_item_from_window(
            window,
            line_by_id,
            answer_by_qnum.get(window.parsed_question_number or -1),
        )
        answers_mapped += ans_inc
        solutions_mapped += sol_inc
        items.append(item)

    return WindowFinalBuildResult(
        items=items,
        answers_mapped_count=answers_mapped,
        solutions_mapped_count=solutions_mapped,
        deterministic_window_questions_built=len(items),
        warnings=[],
    )


def _semantic_underbound_detected(
    *,
    semantic_count: int,
    question_window_count: int,
    expected_count: int | None,
) -> bool:
    """True when semantic binding under-filled relative to windows or expected count."""
    if question_window_count <= 0 or semantic_count <= 0:
        return False
    if semantic_count < int(question_window_count * SEMANTIC_UNDERBOUND_WINDOW_RATIO):
        return True
    if expected_count and expected_count > 0:
        windows_sufficient = question_window_count >= int(
            expected_count * WINDOW_EXPECTED_COVERAGE_RATIO,
        )
        semantic_low = semantic_count < int(expected_count * SEMANTIC_EXPECTED_COVERAGE_RATIO)
        if windows_sufficient and semantic_low:
            return True
    return False


def merge_semantic_and_window_exports(
    *,
    semantic_items: list[FinalQuestionItem],
    window_result: WindowFinalBuildResult,
    question_window_count: int,
    expected_count: int | None = None,
) -> WindowExportMergeResult:
    """Prefer semantic items; fallback or fill missing from deterministic windows."""
    warnings: list[str] = []
    semantic_count = len(semantic_items)
    window_items = window_result.items

    if semantic_count == 0 and question_window_count > 0 and window_items:
        warnings.append("deterministic_window_export_fallback_used")
        return WindowExportMergeResult(
            items=_reorder_items(window_items),
            answers_mapped_count=window_result.answers_mapped_count,
            solutions_mapped_count=window_result.solutions_mapped_count,
            deterministic_window_export_used=True,
            deterministic_window_questions_built=window_result.deterministic_window_questions_built,
            semantic_underbound_window_fallback_used=False,
            warnings=warnings,
        )

    if (
        semantic_count > 0
        and question_window_count > 0
        and _semantic_underbound_detected(
            semantic_count=semantic_count,
            question_window_count=question_window_count,
            expected_count=expected_count,
        )
    ):
        warnings.append("semantic_underbound_window_fallback_used")
        by_qnum = {
            item.question_number: item
            for item in semantic_items
            if item.question_number is not None
        }
        merged = list(semantic_items)
        for win_item in window_items:
            qnum = win_item.question_number
            if qnum is not None and qnum not in by_qnum:
                merged.append(win_item)
        merged = _reorder_items(merged)
        return WindowExportMergeResult(
            items=merged,
            answers_mapped_count=window_result.answers_mapped_count,
            solutions_mapped_count=window_result.solutions_mapped_count,
            deterministic_window_export_used=False,
            deterministic_window_questions_built=window_result.deterministic_window_questions_built,
            semantic_underbound_window_fallback_used=True,
            warnings=warnings,
        )

    if semantic_count == 0 and not window_items:
        warnings.append("deterministic_window_export_fallback_used")

    return WindowExportMergeResult(
        items=_reorder_items(semantic_items),
        answers_mapped_count=0,
        solutions_mapped_count=0,
        deterministic_window_export_used=False,
        deterministic_window_questions_built=0,
        semantic_underbound_window_fallback_used=False,
        warnings=warnings,
    )


def should_use_window_fallback(
    *,
    semantic_item_count: int,
    question_window_count: int,
    final_item_count: int,
    expected_count: int | None = None,
) -> bool:
    if question_window_count <= 0:
        return False
    if semantic_item_count == 0:
        return True
    if final_item_count == 0:
        return True
    return _semantic_underbound_detected(
        semantic_count=semantic_item_count,
        question_window_count=question_window_count,
        expected_count=expected_count,
    )


def _build_item_from_window(
    window: QuestionWindow,
    line_by_id: dict[str, EvidenceLine],
    map_entry: AnswerSolutionMapEntry | None,
) -> tuple[FinalQuestionItem, int, int]:
    window_lines = [line_by_id[lid] for lid in window.line_ids if lid in line_by_id]
    parse_result = parse_options_from_window_lines(
        window_lines,
        anchor_line_ids=set(window.question_anchor_line_ids),
        option_candidate_line_ids=set(window.option_candidate_line_ids),
    )
    options = [_parsed_option_to_final(opt) for opt in parse_result.options]
    question_text = " ".join(parse_result.question_text_parts).strip()
    if not question_text and window_lines:
        question_text = _fallback_question_text(window, window_lines)
    visual_assets = _collect_visual_assets(window_lines)

    answer_key: str | None = None
    answer_text: str | None = None
    answer_source = FinalAnswerSource.UNAVAILABLE
    solution_text: str | None = None
    solution_source: str | None = None
    solution_line_ids: list[str] = []
    issues: list[str] = strip_stale_window_issues(list(window.issues))
    answers_mapped = 0
    solutions_mapped = 0

    if map_entry and map_entry.answer_label:
        answer_key = map_entry.answer_label
        answer_text = _option_text_for_key(options, answer_key)
        if answer_text:
            answer_source = FinalAnswerSource.SEPARATE_SOLUTION_SECTION
            answers_mapped = 1
        else:
            issues.append("answer_key_not_in_options")
        if map_entry.solution_text.strip():
            solution_text = map_entry.solution_text.strip()
            solution_source = "solution_section"
            solution_line_ids = list(map_entry.line_ids)
            solutions_mapped = 1

    quality = _resolve_window_quality(
        options=options,
        answer_key=answer_key,
        answer_text=answer_text,
        visual_assets=visual_assets,
        issues=issues,
    )

    qnum = window.parsed_question_number
    return (
        FinalQuestionItem(
            final_question_id=f"fq_win_{window.window_id}",
            global_order=window.global_order,
            source_question_number_raw=window.source_question_number_raw,
            question_number=qnum,
            question_text_raw=question_text,
            options=options,
            correct_answer_key=answer_key,
            correct_answer_text=answer_text,
            answer_source=answer_source,
            solution_text_raw=solution_text,
            solution_source=solution_source,
            visual_assets=visual_assets,
            source_trace=FinalQuestionSourceTrace(
                question_line_ids=list(window.question_anchor_line_ids or window.line_ids[:3]),
                solution_line_ids=solution_line_ids,
                provenance=["question_windows", "window_final_builder"]
                + (["answer_solution_map"] if map_entry else []),
            ),
            quality_status=quality,
            final_gate_status="window_deterministic",
            confidence=window.confidence,
            issues=_dedupe(issues),
        ),
        answers_mapped,
        solutions_mapped,
    )


def _parsed_option_to_final(parsed: ParsedOption) -> FinalQuestionOption:
    return _make_option(
        key=parsed.canonical_key.lower(),
        key_raw=parsed.key_raw,
        text_raw=parsed.text_raw,
        canonical_key=parsed.canonical_key,
        line=parsed.source_line,
    )


def _fallback_question_text(
    window: QuestionWindow,
    window_lines: list[EvidenceLine],
) -> str:
    anchor_ids = set(window.question_anchor_line_ids)
    for line in window_lines:
        if line.line_id in anchor_ids or line.line_id == window.start_line_id:
            text = RE_Q_ANCHOR_STRIP.sub("", line.text_raw.strip(), count=1).strip()
            marker = re.search(r"\([A-Da-d]\)", text)
            if marker:
                text = text[: marker.start()].strip()
            if text:
                return text
    return window_lines[0].text_raw.strip() if window_lines else ""


def _collect_visual_assets(window_lines: list[EvidenceLine]) -> list[str]:
    assets: list[str] = []
    for line in window_lines:
        for match in RE_IMAGE.finditer(line.text_raw):
            path = match.group(1).strip()
            if path and path not in assets:
                assets.append(path)
    return assets


def _resolve_window_quality(
    *,
    options: list[FinalQuestionOption],
    answer_key: str | None,
    answer_text: str | None,
    visual_assets: list[str],
    issues: list[str],
) -> FinalQuestionQualityStatus:
    if visual_assets and sum(1 for opt in options if opt.text_raw.strip()) < 2:
        return FinalQuestionQualityStatus.VISUAL_REQUIRED
    usable = count_usable_options(options)
    if usable < 4:
        if "incomplete_options" not in issues:
            issues.append("incomplete_options")
        return FinalQuestionQualityStatus.REVIEW_REQUIRED
    if answer_key and not answer_text:
        if "correct_answer_text_unavailable" not in issues:
            issues.append("correct_answer_text_unavailable")
        return FinalQuestionQualityStatus.REVIEW_REQUIRED
    if answer_key and answer_text:
        return FinalQuestionQualityStatus.ACCEPTED_SAFE
    if answer_key and "answer_key_not_in_options" in issues:
        return FinalQuestionQualityStatus.REVIEW_REQUIRED
    if answer_key:
        return FinalQuestionQualityStatus.REVIEW_REQUIRED
    return FinalQuestionQualityStatus.ANSWER_UNAVAILABLE


def _option_text_for_key(
    options: list[FinalQuestionOption],
    answer_key: str,
) -> str | None:
    for opt in options:
        canon, _ = normalize_answer_key(opt.canonical_key or opt.key or opt.key_raw)
        if canon == answer_key and opt.text_raw.strip():
            return opt.text_raw.strip()
    return None


def _make_option(
    *,
    key: str,
    key_raw: str,
    text_raw: str,
    canonical_key: str | None,
    line: EvidenceLine,
    option_index: int | None = None,
) -> FinalQuestionOption:
    return FinalQuestionOption(
        key=key,
        key_raw=key_raw,
        text_raw=text_raw,
        canonical_key=canonical_key,
        option_index=option_index,
        source_spans=[
            SourceSpan(
                line_id=line.line_id,
                page_number=line.page_number,
                extractor=line.source_extractor,
            ),
        ],
        source_engine=line.source_extractor,
        confidence=0.75,
    )


def _reorder_items(items: list[FinalQuestionItem]) -> list[FinalQuestionItem]:
    ordered = sorted(
        items,
        key=lambda item: (
            item.global_order,
            item.question_number is None,
            item.question_number or 99999,
        ),
    )
    out: list[FinalQuestionItem] = []
    for idx, item in enumerate(ordered, start=1):
        out.append(item.model_copy(update={"global_order": idx}))
    return out


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
