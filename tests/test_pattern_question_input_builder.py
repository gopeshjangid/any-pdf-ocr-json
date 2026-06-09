"""Tests for Part 12 pattern question input handoff builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    AnswerCandidate,
    MappingStatus,
    QuestionAnswerSolutionMapping,
)
from meritranker_data_ingestion.schemas.artifact_reconciliation import QualityGateStatus
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionAnswer,
    FinalQuestionAsset,
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionPackage,
    FinalQuestionSolution,
    FinalQuestionSourceTrace,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.ingestion_eligibility import (
    EligibilityBuildStatus,
    EligibilityStatus,
    IngestionEligibilityItem,
    IngestionEligibilityReport,
)
from meritranker_data_ingestion.schemas.pattern_question_input import (
    PatternExportMode,
    PatternIngestionAction,
)
from meritranker_data_ingestion.schemas.question_candidates import AssetRole
from meritranker_data_ingestion.services.pattern_question_input_builder import (
    ELIGIBILITY_REQUIRED_ERROR,
    QUALITY_GATE_FAILED_ERROR,
    PatternInputBuildError,
    build_pattern_question_input,
    build_pattern_question_input_package,
)


def _trace() -> FinalQuestionSourceTrace:
    return FinalQuestionSourceTrace(start_line=10, end_line=20, line_numbers=[10, 11, 20])


def _option(key: str = "A", *, text: str = "opt1", linked: list[str] | None = None) -> FinalQuestionOption:
    return FinalQuestionOption(
        key=key,
        key_raw=f"({key.lower()})",
        text_raw=text,
        linked_asset_paths=linked or [],
        source_trace=_trace(),
        confidence=0.9,
    )


def _final_item(
    qid: str,
    qnum: int,
    *,
    status: ValidationStatus = ValidationStatus.VALIDATED,
    question_text: str = "Question text",
    raw_text: str | None = None,
    options: list[FinalQuestionOption] | None = None,
    assets: list[FinalQuestionAsset] | None = None,
    answer_key: str = "A",
) -> FinalQuestionItem:
    return FinalQuestionItem(
        question_id=qid,
        question_number=qnum,
        question_number_raw=f"Q{qnum}",
        question_text_raw=question_text,
        raw_text=raw_text or question_text,
        options=options or [_option("A"), _option("B"), _option("C"), _option("D")],
        answer=FinalQuestionAnswer(
            available=True,
            key=answer_key,
            key_raw="(a)",
            source_text_raw="Ans.(a)",
            source_line=99,
            confidence=0.9,
        ),
        solution=FinalQuestionSolution(
            available=True,
            text_raw="Solution verbatim.",
            start_line=100,
            end_line=101,
            confidence=0.9,
        ),
        assets=assets or [],
        source_trace=_trace(),
        validation_status=status,
        confidence=0.9,
        issues=[],
    )


def _elig_item(
    qid: str,
    qnum: int,
    *,
    status: EligibilityStatus,
    review: list[str] | None = None,
    blocking: list[str] | None = None,
) -> IngestionEligibilityItem:
    return IngestionEligibilityItem(
        question_id=qid,
        question_number=qnum,
        validation_status="validated",
        eligibility_status=status,
        eligibility_reasons=[],
        blocking_reasons=blocking or [],
        review_reasons=review or [],
        answer_available=True,
        solution_available=True,
        has_visual_assets=False,
        has_linked_option_assets=False,
        duplicate_solution_issue=False,
        source_trace=_trace(),
        recommended_action="action",
    )


def _write_package(
    tmp_path: Path,
    *,
    items: list[FinalQuestionItem],
    eligibility_items: list[IngestionEligibilityItem],
    quality_gate: str | None = "warning",
    review_ids: list[str] | None = None,
) -> Path:
    pkg = tmp_path / "extraction_package"
    for sub in ("final", "eligibility", "mappings", "diagnostics", "review", "audit"):
        (pkg / sub).mkdir(parents=True)

    package = FinalQuestionPackage(
        source_file_name="paper1.pdf",
        parser_engine="marker",
        total_questions=len(items),
        valid_questions=len(items),
        items=items,
    )
    (pkg / "final" / "questions.json").write_text(
        json.dumps(package.model_dump(mode="json")),
        encoding="utf-8",
    )

    elig_report = IngestionEligibilityReport(
        status=EligibilityBuildStatus.SUCCEEDED,
        package_dir=str(pkg),
        total_questions=len(items),
        eligible_count=sum(
            1 for i in eligibility_items if i.eligibility_status == EligibilityStatus.ELIGIBLE_FOR_INGESTION
        ),
        review_required_count=sum(
            1 for i in eligibility_items if i.eligibility_status == EligibilityStatus.REVIEW_REQUIRED
        ),
        blocked_count=sum(
            1 for i in eligibility_items if i.eligibility_status == EligibilityStatus.BLOCKED
        ),
        items=eligibility_items,
    )
    (pkg / "eligibility" / "ingestion-eligibility-report.json").write_text(
        json.dumps(elig_report.model_dump(mode="json")),
        encoding="utf-8",
    )

    mappings = []
    for item in items:
        mappings.append(
            QuestionAnswerSolutionMapping(
                question_id=item.question_id,
                question_number=item.question_number,
                answer_available=True,
                answer=AnswerCandidate(
                    question_number=item.question_number or 1,
                    answer_key=item.answer.key,
                    answer_key_raw="a",
                    source_line=99,
                    source_text_raw="Ans.(a)",
                    confidence=0.9,
                ),
                solution_available=True,
                mapping_status=MappingStatus.MAPPED,
                confidence=0.9,
            ).model_dump(mode="json"),
        )
    (pkg / "mappings" / "answer-solution-map.json").write_text(
        json.dumps(mappings),
        encoding="utf-8",
    )

    if quality_gate:
        (pkg / "diagnostics" / "artifact-reconciliation.json").write_text(
            json.dumps({"quality_gate_status": quality_gate, "checks": []}),
            encoding="utf-8",
        )

    from meritranker_data_ingestion.schemas.review_export import ReviewExportReport

    (pkg / "review" / "review-items.json").write_text(
        json.dumps(
            ReviewExportReport(
                package_dir=str(pkg),
                total_final_questions=len(items),
                review_item_count=len(review_ids or []),
                items=[],
            ).model_dump(mode="json"),
        ),
        encoding="utf-8",
    )
    (pkg / "audit" / "final-package-audit.json").write_text(
        json.dumps({"status": "warning", "expected_count_match": True}),
        encoding="utf-8",
    )
    (pkg / "manifest.json").write_text(
        json.dumps({"source_file_name": "paper1.pdf"}),
        encoding="utf-8",
    )
    return pkg


@pytest.fixture
def sample_pkg(tmp_path: Path) -> Path:
    items = [
        _final_item("q_0019", 19, question_text="Q19 age question."),
        _final_item(
            "q_0031",
            31,
            question_text="How many squares are in the following figure?",
            assets=[
                FinalQuestionAsset(
                    raw_markdown="![](f.jpeg)",
                    asset_path="f.jpeg",
                    role=AssetRole.QUESTION_IMAGE,
                    line_number=2,
                    confidence=0.9,
                ),
            ],
        ),
        _final_item("q_0052", 52, options=[], status=ValidationStatus.INCOMPLETE),
    ]
    elig = [
        _elig_item("q_0019", 19, status=EligibilityStatus.ELIGIBLE_FOR_INGESTION),
        _elig_item(
            "q_0031",
            31,
            status=EligibilityStatus.REVIEW_REQUIRED,
            review=["visual_question_requires_diagram_syntax"],
        ),
        _elig_item(
            "q_0052",
            52,
            status=EligibilityStatus.BLOCKED,
            blocking=["missing_options", "source_backed_option_labels_missing"],
        ),
    ]
    return _write_package(tmp_path, items=items, eligibility_items=elig)


def test_eligible_only_exports_eligible(sample_pkg: Path) -> None:
    result = build_pattern_question_input(sample_pkg, export_mode=PatternExportMode.ELIGIBLE_ONLY)
    assert result.package.exported_count == 1
    assert result.package.items[0].question_number == 19
    assert result.package.items[0].ingestion_action == PatternIngestionAction.READY_FOR_PATTERN_INGESTION


def test_include_review_exports_eligible_and_review(sample_pkg: Path) -> None:
    result = build_pattern_question_input(sample_pkg, export_mode=PatternExportMode.INCLUDE_REVIEW)
    assert result.package.exported_count == 2
    nums = {item.question_number for item in result.package.items}
    assert nums == {19, 31}
    review_item = next(i for i in result.package.items if i.question_number == 31)
    assert review_item.ingestion_action == PatternIngestionAction.HOLD_FOR_REVIEW


def test_include_blocked_exports_eligible_and_blocked(sample_pkg: Path) -> None:
    result = build_pattern_question_input(sample_pkg, export_mode=PatternExportMode.INCLUDE_BLOCKED)
    assert result.package.exported_count == 2
    nums = {item.question_number for item in result.package.items}
    assert nums == {19, 52}
    blocked = next(i for i in result.package.items if i.question_number == 52)
    assert blocked.ingestion_action == PatternIngestionAction.BLOCKED_DO_NOT_INGEST


def test_all_exports_all(sample_pkg: Path) -> None:
    result = build_pattern_question_input(sample_pkg, export_mode=PatternExportMode.ALL)
    assert result.package.exported_count == 3


def test_visual_assets_preserved(sample_pkg: Path) -> None:
    result = build_pattern_question_input(sample_pkg, export_mode=PatternExportMode.INCLUDE_REVIEW)
    q31 = next(i for i in result.package.items if i.question_number == 31)
    assert len(q31.visual_assets) == 1
    assert q31.visual_assets[0].role == AssetRole.QUESTION_IMAGE
    assert q31.visual_assets[0].asset_path == "f.jpeg"


def test_source_text_unchanged(sample_pkg: Path) -> None:
    result = build_pattern_question_input(sample_pkg, export_mode=PatternExportMode.ALL)
    q19 = next(i for i in result.package.items if i.question_number == 19)
    assert q19.question_text_raw == "Q19 age question."
    assert q19.raw_text == "Q19 age question."
    assert q19.solution.text_raw == "Solution verbatim."


def test_linked_option_assets_preserved(tmp_path: Path) -> None:
    item = _final_item(
        "q_0067",
        67,
        options=[_option("A", text="", linked=["a.jpeg"])],
    )
    pkg = _write_package(
        tmp_path,
        items=[item],
        eligibility_items=[_elig_item("q_0067", 67, status=EligibilityStatus.ELIGIBLE_FOR_INGESTION)],
    )
    result = build_pattern_question_input(pkg)
    assert result.package.items[0].options[0].linked_asset_paths == ["a.jpeg"]


def test_missing_eligibility_fails(sample_pkg: Path) -> None:
    (sample_pkg / "eligibility" / "ingestion-eligibility-report.json").unlink()
    with pytest.raises(PatternInputBuildError, match=ELIGIBILITY_REQUIRED_ERROR):
        build_pattern_question_input(sample_pkg)


def test_failed_quality_gate_fails(sample_pkg: Path) -> None:
    (sample_pkg / "diagnostics" / "artifact-reconciliation.json").write_text(
        json.dumps({"quality_gate_status": QualityGateStatus.FAILED.value, "checks": []}),
        encoding="utf-8",
    )
    with pytest.raises(PatternInputBuildError, match=QUALITY_GATE_FAILED_ERROR):
        build_pattern_question_input(sample_pkg)


def test_warning_quality_gate_allowed_with_warning(sample_pkg: Path) -> None:
    result = build_pattern_question_input(sample_pkg)
    assert any("quality_gate_status:warning" in w for w in result.package.warnings)


def test_review_export_missing_flag(sample_pkg: Path) -> None:
    result = build_pattern_question_input(sample_pkg, export_mode=PatternExportMode.INCLUDE_REVIEW)
    q31 = next(i for i in result.package.items if i.question_number == 31)
    assert "review_export_missing_for_item" in q31.audit_flags


def test_review_export_incomplete_package_warning(sample_pkg: Path) -> None:
    (sample_pkg / "diagnostics" / "artifact-reconciliation.json").write_text(
        json.dumps({
            "quality_gate_status": "warning",
            "checks": [{"check_id": "flagged_final_items_missing_from_review", "severity": "warning"}],
        }),
        encoding="utf-8",
    )
    result = build_pattern_question_input(sample_pkg)
    assert "review_export_incomplete" in result.package.warnings


def test_package_writes_artifacts(sample_pkg: Path) -> None:
    result = build_pattern_question_input_package(sample_pkg)
    assert Path(result.output_paths["package_json"]).is_file()
    assert Path(result.output_paths["summary_md"]).is_file()
    summary = Path(result.output_paths["summary_md"]).read_text()
    assert "does not perform pattern ingestion" in summary


def test_eligible_count_reconciles(sample_pkg: Path) -> None:
    result = build_pattern_question_input(sample_pkg, export_mode=PatternExportMode.ELIGIBLE_ONLY)
    elig = json.loads(
        (sample_pkg / "eligibility" / "ingestion-eligibility-report.json").read_text(),
    )
    assert result.package.exported_count == elig["eligible_count"]
