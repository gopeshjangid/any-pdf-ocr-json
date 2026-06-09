"""Tests for Part 13D semantic binder hardening and evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    SEMANTIC_BINDING_EVALUATION_JSON_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    RoleHint,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
    SemanticBoundSolution,
)
from meritranker_data_ingestion.services.answer_key_evidence_extractor import (
    extract_answer_key_candidates,
)
from meritranker_data_ingestion.services.llm_provider import (
    LlmProviderError,
    MockLlmProvider,
    OpenAICompatibleProvider,
    resolve_llm_provider,
)
from meritranker_data_ingestion.services.semantic_binder import bind_semantically_package
from meritranker_data_ingestion.services.semantic_binding_evaluation import (
    build_semantic_binding_evaluation,
)
from meritranker_data_ingestion.services.semantic_binding_validator import validate_semantic_items


def _line(line_id: str, text: str, hints: list[RoleHint] | None = None) -> EvidenceLine:
    return EvidenceLine(
        line_id=line_id,
        text_raw=text,
        normalized_preview=text,
        source_extractor="marker",
        source_span=SourceSpan(extractor="marker", line_id=line_id),
        role_hints=hints or [],
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


def test_compact_answer_key_line_extracts_three_candidates() -> None:
    lines = [
        _line("marker_l_000100", "1.A 2.B 3.C", [RoleHint.ANSWER_KEY_CANDIDATE]),
    ]
    candidates = extract_answer_key_candidates(lines)
    assert len(candidates) == 3
    assert candidates[0].question_number == 1
    assert candidates[0].answer_key == "A"
    assert candidates[2].question_number == 3
    assert candidates[2].answer_key == "C"


def test_multiline_answer_key_table_style() -> None:
    lines = [
        _line("marker_l_000200", "Answer Key", [RoleHint.SOLUTION_HEADING_CANDIDATE]),
        _line("marker_l_000201", "1 - A    2 - B"),
        _line("marker_l_000202", "3 - C    4 - D"),
    ]
    candidates = extract_answer_key_candidates(lines)
    keys = {c.question_number: c.answer_key for c in candidates}
    assert keys.get(1) == "A"
    assert keys.get(4) == "D"


def test_mock_binder_applies_answer_key_evidence(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [
        _line("marker_l_000001", "**1.** Question one?", [RoleHint.QUESTION_ANCHOR_CANDIDATE]),
        _line("marker_l_000002", "- **A** 50", [RoleHint.OPTION_LABEL_CANDIDATE]),
        _line("marker_l_000003", "- **B** 67", [RoleHint.OPTION_LABEL_CANDIDATE]),
        _line("marker_l_000004", "- **C** 52", [RoleHint.OPTION_LABEL_CANDIDATE]),
        _line("marker_l_000005", "- **D** 63", [RoleHint.OPTION_LABEL_CANDIDATE]),
        _line("marker_l_000100", "1.A", [RoleHint.ANSWER_KEY_CANDIDATE]),
    ]
    _write_evidence(package_dir, _evidence(lines))

    result = bind_semantically_package(
        package_dir,
        llm_provider=MockLlmProvider(),
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        force=True,
        include_answer_key_evidence=True,
    )

    item = result.package.items[0]
    assert item.answer.available
    assert item.answer.key == "A"
    assert item.answer.source_spans[0].line_id == "marker_l_000100"


def test_evaluation_report_counts_four_options(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [
        _line("marker_l_000001", "**1.** Q"),
        _line("marker_l_000002", "- **A** 1"),
        _line("marker_l_000003", "- **B** 2"),
        _line("marker_l_000004", "- **C** 3"),
        _line("marker_l_000005", "- **D** 4"),
    ]
    _write_evidence(package_dir, _evidence(lines))
    result = bind_semantically_package(
        package_dir,
        llm_provider=MockLlmProvider(),
        force=True,
    )
    eval_report = json.loads(
        (package_dir / "semantic-binding" / SEMANTIC_BINDING_EVALUATION_JSON_NAME).read_text(
            encoding="utf-8",
        ),
    )
    assert eval_report["questions_with_4_options_count"] >= 1


def test_evaluation_detects_missing_question_numbers() -> None:
    items = [
        SemanticBoundQuestion(
            semantic_question_id="sq_1",
            question_number=1,
            question_text_raw="Q1",
            raw_text="Q1",
            options=[
                SemanticBoundOption(
                    key="A",
                    key_raw="A",
                    text_raw="1",
                    source_spans=[SourceSpan(extractor="marker", line_id="l1")],
                ),
            ],
            source_spans=[SourceSpan(extractor="marker", line_id="l1")],
        ),
    ]
    evidence = _evidence([_line("l1", "**1.** Q1")])
    validation = validate_semantic_items(
        items,
        [],
        evidence,
        answer_mode=SemanticBinderAnswerMode.QUESTION_ONLY,
        expected_count=5,
    )
    evaluation = build_semantic_binding_evaluation(
        type("P", (), {"items": items, "binder_provider": "mock", "binder_model": "m", "warnings": [], "errors": []})(),
        validation,
        expected_count=5,
        chunk_count=1,
    )
    assert 2 in evaluation.missing_question_numbers


def test_openai_compatible_request_shape() -> None:
    provider = OpenAICompatibleProvider(
        endpoint="https://api.example.com/v1",
        api_key="test-key",
        model="gpt-test",
        timeout_seconds=30,
        max_retries=0,
    )
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {
            "choices": [
                {"message": {"content": '{"items": [], "metadata_candidates": []}'}},
            ],
        },
    ).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = provider.generate_json("test prompt", "semantic_binding_chunk")

    assert result == {"items": [], "metadata_candidates": []}
    assert provider.last_request_body is not None
    assert provider.last_request_body["temperature"] == 0
    assert provider.last_request_body["model"] == "gpt-test"
    assert provider.last_request_body["response_format"] == {"type": "json_object"}


def test_provider_missing_env_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MERITRANKER_BINDER_ENDPOINT", raising=False)
    monkeypatch.delenv("MERITRANKER_BINDER_API_KEY", raising=False)
    monkeypatch.delenv("MERITRANKER_BINDER_MODEL", raising=False)
    with pytest.raises(LlmProviderError, match="Missing semantic binder LLM configuration"):
        resolve_llm_provider(provider="openai-compatible")


def test_invalid_provider_json_fails_safely() -> None:
    provider = OpenAICompatibleProvider(
        endpoint="https://api.example.com/v1/chat/completions",
        api_key="key",
        model="m",
        max_retries=0,
    )
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {"choices": [{"message": {"content": "not json"}}]},
    ).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        with pytest.raises(LlmProviderError, match="not valid JSON"):
            provider.generate_json("p", "s")


def test_prompt_includes_answer_key_evidence_when_enabled(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [
        _line("marker_l_000001", "**1.** Q"),
        _line("marker_l_000100", "1.A 2.B", [RoleHint.ANSWER_KEY_CANDIDATE]),
    ]
    _write_evidence(package_dir, _evidence(lines))
    provider = MockLlmProvider()

    bind_semantically_package(
        package_dir,
        llm_provider=provider,
        force=True,
        include_answer_key_evidence=True,
    )

    assert provider.calls
    prompt = provider.calls[0][0]
    assert "ANSWER_KEY_EVIDENCE_JSON:" in prompt


def test_eval_only_regenerates_evaluation(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [
        _line("marker_l_000001", "**1.** Q"),
        _line("marker_l_000002", "- **A** 1"),
    ]
    _write_evidence(package_dir, _evidence(lines))
    bind_semantically_package(package_dir, llm_provider=MockLlmProvider(), force=True)

    result = bind_semantically_package(
        package_dir,
        eval_only=True,
        expected_count=10,
    )
    assert result.from_cache
    assert result.evaluation_path.exists()


def test_cached_result_avoids_provider_call(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    lines = [_line("marker_l_000001", "**1.** Q"), _line("marker_l_000002", "- **A** 1")]
    _write_evidence(package_dir, _evidence(lines))
    provider = MockLlmProvider()

    bind_semantically_package(package_dir, llm_provider=provider, force=True)
    first_calls = len(provider.calls)
    bind_semantically_package(package_dir, llm_provider=provider, force=False)
    assert len(provider.calls) == first_calls
