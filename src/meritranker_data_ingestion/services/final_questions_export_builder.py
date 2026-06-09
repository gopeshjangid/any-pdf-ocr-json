"""Build unified final-questions.json export (Part 14A)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from meritranker_data_ingestion.config import (
    FINAL_QUESTIONS_DIR,
    FINAL_QUESTIONS_JSON_NAME,
    FINAL_QUESTIONS_PACKAGE_VERSION,
    FINAL_QUESTIONS_REPORT_NAME,
    FINAL_QUESTIONS_SUMMARY_MD_NAME,
    OCR_DIR,
    OCR_EVIDENCE_JSON_NAME,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
)
from meritranker_data_ingestion.schemas.ocr_evidence import OcrEvidencePackage
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage
from meritranker_data_ingestion.schemas.final_questions_export import (
    ExtractionProfileSummary,
    FinalAnswerSource,
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionQualityStatus,
    FinalQuestionSourceTrace,
    FinalQuestionsPackage,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingPackage,
    SemanticBoundOption,
    SemanticBoundQuestion,
)
from meritranker_data_ingestion.services.evidence_resolver import load_document_evidence
from meritranker_data_ingestion.services.extraction_capability_router import (
    profile_extraction_capability,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.ocr_runtime_preflight import resolve_ocr_used
from meritranker_data_ingestion.services.option_key_normalizer import (
    numeric_to_canonical,
    parse_multiple_numeric_options,
    parse_numeric_option_line,
    split_combined_option_text,
)
from meritranker_data_ingestion.services.ocr_role_hints import (
    parse_chosen_option_canonical_key,
    parse_chosen_option_key,
)
from meritranker_data_ingestion.services.semantic_final_acceptance_gate import (
    FinalGateStatus,
    evaluate_final_acceptance_gate,
)
from meritranker_data_ingestion.services.evidence_answer_solution_mapper import (
    load_evidence_answer_solution_map,
    map_index,
)
from meritranker_data_ingestion.services.expected_count_canonicalizer import (
    write_canonicalization_diagnostics,
)
from meritranker_data_ingestion.services.final_export_enhancer import enhance_final_export_items
from meritranker_data_ingestion.services.final_readiness_resolver import answers_expected_from_profile
from meritranker_data_ingestion.services.final_review_analyzer import build_final_review_analyzer
from meritranker_data_ingestion.services.question_window_builder import load_question_windows
from meritranker_data_ingestion.services.semantic_key_normalizer import normalize_answer_key
from meritranker_data_ingestion.services.window_final_question_builder import (
    build_window_final_questions,
    merge_semantic_and_window_exports,
)
from meritranker_data_ingestion.schemas.evidence_answer_solution_map import AnswerSolutionMapEntry


class FinalQuestionsExportError(Exception):
    """Raised when final questions export cannot proceed."""


@dataclass
class FinalQuestionsExportResult:
    package: FinalQuestionsPackage
    json_path: Path
    report_path: Path
    summary_path: Path


def build_final_questions_export(
    package_dir: Path,
    *,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    expected_count: int | None = None,
) -> FinalQuestionsExportResult:
    """Build final-questions.json including all detected questions with statuses."""
    resolved = resolve_path(package_dir)
    repaired_path = resolved / SEMANTIC_BINDING_DIR / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME
    if not repaired_path.exists():
        raise FinalQuestionsExportError(
            f"Missing repaired semantic binding: {repaired_path}",
        )

    binding = SemanticBindingPackage.model_validate_json(repaired_path.read_text(encoding="utf-8"))
    try:
        evidence, _ = load_document_evidence(resolved)
    except FileNotFoundError:
        evidence = None

    profile_result = profile_extraction_capability(resolved)
    effective_mode = answer_mode
    if answer_mode == SemanticBinderAnswerMode.AUTO:
        effective_mode = SemanticBinderAnswerMode(
            profile_result.recommended_answer_mode.value,
        )
    chosen_by_qnum = _index_chosen_options(evidence) if evidence else {}
    ocr_stats = _load_ocr_stats(resolved)
    answer_map_pkg = load_evidence_answer_solution_map(resolved)
    answer_by_qnum = map_index(answer_map_pkg) if answer_map_pkg else {}

    semantic_items, answers_mapped_count, solutions_mapped_count = _build_semantic_final_items(
        binding=binding,
        evidence=evidence,
        profile=profile_result.profile,
        effective_mode=effective_mode,
        chosen_by_qnum=chosen_by_qnum,
        answer_by_qnum=answer_by_qnum,
    )

    windows_pkg = load_question_windows(resolved)
    window_result = None
    merge_result = None
    export_warnings = list(profile_result.raw.get("warnings", []))

    if windows_pkg and windows_pkg.windows and evidence is not None:
        window_result = build_window_final_questions(
            windows_pkg=windows_pkg,
            evidence=evidence,
            answer_by_qnum=answer_by_qnum,
        )
        merge_result = merge_semantic_and_window_exports(
            semantic_items=semantic_items,
            window_result=window_result,
            question_window_count=windows_pkg.total_windows,
            expected_count=expected_count,
        )
        items = merge_result.items
        export_warnings.extend(merge_result.warnings)
        if merge_result.deterministic_window_export_used:
            answers_mapped_count = merge_result.answers_mapped_count
            solutions_mapped_count = merge_result.solutions_mapped_count
    else:
        items = semantic_items

    answers_expected = answers_expected_from_profile(profile_result.profile.answer_source_mode)
    enhancement = enhance_final_export_items(
        items,
        windows_pkg=windows_pkg,
        evidence=evidence,
        answer_map_pkg=answer_map_pkg,
        expected_count=expected_count,
        source_file_name=binding.source_file_name,
        answers_expected=answers_expected,
    )
    items = enhancement.items
    gate_metrics = enhancement.gate_metrics
    readiness_counts = enhancement.readiness_counts
    join_diag = enhancement.join_diagnostics
    canonicalization = enhancement.canonicalization
    counts = _count_quality_statuses(items)

    numeric_option_questions = sum(
        1 for item in items if any(opt.option_index for opt in item.options)
    )
    question_only_items = sum(
        1
        for item in items
        if item.quality_status == FinalQuestionQualityStatus.ANSWER_UNAVAILABLE
    )
    chosen_detected_count = sum(1 for item in items if item.chosen_option_key)
    chosen_as_correct = sum(
        1
        for item in items
        if item.correct_answer_key
        and item.chosen_option_key
        and item.correct_answer_key == item.chosen_option_canonical_key
    )

    package = FinalQuestionsPackage(
        package_version=FINAL_QUESTIONS_PACKAGE_VERSION,
        source_file_name=binding.source_file_name,
        created_at=datetime.now(timezone.utc),
        extraction_profile_summary=profile_result.profile,
        total_questions_detected=canonicalization.public_question_count,
        accepted_safe_count=counts[FinalQuestionQualityStatus.ACCEPTED_SAFE],
        review_required_count=counts[FinalQuestionQualityStatus.REVIEW_REQUIRED],
        visual_required_count=counts[FinalQuestionQualityStatus.VISUAL_REQUIRED],
        answer_unavailable_count=counts[FinalQuestionQualityStatus.ANSWER_UNAVAILABLE],
        blocked_count=counts[FinalQuestionQualityStatus.BLOCKED],
        ocr_requested_engine=profile_result.profile.ocr_requested_engine,
        ocr_effective_engine=profile_result.profile.ocr_effective_engine,
        ocr_line_count=ocr_stats["line_count"],
        ocr_used=resolve_ocr_used(
            ocr_line_count=ocr_stats["line_count"],
            ocr_engines_used=ocr_stats["engines_used"],
        ),
        numeric_option_questions_count=numeric_option_questions,
        question_only_items_count=question_only_items,
        chosen_option_detected_count=chosen_detected_count,
        chosen_option_as_correct_answer_count=chosen_as_correct,
        ready_count=readiness_counts.ready_count,
        review_count=readiness_counts.review_count,
        review_items_count=readiness_counts.review_items_count,
        answer_available_count=sum(
            1 for item in items if item.correct_answer_key and item.correct_answer_text
        ),
        solution_available_count=sum(
            1 for item in items if item.solution_text_raw and item.solution_text_raw.strip()
        ),
        incomplete_options_count=gate_metrics.questions_with_incomplete_options_count,
        missing_question_count=canonicalization.missing_placeholder_count,
        answer_solution_join_gap_count=join_diag.answer_solution_join_gap_count,
        items=items,
        warnings=export_warnings,
    )

    out_dir = resolved / FINAL_QUESTIONS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if expected_count and expected_count > 0:
        write_canonicalization_diagnostics(
            out_dir,
            extra_candidates=enhancement.extra_candidates,
            duplicate_candidates=enhancement.duplicate_candidates,
            report=canonicalization,
        )
    json_path = out_dir / FINAL_QUESTIONS_JSON_NAME
    report_path = out_dir / FINAL_QUESTIONS_REPORT_NAME
    summary_path = out_dir / FINAL_QUESTIONS_SUMMARY_MD_NAME

    json_path.write_text(
        json.dumps(package.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "total_questions_detected": package.total_questions_detected,
                "accepted_safe_count": package.accepted_safe_count,
                "review_required_count": package.review_required_count,
                "visual_required_count": package.visual_required_count,
                "answer_unavailable_count": package.answer_unavailable_count,
                "blocked_count": package.blocked_count,
                "ocr_requested_engine": package.ocr_requested_engine,
                "ocr_effective_engine": package.ocr_effective_engine,
                "ocr_line_count": package.ocr_line_count,
                "ocr_used": package.ocr_used,
                "numeric_option_questions_count": package.numeric_option_questions_count,
                "question_only_items_count": package.question_only_items_count,
                "chosen_option_detected_count": package.chosen_option_detected_count,
                "chosen_option_as_correct_answer_count": (
                    package.chosen_option_as_correct_answer_count
                ),
                "answers_mapped_from_solution_count": answers_mapped_count,
                "solutions_mapped_count": solutions_mapped_count,
                "answer_solution_map_count": (
                    answer_map_pkg.total_mapped if answer_map_pkg else 0
                ),
                "deterministic_window_export_used": (
                    merge_result.deterministic_window_export_used if merge_result else False
                ),
                "deterministic_window_questions_built": (
                    merge_result.deterministic_window_questions_built if merge_result else 0
                ),
                "semantic_underbound_window_fallback_used": (
                    merge_result.semantic_underbound_window_fallback_used
                    if merge_result
                    else False
                ),
                "semantic_item_count": len(binding.items),
                "question_window_count": windows_pkg.total_windows if windows_pkg else 0,
                "questions_with_4_options_count": gate_metrics.questions_with_4_options_count,
                "questions_with_incomplete_options_count": (
                    gate_metrics.questions_with_incomplete_options_count
                ),
                "accepted_safe_with_incomplete_options_count": (
                    gate_metrics.accepted_safe_with_incomplete_options_count
                ),
                "ready_count": package.ready_count,
                "review_count": package.review_count,
                "review_items_count": package.review_items_count,
                "answer_available_count": package.answer_available_count,
                "solution_available_count": package.solution_available_count,
                "incomplete_options_count": package.incomplete_options_count,
                "missing_question_count": package.missing_question_count,
                "answer_solution_join_gap_count": package.answer_solution_join_gap_count,
                "answer_solution_join_gap_items": [
                    {
                        "question_number": gap.question_number,
                        "answer_label": gap.answer_label,
                        "reason": gap.reason,
                        "issues": gap.issues,
                    }
                    for gap in join_diag.answer_solution_join_gap_items
                ],
                "expected_count": canonicalization.expected_count or expected_count,
                "public_question_count": canonicalization.public_question_count,
                "raw_candidate_count": canonicalization.raw_candidate_count,
                "extra_candidate_count": canonicalization.extra_candidate_count,
                "duplicate_candidate_count": canonicalization.duplicate_candidate_count,
                "missing_question_numbers": canonicalization.missing_question_numbers,
                "duplicate_question_numbers": canonicalization.duplicate_question_numbers,
                "missing_question_placeholders_added": canonicalization.missing_placeholder_count,
                "over_detection": canonicalization.over_detection,
                "extraction_profile": profile_result.raw,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    summary_path.write_text(_render_summary(package), encoding="utf-8")
    build_final_review_analyzer(
        resolved,
        package=package,
        join_diagnostics=join_diag,
    )

    return FinalQuestionsExportResult(
        package=package,
        json_path=json_path,
        report_path=report_path,
        summary_path=summary_path,
    )


def _build_semantic_final_items(
    *,
    binding: SemanticBindingPackage,
    evidence: DocumentEvidencePackage | None,
    profile: ExtractionProfileSummary,
    effective_mode: SemanticBinderAnswerMode,
    chosen_by_qnum: dict,
    answer_by_qnum: dict[int, AnswerSolutionMapEntry],
) -> tuple[list[FinalQuestionItem], int, int]:
    items: list[FinalQuestionItem] = []
    answers_mapped_count = 0
    solutions_mapped_count = 0

    sorted_questions = sorted(
        binding.items,
        key=lambda q: (q.question_number is None, q.question_number or 99999),
    )

    for order, bound in enumerate(sorted_questions, start=1):
        gate = evaluate_final_acceptance_gate(
            bound,
            answer_mode=effective_mode,
            expected_count=None,
        )

        options = _build_options(bound, evidence)
        answer_key, answer_text, answer_source = _resolve_answer_fields(
            bound,
            options,
            profile,
        )
        solution_text = bound.solution.text_raw if bound.solution.available else None
        solution_source = "semantic_binding" if bound.solution.available else None
        solution_line_ids = [
            sp.line_id for sp in bound.solution.source_spans if sp.line_id
        ]
        map_entry: AnswerSolutionMapEntry | None = None
        if bound.question_number is not None:
            map_entry = answer_by_qnum.get(bound.question_number)

        map_issues: list[str] = []
        if map_entry and not answer_key:
            mapped_key, mapped_text, map_issues = _apply_solution_map(
                map_entry,
                options,
            )
            if mapped_key:
                answer_key = mapped_key
                answer_text = mapped_text
                answer_source = FinalAnswerSource.SEPARATE_SOLUTION_SECTION
                answers_mapped_count += 1
            if map_entry.solution_text.strip():
                solution_text = map_entry.solution_text.strip()
                solution_source = "solution_section"
                solution_line_ids = list(map_entry.line_ids)
                solutions_mapped_count += 1

        quality = _resolve_quality_with_solution_map(
            bound,
            gate,
            options,
            answer_key,
            answer_text,
            map_entry is not None,
            effective_mode,
        )

        chosen_key, chosen_canonical, chosen_source = _resolve_chosen_option(
            bound,
            chosen_by_qnum,
            evidence,
        )

        items.append(
            FinalQuestionItem(
                final_question_id=f"fq_{bound.semantic_question_id}",
                global_order=order,
                source_question_number_raw=bound.question_number_raw,
                question_number=bound.question_number,
                question_text_raw=bound.question_text_raw,
                options=options,
                correct_answer_key=answer_key,
                correct_answer_text=answer_text,
                answer_source=answer_source,
                chosen_option_key=chosen_key,
                chosen_option_canonical_key=chosen_canonical,
                chosen_option_source=chosen_source,
                solution_text_raw=solution_text,
                solution_source=solution_source,
                visual_assets=[
                    vis.asset_path for vis in bound.visual_references if vis.asset_path
                ],
                source_trace=FinalQuestionSourceTrace(
                    question_line_ids=[
                        sp.line_id for sp in bound.source_spans if sp.line_id
                    ],
                    answer_line_ids=[
                        sp.line_id for sp in bound.answer.source_spans if sp.line_id
                    ],
                    solution_line_ids=solution_line_ids,
                    provenance=_provenance_for_item(
                        bound,
                        map_entry is not None and bool(answer_key),
                    ),
                ),
                quality_status=quality,
                final_gate_status=gate.status.value,
                confidence=bound.confidence,
                issues=_dedupe(bound.issues + gate.gate_issues + map_issues),
            ),
        )

    return items, answers_mapped_count, solutions_mapped_count


def _count_quality_statuses(
    items: list[FinalQuestionItem],
) -> dict[FinalQuestionQualityStatus, int]:
    counts = {
        FinalQuestionQualityStatus.ACCEPTED_SAFE: 0,
        FinalQuestionQualityStatus.REVIEW_REQUIRED: 0,
        FinalQuestionQualityStatus.VISUAL_REQUIRED: 0,
        FinalQuestionQualityStatus.ANSWER_UNAVAILABLE: 0,
        FinalQuestionQualityStatus.BLOCKED: 0,
    }
    for item in items:
        counts[item.quality_status] += 1
    return counts


def _build_options(
    bound: SemanticBoundQuestion,
    evidence: DocumentEvidencePackage | None,
) -> list[FinalQuestionOption]:
    options: list[FinalQuestionOption] = []
    for opt in bound.options:
        text = opt.text_raw.strip()
        source_engine = "marker"
        confidence = opt.confidence

        if not text and evidence:
            recovered = _recover_option_from_evidence(opt, evidence)
            if recovered:
                text, source_engine, confidence = recovered

        built = _option_from_bound_fields(opt, text, source_engine, confidence)
        if not (opt.key or opt.key_raw or "").strip() and text:
            multi = parse_multiple_numeric_options(text)
            if len(multi) > 1:
                for norm in multi:
                    options.append(
                        FinalQuestionOption(
                            key=norm.key,
                            key_raw=norm.key_raw,
                            text_raw=norm.text_raw,
                            canonical_key=norm.canonical_key,
                            option_index=norm.option_index,
                            visual_asset_refs=list(opt.asset_refs),
                            source_spans=list(opt.source_spans),
                            source_engine=source_engine,
                            confidence=confidence,
                        ),
                    )
                continue

        options.append(built)
    return options


def _option_from_bound_fields(
    opt: SemanticBoundOption,
    text: str,
    source_engine: str,
    confidence: float,
) -> FinalQuestionOption:
    key = (opt.key or opt.key_raw or "").strip().rstrip(".")
    key_raw = opt.key_raw or (f"{key}." if key.isdigit() else key)
    canonical_key = numeric_to_canonical(key) if key.isdigit() else None
    if not canonical_key:
        canon, _ = normalize_answer_key(opt.key or opt.key_raw)
        canonical_key = canon
    option_index = int(key) if key.isdigit() else None
    if not key and text:
        split = split_combined_option_text(text)
        if split:
            key = split.key
            key_raw = split.key_raw
            text = split.text_raw
            canonical_key = split.canonical_key
            option_index = split.option_index

    return FinalQuestionOption(
        key=key,
        key_raw=key_raw,
        text_raw=text,
        canonical_key=canonical_key,
        option_index=option_index,
        visual_asset_refs=list(opt.asset_refs),
        source_spans=list(opt.source_spans),
        source_engine=source_engine,
        confidence=confidence,
    )


def _recover_option_from_evidence(
    opt: SemanticBoundOption,
    evidence: DocumentEvidencePackage,
) -> tuple[str, str, float] | None:
    for span in opt.source_spans:
        if not span.line_id:
            continue
        for line in evidence.lines:
            if line.line_id != span.line_id:
                continue
            for alt in line.alternate_evidence:
                if alt.text_raw.strip():
                    return alt.text_raw.strip(), alt.engine, alt.confidence
    for line in evidence.lines:
        for alt in line.alternate_evidence:
            if alt.text_raw.strip():
                return alt.text_raw.strip(), alt.engine, alt.confidence
    return None


def _resolve_answer_fields(
    bound: SemanticBoundQuestion,
    options: list[FinalQuestionOption],
    profile: ExtractionProfileSummary,
) -> tuple[str | None, str | None, FinalAnswerSource]:
    if profile.chosen_option_detected and not bound.answer.source_spans:
        return None, None, FinalAnswerSource.UNAVAILABLE

    answer_key, _ = normalize_answer_key(bound.answer.key or bound.answer.key_raw)
    if not bound.answer.available or not answer_key:
        return None, None, FinalAnswerSource.UNAVAILABLE

    if not bound.answer.source_spans:
        return None, None, FinalAnswerSource.UNAVAILABLE

    answer_text = None
    for opt in options:
        canon, _ = normalize_answer_key(opt.key or opt.key_raw)
        if canon == answer_key and opt.text_raw.strip():
            answer_text = opt.text_raw.strip()
            break

    source = FinalAnswerSource.UNAVAILABLE
    if profile.answer_source_mode == "answer_key_table":
        source = FinalAnswerSource.ANSWER_KEY_TABLE
    elif profile.answer_source_mode == "inline_answer":
        source = FinalAnswerSource.INLINE_ANSWER
    elif profile.answer_source_mode == "separate_solution_section":
        source = FinalAnswerSource.SEPARATE_SOLUTION_SECTION
    elif bound.answer.source_spans:
        source = FinalAnswerSource.ANSWER_KEY_TABLE

    if not answer_text:
        return answer_key, None, FinalAnswerSource.UNAVAILABLE
    return answer_key, answer_text, source


def _resolve_chosen_option(
    bound: SemanticBoundQuestion,
    chosen_by_qnum: dict[int, tuple[str, str, str | None]],
    evidence: DocumentEvidencePackage | None,
) -> tuple[str | None, str | None, str | None]:
    if bound.question_number is None:
        return None, None, None
    entry = chosen_by_qnum.get(bound.question_number)
    if entry:
        return entry[0], entry[2], entry[1]
    return None, None, None


def _index_chosen_options(
    evidence: DocumentEvidencePackage,
) -> dict[int, tuple[str, str, str | None]]:
    chosen: dict[int, tuple[str, str, str | None]] = {}
    current_qnum: int | None = None
    for line in evidence.lines:
        anchor = re.match(r"^\*?\*?(\d+)\.", line.text_raw.strip())
        if anchor:
            current_qnum = int(anchor.group(1))
        if "chosen option" not in line.text_raw.lower():
            continue
        key = parse_chosen_option_key(line.text_raw)
        if key and current_qnum is not None:
            chosen[current_qnum] = (
                key,
                line.line_id,
                parse_chosen_option_canonical_key(line.text_raw),
            )
    return chosen


def _load_ocr_stats(package_dir: Path) -> dict:
    ocr_path = package_dir / OCR_DIR / OCR_EVIDENCE_JSON_NAME
    if not ocr_path.exists():
        return {"line_count": 0, "engines_used": []}
    package = OcrEvidencePackage.model_validate_json(ocr_path.read_text(encoding="utf-8"))
    return {
        "line_count": len(package.lines),
        "engines_used": list(package.ocr_engines_used),
    }


def _apply_solution_map(
    entry: AnswerSolutionMapEntry,
    options: list[FinalQuestionOption],
) -> tuple[str | None, str | None, list[str]]:
    issues: list[str] = []
    if not entry.answer_label:
        return None, None, issues
    answer_key = entry.answer_label
    answer_text = _option_text_for_key(options, answer_key)
    if not answer_text:
        issues.append("answer_key_not_in_options")
    return answer_key, answer_text, issues


def _option_text_for_key(
    options: list[FinalQuestionOption],
    answer_key: str,
) -> str | None:
    for opt in options:
        canon, _ = normalize_answer_key(
            opt.canonical_key or opt.key or opt.key_raw,
        )
        if canon == answer_key and opt.text_raw.strip():
            return opt.text_raw.strip()
    return None


def _provenance_for_item(
    bound: SemanticBoundQuestion,
    map_used: bool,
) -> list[str]:
    out = ["semantic_binding", "semantic_repair"]
    if map_used:
        out.append("answer_solution_map")
    return out


def _resolve_quality_with_solution_map(
    bound: SemanticBoundQuestion,
    gate,
    options: list[FinalQuestionOption],
    answer_key: str | None,
    answer_text: str | None,
    map_used: bool,
    effective_mode: SemanticBinderAnswerMode,
) -> FinalQuestionQualityStatus:
    base = _map_quality_status(bound, gate.status)
    if not map_used or not answer_key:
        return base
    if base in {
        FinalQuestionQualityStatus.BLOCKED,
        FinalQuestionQualityStatus.VISUAL_REQUIRED,
    }:
        return base
    text_option_count = sum(1 for opt in options if opt.text_raw.strip())
    if text_option_count < 4 or not answer_text:
        if base == FinalQuestionQualityStatus.ANSWER_UNAVAILABLE:
            return FinalQuestionQualityStatus.REVIEW_REQUIRED
        return base
    if gate.status == FinalGateStatus.ACCEPTED_SAFE:
        return FinalQuestionQualityStatus.ACCEPTED_SAFE
    if bound.binding_status == SemanticBindingItemStatus.ACCEPTED and not bound.bad_item_classes:
        corrupt_only_answer = (
            gate.status in {
                FinalGateStatus.ANSWER_UNAVAILABLE_EXPORT,
                FinalGateStatus.REVIEW_EVIDENCE_CORRUPT,
            }
            and "missing_answer_answer_key_only_mode" in gate.gate_issues
        )
        if corrupt_only_answer or base == FinalQuestionQualityStatus.ANSWER_UNAVAILABLE:
            return FinalQuestionQualityStatus.ACCEPTED_SAFE
    if base == FinalQuestionQualityStatus.ANSWER_UNAVAILABLE:
        return FinalQuestionQualityStatus.REVIEW_REQUIRED
    return base


def _map_quality_status(
    bound: SemanticBoundQuestion,
    gate_status: FinalGateStatus,
) -> FinalQuestionQualityStatus:
    if gate_status == FinalGateStatus.ACCEPTED_SAFE:
        return FinalQuestionQualityStatus.ACCEPTED_SAFE
    if gate_status == FinalGateStatus.ANSWER_UNAVAILABLE_EXPORT:
        return FinalQuestionQualityStatus.ANSWER_UNAVAILABLE
    if gate_status == FinalGateStatus.REVIEW_VISUAL_REQUIRED:
        return FinalQuestionQualityStatus.VISUAL_REQUIRED
    if gate_status == FinalGateStatus.BLOCKED_BAD_ITEM:
        return FinalQuestionQualityStatus.BLOCKED
    if not bound.answer.available:
        return FinalQuestionQualityStatus.ANSWER_UNAVAILABLE
    if bound.binding_status == SemanticBindingItemStatus.REVIEW_REQUIRED:
        return FinalQuestionQualityStatus.REVIEW_REQUIRED
    return FinalQuestionQualityStatus.REVIEW_REQUIRED


def _render_summary(package: FinalQuestionsPackage) -> str:
    profile = package.extraction_profile_summary
    return "\n".join(
        [
            "# Final Questions Export Summary",
            "",
            f"- source_file_name: {package.source_file_name}",
            f"- total_questions_detected: {package.total_questions_detected}",
            f"- ready_count: {package.ready_count}",
            f"- review_count: {package.review_count}",
            f"- visual_required_count: {package.visual_required_count}",
            f"- blocked_count: {package.blocked_count}",
            f"- answer_available_count: {package.answer_available_count}",
            f"- solution_available_count: {package.solution_available_count}",
            "",
            "## Extraction profile",
            f"- language: {profile.language}",
            f"- ocr_used: {profile.ocr_used}",
            f"- chosen_option_detected: {profile.chosen_option_detected}",
            f"- recommended_answer_mode: {profile.recommended_answer_mode}",
        ],
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
