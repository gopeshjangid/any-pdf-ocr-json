"""Triage false-positive hallucination blocks using local window evidence (Part 14S)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage, EvidenceLine
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionQualityStatus,
)
from meritranker_data_ingestion.schemas.question_window import QuestionWindow
from meritranker_data_ingestion.services.issue_severity_resolver import (
    is_hallucination_issue,
    is_quarantine_issue,
)
from meritranker_data_ingestion.services.semantic_binding_validator import text_in_evidence

RE_CROSS_WINDOW = re.compile(r"cross_window", re.I)
RE_TOKEN = re.compile(r"[a-z0-9]{3,}", re.I)
MIN_OPTION_TOKEN_LEN = 4
MIN_QUESTION_TOKEN_OVERLAP = 0.55


def triage_hallucination_blocked_item(
    item: FinalQuestionItem,
    *,
    window: QuestionWindow | None,
    evidence: DocumentEvidencePackage | None,
) -> FinalQuestionItem:
    """Downgrade blocked hallucination flags when local window evidence supports content."""
    if not _only_hallucination_block(item):
        return item
    if any(RE_CROSS_WINDOW.search(issue) for issue in item.issues):
        return item
    if not (item.question_text_raw or "").strip():
        return item

    window_lines = _window_lines(window, evidence)
    if not window_lines:
        return item

    line_map = {line.line_id: line.text_raw for line in window_lines}
    line_ids = list(line_map.keys())

    combined_window = " ".join(line_map.values())
    if not text_in_evidence(item.question_text_raw, line_ids, line_map):
        if not _question_token_overlap(item.question_text_raw, combined_window):
            return item
    if not _options_source_backed(item, line_ids, line_map):
        return item

    cleaned = [
        issue
        for issue in item.issues
        if not is_hallucination_issue(issue)
        and not is_quarantine_issue(issue)
        and not issue.startswith("missing_option_source_spans")
        and not issue.startswith("missing_question_source_spans")
    ]

    return item.model_copy(
        update={
            "issues": _dedupe(cleaned),
            "quality_status": FinalQuestionQualityStatus.REVIEW_REQUIRED,
            "final_gate_status": None,
        },
    )


def _only_hallucination_block(item: FinalQuestionItem) -> bool:
    if "question_missing_from_extraction" in item.issues:
        return False
    gate = (item.final_gate_status or "").lower()
    stale_gate = any(
        marker in gate
        for marker in ("block", "quarantine", "reject", "bad_item", "hallucinat")
    )
    has_hallucination = any(is_hallucination_issue(i) for i in item.issues)
    has_quarantine = any(is_quarantine_issue(i) for i in item.issues)
    if has_hallucination or has_quarantine:
        return True
    if item.quality_status == FinalQuestionQualityStatus.BLOCKED and stale_gate:
        return True
    return False


def _options_source_backed(
    item: FinalQuestionItem,
    line_ids: list[str],
    line_map: dict[str, str],
) -> bool:
    options = [opt for opt in item.options if (opt.text_raw or "").strip()]
    if not options:
        return True
    for opt in options:
        text = (opt.text_raw or "").strip()
        if len(text) < MIN_OPTION_TOKEN_LEN:
            if not text_in_evidence(text, line_ids, line_map):
                return False
            continue
        if not text_in_evidence(text, line_ids, line_map):
            tokens = [tok for tok in re.split(r"\s+", text) if len(tok) >= MIN_OPTION_TOKEN_LEN]
            if not tokens:
                return False
            if not any(text_in_evidence(tok, line_ids, line_map) for tok in tokens[:3]):
                return False
    return True


def _window_lines(
    window: QuestionWindow | None,
    evidence: DocumentEvidencePackage | None,
) -> list[EvidenceLine]:
    if window is None or evidence is None:
        return []
    line_by_id = {line.line_id: line for line in evidence.lines}
    return [line_by_id[lid] for lid in window.line_ids if lid in line_by_id]


def _question_token_overlap(question_text: str, window_text: str) -> bool:
    tokens = RE_TOKEN.findall(question_text or "")
    if len(tokens) < 3:
        return False
    haystack = (window_text or "").lower()
    hits = sum(1 for token in tokens if token.lower() in haystack)
    return (hits / len(tokens)) >= MIN_QUESTION_TOKEN_OVERLAP


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
