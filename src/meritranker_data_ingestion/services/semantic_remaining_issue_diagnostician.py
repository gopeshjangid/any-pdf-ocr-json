"""Diagnose remaining semantic binding issues after repair (Part 13G)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME,
    SEMANTIC_BINDING_REMAINING_ISSUES_MD_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBindingItemStatus,
    SemanticBindingPackage,
    SemanticBoundQuestion,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.semantic_key_normalizer import (
    canonical_option_keys,
    normalize_answer_key,
)


class FailureClass(str, Enum):
    TABLE_ROW_OPTION_EMBEDDING = "table_row_option_embedding"
    QUESTION_AND_OPTIONS_SAME_LINE = "question_and_options_same_line"
    OPTIONS_IN_PREVIOUS_OR_NEXT_LINE = "options_in_previous_or_next_line"
    MISSING_ANCHOR = "missing_anchor"
    ANSWER_KEY_OPTION_MISMATCH = "answer_key_option_mismatch"
    UNRESOLVED_SOURCE_SPAN = "unresolved_source_span"
    GENUINELY_INCOMPLETE_SOURCE = "genuinely_incomplete_source"
    VISUAL_ONLY_OPTIONS = "visual_only_options"
    NOISE_IN_QUESTION = "noise_in_question_text"


class Repairability(str, Enum):
    REPAIRABLE = "repairable"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


class NearbyEvidenceLine(BaseModel):
    line_id: str
    text_preview: str


class SemanticRemainingIssue(BaseModel):
    semantic_question_id: str
    question_number: int | None = None
    current_status: str
    failure_class: str
    repairability: str
    answer_key: str | None = None
    option_keys: list[str] = Field(default_factory=list)
    missing_option_keys: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    recommended_action: str
    patch_id: str
    bad_item_classes: list[str] = Field(default_factory=list)
    chunk_id: str | None = None
    source_span_missing_fields: list[str] = Field(default_factory=list)
    nearby_evidence_lines: list[NearbyEvidenceLine] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "failure_class" not in data and "suspected_failure_class" in data:
            data["failure_class"] = data.pop("suspected_failure_class")
        if "recommended_action" not in data and "recommended_deterministic_action" in data:
            data["recommended_action"] = data.pop("recommended_deterministic_action")
        if "option_keys" not in data and "option_keys_found" in data:
            data["option_keys"] = data.pop("option_keys_found")
        if "patch_id" not in data:
            qnum = data.get("question_number")
            sid = data.get("semantic_question_id", "unknown")
            data["patch_id"] = f"q_{qnum:04d}" if qnum is not None else sid
        return data


class SemanticRemainingIssuesReport(BaseModel):
    total_items: int = 0
    non_accepted_count: int = 0
    items: list[SemanticRemainingIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_items_key(cls, data: object) -> object:
        if isinstance(data, dict) and "issues" in data and "items" not in data:
            data["items"] = data.pop("issues")
        return data


@dataclass
class SemanticRemainingIssuesResult:
    report: SemanticRemainingIssuesReport
    json_path: Path
    md_path: Path


class SemanticRemainingIssuesError(Exception):
    """Raised when diagnosis cannot proceed."""


def diagnose_semantic_remaining_issues(
    package_dir: Path,
    *,
    use_repaired: bool = True,
    context_radius: int = 5,
) -> SemanticRemainingIssuesResult:
    """Diagnose non-accepted semantic items with nearby evidence context."""
    resolved = resolve_path(package_dir)
    out_dir = resolved / SEMANTIC_BINDING_DIR
    package_name = (
        SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME if use_repaired else SEMANTIC_BOUND_QUESTIONS_NAME
    )
    package_path = out_dir / package_name
    if not package_path.exists():
        raise SemanticRemainingIssuesError(
            f"Missing semantic package at {package_path}. "
            f"Run {'repair-semantic-binding' if use_repaired else 'bind-semantically'} first.",
        )

    evidence_path = resolved / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    if not evidence_path.exists():
        raise SemanticRemainingIssuesError(f"Missing evidence at {evidence_path}.")

    package = SemanticBindingPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    evidence = DocumentEvidencePackage.model_validate_json(
        evidence_path.read_text(encoding="utf-8"),
    )
    line_index = {line.line_id: idx for idx, line in enumerate(evidence.lines)}

    diagnosed: list[SemanticRemainingIssue] = []
    for item in package.items:
        if item.binding_status == SemanticBindingItemStatus.ACCEPTED:
            continue
        diagnosed.append(_diagnose_item(item, evidence.lines, line_index, context_radius))

    report = SemanticRemainingIssuesReport(
        total_items=len(package.items),
        non_accepted_count=len(diagnosed),
        items=diagnosed,
    )

    json_path = out_dir / SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME
    md_path = out_dir / SEMANTIC_BINDING_REMAINING_ISSUES_MD_NAME
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(_render_md(report), encoding="utf-8")

    return SemanticRemainingIssuesResult(report=report, json_path=json_path, md_path=md_path)


def _diagnose_item(
    item: SemanticBoundQuestion,
    lines: list,
    line_index: dict[str, int],
    context_radius: int,
) -> SemanticRemainingIssue:
    option_keys = sorted(canonical_option_keys(item))
    answer_key, _ = normalize_answer_key(item.answer.key or item.answer.key_raw)
    missing_keys = [key for key in ("A", "B", "C", "D") if key not in option_keys]

    span_missing = _missing_span_fields(item)
    failure_class, action, repairability = _classify_failure(item, option_keys, answer_key, missing_keys)

    anchor_idx = _anchor_line_index(item, lines, line_index)
    nearby: list[NearbyEvidenceLine] = []
    if anchor_idx is not None:
        start = max(0, anchor_idx - context_radius)
        end = min(len(lines), anchor_idx + context_radius + 1)
        for line in lines[start:end]:
            preview = line.text_raw.strip().replace("\n", " ")
            if len(preview) > 120:
                preview = preview[:117] + "..."
            nearby.append(NearbyEvidenceLine(line_id=line.line_id, text_preview=preview))

    patch_id = (
        f"q_{item.question_number:04d}"
        if item.question_number is not None
        else item.semantic_question_id
    )
    return SemanticRemainingIssue(
        semantic_question_id=item.semantic_question_id,
        question_number=item.question_number,
        current_status=item.binding_status.value,
        failure_class=failure_class.value,
        repairability=repairability.value,
        answer_key=answer_key,
        option_keys=option_keys,
        missing_option_keys=missing_keys,
        issues=list(item.issues),
        recommended_action=action,
        patch_id=patch_id,
        bad_item_classes=list(item.bad_item_classes),
        chunk_id=item.chunk_id,
        source_span_missing_fields=span_missing,
        nearby_evidence_lines=nearby,
    )


def _missing_span_fields(item: SemanticBoundQuestion) -> list[str]:
    missing: list[str] = []
    if not item.source_spans:
        missing.append("question")
    for option in item.options:
        if (option.text_raw.strip() or (option.key or option.key_raw)) and not option.source_spans:
            missing.append(f"option:{option.key or option.key_raw or '?'}")
    if item.answer.available and not item.answer.source_spans:
        missing.append("answer")
    if item.solution.available and not item.solution.source_spans:
        missing.append("solution")
    return missing


def _classify_failure(
    item: SemanticBoundQuestion,
    option_keys: list[str],
    answer_key: str | None,
    missing_keys: list[str],
) -> tuple[FailureClass, str, Repairability]:
    issue_text = " ".join(item.issues)

    if "noise_in_question_text" in item.issues:
        return (
            FailureClass.NOISE_IN_QUESTION,
            "Manual review — question text contains advertisement/noise; strip only if evidence supports.",
            Repairability.NEEDS_REVIEW,
        )
    if "options_and_following_questions_mixed_in_single_line" in item.issues:
        return (
            FailureClass.QUESTION_AND_OPTIONS_SAME_LINE,
            "Split options from mega-line using embedded label parser within question block.",
            Repairability.REPAIRABLE,
        )
    if "question_and_options_embedded_in_previous_line" in item.issues:
        return (
            FailureClass.OPTIONS_IN_PREVIOUS_OR_NEXT_LINE,
            "Search neighbouring evidence lines for explicit option labels before next anchor.",
            Repairability.REPAIRABLE,
        )
    if not item.source_spans and item.question_number is not None:
        return (
            FailureClass.MISSING_ANCHOR,
            "Locate question anchor by number or text snippet in evidence lines.",
            Repairability.REPAIRABLE,
        )
    if "missing_options" in item.issues or not option_keys:
        q_lower = (item.question_text_raw or "").lower()
        if "figure" in q_lower or "image" in q_lower or "mirror" in q_lower:
            return (
                FailureClass.VISUAL_ONLY_OPTIONS,
                "Keep review — options appear image-only in source evidence.",
                Repairability.BLOCKED,
            )
        return (
            FailureClass.GENUINELY_INCOMPLETE_SOURCE,
            "Keep review/rejected — no explicit A–D labels found in evidence window.",
            Repairability.BLOCKED,
        )
    if answer_key and answer_key not in option_keys:
        return (
            FailureClass.ANSWER_KEY_OPTION_MISMATCH,
            "Repair missing option labels from evidence; re-normalize keys and re-validate.",
            Repairability.REPAIRABLE if missing_keys else Repairability.NEEDS_REVIEW,
        )
    if any("missing_" in issue and "source_spans" in issue for issue in item.issues):
        if "|" in item.question_text_raw or any("|" in o.text_raw for o in item.options):
            return (
                FailureClass.TABLE_ROW_OPTION_EMBEDDING,
                "Parse pipe/table row options with homoglyph normalization.",
                Repairability.REPAIRABLE,
            )
        return (
            FailureClass.UNRESOLVED_SOURCE_SPAN,
            "Attach source spans when evidence line contains matching text.",
            Repairability.REPAIRABLE,
        )
    return (
        FailureClass.GENUINELY_INCOMPLETE_SOURCE,
        "Manual review — source evidence may be incomplete for this item.",
        Repairability.NEEDS_REVIEW,
    )


def _anchor_line_index(
    item: SemanticBoundQuestion,
    lines: list,
    line_index: dict[str, int],
) -> int | None:
    for span in item.source_spans:
        if span.line_id and span.line_id in line_index:
            return line_index[span.line_id]
    if item.question_number is None:
        return None
    qn = item.question_number
    for idx, line in enumerate(lines):
        prefix = line.text_raw[:50]
        if f"**{qn}.**" in prefix or f"{qn}." in prefix or f"| {qn}." in prefix:
            return idx
    return None


def _render_md(report: SemanticRemainingIssuesReport) -> str:
    lines = [
        "# Semantic Remaining Issues",
        "",
        f"- total_items: {report.total_items}",
        f"- non_accepted_count: {report.non_accepted_count}",
        "",
    ]
    for issue in report.items:
        lines.extend(
            [
                f"## Q{issue.question_number or '?'} ({issue.semantic_question_id})",
                f"- patch_id: {issue.patch_id}",
                f"- status: {issue.current_status}",
                f"- failure_class: {issue.failure_class}",
                f"- repairability: {issue.repairability}",
                f"- answer_key: {issue.answer_key}",
                f"- option_keys: {', '.join(issue.option_keys) or '(none)'}",
                f"- missing_option_keys: {', '.join(issue.missing_option_keys) or '(none)'}",
                f"- recommended_action: {issue.recommended_action}",
                f"- issues: {', '.join(issue.issues)}",
                "",
            ],
        )
    return "\n".join(lines) + "\n"
