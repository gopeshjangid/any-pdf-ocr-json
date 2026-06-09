"""Extraction capability profiling from merged evidence (Part 14A)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    EXTRACTION_CAPABILITY_PROFILE_NAME,
    MERGED_DOCUMENT_EVIDENCE_JSON_NAME,
    OCR_DIR,
    OCR_EVIDENCE_JSON_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage
from meritranker_data_ingestion.schemas.final_questions_export import ExtractionProfileSummary
from meritranker_data_ingestion.schemas.semantic_binding import SemanticBinderAnswerMode
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.schemas.ocr_evidence import OcrEvidencePackage
from meritranker_data_ingestion.services.ocr_role_hints import (
    detect_language_profile,
    has_chosen_option_metadata,
    is_chosen_option_metadata_line,
    parse_chosen_option_key,
)
from meritranker_data_ingestion.services.ocr_runtime_preflight import (
    OcrPreflightResult,
    resolve_ocr_used,
    run_ocr_runtime_preflight,
)
from meritranker_data_ingestion.services.unsupported_layout_detector import (
    UnsupportedLayoutResult,
    detect_unsupported_layout,
)

RE_ANSWER_KEY_TABLE = re.compile(r"^\d+\s*[\.\)]\s*[A-Da-d]\b", re.MULTILINE)
RE_INLINE_ANS = re.compile(
    r"\bAns(?:wer)?\s*(?:\*{1,2})?\s*[\.\:]?\s*\(?\s*[A-Da-d]\s*\)?",
    re.IGNORECASE,
)
RE_SOLUTION = re.compile(r"\b(?:Solution|Explanation)\s*[\.\:]", re.IGNORECASE)


@dataclass(frozen=True)
class ExtractionCapabilityResult:
    profile: ExtractionProfileSummary
    recommended_answer_mode: SemanticBinderAnswerMode
    profile_path: Path
    raw: dict


def profile_extraction_capability(
    package_dir: Path,
    *,
    ocr_preflight: OcrPreflightResult | None = None,
    ocr_line_count: int | None = None,
    ocr_engines_used: list[str] | None = None,
    ocr_failed: bool = False,
    ocr_failed_reason: str | None = None,
    ocr_fallback_used: bool = False,
    ocr_pages_attempted: int = 0,
    ocr_pages_succeeded: int = 0,
    ocr_pages_failed: int = 0,
    layout_result: UnsupportedLayoutResult | None = None,
) -> ExtractionCapabilityResult:
    """Detect language, answer sources, OCR need from merged or document evidence."""
    resolved = resolve_path(package_dir)
    evidence = _load_evidence(resolved)
    preflight = ocr_preflight or run_ocr_runtime_preflight()
    ocr_stats = _load_ocr_stats(
        resolved,
        ocr_line_count=ocr_line_count,
        ocr_engines_used=ocr_engines_used,
    )
    ocr_used = resolve_ocr_used(
        ocr_line_count=ocr_stats["line_count"],
        ocr_engines_used=ocr_stats["engines_used"],
    )
    text_samples = [line.text_raw for line in evidence.lines[:200]]

    language = detect_language_profile(text_samples)
    chosen_detected = has_chosen_option_metadata(evidence.lines)
    blank_options = sum(
        1
        for line in evidence.lines
        if re.match(r"^(\d+|[A-Da-d])\s*[\.\)]\s*$", line.text_raw.strip())
    )
    option_lines = sum(
        1
        for line in evidence.lines
        if line.role_hints and any(h.value == "option_label_candidate" for h in line.role_hints)
    )
    ocr_recoveries = sum(
        1 for line in evidence.lines if line.alternate_evidence
    )

    answer_source = _detect_answer_source(evidence, chosen_detected)
    layout = layout_result or detect_unsupported_layout(
        evidence,
        answer_source_mode=answer_source,
    )
    effective_engine = (
        ocr_stats["engines_used"][0]
        if ocr_used and ocr_stats["engines_used"]
        else None
    )
    text_availability = "good" if len(evidence.lines) > 20 else "sparse"
    option_availability = (
        "ocr_recoverable"
        if blank_options > 0 and ocr_recoveries > 0
        else "good"
        if option_lines >= 4
        else "sparse"
        if blank_options > 0
        else "unknown"
    )

    recommended = _recommend_answer_mode(answer_source, chosen_detected)
    vlm_required = blank_options > option_lines and not ocr_recoveries

    profile = ExtractionProfileSummary(
        language=language,
        text_availability=text_availability,
        option_availability=option_availability,
        answer_source_mode=answer_source,
        ocr_required=blank_options > 0 or text_availability == "sparse",
        ocr_used=ocr_used,
        ocr_requested_engine=preflight.requested_engine,
        ocr_effective_engine=effective_engine,
        ocr_available=preflight.ocr_available,
        ocr_failed_reason=ocr_failed_reason or preflight.ocr_failed_reason,
        ocr_failed=ocr_failed,
        ocr_fallback_used=ocr_fallback_used,
        ocr_pages_attempted=ocr_pages_attempted,
        ocr_pages_succeeded=ocr_pages_succeeded,
        ocr_pages_failed=ocr_pages_failed,
        unsupported_layout_detected=layout.unsupported_layout_detected,
        recommended_pipeline_action=layout.recommended_pipeline_action,
        vlm_required_later=vlm_required,
        recommended_answer_mode=recommended.value,
        chosen_option_detected=chosen_detected,
    )

    raw = {
        "language": language,
        "text_availability": text_availability,
        "option_availability": option_availability,
        "blank_option_marker_lines": blank_options,
        "option_label_lines": option_lines,
        "ocr_recoveries": ocr_recoveries,
        "answer_source": answer_source,
        "chosen_option_detected": chosen_detected,
        "ocr_required": profile.ocr_required,
        "ocr_used": ocr_used,
        "ocr_requested_engine": preflight.requested_engine,
        "ocr_effective_engine": effective_engine,
        "ocr_available": preflight.ocr_available,
        "ocr_failed_reason": ocr_failed_reason or preflight.ocr_failed_reason,
        "ocr_failed": ocr_failed,
        "ocr_fallback_used": ocr_fallback_used,
        "ocr_pages_attempted": ocr_pages_attempted,
        "ocr_pages_succeeded": ocr_pages_succeeded,
        "ocr_pages_failed": ocr_pages_failed,
        "ocr_line_count": ocr_stats["line_count"],
        "ocr_engines_used": ocr_stats["engines_used"],
        "unsupported_layout_detected": layout.unsupported_layout_detected,
        "unsupported_reason": layout.unsupported_reason,
        "recommended_pipeline_action": layout.recommended_pipeline_action,
        "recommended_next_pipeline": layout.recommended_next_pipeline,
        "repeated_question_numbers": layout.repeated_question_numbers,
        "vlm_required_later": vlm_required,
        "recommended_answer_mode": recommended.value,
        "chosen_option_samples": _chosen_option_samples(evidence),
    }

    profile_path = resolved / EVIDENCE_DIR / EXTRACTION_CAPABILITY_PROFILE_NAME
    profile_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    return ExtractionCapabilityResult(
        profile=profile,
        recommended_answer_mode=recommended,
        profile_path=profile_path,
        raw=raw,
    )


def resolve_effective_answer_mode(
    requested: SemanticBinderAnswerMode,
    profile: ExtractionCapabilityResult | None,
) -> SemanticBinderAnswerMode:
    if requested != SemanticBinderAnswerMode.AUTO:
        return requested
    if profile is not None:
        return profile.recommended_answer_mode
    return SemanticBinderAnswerMode.ANSWER_KEY_ONLY


def _load_evidence(package_dir: Path) -> DocumentEvidencePackage:
    merged = package_dir / EVIDENCE_DIR / MERGED_DOCUMENT_EVIDENCE_JSON_NAME
    doc = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    path = merged if merged.exists() else doc
    return DocumentEvidencePackage.model_validate_json(path.read_text(encoding="utf-8"))


def _detect_answer_source(
    evidence: DocumentEvidencePackage,
    chosen_detected: bool,
) -> str:
    full_text = "\n".join(line.text_raw for line in evidence.lines)
    if RE_ANSWER_KEY_TABLE.search(full_text):
        return "answer_key_table"
    if RE_INLINE_ANS.search(full_text):
        return "inline_answer"
    if RE_SOLUTION.search(full_text):
        return "separate_solution_section"
    if chosen_detected:
        return "chosen_option_metadata_only"
    return "unavailable"


def _recommend_answer_mode(
    answer_source: str,
    chosen_detected: bool,
) -> SemanticBinderAnswerMode:
    if answer_source == "answer_key_table":
        return SemanticBinderAnswerMode.ANSWER_KEY_ONLY
    if answer_source == "inline_answer":
        return SemanticBinderAnswerMode.REQUIRED
    if chosen_detected:
        return SemanticBinderAnswerMode.QUESTION_ONLY
    if answer_source == "separate_solution_section":
        return SemanticBinderAnswerMode.OPTIONAL
    return SemanticBinderAnswerMode.ANSWER_KEY_ONLY


def _load_ocr_stats(
    package_dir: Path,
    *,
    ocr_line_count: int | None,
    ocr_engines_used: list[str] | None,
) -> dict:
    if ocr_line_count is not None and ocr_engines_used is not None:
        return {
            "line_count": ocr_line_count,
            "engines_used": ocr_engines_used,
        }
    ocr_path = package_dir / OCR_DIR / OCR_EVIDENCE_JSON_NAME
    if not ocr_path.exists():
        return {"line_count": 0, "engines_used": []}
    package = OcrEvidencePackage.model_validate_json(ocr_path.read_text(encoding="utf-8"))
    return {
        "line_count": len(package.lines),
        "engines_used": list(package.ocr_engines_used),
    }


def _chosen_option_samples(evidence: DocumentEvidencePackage) -> list[dict]:
    samples: list[dict] = []
    for line in evidence.lines:
        if not is_chosen_option_metadata_line(line.text_raw):
            continue
        samples.append(
            {
                "line_id": line.line_id,
                "text_preview": line.text_raw[:120],
                "parsed_chosen_key": parse_chosen_option_key(line.text_raw),
            },
        )
        if len(samples) >= 10:
            break
    return samples
