"""Quarantine bad semantic binding items before repair/export (Part 13I)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    SEMANTIC_BINDING_BAD_ITEMS_JSON_NAME,
    SEMANTIC_BINDING_BAD_ITEMS_MD_NAME,
    SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BINDING_VALIDATION_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingPackage,
    SemanticBoundQuestion,
)
from meritranker_data_ingestion.schemas.semantic_chunk_diagnostics import (
    SemanticChunkDiagnosticPackage,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.semantic_binding_validator import validate_semantic_items
from meritranker_data_ingestion.services.semantic_final_quality import classify_extra_item

RE_NOISE = re.compile(
    r"(?:Free\s+Mock\s+Test|Download\s+PDF|www\.|http://|https://|"
    r"Subscribe\s+Now|Previous\s+Papers?)",
    re.IGNORECASE,
)

BAD_ITEM_CLASS = {
    "non_numeric_question_number",
    "question_number_out_of_range",
    "expected_count_overflow",
    "duplicate_question_number",
    "hallucinated_question_text",
    "answer_key_without_question",
    "noise_or_ad_as_question",
    "empty_question_text",
    "missing_source_anchor",
}


class SemanticBadItemRecord(BaseModel):
    semantic_question_id: str
    question_number: int | None = None
    original_status: str
    new_status: str
    bad_item_classes: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    source_spans: list[dict] = Field(default_factory=list)
    chunk_id: str | None = None
    reason: str = ""
    export_allowed: bool = False


class SemanticBadItemsReport(BaseModel):
    total_items: int = 0
    bad_item_count: int = 0
    quarantined_item_count: int = 0
    items: list[SemanticBadItemRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass
class SemanticBadItemGuardResult:
    package: SemanticBindingPackage
    report: SemanticBadItemsReport
    json_path: Path
    md_path: Path
    quarantined_count: int


class SemanticBadItemGuardError(Exception):
    """Raised when bad item guard cannot proceed."""


PRE_REPAIR_CLASSES = {
    "non_numeric_question_number",
    "question_number_out_of_range",
    "expected_count_overflow",
    "duplicate_question_number",
    "hallucinated_question_text",
    "noise_or_ad_as_question",
    "empty_question_text",
}


def apply_semantic_bad_item_guard(
    package_dir: Path,
    *,
    expected_count: int | None = None,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    pre_repair: bool = True,
) -> SemanticBadItemGuardResult:
    """Classify and quarantine bad semantic items in the raw binding package."""
    resolved = resolve_path(package_dir)
    sem_dir = resolved / SEMANTIC_BINDING_DIR
    binding_path = sem_dir / SEMANTIC_BOUND_QUESTIONS_NAME
    if not binding_path.exists():
        raise SemanticBadItemGuardError(
            f"Missing semantic binding at {binding_path}. Run bind-semantically first.",
        )

    evidence_path = resolved / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    if not evidence_path.exists():
        raise SemanticBadItemGuardError(f"Missing evidence at {evidence_path}.")

    binding = SemanticBindingPackage.model_validate_json(binding_path.read_text(encoding="utf-8"))
    evidence = DocumentEvidencePackage.model_validate_json(
        evidence_path.read_text(encoding="utf-8"),
    )
    chunk_map = _load_chunk_id_map(sem_dir)

    bad_records: list[SemanticBadItemRecord] = []
    overflow_ids = _overflow_item_ids(binding.items, expected_count=expected_count)

    for item in binding.items:
        original_status = item.binding_status.value
        classes = classify_bad_item(
            item,
            expected_count=expected_count,
            overflow_ids=overflow_ids,
            pre_repair=pre_repair,
            answer_mode=answer_mode,
        )
        if pre_repair:
            classes = [c for c in classes if c in PRE_REPAIR_CLASSES]
        if not classes:
            continue

        item.bad_item_classes = sorted(set(classes))
        for cls in classes:
            issue = f"bad_item:{cls}"
            if issue not in item.issues:
                item.issues.append(issue)

        item.quarantine_status = "quarantined"
        item.excluded_from_export = True
        if item.binding_status != SemanticBindingItemStatus.REJECTED:
            item.binding_status = SemanticBindingItemStatus.REJECTED

        reason = _reason_for_classes(classes)
        bad_records.append(
            SemanticBadItemRecord(
                semantic_question_id=item.semantic_question_id,
                question_number=item.question_number,
                original_status=original_status,
                new_status=item.binding_status.value,
                bad_item_classes=item.bad_item_classes,
                issues=list(item.issues),
                source_spans=[s.model_dump() for s in item.source_spans[:3]],
                chunk_id=item.chunk_id or chunk_map.get(item.semantic_question_id),
                reason=reason,
                export_allowed=False,
            ),
        )

    quarantined_ids = {record.semantic_question_id for record in bad_records}

    validation = validate_semantic_items(
        binding.items,
        binding.metadata_candidates,
        evidence,
        answer_mode=answer_mode,
        expected_count=expected_count,
    )
    for item in binding.items:
        if item.semantic_question_id in quarantined_ids:
            item.quarantine_status = "quarantined"
            item.excluded_from_export = True
            item.binding_status = SemanticBindingItemStatus.REJECTED
    binding.validation_summary = validation
    binding_path.write_text(binding.model_dump_json(indent=2), encoding="utf-8")
    (sem_dir / SEMANTIC_BINDING_VALIDATION_NAME).write_text(
        validation.model_dump_json(indent=2),
        encoding="utf-8",
    )

    report = SemanticBadItemsReport(
        total_items=len(binding.items),
        bad_item_count=len(bad_records),
        quarantined_item_count=len(bad_records),
        items=bad_records,
    )
    if expected_count is not None and len(binding.items) != expected_count:
        report.warnings.append(
            f"expected_count_mismatch: expected={expected_count} actual={len(binding.items)}",
        )

    json_path = sem_dir / SEMANTIC_BINDING_BAD_ITEMS_JSON_NAME
    md_path = sem_dir / SEMANTIC_BINDING_BAD_ITEMS_MD_NAME
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(_render_bad_items_md(report), encoding="utf-8")

    return SemanticBadItemGuardResult(
        package=binding,
        report=report,
        json_path=json_path,
        md_path=md_path,
        quarantined_count=len(bad_records),
    )


def classify_bad_item(
    item: SemanticBoundQuestion,
    *,
    expected_count: int | None,
    overflow_ids: set[str] | None = None,
    pre_repair: bool = False,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
) -> list[str]:
    """Return bad-item classes for one semantic item."""
    classes: list[str] = []

    if not item.question_text_raw.strip():
        classes.append("empty_question_text")

    if item.question_number is None:
        classes.append("non_numeric_question_number")

    if expected_count is not None and item.question_number is not None:
        if item.question_number < 1 or item.question_number > expected_count:
            classes.append("question_number_out_of_range")

    extra = classify_extra_item(item, expected_count=expected_count)
    if extra == "non_numeric_question_number" and "non_numeric_question_number" not in classes:
        classes.append("non_numeric_question_number")
    if extra in ("out_of_range_question_number", "extra_question_candidate"):
        if "question_number_out_of_range" not in classes:
            classes.append("question_number_out_of_range")

    if overflow_ids and item.semantic_question_id in overflow_ids:
        classes.append("expected_count_overflow")

    if "duplicate_question_number" in item.issues:
        classes.append("duplicate_question_number")

    if any("hallucinated" in issue for issue in item.issues):
        if answer_mode == SemanticBinderAnswerMode.QUESTION_ONLY:
            if "hallucinated_question_text" in item.issues:
                classes.append("hallucinated_question_text")
        else:
            classes.append("hallucinated_question_text")

    if "noise_in_question_text" in item.issues or (
        item.question_text_raw.strip() and RE_NOISE.search(item.question_text_raw)
    ):
        classes.append("noise_or_ad_as_question")

    if not pre_repair:
        if item.answer.available and (
            not item.question_text_raw.strip()
            or (item.question_number is None and not item.source_spans)
        ):
            classes.append("answer_key_without_question")

        if not item.source_spans and item.question_text_raw.strip():
            classes.append("missing_source_anchor")

    return classes


def _hallucination_answer_only(item: SemanticBoundQuestion) -> bool:
    issues = [i for i in item.issues if "hallucinated" in i]
    if not issues:
        return False
    return not any(
        i == "hallucinated_question_text" or "hallucinated_option_text" in i
        for i in issues
    )


def _overflow_item_ids(
    items: list[SemanticBoundQuestion],
    *,
    expected_count: int | None,
) -> set[str]:
    if expected_count is None or len(items) <= expected_count:
        return set()

    overflow: set[str] = set()
    for item in items:
        if classify_extra_item(item, expected_count=expected_count):
            overflow.add(item.semantic_question_id)
        elif item.question_number is None:
            overflow.add(item.semantic_question_id)

    if len(overflow) < len(items) - expected_count:
        non_numeric = [i for i in items if i.question_number is None]
        for item in non_numeric:
            overflow.add(item.semantic_question_id)

    semantic_overflow = []
    for item in items:
        match = re.match(r"sq_(\d+)$", item.semantic_question_id)
        if match and int(match.group(1)) > (expected_count or 0):
            semantic_overflow.append(item)
    for item in semantic_overflow:
        overflow.add(item.semantic_question_id)

    return overflow


def _load_chunk_id_map(sem_dir: Path) -> dict[str, str]:
    diag_path = sem_dir / SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME
    if not diag_path.exists():
        return {}
    try:
        package = SemanticChunkDiagnosticPackage.model_validate_json(
            diag_path.read_text(encoding="utf-8"),
        )
    except Exception:
        return {}
    mapping: dict[str, str] = {}
    for chunk in package.chunks:
        for sid in chunk.returned_semantic_question_ids:
            mapping[sid] = chunk.chunk_id
    return mapping


def _reason_for_classes(classes: list[str]) -> str:
    return "; ".join(classes)


def _render_bad_items_md(report: SemanticBadItemsReport) -> str:
    lines = [
        "# Semantic Bad Items",
        "",
        f"- total_items: {report.total_items}",
        f"- bad_item_count: {report.bad_item_count}",
        f"- quarantined_item_count: {report.quarantined_item_count}",
        "",
    ]
    for item in report.items:
        lines.extend(
            [
                f"## {item.semantic_question_id} (Q{item.question_number or '?'})",
                f"- chunk_id: {item.chunk_id}",
                f"- original_status: {item.original_status}",
                f"- new_status: {item.new_status}",
                f"- bad_item_classes: {', '.join(item.bad_item_classes)}",
                f"- reason: {item.reason}",
                f"- export_allowed: {item.export_allowed}",
                "",
            ],
        )
    return "\n".join(lines) + "\n"
