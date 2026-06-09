"""Source-faithful final question package builder and validator (Part 6)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    ANSWER_SOLUTION_MAP_NAME,
    ANSWER_SOLUTION_REPORT_NAME,
    FINAL_DIR,
    FINAL_QUESTIONS_NAME,
    FINAL_VALIDATION_REPORT_NAME,
    MAPPINGS_DIR,
    PACKAGE_MANIFEST_NAME,
    QUESTION_CANDIDATE_REPORT_NAME,
    QUESTION_CANDIDATES_NAME,
    QUESTIONS_DIR,
)
from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    MappingStatus,
    QuestionAnswerSolutionMapping,
)
from meritranker_data_ingestion.schemas.extraction import ExtractionPackageManifest
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionAnswer,
    FinalQuestionAsset,
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionPackage,
    FinalQuestionSolution,
    FinalQuestionSourceTrace,
    FinalQuestionValidationReport,
    FinalizeStatus,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.question_candidates import (
    CandidateReviewStatus,
    QuestionCandidate,
    QuestionCandidateParseResult,
)
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)

PACKAGE_VERSION = "1.0.0"


class FinalizeError(Exception):
    """Raised when final package build cannot proceed."""


@dataclass(frozen=True)
class FinalPackagePaths:
    """Paths for final package build input/output."""

    package_dir: Path
    candidates_json: Path
    candidate_report_json: Path
    mapping_json: Path
    mapping_report_json: Path
    manifest_json: Path
    final_dir: Path
    questions_json: Path
    validation_report_json: Path


def build_final_package_paths(package_dir: Path) -> FinalPackagePaths:
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")

    final_dir = resolved / FINAL_DIR
    return FinalPackagePaths(
        package_dir=resolved,
        candidates_json=resolved / QUESTIONS_DIR / QUESTION_CANDIDATES_NAME,
        candidate_report_json=resolved / QUESTIONS_DIR / QUESTION_CANDIDATE_REPORT_NAME,
        mapping_json=resolved / MAPPINGS_DIR / ANSWER_SOLUTION_MAP_NAME,
        mapping_report_json=resolved / MAPPINGS_DIR / ANSWER_SOLUTION_REPORT_NAME,
        manifest_json=resolved / PACKAGE_MANIFEST_NAME,
        final_dir=final_dir,
        questions_json=final_dir / FINAL_QUESTIONS_NAME,
        validation_report_json=final_dir / FINAL_VALIDATION_REPORT_NAME,
    )


def _load_json_list(path: Path, model: type) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [model.model_validate(item) for item in payload]


def _load_manifest(path: Path) -> ExtractionPackageManifest | None:
    if not path.is_file():
        return None
    return ExtractionPackageManifest.model_validate(
        json.loads(path.read_text(encoding="utf-8")),
    )


def _load_candidate_report(path: Path) -> QuestionCandidateParseResult | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return QuestionCandidateParseResult.model_validate(data)


def _trace_from_candidate(trace) -> FinalQuestionSourceTrace:
    return FinalQuestionSourceTrace(
        start_line=trace.start_line,
        end_line=trace.end_line,
        page_start=trace.page_start,
        page_end=trace.page_end,
        line_numbers=list(trace.line_numbers),
    )


def _build_option(opt) -> FinalQuestionOption:
    return FinalQuestionOption(
        key=opt.key,
        key_raw=opt.key_raw,
        text_raw=opt.text_raw,
        source_trace=FinalQuestionSourceTrace(
            start_line=opt.start_line,
            end_line=opt.end_line,
            line_numbers=list(range(opt.start_line, opt.end_line + 1)),
        ),
        linked_asset_paths=list(opt.linked_asset_paths),
        confidence=opt.confidence,
        issues=list(opt.issues),
    )


def _build_asset(asset) -> FinalQuestionAsset:
    return FinalQuestionAsset(
        raw_markdown=asset.raw_markdown,
        asset_path=asset.asset_path,
        role=asset.role,
        option_key=asset.option_key,
        line_number=asset.line_number,
        confidence=asset.confidence,
        issues=list(asset.issues),
    )


def _build_answer(mapping: QuestionAnswerSolutionMapping | None) -> FinalQuestionAnswer:
    if mapping is None or not mapping.answer_available or mapping.answer is None:
        return FinalQuestionAnswer(available=False, confidence=0.0)

    ans = mapping.answer
    return FinalQuestionAnswer(
        available=True,
        key=ans.answer_key,
        key_raw=ans.answer_key_raw,
        source_text_raw=ans.source_text_raw,
        source_line=ans.source_line,
        confidence=ans.confidence,
        issues=list(ans.issues),
    )


def _build_solution(mapping: QuestionAnswerSolutionMapping | None) -> FinalQuestionSolution:
    if mapping is None or not mapping.solution_available or mapping.solution is None:
        return FinalQuestionSolution(available=False, confidence=0.0)

    sol = mapping.solution
    return FinalQuestionSolution(
        available=True,
        text_raw=sol.raw_text,
        start_line=sol.start_line,
        end_line=sol.end_line,
        image_references=list(sol.image_references),
        confidence=sol.confidence,
        issues=list(sol.issues),
    )


def _answer_matches_options(answer: FinalQuestionAnswer, options: list[FinalQuestionOption]) -> bool:
    if not answer.available or not answer.key:
        return True
    if not options:
        return True
    option_keys = {opt.key for opt in options if opt.key}
    if not option_keys:
        return True
    return answer.key in option_keys


def _determine_validation_status(
    candidate: QuestionCandidate,
    mapping: QuestionAnswerSolutionMapping | None,
    answer: FinalQuestionAnswer,
    issues: list[str],
) -> ValidationStatus:
    if candidate.review_status == CandidateReviewStatus.CANDIDATE_REJECTED:
        return ValidationStatus.REJECTED

    if mapping and mapping.mapping_status == MappingStatus.DUPLICATE_CONFLICT:
        return ValidationStatus.DUPLICATE_CONFLICT

    if candidate.review_status == CandidateReviewStatus.CANDIDATE_DUPLICATE:
        return ValidationStatus.DUPLICATE_CONFLICT

    if candidate.review_status == CandidateReviewStatus.CANDIDATE_INCOMPLETE or not candidate.options:
        return ValidationStatus.INCOMPLETE

    if mapping and mapping.mapping_status == MappingStatus.NEEDS_REVIEW:
        return ValidationStatus.NEEDS_REVIEW

    if candidate.review_status == CandidateReviewStatus.CANDIDATE_NEEDS_REVIEW:
        return ValidationStatus.NEEDS_REVIEW

    if "answer_option_mismatch" in issues:
        return ValidationStatus.NEEDS_REVIEW

    if any("noise_inside" in i for i in issues):
        return ValidationStatus.NEEDS_REVIEW

    if candidate.confidence < 0.75 or (mapping and mapping.confidence < 0.75):
        return ValidationStatus.NEEDS_REVIEW

    if not answer.available and (mapping is None or mapping.mapping_status == MappingStatus.NOT_AVAILABLE):
        return ValidationStatus.QUESTION_ONLY_VALIDATED

    if answer.available and _answer_matches_options(answer, [_build_option(o) for o in candidate.options]):
        if candidate.review_status == CandidateReviewStatus.CANDIDATE_VALID:
            return ValidationStatus.VALIDATED

    if answer.available:
        return ValidationStatus.VALIDATED

    return ValidationStatus.NEEDS_REVIEW


def _build_final_item(
    candidate: QuestionCandidate,
    mapping: QuestionAnswerSolutionMapping | None,
) -> FinalQuestionItem:
    issues: list[str] = list(candidate.issues)
    if mapping:
        issues.extend(mapping.issues)

    options = [_build_option(opt) for opt in candidate.options]
    assets = [_build_asset(asset) for asset in candidate.assets]
    answer = _build_answer(mapping)
    solution = _build_solution(mapping)

    if answer.available and options and not _answer_matches_options(answer, options):
        issues.append("answer_option_mismatch")

    if not candidate.question_text_raw.strip():
        issues.append("missing_question_text")

    validation_status = _determine_validation_status(candidate, mapping, answer, issues)

    confidences = [candidate.confidence]
    if mapping:
        confidences.append(mapping.confidence)
    if answer.available:
        confidences.append(answer.confidence)
    if solution.available:
        confidences.append(solution.confidence)
    confidence = sum(confidences) / len(confidences)

    return FinalQuestionItem(
        question_id=candidate.question_id,
        question_number=candidate.question_number,
        question_number_raw=candidate.question_number_raw,
        question_text_raw=candidate.question_text_raw,
        raw_text=candidate.raw_text,
        options=options,
        answer=answer,
        solution=solution,
        assets=assets,
        source_trace=_trace_from_candidate(candidate.source_trace),
        validation_status=validation_status,
        confidence=confidence,
        issues=sorted(set(issues)),
    )


def build_final_package(
    candidates: list[QuestionCandidate],
    mappings: list[QuestionAnswerSolutionMapping],
    *,
    source_file_name: str | None = None,
    parser_engine: str | None = None,
    missing_question_numbers: list[int] | None = None,
    duplicate_question_numbers: list[int] | None = None,
    warnings: list[str] | None = None,
) -> tuple[FinalQuestionPackage, FinalQuestionValidationReport]:
    """Merge candidates and mappings into final package + validation report."""
    mapping_by_id = {m.question_id: m for m in mappings}
    items: list[FinalQuestionItem] = []
    answer_mismatch_count = 0

    for candidate in candidates:
        mapping = mapping_by_id.get(candidate.question_id)
        item = _build_final_item(candidate, mapping)
        if "answer_option_mismatch" in item.issues:
            answer_mismatch_count += 1
        items.append(item)

    if len(items) != len(candidates):
        raise ValueError("Final item count must equal candidate count")

    validated = sum(1 for i in items if i.validation_status == ValidationStatus.VALIDATED)
    question_only = sum(
        1 for i in items if i.validation_status == ValidationStatus.QUESTION_ONLY_VALIDATED
    )
    needs_review = sum(1 for i in items if i.validation_status == ValidationStatus.NEEDS_REVIEW)
    incomplete = sum(1 for i in items if i.validation_status == ValidationStatus.INCOMPLETE)
    duplicate_conflict = sum(
        1 for i in items if i.validation_status == ValidationStatus.DUPLICATE_CONFLICT
    )
    rejected = sum(1 for i in items if i.validation_status == ValidationStatus.REJECTED)
    visual = sum(1 for i in items if i.assets)
    answered = sum(1 for i in items if i.answer.available)
    solved = sum(1 for i in items if i.solution.available)

    package = FinalQuestionPackage(
        package_version=PACKAGE_VERSION,
        source_file_name=source_file_name,
        parser_engine=parser_engine,
        total_questions=len(items),
        valid_questions=validated + question_only,
        review_required_questions=needs_review + incomplete + duplicate_conflict + rejected,
        question_only_count=question_only,
        answered_count=answered,
        solved_count=solved,
        visual_question_count=visual,
        items=items,
    )

    status_distribution = {
        ValidationStatus.VALIDATED.value: validated,
        ValidationStatus.QUESTION_ONLY_VALIDATED.value: question_only,
        ValidationStatus.NEEDS_REVIEW.value: needs_review,
        ValidationStatus.INCOMPLETE.value: incomplete,
        ValidationStatus.DUPLICATE_CONFLICT.value: duplicate_conflict,
        ValidationStatus.REJECTED.value: rejected,
    }
    status_distribution = {k: v for k, v in status_distribution.items() if v > 0}

    report_warnings = list(warnings or [])
    report_errors: list[str] = []
    if len(items) != len(candidates):
        report_warnings.append("candidate_count_mismatch")
    if sum(status_distribution.values()) != len(items):
        report_errors.append("status_distribution_mismatch")

    report = FinalQuestionValidationReport(
        status=FinalizeStatus.SUCCEEDED if not report_errors else FinalizeStatus.FAILED,
        package_version=PACKAGE_VERSION,
        total_questions=len(items),
        validated_count=validated,
        question_only_validated_count=question_only,
        needs_review_count=needs_review,
        incomplete_count=incomplete,
        duplicate_conflict_count=duplicate_conflict,
        rejected_count=rejected,
        visual_question_count=visual,
        answer_option_mismatch_count=answer_mismatch_count,
        missing_question_numbers=list(missing_question_numbers or []),
        duplicate_question_numbers=list(duplicate_question_numbers or []),
        status_distribution=status_distribution,
        warnings=report_warnings,
        errors=report_errors,
    )

    return package, report


def write_final_outputs(
    package: FinalQuestionPackage,
    report: FinalQuestionValidationReport,
    paths: FinalPackagePaths,
) -> None:
    """Write final/questions.json and final/validation-report.json."""
    assert_output_contains(paths.package_dir, paths.final_dir)
    paths.final_dir.mkdir(parents=True, exist_ok=True)

    assert_output_contains(paths.package_dir, paths.questions_json)
    paths.questions_json.write_text(
        json.dumps(package.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )

    assert_output_contains(paths.package_dir, paths.validation_report_json)
    paths.validation_report_json.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )


def build_final_package_from_directory(package_dir: Path) -> tuple[FinalQuestionPackage, FinalQuestionValidationReport]:
    """Load inputs, build final package, write outputs."""
    paths = build_final_package_paths(package_dir)

    missing: list[str] = []
    if not paths.candidates_json.is_file():
        missing.append(f"Missing question candidates: {paths.candidates_json}")
    if not paths.mapping_json.is_file():
        missing.append(f"Missing answer/solution map: {paths.mapping_json}")

    if missing:
        report = FinalQuestionValidationReport(
            status=FinalizeStatus.FAILED,
            errors=missing,
        )
        _try_write_failed_report(report, paths)
        raise FinalizeError(missing[0])

    candidates = _load_json_list(paths.candidates_json, QuestionCandidate)
    mappings = _load_json_list(paths.mapping_json, QuestionAnswerSolutionMapping)

    manifest = _load_manifest(paths.manifest_json)
    candidate_report = _load_candidate_report(paths.candidate_report_json)

    warnings: list[str] = []
    missing_q: list[int] = []
    duplicate_q: list[int] = []

    if candidate_report:
        missing_q = list(candidate_report.missing_question_numbers)
        duplicate_q = list(candidate_report.duplicate_question_numbers)
        warnings.extend(candidate_report.warnings)

    if paths.mapping_report_json.is_file():
        map_report_data = json.loads(paths.mapping_report_json.read_text(encoding="utf-8"))
        warnings.extend(map_report_data.get("warnings", []))

    package, report = build_final_package(
        candidates,
        mappings,
        source_file_name=manifest.source_file_name if manifest else None,
        parser_engine=manifest.parser_engine if manifest else None,
        missing_question_numbers=missing_q,
        duplicate_question_numbers=duplicate_q,
        warnings=warnings,
    )

    write_final_outputs(package, report, paths)
    return package, report


def _try_write_failed_report(report: FinalQuestionValidationReport, paths: FinalPackagePaths) -> None:
    try:
        paths.final_dir.mkdir(parents=True, exist_ok=True)
        paths.validation_report_json.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
    except (PathValidationError, OSError):
        pass
