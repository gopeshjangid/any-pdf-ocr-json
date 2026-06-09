"""Tests for final package quality auditor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meritranker_data_ingestion.schemas.final_package_audit import AuditStatus
from meritranker_data_ingestion.schemas.final_question_package import (
    FinalQuestionAnswer,
    FinalQuestionAsset,
    FinalQuestionItem,
    FinalQuestionPackage,
    FinalQuestionSolution,
    FinalQuestionSourceTrace,
    FinalQuestionValidationReport,
    FinalizeStatus,
    ValidationStatus,
)
from meritranker_data_ingestion.schemas.question_candidates import AssetRole
from meritranker_data_ingestion.services.final_package_auditor import (
    AuditError,
    audit_final_package,
    audit_final_package_from_directory,
    render_audit_markdown,
)


def _trace() -> FinalQuestionSourceTrace:
    return FinalQuestionSourceTrace(start_line=1, end_line=3, line_numbers=[1, 2, 3])


def _item(
    qid: str = "q_0001",
    qnum: int = 1,
    *,
    status: ValidationStatus = ValidationStatus.VALIDATED,
    options: bool = True,
    answer_key: str | None = None,
    issues: list[str] | None = None,
    assets: list[FinalQuestionAsset] | None = None,
    raw_text: str = "Q1. What is 2+2?",
) -> FinalQuestionItem:
    from meritranker_data_ingestion.schemas.final_question_package import FinalQuestionOption

    opts = []
    if options:
        opts = [
            FinalQuestionOption(
                key="A", key_raw="(a)", text_raw="3",
                source_trace=_trace(), confidence=0.9,
            ),
            FinalQuestionOption(
                key="B", key_raw="(b)", text_raw="4",
                source_trace=_trace(), confidence=0.9,
            ),
        ]
    answer = FinalQuestionAnswer(available=False)
    if answer_key:
        answer = FinalQuestionAnswer(
            available=True, key=answer_key, key_raw=answer_key.lower(),
            source_text_raw=f"Ans.({answer_key.lower()})", confidence=0.9,
        )
    return FinalQuestionItem(
        question_id=qid,
        question_number=qnum,
        question_number_raw=f"Q{qnum}",
        question_text_raw=raw_text.split("\n")[0],
        raw_text=raw_text,
        options=opts,
        answer=answer,
        solution=FinalQuestionSolution(available=False),
        assets=assets or [],
        source_trace=_trace(),
        validation_status=status,
        confidence=0.9,
        issues=issues or [],
    )


def _package(*items: FinalQuestionItem, source: str = "exam.pdf") -> FinalQuestionPackage:
    return FinalQuestionPackage(
        source_file_name=source,
        parser_engine="marker",
        total_questions=len(items),
        valid_questions=sum(
            1 for i in items
            if i.validation_status in (
                ValidationStatus.VALIDATED,
                ValidationStatus.QUESTION_ONLY_VALIDATED,
            )
        ),
        items=list(items),
    )


def _validation_report(**kwargs) -> FinalQuestionValidationReport:
    defaults = {
        "status": FinalizeStatus.SUCCEEDED,
        "total_questions": 1,
    }
    defaults.update(kwargs)
    return FinalQuestionValidationReport(**defaults)


def test_audit_success_clean_package() -> None:
    report = audit_final_package(
        _package(_item()),
        _validation_report(),
    )
    assert report.status == AuditStatus.PASSED
    assert report.total_questions == 1
    assert report.validated_count == 1
    assert not report.high_risk_items


def test_audit_warning_missing_question_numbers() -> None:
    report = audit_final_package(
        _package(_item()),
        _validation_report(missing_question_numbers=[5, 10]),
    )
    assert report.status == AuditStatus.WARNING
    assert report.missing_question_numbers == [5, 10]
    assert any(i.issue_type == "missing_question_number" for i in report.issues)


def test_audit_warning_duplicate_question_numbers() -> None:
    report = audit_final_package(
        _package(_item("q_0001", 1), _item("q_0002", 1, status=ValidationStatus.DUPLICATE_CONFLICT)),
        _validation_report(total_questions=2, duplicate_question_numbers=[1]),
    )
    assert report.status == AuditStatus.WARNING
    assert report.duplicate_question_numbers == [1]


def test_audit_warning_needs_review_items() -> None:
    report = audit_final_package(
        _package(_item(status=ValidationStatus.NEEDS_REVIEW)),
        _validation_report(needs_review_count=1),
    )
    assert report.status == AuditStatus.WARNING
    assert report.needs_review_count == 1
    assert "q_0001" in report.high_risk_items


def test_audit_warning_answer_option_mismatch() -> None:
    report = audit_final_package(
        _package(_item(answer_key="Z", issues=["answer_option_mismatch"], status=ValidationStatus.NEEDS_REVIEW)),
        _validation_report(answer_option_mismatch_count=1),
    )
    assert report.status == AuditStatus.WARNING
    assert report.answer_option_mismatch_count == 1


def test_audit_failure_zero_questions() -> None:
    report = audit_final_package(
        FinalQuestionPackage(total_questions=0, items=[]),
    )
    assert report.status == AuditStatus.FAILED
    assert "total_questions is zero" in report.errors[0]


def test_audit_failure_schema_inconsistency() -> None:
    pkg = FinalQuestionPackage(total_questions=5, items=[_item()])
    report = audit_final_package(pkg)
    assert report.status == AuditStatus.FAILED
    assert any("schema_inconsistency" in e for e in report.errors)


def test_expected_count_match() -> None:
    report = audit_final_package(_package(_item()), expected_question_count=1)
    assert report.expected_count_match is True
    assert report.status == AuditStatus.PASSED


def test_expected_count_mismatch_warning() -> None:
    report = audit_final_package(_package(_item()), expected_question_count=2)
    assert report.expected_count_match is False
    assert report.status == AuditStatus.WARNING


def test_expected_count_very_different_failed() -> None:
    items = [_item(f"q_{i:04d}", i) for i in range(1, 51)]
    report = audit_final_package(
        _package(*items),
        expected_question_count=100,
    )
    assert report.status == AuditStatus.FAILED
    assert any(i.issue_type == "expected_count_mismatch" for i in report.issues)


def test_visual_asset_preservation_in_audit() -> None:
    assets = [
        FinalQuestionAsset(
            raw_markdown="![](fig/1.png)",
            asset_path="fig/1.png",
            role=AssetRole.QUESTION_IMAGE,
            line_number=2,
            confidence=0.9,
        ),
    ]
    report = audit_final_package(
        _package(_item(assets=assets)),
        _validation_report(visual_question_count=1),
    )
    assert report.visual_question_count == 1
    assert report.status == AuditStatus.PASSED


def test_visual_without_assets_warning() -> None:
    report = audit_final_package(
        _package(_item(raw_text="Q1. See diagram\n![](fig/1.png)")),
    )
    assert report.status == AuditStatus.WARNING
    assert any(i.issue_type == "visual_without_assets" for i in report.issues)


def test_candidates_without_options_counted() -> None:
    report = audit_final_package(
        _package(_item(options=False, status=ValidationStatus.INCOMPLETE)),
    )
    assert report.candidates_without_options == 1
    assert report.incomplete_count == 1


def test_markdown_report_generation() -> None:
    report = audit_final_package(
        _package(_item(status=ValidationStatus.NEEDS_REVIEW)),
        _validation_report(needs_review_count=1),
    )
    md = render_audit_markdown(report)
    assert "# Final Package Quality Audit" in md
    assert "does not modify" in md
    assert "warning" in md.lower()
    assert "Recommended Next Action" in md


def _write_final_package(
    package_dir: Path,
    package: FinalQuestionPackage,
    validation: FinalQuestionValidationReport | None = None,
) -> Path:
    final = package_dir / "final"
    final.mkdir(parents=True)
    (final / "questions.json").write_text(
        json.dumps(package.model_dump(mode="json")),
        encoding="utf-8",
    )
    if validation:
        (final / "validation-report.json").write_text(
            json.dumps(validation.model_dump(mode="json")),
            encoding="utf-8",
        )
    return package_dir


def test_audit_from_directory_success(tmp_path: Path) -> None:
    pkg = tmp_path / "extraction_package"
    _write_final_package(pkg, _package(_item()), _validation_report())
    report = audit_final_package_from_directory(pkg)
    assert report.status == AuditStatus.PASSED
    assert (pkg / "audit" / "final-package-audit.json").exists()
    assert (pkg / "audit" / "final-package-audit.md").exists()


def test_audit_from_directory_missing_final(tmp_path: Path) -> None:
    pkg = tmp_path / "extraction_package"
    pkg.mkdir()
    with pytest.raises(AuditError, match="Missing final package"):
        audit_final_package_from_directory(pkg)
    assert (pkg / "audit" / "final-package-audit.json").exists()
