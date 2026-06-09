"""Tests for Part 14A OCR evidence + final questions export."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    EXTRACTION_PACKAGE_DIR,
    MERGED_DOCUMENT_EVIDENCE_JSON_NAME,
    OCR_DIR,
    OCR_EVIDENCE_JSON_NAME,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import (
    AlternateEvidence,
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.ocr_evidence import OcrEvidencePackage, OcrLine
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingItemStatus,
    SemanticBindingStatus,
    SemanticBoundAnswer,
    SemanticBoundOption,
    SemanticBoundQuestion,
    SemanticBindingPackage,
)
from meritranker_data_ingestion.services.evidence_merger import merge_evidence_package
from meritranker_data_ingestion.services.evidence_resolver import (
    load_document_evidence,
    resolve_document_evidence_path,
    select_binding_lines,
)
from meritranker_data_ingestion.services.extraction_capability_router import (
    profile_extraction_capability,
)
from meritranker_data_ingestion.services.final_questions_export_builder import (
    build_final_questions_export,
)
from meritranker_data_ingestion.services.llm_provider import (
    AzureOpenAIProvider,
    LlmProviderError,
)
from meritranker_data_ingestion.services.ocr_role_hints import parse_chosen_option_key
from meritranker_data_ingestion.services.paddle_ocr_adapter import PaddleOcrAdapter
from meritranker_data_ingestion.schemas.ocr_evidence import OcrEvidencePackage as OcrPkg
from meritranker_data_ingestion.services.azure_ocr_adapter import AzureOcrAdapter


def _write_doc_evidence(tmp_path: Path, lines: list[EvidenceLine]) -> Path:
    pkg = tmp_path / EXTRACTION_PACKAGE_DIR
    ev = pkg / EVIDENCE_DIR
    ev.mkdir(parents=True, exist_ok=True)
    package = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="test.pdf",
        primary_extractor="marker",
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=lines,
    )
    path = ev / DOCUMENT_EVIDENCE_JSON_NAME
    path.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    return pkg


def _span(line_id: str) -> list[SourceSpan]:
    return [SourceSpan(extractor="marker", line_id=line_id)]


def test_ocr_evidence_schema_generation() -> None:
    package = OcrPkg(
        source_file_name="exam.pdf",
        ocr_engines_used=["azure_ocr"],
        lines=[
            OcrLine(
                line_id="azure_ocr_l_000001",
                page_number=1,
                text="1. प्लाज्मा",
                normalized_text="1. प्लाज्मा",
                confidence=0.95,
                engine="azure_ocr",
            ),
        ],
    )
    data = json.loads(package.model_dump_json())
    assert data["lines"][0]["engine"] == "azure_ocr"


def test_azure_ocr_adapter_mocked_from_artifacts(tmp_path: Path) -> None:
    pkg = _write_doc_evidence(
        tmp_path,
        [EvidenceLine(
            line_id="marker_l_1",
            text_raw="Q1",
            normalized_preview="Q1",
            source_extractor="marker",
        )],
    )
    azure_dir = pkg / "extractors" / "azure-di"
    azure_dir.mkdir(parents=True, exist_ok=True)
    (azure_dir / "lines.json").write_text(
        json.dumps([{"content": "1. प्लाज्मा", "pageNumber": 1}]),
        encoding="utf-8",
    )
    adapter = AzureOcrAdapter(endpoint=None, api_key=None)
    result = adapter.extract(pkg)
    assert result.lines
    assert result.lines[0].text == "1. प्लाज्मा"


def test_paddle_missing_dependency_handled() -> None:
    adapter = PaddleOcrAdapter()
    with patch.object(adapter, "is_available", return_value=False):
        result = adapter.extract(Path("/tmp/unused"))
    assert "paddle_ocr_not_installed" in result.warnings


def test_marker_blank_options_recovered_by_merge(tmp_path: Path) -> None:
    pkg = _write_doc_evidence(
        tmp_path,
        [
            EvidenceLine(
                line_id="marker_l_1",
                page_number=1,
                text_raw="1.",
                normalized_preview="1.",
                source_extractor="marker",
                source_span=SourceSpan(extractor="marker", line_id="marker_l_1", page_number=1),
            ),
        ],
    )
    ocr_dir = pkg / OCR_DIR
    ocr_dir.mkdir(parents=True, exist_ok=True)
    ocr_pkg = OcrEvidencePackage(
        source_file_name="test.pdf",
        ocr_engines_used=["azure_ocr"],
        lines=[
            OcrLine(
                line_id="azure_ocr_l_000001",
                page_number=1,
                text="1. प्लाज्मा",
                normalized_text="1. प्लाज्मा",
                confidence=0.9,
                engine="azure_ocr",
            ),
        ],
    )
    (ocr_dir / OCR_EVIDENCE_JSON_NAME).write_text(
        ocr_pkg.model_dump_json(indent=2),
        encoding="utf-8",
    )
    result = merge_evidence_package(pkg)
    marker_line = result.package.lines[0]
    assert marker_line.alternate_evidence
    assert "प्लाज्मा" in marker_line.alternate_evidence[0].text_raw
    assert result.summary["ocr_option_recoveries"] == 1


def test_merged_evidence_preserves_marker_and_ocr_sources(tmp_path: Path) -> None:
    pkg = _write_doc_evidence(
        tmp_path,
        [
            EvidenceLine(
                line_id="marker_l_1",
                page_number=1,
                text_raw="Question stem",
                normalized_preview="Question stem",
                source_extractor="marker",
            ),
        ],
    )
    ocr_dir = pkg / OCR_DIR
    ocr_dir.mkdir(parents=True, exist_ok=True)
    (ocr_dir / OCR_EVIDENCE_JSON_NAME).write_text(
        OcrEvidencePackage(
            source_file_name="test.pdf",
            ocr_engines_used=["azure_ocr"],
            lines=[
                OcrLine(
                    line_id="azure_ocr_l_000099",
                    page_number=1,
                    text="Unique OCR only line",
                    normalized_text="Unique OCR only line",
                    confidence=0.8,
                    engine="azure_ocr",
                ),
            ],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    result = merge_evidence_package(pkg)
    extractors = {line.source_extractor for line in result.package.lines}
    assert "marker" in extractors
    assert "azure_ocr" in extractors


def test_semantic_binder_uses_merged_evidence(tmp_path: Path) -> None:
    pkg = _write_doc_evidence(
        tmp_path,
        [
            EvidenceLine(
                line_id="marker_l_1",
                text_raw="m",
                normalized_preview="m",
                source_extractor="marker",
            ),
        ],
    )
    merged_path = pkg / EVIDENCE_DIR / MERGED_DOCUMENT_EVIDENCE_JSON_NAME
    merged = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="test.pdf",
        primary_extractor="marker",
        extractors_used=["marker", "azure_ocr"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=[
            EvidenceLine(
                line_id="marker_l_1",
                text_raw="m",
                normalized_preview="m",
                source_extractor="marker",
            ),
            EvidenceLine(
                line_id="azure_ocr_l_1",
                text_raw="ocr extra",
                normalized_preview="ocr extra",
                source_extractor="azure_ocr",
            ),
        ],
    )
    merged_path.write_text(merged.model_dump_json(indent=2), encoding="utf-8")

    evidence, path = load_document_evidence(pkg)
    lines = select_binding_lines(evidence, path)
    assert path.name == MERGED_DOCUMENT_EVIDENCE_JSON_NAME
    assert len(lines) == 2


def test_chosen_option_not_used_as_correct_answer(tmp_path: Path) -> None:
    pkg = _write_doc_evidence(
        tmp_path,
        [
            EvidenceLine(
                line_id="marker_l_1",
                text_raw="**1.** Question?",
                normalized_preview="1. Question?",
                source_extractor="marker",
            ),
            EvidenceLine(
                line_id="marker_l_2",
                text_raw="Chosen Option : 2",
                normalized_preview="Chosen Option : 2",
                source_extractor="marker",
            ),
        ],
    )
    merge_evidence_package(pkg)
    profile = profile_extraction_capability(pkg)
    assert profile.profile.chosen_option_detected is True
    assert profile.recommended_answer_mode == SemanticBinderAnswerMode.QUESTION_ONLY
    assert parse_chosen_option_key("Chosen Option : 2") == "2"


def test_visual_blank_routes_visual_required_in_final_export(tmp_path: Path) -> None:
    pkg = _write_binding_pkg(
        tmp_path,
        [
            SemanticBoundQuestion(
                semantic_question_id="sq_0001",
                question_number=1,
                question_text_raw="**1.** Select the figure in the series.",
                raw_text="**1.** Select the figure in the series.",
                options=[
                    SemanticBoundOption(key="", key_raw="", text_raw="", source_spans=[]),
                ] * 4,
                answer=SemanticBoundAnswer(available=True, key="A", key_raw="A", source_spans=[]),
                binding_status=SemanticBindingItemStatus.ACCEPTED,
            ),
        ],
    )
    merge_evidence_package(pkg)
    result = build_final_questions_export(pkg)
    item = result.package.items[0]
    assert item.quality_status.value == "visual_required"


def test_final_questions_includes_all_status_buckets(tmp_path: Path) -> None:
    pkg = _write_binding_pkg(
        tmp_path,
        [
            _good_mcq(1),
            SemanticBoundQuestion(
                semantic_question_id="sq_0002",
                question_number=2,
                question_text_raw="**2.** Figure question",
                raw_text="**2.** Figure question",
                options=[],
                answer=SemanticBoundAnswer(available=False, source_spans=[]),
                binding_status=SemanticBindingItemStatus.REJECTED,
                issues=["hallucinated_question_text"],
                bad_item_classes=["hallucination"],
            ),
        ],
    )
    merge_evidence_package(pkg)
    result = build_final_questions_export(pkg)
    assert result.package.total_questions_detected == 2
    statuses = {item.quality_status.value for item in result.package.items}
    assert "accepted_safe" in statuses
    assert "blocked" in statuses


def test_ocr_recovered_option_source_engine(tmp_path: Path) -> None:
    pkg = _write_doc_evidence(
        tmp_path,
        [
            EvidenceLine(
                line_id="marker_l_opt",
                page_number=1,
                text_raw="A.",
                normalized_preview="A.",
                source_extractor="marker",
                source_span=SourceSpan(extractor="marker", line_id="marker_l_opt", page_number=1),
                alternate_evidence=[
                    AlternateEvidence(
                        engine="azure_ocr",
                        text_raw="प्लाज्मा",
                        line_id="azure_ocr_l_1",
                        confidence=0.91,
                    ),
                ],
            ),
        ],
    )
    binding_pkg = _write_binding_pkg(
        pkg,
        [
            SemanticBoundQuestion(
                semantic_question_id="sq_0001",
                question_number=1,
                question_text_raw="**1.** Q?",
                raw_text="**1.** Q?",
                options=[
                    SemanticBoundOption(
                        key="A",
                        key_raw="A",
                        text_raw="",
                        source_spans=_span("marker_l_opt"),
                    ),
                    SemanticBoundOption(key="B", key_raw="B", text_raw="two", source_spans=_span("b")),
                    SemanticBoundOption(key="C", key_raw="C", text_raw="three", source_spans=_span("c")),
                    SemanticBoundOption(key="D", key_raw="D", text_raw="four", source_spans=_span("d")),
                ],
                answer=SemanticBoundAnswer(
                    available=True,
                    key="A",
                    key_raw="A",
                    source_spans=_span("a"),
                ),
                source_spans=_span("q"),
                binding_status=SemanticBindingItemStatus.ACCEPTED,
            ),
        ],
    )
    result = build_final_questions_export(binding_pkg)
    opt_a = result.package.items[0].options[0]
    assert opt_a.text_raw == "प्लाज्मा"
    assert opt_a.source_engine == "azure_ocr"


def test_provider_remote_disconnected_clean_error() -> None:
    provider = AzureOpenAIProvider(
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        model="deployment",
        api_version="2024-02-01",
        max_retries=0,
        timeout_seconds=5,
    )

    def _raise_remote(*_args, **_kwargs):
        raise http.client.RemoteDisconnected("connection dropped")

    with patch("urllib.request.urlopen", side_effect=_raise_remote):
        with pytest.raises(LlmProviderError) as exc_info:
            provider.generate_json("test", "schema")
    assert "connection dropped" in str(exc_info.value).lower() or "RemoteDisconnected" in str(exc_info.value)


def _good_mcq(qnum: int) -> SemanticBoundQuestion:
    opts = [
        SemanticBoundOption(
            key=k,
            key_raw=k,
            text_raw=f"opt {k}",
            source_spans=_span(f"lo_{k}"),
        )
        for k in ("A", "B", "C", "D")
    ]
    return SemanticBoundQuestion(
        semantic_question_id=f"sq_{qnum:04d}",
        question_number=qnum,
        question_text_raw=f"**{qnum}.** What is the answer?",
        raw_text=f"**{qnum}.** What is the answer?",
        options=opts,
        answer=SemanticBoundAnswer(
            available=True,
            key="B",
            key_raw="B",
            source_spans=_span("la"),
        ),
        source_spans=_span("lq"),
        binding_status=SemanticBindingItemStatus.ACCEPTED,
    )


def _package_dir(base: Path) -> Path:
    if (base / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME).exists():
        return base
    return base / EXTRACTION_PACKAGE_DIR


def _write_binding_pkg(base: Path, items: list[SemanticBoundQuestion]) -> Path:
    pkg = _package_dir(base)
    sem = pkg / SEMANTIC_BINDING_DIR
    sem.mkdir(parents=True, exist_ok=True)
    package = SemanticBindingPackage(
        package_version="1.0",
        source_file_name="test.pdf",
        binder_provider="mock",
        binder_model="mock",
        answer_mode=SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
        status=SemanticBindingStatus.SUCCEEDED,
        input_evidence_hash="test-hash",
        items=items,
    )
    (sem / SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME).write_text(
        package.model_dump_json(indent=2),
        encoding="utf-8",
    )
    if not (pkg / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME).exists():
        _write_doc_evidence(base, [])
    return pkg
