"""Tests for Part 13E real-provider evaluation harness and provider hardening."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    INGESTION_ELIGIBILITY_REPORT_NAME,
    QUESTION_CANDIDATE_REPORT_NAME,
    QUESTIONS_DIR,
    SEMANTIC_BINDING_EVALUATION_JSON_NAME,
    SEMANTIC_BOUND_QUESTIONS_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingQualityThresholds,
    SemanticBindingValidationReport,
)
from meritranker_data_ingestion.services.llm_provider import (
    AzureOpenAIProvider,
    LlmProviderError,
    MockLlmProvider,
    OpenAICompatibleProvider,
    resolve_llm_provider,
)
from meritranker_data_ingestion.services.pipeline_runner import PipelineOptions, run_pipeline
from meritranker_data_ingestion.services.semantic_binder import (
    bind_semantically_package,
    evaluate_semantic_binding_package,
)
from meritranker_data_ingestion.services.semantic_binding_evaluation import (
    build_semantic_binding_evaluation,
)


def _line(line_id: str, text: str) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
        source_span=SourceSpan(extractor="marker", line_id=line_id),
        role_hints=[],
    )


def _evidence(lines: list[EvidenceLine]) -> DocumentEvidencePackage:
    return DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="exam.pdf",
        primary_extractor="marker",
        extractors_available=["marker"],
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=lines,
    )


def _write_evidence(package_dir: Path, evidence: DocumentEvidencePackage) -> None:
    path = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")


def test_azure_provider_url_and_request_shape() -> None:
    provider = AzureOpenAIProvider(
        endpoint="https://myresource.openai.azure.com",
        api_key="azure-key",
        model="gpt-deploy",
        deployment="gpt-deploy",
        api_version="2024-02-15-preview",
        max_retries=0,
    )
    assert provider._build_url() == (
        "https://myresource.openai.azure.com/openai/deployments/gpt-deploy/"
        "chat/completions?api-version=2024-02-15-preview"
    )

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {"choices": [{"message": {"content": '{"items": [], "metadata_candidates": []}'}}]},
    ).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
        provider.generate_json("prompt", "schema")

    request = mock_open.call_args[0][0]
    assert request.get_header("Api-key") == "azure-key"
    assert provider.last_request_body is not None
    assert provider.last_request_body["temperature"] == 0


def test_openai_compatible_full_url_mode() -> None:
    provider = OpenAICompatibleProvider(
        endpoint="https://api.example.com/v1/chat/completions",
        api_key="key",
        model="gpt-4",
        provider_name="openai-compatible",
        url_mode="full_url",
        max_retries=0,
    )
    assert provider._build_url() == "https://api.example.com/v1/chat/completions"

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {"choices": [{"message": {"content": '{"items": []}'}}]},
    ).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
        provider.generate_json("p", "s")

    request = mock_open.call_args[0][0]
    assert request.get_header("Authorization") == "Bearer key"


def test_azure_provider_missing_api_version_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERITRANKER_BINDER_PROVIDER", "azure-openai")
    monkeypatch.setenv("MERITRANKER_BINDER_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("MERITRANKER_BINDER_API_KEY", "k")
    monkeypatch.setenv("MERITRANKER_BINDER_MODEL", "dep")
    monkeypatch.delenv("MERITRANKER_BINDER_API_VERSION", raising=False)
    with pytest.raises(LlmProviderError, match="MERITRANKER_BINDER_API_VERSION"):
        resolve_llm_provider(provider="azure-openai")


def test_pipeline_runner_passes_include_answer_key_evidence(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    package_dir.mkdir(parents=True)
    evidence_path = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        _evidence([_line("l1", "**1.** Q")]).model_dump_json(indent=2),
        encoding="utf-8",
    )

    captured: dict = {}

    def _capture_bind(*args, **kwargs):
        captured.update(kwargs)
        from meritranker_data_ingestion.schemas.semantic_binding import (
            SemanticBindingPackage,
            SemanticBindingStatus,
            SemanticBindingValidationReport,
        )
        from meritranker_data_ingestion.services.semantic_binder import SemanticBindingResult

        pkg = SemanticBindingPackage(
            package_version="1.0",
            source_file_name="exam.pdf",
            binder_provider="mock",
            binder_model="m",
            answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
            input_evidence_hash="x",
            status=SemanticBindingStatus.SUCCEEDED,
            validation_summary=SemanticBindingValidationReport(total_items=1, accepted_count=1),
        )
        return SemanticBindingResult(
            package=pkg,
            output_path=package_dir / "semantic-binding" / SEMANTIC_BOUND_QUESTIONS_NAME,
            validation_path=package_dir / "semantic-binding" / "validation.json",
            report_path=package_dir / "semantic-binding" / "report.json",
            evaluation_path=package_dir / "semantic-binding" / SEMANTIC_BINDING_EVALUATION_JSON_NAME,
        )

    with patch(
        "meritranker_data_ingestion.services.pipeline_runner.MarkerAdapter",
    ) as mock_marker, patch(
        "meritranker_data_ingestion.services.pipeline_runner.bind_semantically_package",
        side_effect=_capture_bind,
    ), patch(
        "meritranker_data_ingestion.services.pipeline_runner.classify_package",
    ) as mock_classify, patch(
        "meritranker_data_ingestion.services.pipeline_runner.parse_question_candidates_package",
    ) as mock_parse, patch(
        "meritranker_data_ingestion.services.pipeline_runner.map_answers_solutions_package",
    ) as mock_map, patch(
        "meritranker_data_ingestion.services.pipeline_runner.build_final_package_from_directory",
    ) as mock_final, patch(
        "meritranker_data_ingestion.services.pipeline_runner.export_review_items_package",
    ) as mock_review:
        from meritranker_data_ingestion.schemas.answer_solution_mapping import MapperStatus
        from meritranker_data_ingestion.schemas.extraction import ExtractionStatus
        from meritranker_data_ingestion.schemas.classification import ClassificationStatus
        from meritranker_data_ingestion.schemas.final_question_package import FinalizeStatus
        from meritranker_data_ingestion.schemas.question_candidates import ParseStatus
        from meritranker_data_ingestion.schemas.review_export import ReviewExportReport

        manifest = MagicMock()
        manifest.status = ExtractionStatus.SUCCEEDED
        manifest.source_file_name = "exam.pdf"
        manifest.errors = []
        mock_marker.return_value.extract.return_value = manifest

        mock_classify.return_value.status = ClassificationStatus.SUCCEEDED
        mock_classify.return_value.content_question_anchor_count = 1
        mock_classify.return_value.errors = []

        mock_parse.return_value.status = ParseStatus.SUCCEEDED
        mock_parse.return_value.total_candidates = 1
        mock_parse.return_value.valid_candidates = 1
        mock_parse.return_value.errors = []

        mock_map.return_value.status = MapperStatus.SUCCEEDED
        mock_map.return_value.mapped_count = 1
        mock_map.return_value.content_lines_used = True
        mock_map.return_value.errors = []

        final_report = MagicMock()
        final_report.status = FinalizeStatus.SUCCEEDED
        final_report.total_questions = 1
        final_report.validated_count = 1
        final_report.needs_review_count = 0
        final_report.incomplete_count = 0
        final_report.errors = []
        mock_final.return_value = (MagicMock(), final_report)

        mock_review.return_value = ReviewExportReport(
            package_dir=str(package_dir),
            review_item_count=0,
        )

        run_pipeline(
            PipelineOptions(
                input_pdf=tmp_path / "in.pdf",
                output_dir=tmp_path,
                force=True,
                stop_on_failure=False,
                skip_audit=True,
                skip_inspection=True,
                skip_diagnosis=True,
                normalize_evidence=False,
                force_semantic_binder=True,
                include_answer_key_evidence=False,
                semantic_binder_timeout_seconds=180,
            ),
        )

    assert captured["include_answer_key_evidence"] is False
    assert captured["timeout_seconds"] == 180


def test_evaluate_command_reads_existing_artifacts(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [_line("l1", "**1.** Q"), _line("l2", "- **A** 1")]
    _write_evidence(package_dir, _evidence(lines))
    bind_semantically_package(package_dir, llm_provider=MockLlmProvider(), force=True)

    result = evaluate_semantic_binding_package(package_dir, expected_count=5)
    assert result.evaluation_path.exists()
    data = json.loads(result.evaluation_path.read_text(encoding="utf-8"))
    assert data["semantic_item_count"] >= 1
    assert "deterministic_comparison" in data or data.get("deterministic_comparison") is None


def test_strict_semantic_quality_fails_on_low_item_count() -> None:
    validation = SemanticBindingValidationReport(
        total_items=10,
        accepted_count=10,
        hallucination_suspected_count=0,
        source_span_missing_count=0,
    )
    from meritranker_data_ingestion.schemas.semantic_binding import SemanticBindingPackage

    package = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="x.pdf",
        binder_provider="mock",
        binder_model="m",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        input_evidence_hash="abc",
        items=[],
    )
    report = build_semantic_binding_evaluation(
        package,
        validation,
        expected_count=100,
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    )
    assert report.quality_status in {"warning", "failed"}
    assert report.threshold_violations


def test_passed_quality_when_thresholds_met() -> None:
    from meritranker_data_ingestion.schemas.semantic_binding import (
        SemanticBoundAnswer,
        SemanticBoundOption,
        SemanticBoundQuestion,
    )

    items = []
    for i in range(1, 101):
        items.append(
            SemanticBoundQuestion(
                semantic_question_id=f"sq_{i}",
                question_number=i,
                question_text_raw=f"Q{i}",
                raw_text=f"Q{i}",
                options=[
                    SemanticBoundOption(
                        key=k,
                        key_raw=k,
                        text_raw=str(j),
                        source_spans=[SourceSpan(extractor="marker", line_id=f"l{i}_{k}")],
                    )
                    for j, k in enumerate(["A", "B", "C", "D"])
                ],
                answer=SemanticBoundAnswer(
                    available=True,
                    key="A",
                    source_spans=[SourceSpan(extractor="marker", line_id=f"ak{i}")],
                ),
                source_spans=[SourceSpan(extractor="marker", line_id=f"l{i}")],
            ),
        )

    validation = SemanticBindingValidationReport(
        total_items=100,
        accepted_count=100,
        hallucination_suspected_count=0,
        source_span_missing_count=0,
        answer_key_not_in_options_count=0,
    )
    from meritranker_data_ingestion.schemas.semantic_binding import SemanticBindingPackage

    package = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="x.pdf",
        binder_provider="mock",
        binder_model="m",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        input_evidence_hash="abc",
        items=items,
    )
    report = build_semantic_binding_evaluation(
        package,
        validation,
        expected_count=100,
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    )
    assert report.quality_status == "passed"
    assert not report.threshold_violations


def test_deterministic_vs_semantic_comparison_fields(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [_line("l1", "**1.** Q"), _line("l2", "- **A** 1")]
    _write_evidence(package_dir, _evidence(lines))

    q_report_dir = package_dir / QUESTIONS_DIR
    q_report_dir.mkdir(parents=True)
    q_report_dir.joinpath(QUESTION_CANDIDATE_REPORT_NAME).write_text(
        json.dumps(
            {
                "total_candidates": 85,
                "valid_candidates": 0,
                "candidates_with_no_options": 83,
            },
        ),
        encoding="utf-8",
    )

    bind_semantically_package(package_dir, llm_provider=MockLlmProvider(), force=True)
    result = evaluate_semantic_binding_package(package_dir, expected_count=100)
    comp = json.loads(result.evaluation_path.read_text(encoding="utf-8"))["deterministic_comparison"]
    assert comp["deterministic_total_candidates"] == 85
    assert comp["deterministic_valid_candidates"] == 0
    assert comp["deterministic_no_options_count"] == 83
    assert "improvement_summary" in comp


def test_cached_result_avoids_provider_call(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [_line("l1", "**1.** Q"), _line("l2", "- **A** 1")]
    _write_evidence(package_dir, _evidence(lines))
    provider = MockLlmProvider()
    bind_semantically_package(package_dir, llm_provider=provider, force=True)
    first = len(provider.calls)
    bind_semantically_package(package_dir, llm_provider=provider, force=False)
    assert len(provider.calls) == first


def test_warning_quality_when_close_but_not_met() -> None:
    from meritranker_data_ingestion.schemas.semantic_binding import (
        SemanticBindingPackage,
        SemanticBoundAnswer,
        SemanticBoundOption,
        SemanticBoundQuestion,
    )

    items = [
        SemanticBoundQuestion(
            semantic_question_id=f"sq_{i}",
            question_number=i,
            question_text_raw=f"Q{i}",
            raw_text=f"Q{i}",
            options=[
                SemanticBoundOption(
                    key="A",
                    key_raw="A",
                    text_raw="1",
                    source_spans=[SourceSpan(extractor="marker", line_id=f"l{i}")],
                ),
            ],
            answer=SemanticBoundAnswer(available=True, key="A"),
            source_spans=[SourceSpan(extractor="marker", line_id=f"l{i}")],
        )
        for i in range(1, 97)
    ]
    validation = SemanticBindingValidationReport(
        total_items=96,
        accepted_count=90,
        review_required_count=6,
        hallucination_suspected_count=0,
        source_span_missing_count=0,
        answer_key_not_in_options_count=1,
    )
    package = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="x.pdf",
        binder_provider="mock",
        binder_model="m",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        input_evidence_hash="abc",
        items=items,
    )
    report = build_semantic_binding_evaluation(
        package,
        validation,
        expected_count=100,
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        thresholds=SemanticBindingQualityThresholds(min_semantic_item_ratio=0.95),
    )
    assert report.quality_status == "warning"
    assert any("questions_with_4_options" in v for v in report.threshold_violations)
