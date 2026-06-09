"""One-command full pipeline orchestration (Part 8)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import EXTRACTION_PACKAGE_DIR
from meritranker_data_ingestion.schemas.classification import ClassificationStatus
from meritranker_data_ingestion.schemas.extraction import ExtractionStatus
from meritranker_data_ingestion.schemas.final_package_audit import AuditStatus
from meritranker_data_ingestion.schemas.final_question_package import FinalizeStatus
from meritranker_data_ingestion.schemas.answer_solution_mapping import MapperStatus
from meritranker_data_ingestion.schemas.pipeline import (
    PipelineRunResult,
    PipelineRunStatus,
    PipelineStageResult,
    PipelineStageStatus,
)
from meritranker_data_ingestion.schemas.question_candidates import ParseStatus
from meritranker_data_ingestion.services.answer_solution_mapper import (
    MappingError,
    map_answers_solutions_package,
)
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    resolve_path,
)
from meritranker_data_ingestion.services.final_package_auditor import (
    AuditError,
    audit_final_package_from_directory,
)
from meritranker_data_ingestion.services.final_question_package_builder import (
    FinalizeError,
    build_final_package_from_directory,
)
from meritranker_data_ingestion.services.markdown_classifier import (
    ClassificationError,
    classify_package,
)
from meritranker_data_ingestion.services.marker_adapter import MarkerAdapter
from meritranker_data_ingestion.services.question_candidate_parser import (
    QuestionParseError,
    parse_question_candidates_package,
)
from meritranker_data_ingestion.services.question_coverage_diagnostician import (
    CoverageDiagnosticError,
    diagnose_question_coverage_package,
)
from meritranker_data_ingestion.services.raw_markdown_inspector import (
    InspectionError,
    inspect_raw_markdown_package,
)
from meritranker_data_ingestion.schemas.ingestion_eligibility import AnswerMode
from meritranker_data_ingestion.services.ingestion_eligibility_builder import (
    EligibilityBuildError,
    build_ingestion_eligibility_package,
)
from meritranker_data_ingestion.services.review_exporter import (
    ReviewExportError,
    export_review_items_package,
)
from meritranker_data_ingestion.schemas.artifact_reconciliation import QualityGateStatus
from meritranker_data_ingestion.services.artifact_reconciler import (
    ArtifactReconciliationError,
    reconcile_artifacts_package,
)
from meritranker_data_ingestion.schemas.pattern_question_input import PatternExportMode
from meritranker_data_ingestion.services.pattern_question_input_builder import (
    PatternInputBuildError,
    build_pattern_question_input_package,
)
from meritranker_data_ingestion.schemas.document_evidence import EvidenceExtractionStatus
from meritranker_data_ingestion.services.document_evidence_normalizer import (
    EvidenceNormalizationError,
    normalize_evidence_package,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingStatus,
)
from meritranker_data_ingestion.services.semantic_binder import (
    SemanticBindingError,
    bind_semantically_package,
    evaluate_binder_trigger,
)
from meritranker_data_ingestion.config import DOCUMENT_EVIDENCE_JSON_NAME, EVIDENCE_DIR
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage


class PipelineError(Exception):
    """Raised when pipeline cannot start."""


@dataclass(frozen=True)
class PipelineOptions:
    """Options for a full pipeline run."""

    input_pdf: Path
    output_dir: Path
    expected_count: int | None = None
    skip_audit: bool = False
    skip_inspection: bool = False
    skip_diagnosis: bool = False
    force: bool = False
    stop_on_failure: bool = True
    build_eligibility: bool = False
    answer_mode: AnswerMode = AnswerMode.REQUIRED
    reconcile_artifacts: bool = False
    strict_quality_gate: bool = False
    build_pattern_input: bool = False
    pattern_export_mode: PatternExportMode = PatternExportMode.ELIGIBLE_ONLY
    allow_failed_quality_gate_for_pattern: bool = False
    normalize_evidence: bool = False
    use_semantic_binder: bool = False
    force_semantic_binder: bool = False
    semantic_binder_provider: str | None = None
    semantic_binder_model: str | None = None
    semantic_answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY
    include_answer_key_evidence: bool = True
    semantic_binder_timeout_seconds: int | None = None


def _package_dir(output_dir: Path) -> Path:
    return resolve_path(output_dir) / EXTRACTION_PACKAGE_DIR


def _stage_ok(result: PipelineStageResult, *, stop_on_failure: bool) -> bool:
    if result.status == PipelineStageStatus.SKIPPED:
        return True
    if result.status == PipelineStageStatus.SUCCEEDED:
        return True
    return not stop_on_failure


def run_pipeline(options: PipelineOptions) -> PipelineRunResult:
    """Run full ingestion pipeline via direct service calls (no subprocess)."""
    output_dir = resolve_path(options.output_dir)
    package_dir = _package_dir(output_dir)

    if package_dir.exists() and not options.force:
        raise PipelineError(
            f"Extraction package already exists: {package_dir}. "
            "Use --force to rerun or remove output directory.",
        )

    stages: list[PipelineStageResult] = []
    errors: list[str] = []
    summary: dict = {}

    def record(stage: str, status: PipelineStageStatus, stage_summary: dict, stage_errors: list[str]) -> PipelineStageResult:
        result = PipelineStageResult(
            stage=stage,
            status=status,
            summary=stage_summary,
            errors=stage_errors,
        )
        stages.append(result)
        if stage_errors:
            errors.extend(stage_errors)
        return result

    def should_continue() -> bool:
        if not options.stop_on_failure:
            return True
        return all(
            s.status in {PipelineStageStatus.SUCCEEDED, PipelineStageStatus.SKIPPED}
            for s in stages
        )

    # 1. prepare
    try:
        manifest = MarkerAdapter().extract(options.input_pdf, output_dir)
        ok = manifest.status == ExtractionStatus.SUCCEEDED
        record(
            "prepare",
            PipelineStageStatus.SUCCEEDED if ok else PipelineStageStatus.FAILED,
            {"status": manifest.status.value, "source_file_name": manifest.source_file_name},
            list(manifest.errors) if not ok else [],
        )
    except PathValidationError as exc:
        record("prepare", PipelineStageStatus.FAILED, {}, [str(exc)])
    except Exception as exc:  # noqa: BLE001
        record("prepare", PipelineStageStatus.FAILED, {}, [str(exc)])

    if not should_continue():
        return PipelineRunResult(
            status=PipelineRunStatus.FAILED,
            package_dir=str(package_dir),
            output_dir=str(output_dir),
            stages=stages,
            summary=summary,
            errors=errors,
        )

    # 1b. normalize-evidence (optional)
    if options.normalize_evidence:
        try:
            result = normalize_evidence_package(package_dir)
            ok = result.package.extraction_status in {
                EvidenceExtractionStatus.SUCCEEDED,
                EvidenceExtractionStatus.PARTIAL,
            }
            record(
                "normalize-evidence",
                PipelineStageStatus.SUCCEEDED if ok else PipelineStageStatus.FAILED,
                {
                    "extraction_status": result.package.extraction_status.value,
                    "primary_extractor": result.package.primary_extractor,
                    "line_count": result.summary.line_count,
                },
                list(result.package.errors) if not ok else [],
            )
        except (PathValidationError, EvidenceNormalizationError) as exc:
            record("normalize-evidence", PipelineStageStatus.FAILED, {}, [str(exc)])
        except Exception as exc:  # noqa: BLE001
            record("normalize-evidence", PipelineStageStatus.FAILED, {}, [str(exc)])

        if not should_continue():
            return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 1c. semantic binder (optional)
    if options.use_semantic_binder or options.force_semantic_binder:
        evidence_path = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
        try:
            if not evidence_path.exists():
                record(
                    "bind-semantically",
                    PipelineStageStatus.FAILED,
                    {},
                    ["document evidence missing; run with --normalize-evidence"],
                )
            else:
                evidence = DocumentEvidencePackage.model_validate_json(
                    evidence_path.read_text(encoding="utf-8"),
                )
                trigger = evaluate_binder_trigger(
                    package_dir,
                    evidence,
                    force=options.force_semantic_binder,
                )
                if not trigger.should_run:
                    record(
                        "bind-semantically",
                        PipelineStageStatus.SKIPPED,
                        {"trigger_reasons": trigger.reasons},
                        [],
                    )
                else:
                    bind_result = bind_semantically_package(
                        package_dir,
                        provider=options.semantic_binder_provider,
                        model=options.semantic_binder_model,
                        answer_mode=options.semantic_answer_mode,
                        expected_count=options.expected_count,
                        force=options.force_semantic_binder,
                        include_answer_key_evidence=options.include_answer_key_evidence,
                        timeout_seconds=options.semantic_binder_timeout_seconds,
                    )
                    ok = bind_result.package.status != SemanticBindingStatus.FAILED
                    record(
                        "bind-semantically",
                        PipelineStageStatus.SUCCEEDED if ok else PipelineStageStatus.FAILED,
                        {
                            "status": bind_result.package.status.value,
                            "total_items": bind_result.package.validation_summary.total_items,
                            "accepted_count": bind_result.package.validation_summary.accepted_count,
                            "trigger_reasons": trigger.reasons,
                        },
                        list(bind_result.package.errors) if not ok else [],
                    )
        except (PathValidationError, SemanticBindingError) as exc:
            record("bind-semantically", PipelineStageStatus.FAILED, {}, [str(exc)])
        except Exception as exc:  # noqa: BLE001
            record("bind-semantically", PipelineStageStatus.FAILED, {}, [str(exc)])

        if not should_continue():
            return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 2. classify-markdown
    try:
        result = classify_package(package_dir)
        ok = result.status == ClassificationStatus.SUCCEEDED
        record(
            "classify-markdown",
            PipelineStageStatus.SUCCEEDED if ok else PipelineStageStatus.FAILED,
            {
                "status": result.status.value,
                "content_question_anchor_count": result.content_question_anchor_count,
            },
            list(result.errors) if not ok else [],
        )
    except (PathValidationError, ClassificationError) as exc:
        record("classify-markdown", PipelineStageStatus.FAILED, {}, [str(exc)])

    if not should_continue():
        return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 3. inspect-raw-markdown
    if options.skip_inspection:
        record("inspect-raw-markdown", PipelineStageStatus.SKIPPED, {}, [])
    else:
        try:
            report = inspect_raw_markdown_package(package_dir)
            record(
                "inspect-raw-markdown",
                PipelineStageStatus.SUCCEEDED,
                {
                    "total_raw_lines": report["total_raw_lines"],
                    "image_reference_count": report["image_reference_count"],
                },
                [],
            )
        except (PathValidationError, InspectionError) as exc:
            record("inspect-raw-markdown", PipelineStageStatus.FAILED, {}, [str(exc)])

    if not should_continue():
        return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 4. parse-question-candidates
    try:
        result = parse_question_candidates_package(package_dir)
        ok = result.status == ParseStatus.SUCCEEDED
        record(
            "parse-question-candidates",
            PipelineStageStatus.SUCCEEDED if ok else PipelineStageStatus.FAILED,
            {
                "status": result.status.value,
                "total_candidates": result.total_candidates,
                "valid_candidates": result.valid_candidates,
            },
            list(result.errors) if not ok else [],
        )
    except (PathValidationError, QuestionParseError) as exc:
        record("parse-question-candidates", PipelineStageStatus.FAILED, {}, [str(exc)])

    if not should_continue():
        return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 5. map-answers-solutions
    try:
        result = map_answers_solutions_package(package_dir)
        ok = result.status == MapperStatus.SUCCEEDED
        summary["mapped_count"] = result.mapped_count
        record(
            "map-answers-solutions",
            PipelineStageStatus.SUCCEEDED if ok else PipelineStageStatus.FAILED,
            {
                "status": result.status.value,
                "mapped_count": result.mapped_count,
                "content_lines_used": result.content_lines_used,
            },
            list(result.errors) if not ok else [],
        )
    except (PathValidationError, MappingError) as exc:
        record("map-answers-solutions", PipelineStageStatus.FAILED, {}, [str(exc)])

    if not should_continue():
        return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 6. build-final-package
    try:
        package, report = build_final_package_from_directory(package_dir)
        ok = report.status == FinalizeStatus.SUCCEEDED
        summary.update({
            "total_questions": report.total_questions,
            "validated_count": report.validated_count,
            "needs_review_count": report.needs_review_count,
            "incomplete_count": report.incomplete_count,
        })
        record(
            "build-final-package",
            PipelineStageStatus.SUCCEEDED if ok else PipelineStageStatus.FAILED,
            {
                "status": report.status.value,
                "total_questions": report.total_questions,
            },
            list(report.errors) if not ok else [],
        )
    except (PathValidationError, FinalizeError) as exc:
        record("build-final-package", PipelineStageStatus.FAILED, {}, [str(exc)])

    if not should_continue():
        return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 7. audit-final-package
    audit_status_value: str | None = None
    if options.skip_audit:
        record("audit-final-package", PipelineStageStatus.SKIPPED, {}, [])
    else:
        try:
            audit = audit_final_package_from_directory(
                package_dir,
                expected_question_count=options.expected_count,
            )
            audit_status_value = audit.status.value
            ok = audit.status != AuditStatus.FAILED
            summary.update({
                "audit_status": audit.status.value,
                "expected_count_match": audit.expected_count_match,
            })
            record(
                "audit-final-package",
                PipelineStageStatus.SUCCEEDED if ok else PipelineStageStatus.FAILED,
                {
                    "status": audit.status.value,
                    "expected_count_match": audit.expected_count_match,
                },
                list(audit.errors) if not ok else [],
            )
        except (PathValidationError, AuditError) as exc:
            record("audit-final-package", PipelineStageStatus.FAILED, {}, [str(exc)])

    if not should_continue():
        return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 8. diagnose-question-coverage
    if options.skip_diagnosis or options.expected_count is None:
        record("diagnose-question-coverage", PipelineStageStatus.SKIPPED, {}, [])
    else:
        try:
            cov = diagnose_question_coverage_package(
                package_dir,
                expected_question_count=options.expected_count,
            )
            record(
                "diagnose-question-coverage",
                PipelineStageStatus.SUCCEEDED,
                {
                    "candidate_count": cov["candidate_count"],
                    "missing_at_candidate_stage": cov["missing_at_candidate_stage"],
                },
                [],
            )
        except (PathValidationError, CoverageDiagnosticError) as exc:
            record("diagnose-question-coverage", PipelineStageStatus.FAILED, {}, [str(exc)])

    if not should_continue():
        return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 9. export-review-items
    try:
        review = export_review_items_package(package_dir)
        summary["review_item_count"] = review.review_item_count
        record(
            "export-review-items",
            PipelineStageStatus.SUCCEEDED,
            {"review_item_count": review.review_item_count},
            [],
        )
    except (PathValidationError, ReviewExportError) as exc:
        record("export-review-items", PipelineStageStatus.FAILED, {}, [str(exc)])

    if not should_continue():
        return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 10. build-ingestion-eligibility
    build_eligibility = options.build_eligibility or options.build_pattern_input
    if build_eligibility:
        try:
            elig = build_ingestion_eligibility_package(
                package_dir,
                answer_mode=options.answer_mode,
            )
            summary.update({
                "eligible_count": elig.eligible_count,
                "eligibility_review_count": elig.review_required_count,
                "eligibility_blocked_count": elig.blocked_count,
                "duplicate_solution_count": elig.duplicate_solution_count,
            })
            record(
                "build-ingestion-eligibility",
                PipelineStageStatus.SUCCEEDED if elig.status.value == "succeeded" else PipelineStageStatus.FAILED,
                {
                    "eligible_count": elig.eligible_count,
                    "review_required_count": elig.review_required_count,
                    "blocked_count": elig.blocked_count,
                },
                list(elig.errors),
            )
        except (PathValidationError, EligibilityBuildError) as exc:
            record("build-ingestion-eligibility", PipelineStageStatus.FAILED, {}, [str(exc)])
    else:
        record("build-ingestion-eligibility", PipelineStageStatus.SKIPPED, {}, [])

    should_reconcile = (
        options.reconcile_artifacts
        or options.build_eligibility
        or options.build_pattern_input
    )
    quality_gate_status: str | None = None
    if should_reconcile:
        try:
            recon = reconcile_artifacts_package(package_dir)
            quality_gate_status = recon.quality_gate_status.value
            summary.update({
                "quality_gate_status": quality_gate_status,
                "reconciliation_failed_checks": recon.failed_check_count,
                "reconciliation_warning_count": recon.warning_count,
                "artifact_reconciliation_json": str(
                    package_dir / "diagnostics" / "artifact-reconciliation.json",
                ),
                "artifact_reconciliation_md": str(
                    package_dir / "diagnostics" / "artifact-reconciliation.md",
                ),
            })
            stage_status = PipelineStageStatus.SUCCEEDED
            if recon.quality_gate_status == QualityGateStatus.FAILED:
                stage_status = PipelineStageStatus.FAILED
            record(
                "reconcile-artifacts",
                stage_status,
                {
                    "quality_gate_status": quality_gate_status,
                    "failed_check_count": recon.failed_check_count,
                    "warning_count": recon.warning_count,
                },
                recon.errors,
            )
        except (PathValidationError, ArtifactReconciliationError) as exc:
            record("reconcile-artifacts", PipelineStageStatus.FAILED, {}, [str(exc)])
    else:
        record("reconcile-artifacts", PipelineStageStatus.SKIPPED, {}, [])

    if not should_continue():
        return _failed_result(package_dir, output_dir, stages, summary, errors)

    # 12. build-pattern-input
    if options.build_pattern_input:
        try:
            pattern_result = build_pattern_question_input_package(
                package_dir,
                export_mode=options.pattern_export_mode,
                allow_failed_quality_gate=options.allow_failed_quality_gate_for_pattern,
            )
            summary.update({
                "pattern_input_exported_count": pattern_result.package.exported_count,
                "pattern_input_export_mode": pattern_result.package.export_mode.value,
                "pattern_input_package_json": pattern_result.output_paths.get("package_json"),
            })
            record(
                "build-pattern-input",
                PipelineStageStatus.SUCCEEDED,
                {
                    "exported_count": pattern_result.package.exported_count,
                    "export_mode": pattern_result.package.export_mode.value,
                },
                [],
            )
        except (PathValidationError, PatternInputBuildError) as exc:
            record("build-pattern-input", PipelineStageStatus.FAILED, {}, [str(exc)])
    else:
        record("build-pattern-input", PipelineStageStatus.SKIPPED, {}, [])

    failed_stages = [s for s in stages if s.status == PipelineStageStatus.FAILED]
    if failed_stages:
        overall = PipelineRunStatus.FAILED
    elif quality_gate_status == QualityGateStatus.FAILED.value:
        overall = PipelineRunStatus.FAILED
    elif (
        audit_status_value == AuditStatus.WARNING.value
        or quality_gate_status == QualityGateStatus.WARNING.value
    ):
        overall = PipelineRunStatus.WARNING
    else:
        overall = PipelineRunStatus.SUCCEEDED

    summary.update({
        "package_dir": str(package_dir),
        "final_questions_json": str(package_dir / "final" / "questions.json"),
        "validation_report_json": str(package_dir / "final" / "validation-report.json"),
        "audit_json": str(package_dir / "audit" / "final-package-audit.json"),
        "review_items_json": str(package_dir / "review" / "review-items.json"),
        "review_items_md": str(package_dir / "review" / "review-items.md"),
        "eligibility_report_json": str(package_dir / "eligibility" / "ingestion-eligibility-report.json"),
        "status": overall.value,
        "strict_quality_gate": options.strict_quality_gate,
    })

    return PipelineRunResult(
        status=overall,
        package_dir=str(package_dir),
        output_dir=str(output_dir),
        stages=stages,
        summary=summary,
        errors=errors,
    )


def _failed_result(
    package_dir: Path,
    output_dir: Path,
    stages: list[PipelineStageResult],
    summary: dict,
    errors: list[str],
) -> PipelineRunResult:
    summary["status"] = PipelineRunStatus.FAILED.value
    summary["package_dir"] = str(package_dir)
    return PipelineRunResult(
        status=PipelineRunStatus.FAILED,
        package_dir=str(package_dir),
        output_dir=str(output_dir),
        stages=stages,
        summary=summary,
        errors=errors,
    )


def build_pipeline_summary(result: PipelineRunResult) -> dict:
    """Concise summary for stdout — no full question text."""
    return {
        "status": result.status.value,
        "package_dir": result.package_dir,
        "final_questions_json": result.summary.get("final_questions_json"),
        "validation_report_json": result.summary.get("validation_report_json"),
        "audit_json": result.summary.get("audit_json"),
        "review_items_json": result.summary.get("review_items_json"),
        "review_items_md": result.summary.get("review_items_md"),
        "total_questions": result.summary.get("total_questions"),
        "validated_count": result.summary.get("validated_count"),
        "needs_review_count": result.summary.get("needs_review_count"),
        "incomplete_count": result.summary.get("incomplete_count"),
        "mapped_count": result.summary.get("mapped_count"),
        "review_item_count": result.summary.get("review_item_count"),
        "eligible_count": result.summary.get("eligible_count"),
        "eligibility_blocked_count": result.summary.get("eligibility_blocked_count"),
        "duplicate_solution_count": result.summary.get("duplicate_solution_count"),
        "eligibility_report_json": result.summary.get("eligibility_report_json"),
        "audit_status": result.summary.get("audit_status"),
        "expected_count_match": result.summary.get("expected_count_match"),
        "quality_gate_status": result.summary.get("quality_gate_status"),
        "reconciliation_failed_checks": result.summary.get("reconciliation_failed_checks"),
        "reconciliation_warning_count": result.summary.get("reconciliation_warning_count"),
        "artifact_reconciliation_json": result.summary.get("artifact_reconciliation_json"),
        "pattern_input_exported_count": result.summary.get("pattern_input_exported_count"),
        "pattern_input_export_mode": result.summary.get("pattern_input_export_mode"),
        "pattern_input_package_json": result.summary.get("pattern_input_package_json"),
        "stages": [
            {"stage": s.stage, "status": s.status.value}
            for s in result.stages
        ],
        "errors": result.errors,
    }
