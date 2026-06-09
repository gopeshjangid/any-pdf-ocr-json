"""Tests for Part 11 artifact reconciliation and report metrics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    AnswerCandidate,
    MappingStatus,
    QuestionAnswerSolutionMapping,
)
from meritranker_data_ingestion.schemas.artifact_reconciliation import (
    QualityGateStatus,
    ReconciliationSeverity,
)
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionAnswer,
    FinalQuestionItem,
    FinalQuestionPackage,
    FinalQuestionSolution,
    FinalQuestionSourceTrace,
    FinalQuestionValidationReport,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.ingestion_eligibility import (
    EligibilityBuildStatus,
    EligibilityStatus,
    IngestionEligibilityReport,
)
from meritranker_data_ingestion.schemas.question_candidates import (
    AssetRole,
    CandidateReviewStatus,
    QuestionAssetReference,
    QuestionCandidate,
    QuestionOptionCandidate,
    QuestionSourceTrace,
)
from meritranker_data_ingestion.schemas.review_export import ReviewExportReport
from meritranker_data_ingestion.services.artifact_reconciler import reconcile_artifacts
from meritranker_data_ingestion.services.candidate_report_metrics import (
    candidate_has_noise,
    compute_candidate_report_metrics,
)
from meritranker_data_ingestion.services.question_candidate_parser import (
    write_question_parse_outputs,
    build_question_parse_paths,
)
from meritranker_data_ingestion.schemas.question_candidates import (
    ParseStatus,
    QuestionCandidateParseResult,
)


def _trace() -> QuestionSourceTrace:
    return QuestionSourceTrace(start_line=1, end_line=3, line_numbers=[1, 2, 3])


def _final_trace() -> FinalQuestionSourceTrace:
    return FinalQuestionSourceTrace(start_line=1, end_line=3, line_numbers=[1, 2, 3])


def _candidate(
    qid: str = "q_0019",
    qnum: int = 19,
    *,
    issues: list[str] | None = None,
    assets: list[QuestionAssetReference] | None = None,
    options: list[QuestionOptionCandidate] | None = None,
    status: CandidateReviewStatus = CandidateReviewStatus.CANDIDATE_VALID,
) -> QuestionCandidate:
    return QuestionCandidate(
        question_id=qid,
        question_number=qnum,
        question_number_raw=f"Q{qnum}",
        raw_text=f"Q{qnum} text",
        question_text_raw=f"Q{qnum} text",
        options=options
        or [
            QuestionOptionCandidate(
                key="A",
                key_raw="(a)",
                text_raw="opt",
                start_line=2,
                end_line=2,
                confidence=0.9,
            ),
        ],
        assets=assets or [],
        source_trace=_trace(),
        confidence=0.9,
        review_status=status,
        issues=issues or [],
    )


def _write_package(
    tmp_path: Path,
    *,
    candidates: list[QuestionCandidate],
    report: dict,
    mappings: list[QuestionAnswerSolutionMapping] | None = None,
    mapping_report: dict | None = None,
    final_items: list[FinalQuestionItem] | None = None,
    validation_report: dict | None = None,
    review_report: dict | None = None,
    eligibility_report: dict | None = None,
    eligible: list | None = None,
    review_required: list | None = None,
    blocked: list | None = None,
) -> Path:
    pkg = tmp_path / "extraction_package"
    (pkg / "questions").mkdir(parents=True)
    (pkg / "mappings").mkdir(parents=True)
    (pkg / "final").mkdir(parents=True)
    (pkg / "review").mkdir(parents=True)
    (pkg / "diagnostics").mkdir(parents=True)
    (pkg / "audit").mkdir(parents=True)

    (pkg / "questions" / "question-candidates.json").write_text(
        json.dumps([c.model_dump(mode="json") for c in candidates]),
        encoding="utf-8",
    )
    (pkg / "questions" / "question-candidate-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )

    mappings = mappings or []
    (pkg / "mappings" / "answer-solution-map.json").write_text(
        json.dumps([m.model_dump(mode="json") for m in mappings]),
        encoding="utf-8",
    )
    (pkg / "mappings" / "answer-solution-report.json").write_text(
        json.dumps(mapping_report or {"mapped_count": len(mappings)}),
        encoding="utf-8",
    )

    items = final_items or []
    (pkg / "final" / "questions.json").write_text(
        json.dumps(
            FinalQuestionPackage(
                source_file_name="exam.pdf",
                parser_engine="marker",
                total_questions=len(items),
                valid_questions=len(items),
                items=items,
            ).model_dump(mode="json"),
        ),
        encoding="utf-8",
    )
    (pkg / "final" / "validation-report.json").write_text(
        json.dumps(
            validation_report
            or {
                "status": "succeeded",
                "total_questions": len(items),
                "status_distribution": {},
            },
        ),
        encoding="utf-8",
    )

    if review_report is not None:
        (pkg / "review" / "review-items.json").write_text(
            json.dumps(review_report),
            encoding="utf-8",
        )

    if eligibility_report is not None:
        (pkg / "eligibility").mkdir(parents=True, exist_ok=True)
        (pkg / "eligibility" / "ingestion-eligibility-report.json").write_text(
            json.dumps(eligibility_report),
            encoding="utf-8",
        )
        if eligible is not None:
            (pkg / "eligibility" / "eligible-questions.json").write_text(
                json.dumps(eligible),
                encoding="utf-8",
            )
        if review_required is not None:
            (pkg / "eligibility" / "review-required-questions.json").write_text(
                json.dumps(review_required),
                encoding="utf-8",
            )
        if blocked is not None:
            (pkg / "eligibility" / "blocked-questions.json").write_text(
                json.dumps(blocked),
                encoding="utf-8",
            )

    (pkg / "audit" / "final-package-audit.json").write_text(
        json.dumps({"expected_count_match": True}),
        encoding="utf-8",
    )
    (pkg / "manifest.json").write_text(
        json.dumps({"source_file_name": "exam.pdf"}),
        encoding="utf-8",
    )
    return pkg


def test_noise_candidate_counted_in_metrics() -> None:
    noise_asset = QuestionAssetReference(
        raw_markdown="![](n.jpeg)",
        asset_path="n.jpeg",
        role=AssetRole.NOISE_CANDIDATE,
        line_number=5,
        confidence=0.9,
        issues=["possible_noise_asset_after_options"],
    )
    candidate = _candidate(
        issues=["possible_noise_asset_after_options"],
        assets=[noise_asset],
    )
    assert candidate_has_noise(candidate)
    metrics = compute_candidate_report_metrics([candidate])
    assert metrics["candidates_with_noise"] == 1
    assert metrics["noise_asset_count"] == 1
    assert metrics["possible_noise_asset_after_options_count"] == 1


def test_visual_counters_in_metrics() -> None:
    candidate = _candidate(
        qnum=31,
        issues=["visual_question_requires_diagram_syntax"],
        assets=[
            QuestionAssetReference(
                raw_markdown="![](f.jpeg)",
                asset_path="f.jpeg",
                role=AssetRole.QUESTION_IMAGE,
                line_number=2,
                confidence=0.9,
            ),
            QuestionAssetReference(
                raw_markdown="![](s.jpeg)",
                asset_path="s.jpeg",
                role=AssetRole.QUESTION_SUPPORT_IMAGE,
                line_number=3,
                confidence=0.9,
            ),
        ],
        options=[
            QuestionOptionCandidate(
                key=k,
                key_raw=f"({k.lower()})",
                text_raw="1",
                start_line=i,
                end_line=i,
                confidence=0.9,
                linked_asset_paths=[],
            )
            for i, k in enumerate(["A", "B", "C", "D"], start=4)
        ],
    )
    candidate.question_text_raw = "How many squares are there in the following figure?"
    metrics = compute_candidate_report_metrics([candidate])
    assert metrics["candidates_with_question_images"] == 1
    assert metrics["candidates_with_question_support_images"] == 1
    assert metrics["visual_dependent_count"] == 1
    assert metrics["visual_text_option_count"] == 1


def test_candidate_report_written_with_noise_counters(tmp_path: Path) -> None:
    noise_asset = QuestionAssetReference(
        raw_markdown="![](n.jpeg)",
        asset_path="n.jpeg",
        role=AssetRole.NOISE_CANDIDATE,
        line_number=5,
        confidence=0.9,
    )
    candidate = _candidate(assets=[noise_asset], issues=["possible_noise_asset_after_options"])
    metrics = compute_candidate_report_metrics([candidate])
    result = QuestionCandidateParseResult(
        package_dir=str(tmp_path),
        status=ParseStatus.SUCCEEDED,
        candidates=[candidate],
        status_distribution={"candidate_valid": 1},
        **metrics,
    )
    paths = build_question_parse_paths(tmp_path)
    paths.questions_dir.mkdir(parents=True, exist_ok=True)
    write_question_parse_outputs(result, paths)
    report = json.loads(paths.report_json.read_text())
    assert report["candidates_with_noise"] == 1
    assert report["noise_asset_count"] == 1


def test_status_distribution_mismatch_fails(tmp_path: Path) -> None:
    candidate = _candidate()
    metrics = compute_candidate_report_metrics([candidate])
    report = {
        "total_candidates": 1,
        "status_distribution": {"candidate_valid": 0, "candidate_incomplete": 1},
        **{k: metrics[k] for k in metrics if k != "total_candidates"},
    }
    pkg = _write_package(tmp_path, candidates=[candidate], report=report)
    result = reconcile_artifacts(pkg)
    failed_ids = {c.check_id for c in result.checks if c.severity == ReconciliationSeverity.FAILED}
    assert "candidate_status_distribution_mismatch" in failed_ids
    assert result.quality_gate_status == QualityGateStatus.FAILED


def test_mapping_count_mismatch_fails(tmp_path: Path) -> None:
    candidate = _candidate()
    metrics = compute_candidate_report_metrics([candidate])
    report = {"total_candidates": 1, "status_distribution": {"candidate_valid": 1}, **metrics}
    pkg = _write_package(tmp_path, candidates=[candidate], report=report, mappings=[])
    result = reconcile_artifacts(pkg)
    assert any(c.check_id == "mapping_count_mismatch" for c in result.checks)


def test_answer_key_not_in_options_counted(tmp_path: Path) -> None:
    candidate = _candidate(
        options=[
            QuestionOptionCandidate(
                key="A",
                key_raw="(a)",
                text_raw="1",
                start_line=2,
                end_line=2,
                confidence=0.9,
            ),
        ],
    )
    mapping = QuestionAnswerSolutionMapping(
        question_id="q_0019",
        question_number=19,
        answer_available=True,
        answer=AnswerCandidate(
            question_number=19,
            answer_key="Z",
            answer_key_raw="z",
            source_line=10,
            source_text_raw="Ans.(z)",
            confidence=0.9,
        ),
        solution_available=False,
        mapping_status=MappingStatus.ANSWER_ONLY_MAPPED,
        confidence=0.9,
    )
    metrics = compute_candidate_report_metrics([candidate])
    report = {"total_candidates": 1, "status_distribution": {"candidate_valid": 1}, **metrics}
    pkg = _write_package(
        tmp_path,
        candidates=[candidate],
        report=report,
        mappings=[mapping],
    )
    result = reconcile_artifacts(pkg)
    assert any(c.check_id == "answer_key_not_in_candidate_options" for c in result.checks)


def test_eligibility_missing_is_warning_not_failure(tmp_path: Path) -> None:
    candidate = _candidate()
    metrics = compute_candidate_report_metrics([candidate])
    report = {"total_candidates": 1, "status_distribution": {"candidate_valid": 1}, **metrics}
    mapping = QuestionAnswerSolutionMapping(
        question_id="q_0019",
        question_number=19,
        answer_available=True,
        answer=AnswerCandidate(
            question_number=19,
            answer_key="A",
            answer_key_raw="a",
            source_line=10,
            source_text_raw="Ans.(a)",
            confidence=0.9,
        ),
        solution_available=True,
        mapping_status=MappingStatus.MAPPED,
        confidence=0.9,
    )
    final_item = FinalQuestionItem(
        question_id="q_0019",
        question_number=19,
        question_number_raw="Q19",
        question_text_raw="text",
        raw_text="text",
        options=[],
        answer=FinalQuestionAnswer(available=True, key="A", confidence=0.9),
        solution=FinalQuestionSolution(available=True, text_raw="sol", confidence=0.9),
        assets=[],
        source_trace=_final_trace(),
        validation_status=ValidationStatus.VALIDATED,
        confidence=0.9,
        issues=[],
    )
    pkg = _write_package(
        tmp_path,
        candidates=[candidate],
        report=report,
        mappings=[mapping],
        mapping_report={"mapped_count": 1},
        final_items=[final_item],
        validation_report={
            "status": "succeeded",
            "total_questions": 1,
            "validated_count": 1,
            "status_distribution": {"validated": 1},
        },
    )
    result = reconcile_artifacts(pkg)
    assert "eligibility_not_built" in result.warnings
    assert result.quality_gate_status == QualityGateStatus.WARNING


def test_blocked_in_eligible_fails(tmp_path: Path) -> None:
    candidate = _candidate()
    metrics = compute_candidate_report_metrics([candidate])
    report = {"total_candidates": 1, "status_distribution": {"candidate_valid": 1}, **metrics}
    final_item = FinalQuestionItem(
        question_id="q_0019",
        question_number=19,
        question_number_raw="Q19",
        question_text_raw="text",
        raw_text="text",
        options=[],
        answer=FinalQuestionAnswer(available=True, key="A", confidence=0.9),
        solution=FinalQuestionSolution(available=True, text_raw="sol", confidence=0.9),
        assets=[],
        source_trace=_final_trace(),
        validation_status=ValidationStatus.VALIDATED,
        confidence=0.9,
        issues=[],
    )
    eligibility = IngestionEligibilityReport(
        status=EligibilityBuildStatus.SUCCEEDED,
        package_dir=str(tmp_path),
        total_questions=1,
        eligible_count=1,
        review_required_count=0,
        blocked_count=0,
        items=[
            {
                "question_id": "q_0019",
                "question_number": 19,
                "validation_status": "validated",
                "eligibility_status": "eligible_for_ingestion",
                "eligibility_reasons": [],
                "blocking_reasons": [],
                "review_reasons": [],
                "answer_available": True,
                "solution_available": True,
                "has_visual_assets": False,
                "has_linked_option_assets": False,
                "duplicate_solution_issue": False,
                "source_trace": _final_trace().model_dump(),
                "recommended_action": "ok",
            },
        ],
    )
    pkg = _write_package(
        tmp_path,
        candidates=[candidate],
        report=report,
        final_items=[final_item],
        eligibility_report=eligibility.model_dump(mode="json"),
        eligible=[{"question_id": "q_0019"}],
        blocked=[{"question_id": "q_0019"}],
    )
    result = reconcile_artifacts(pkg)
    assert any(c.check_id == "blocked_in_eligible" for c in result.checks)
    assert result.quality_gate_status == QualityGateStatus.FAILED


def test_matching_report_passes_candidate_checks(tmp_path: Path) -> None:
    candidate = _candidate(
        assets=[
            QuestionAssetReference(
                raw_markdown="![](n.jpeg)",
                asset_path="n.jpeg",
                role=AssetRole.NOISE_CANDIDATE,
                line_number=5,
                confidence=0.9,
            ),
        ],
        issues=["possible_noise_asset_after_options"],
    )
    metrics = compute_candidate_report_metrics([candidate])
    report = {
        "total_candidates": 1,
        "status_distribution": {"candidate_valid": 1},
        **metrics,
    }
    mapping = QuestionAnswerSolutionMapping(
        question_id="q_0019",
        question_number=19,
        answer_available=True,
        answer=AnswerCandidate(
            question_number=19,
            answer_key="A",
            answer_key_raw="a",
            source_line=10,
            source_text_raw="Ans.(a)",
            confidence=0.9,
        ),
        solution_available=True,
        mapping_status=MappingStatus.MAPPED,
        confidence=0.9,
    )
    final_item = FinalQuestionItem(
        question_id="q_0019",
        question_number=19,
        question_number_raw="Q19",
        question_text_raw="text",
        raw_text="text",
        options=[],
        answer=FinalQuestionAnswer(available=True, key="A", confidence=0.9),
        solution=FinalQuestionSolution(available=True, text_raw="sol", confidence=0.9),
        assets=[],
        source_trace=_final_trace(),
        validation_status=ValidationStatus.VALIDATED,
        confidence=0.9,
        issues=["possible_noise_asset_after_options"],
    )
    pkg = _write_package(
        tmp_path,
        candidates=[candidate],
        report=report,
        mappings=[mapping],
        mapping_report={"mapped_count": 1},
        final_items=[final_item],
        validation_report={
            "status": "succeeded",
            "total_questions": 1,
            "validated_count": 1,
            "status_distribution": {"validated": 1},
        },
        review_report=ReviewExportReport(
            package_dir=str(tmp_path),
            total_final_questions=1,
            review_item_count=0,
            items=[],
        ).model_dump(mode="json"),
        eligibility_report=IngestionEligibilityReport(
            status=EligibilityBuildStatus.SUCCEEDED,
            package_dir=str(tmp_path),
            total_questions=1,
            eligible_count=1,
            review_required_count=0,
            blocked_count=0,
            items=[],
        ).model_dump(mode="json"),
        eligible=[{"question_id": "q_0019"}],
        review_required=[],
        blocked=[],
    )
    result = reconcile_artifacts(pkg)
    assert result.failed_check_count == 0
    assert report["candidates_with_noise"] > 0
