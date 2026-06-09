"""Semantic binding evaluation report builder (Part 13D/13E)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from meritranker_data_ingestion.config import (
    INGESTION_ELIGIBILITY_REPORT_NAME,
    ELIGIBILITY_DIR,
    QUESTION_CANDIDATE_REPORT_NAME,
    QUESTIONS_DIR,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    DeterministicSemanticComparison,
    SemanticBinderAnswerMode,
    SemanticBindingEvaluationReport,
    SemanticBindingPackage,
    SemanticBindingQualityThresholds,
    SemanticBindingValidationReport,
)


def build_semantic_binding_evaluation(
    package: SemanticBindingPackage,
    validation: SemanticBindingValidationReport,
    *,
    expected_count: int | None = None,
    chunk_count: int = 0,
    estimated_prompt_chars: int = 0,
    cache_hit: bool = False,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    thresholds: SemanticBindingQualityThresholds | None = None,
    package_dir: Path | None = None,
) -> SemanticBindingEvaluationReport:
    """Build evaluation metrics without duplicating full source text."""
    items = package.items
    resolved_thresholds = thresholds or SemanticBindingQualityThresholds()

    questions_with_options = sum(
        1 for item in items if _source_backed_option_count(item) > 0
    )
    questions_with_4 = sum(
        1 for item in items if _source_backed_option_count(item) >= 4
    )
    answer_available = sum(1 for item in items if item.answer.available)
    solution_available = sum(1 for item in items if item.solution.available)
    noise_count = sum(1 for item in items if "noise_in_question_text" in item.issues)
    visual_count = sum(len(item.visual_references) for item in items)

    duplicate_numbers = _duplicate_question_numbers(items)
    issue_counter: Counter[str] = Counter()
    for item in items:
        for issue in item.issues:
            issue_counter[issue.split(":")[0]] += 1

    top_issues = [f"{name}:{count}" for name, count in issue_counter.most_common(10)]

    comparison = (
        _load_deterministic_comparison(package_dir, validation, questions_with_4, answer_available)
        if package_dir is not None
        else None
    )

    threshold_violations = _check_threshold_violations(
        semantic_item_count=len(items),
        questions_with_4_options=questions_with_4,
        answer_available=answer_available,
        expected_count=expected_count,
        validation=validation,
        thresholds=resolved_thresholds,
        answer_mode=answer_mode,
    )

    quality_status = _derive_quality_status(threshold_violations)

    from meritranker_data_ingestion.services.semantic_final_acceptance_gate import (
        sanitize_duplicate_warnings,
    )

    warnings: list[str] = sanitize_duplicate_warnings(
        list(package.warnings),
        duplicate_question_numbers=duplicate_numbers,
        duplicate_count=validation.duplicate_question_number_count,
    )
    for violation in threshold_violations:
        warnings.append(f"threshold_violation:{violation}")

    return SemanticBindingEvaluationReport(
        expected_count=expected_count,
        semantic_item_count=len(items),
        accepted_count=validation.accepted_count,
        review_required_count=validation.review_required_count,
        rejected_count=validation.rejected_count,
        questions_with_options_count=questions_with_options,
        questions_with_4_options_count=questions_with_4,
        answer_available_count=answer_available,
        solution_available_count=solution_available,
        answer_key_not_in_options_count=validation.answer_key_not_in_options_count,
        source_span_missing_count=validation.source_span_missing_count,
        hallucination_suspected_count=validation.hallucination_suspected_count,
        duplicate_question_numbers=duplicate_numbers,
        missing_question_numbers=list(validation.missing_question_numbers),
        noise_in_question_text_count=noise_count,
        visual_reference_count=visual_count,
        provider=package.binder_provider,
        model=package.binder_model,
        estimated_chunk_count=chunk_count,
        estimated_prompt_chars=estimated_prompt_chars,
        cache_hit=cache_hit,
        quality_status=quality_status,
        thresholds=resolved_thresholds,
        threshold_violations=threshold_violations,
        deterministic_comparison=comparison,
        top_issues=top_issues,
        warnings=_dedupe(warnings),
        errors=list(package.errors),
    )


def _source_backed_option_count(item) -> int:
    """Count options with non-empty key, text, and source spans."""
    count = 0
    for option in item.options:
        has_key = bool((option.key or option.key_raw or "").strip())
        has_text = bool(option.text_raw.strip())
        has_span = bool(option.source_spans)
        if has_key and has_text and has_span:
            count += 1
    return count


def _load_deterministic_comparison(
    package_dir: Path,
    validation: SemanticBindingValidationReport,
    questions_with_4: int,
    answer_available: int,
) -> DeterministicSemanticComparison:
    q_report = _load_json(package_dir / QUESTIONS_DIR / QUESTION_CANDIDATE_REPORT_NAME)
    elig = _load_json(package_dir / ELIGIBILITY_DIR / INGESTION_ELIGIBILITY_REPORT_NAME)

    det_total = int(q_report.get("total_candidates") or 0) if q_report else None
    det_valid = int(q_report.get("valid_candidates") or 0) if q_report else None
    det_no_opts = int(q_report.get("candidates_with_no_options") or 0) if q_report else None
    det_eligible = int(elig.get("eligible_count") or 0) if elig else None

    options_recovered: bool | None = None
    if det_valid is not None:
        options_recovered = det_valid == 0 and questions_with_4 > 0

    summary_parts: list[str] = []
    if det_total is not None and det_valid is not None:
        summary_parts.append(
            f"deterministic_valid={det_valid}/{det_total}",
        )
    summary_parts.append(f"semantic_items={validation.total_items}")
    summary_parts.append(f"semantic_4_options={questions_with_4}")
    summary_parts.append(f"semantic_answers={answer_available}")
    if options_recovered:
        summary_parts.append("options_recovered_from_deterministic_failure=true")

    return DeterministicSemanticComparison(
        deterministic_total_candidates=det_total,
        deterministic_valid_candidates=det_valid,
        deterministic_no_options_count=det_no_opts,
        deterministic_eligible_count=det_eligible,
        semantic_item_count=validation.total_items,
        semantic_accepted_count=validation.accepted_count,
        semantic_review_required_count=validation.review_required_count,
        semantic_rejected_count=validation.rejected_count,
        semantic_questions_with_4_options=questions_with_4,
        semantic_answer_available_count=answer_available,
        options_recovered_from_deterministic_failure=options_recovered,
        improvement_summary="; ".join(summary_parts),
    )


def _check_threshold_violations(
    *,
    semantic_item_count: int,
    questions_with_4_options: int,
    answer_available: int,
    expected_count: int | None,
    validation: SemanticBindingValidationReport,
    thresholds: SemanticBindingQualityThresholds,
    answer_mode: SemanticBinderAnswerMode,
) -> list[str]:
    violations: list[str] = []

    if validation.hallucination_suspected_count > thresholds.max_hallucination_suspected_count:
        violations.append(
            f"hallucination_suspected:{validation.hallucination_suspected_count}"
            f">{thresholds.max_hallucination_suspected_count}",
        )

    if validation.source_span_missing_count > thresholds.max_source_span_missing_count:
        violations.append(
            f"source_span_missing:{validation.source_span_missing_count}"
            f">{thresholds.max_source_span_missing_count}",
        )

    if expected_count is not None and expected_count > 0:
        min_items = int(expected_count * thresholds.min_semantic_item_ratio)
        if semantic_item_count < min_items:
            violations.append(f"semantic_item_count:{semantic_item_count}<{min_items}")

        min_four = int(expected_count * thresholds.min_questions_with_4_options_ratio)
        if questions_with_4_options < min_four:
            violations.append(f"questions_with_4_options:{questions_with_4_options}<{min_four}")

        if answer_mode == SemanticBinderAnswerMode.ANSWER_KEY_ONLY:
            min_answers = int(expected_count * thresholds.min_answer_available_ratio)
            if answer_available < min_answers:
                violations.append(f"answer_available:{answer_available}<{min_answers}")

        if validation.total_items > 0:
            ratio_bad = (
                validation.answer_key_not_in_options_count
                / validation.total_items
            )
            if ratio_bad > thresholds.max_answer_key_not_in_options_ratio:
                violations.append(
                    f"answer_key_not_in_options_ratio:{ratio_bad:.3f}"
                    f">{thresholds.max_answer_key_not_in_options_ratio}",
                )

    return violations


def _derive_quality_status(violations: list[str]) -> str:
    if not violations:
        return "passed"

    critical_prefixes = ("hallucination_suspected:", "source_span_missing:")
    severe_prefixes = ("semantic_item_count:",)
    if any(v.startswith(critical_prefixes) for v in violations):
        return "failed"
    if any(v.startswith(severe_prefixes) for v in violations):
        ratio_violation = next(
            (v for v in violations if v.startswith("semantic_item_count:")),
            "",
        )
        if ratio_violation:
            try:
                actual = int(ratio_violation.split(":")[1].split("<")[0])
                target = int(ratio_violation.split("<")[1])
                if target > 0 and actual / target < 0.5:
                    return "failed"
            except (ValueError, IndexError):
                pass
        return "warning"
    return "warning"


def render_evaluation_md(report: SemanticBindingEvaluationReport) -> str:
    lines = [
        "# Semantic Binding Evaluation",
        "",
        f"- Quality status: `{report.quality_status}`",
        f"- Provider: `{report.provider}` / `{report.model}`",
        f"- Cache hit: {report.cache_hit}",
        f"- Chunks: {report.estimated_chunk_count}",
        f"- Estimated prompt chars: {report.estimated_prompt_chars}",
        "",
        "## Counts",
        f"- Semantic items: {report.semantic_item_count}",
        f"- Accepted: {report.accepted_count}",
        f"- Review required: {report.review_required_count}",
        f"- Rejected: {report.rejected_count}",
        f"- With options: {report.questions_with_options_count}",
        f"- With 4+ options: {report.questions_with_4_options_count}",
        f"- Answer available: {report.answer_available_count}",
        f"- Solution available: {report.solution_available_count}",
        f"- Hallucination suspected: {report.hallucination_suspected_count}",
        f"- Source span missing: {report.source_span_missing_count}",
        "",
    ]
    if report.expected_count is not None:
        lines.append(f"- Expected count: {report.expected_count}")
        lines.append(f"- Missing numbers: {len(report.missing_question_numbers)}")
        lines.append("")
    if report.threshold_violations:
        lines.append("## Threshold violations")
        lines.extend(f"- {v}" for v in report.threshold_violations)
        lines.append("")
    if report.deterministic_comparison is not None:
        comp = report.deterministic_comparison
        lines.append("## Deterministic vs semantic")
        if comp.deterministic_total_candidates is not None:
            lines.append(
                f"- Deterministic candidates: {comp.deterministic_total_candidates} "
                f"(valid: {comp.deterministic_valid_candidates}, "
                f"no-options: {comp.deterministic_no_options_count})",
            )
        if comp.deterministic_eligible_count is not None:
            lines.append(f"- Deterministic eligible: {comp.deterministic_eligible_count}")
        lines.append(f"- Semantic 4+ options: {comp.semantic_questions_with_4_options}")
        lines.append(f"- Semantic answers: {comp.semantic_answer_available_count}")
        if comp.options_recovered_from_deterministic_failure:
            lines.append("- Options recovered from deterministic failure: **yes**")
        if comp.improvement_summary:
            lines.append(f"- Summary: {comp.improvement_summary}")
        lines.append("")
    if report.top_issues:
        lines.append("## Top issues")
        lines.extend(f"- {issue}" for issue in report.top_issues)
        lines.append("")
    return "\n".join(lines)


def _duplicate_question_numbers(items) -> list[int]:
    seen: dict[int, int] = {}
    for item in items:
        if item.question_number is not None:
            seen[item.question_number] = seen.get(item.question_number, 0) + 1
    return sorted(q for q, count in seen.items() if count > 1)


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
