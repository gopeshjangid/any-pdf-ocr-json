"""Import smoke tests — catch config constant regressions early."""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from meritranker_data_ingestion.config import (
    AZURE_DI_CONTENT_MD_NAME,
    AZURE_DI_DIR,
    AZURE_DI_EXTRACTION_LOG_NAME,
    AZURE_DI_FIGURES_NAME,
    AZURE_DI_LAYOUT_RESPONSE_NAME,
    AZURE_DI_LINES_NAME,
    AZURE_DI_PAGES_NAME,
    AZURE_DI_PARAGRAPHS_NAME,
    AZURE_DI_TABLES_NAME,
    BINDER_API_KEY_ENV,
    BINDER_PROVIDER_ENV,
    DOCUMENT_EVIDENCE_JSON_NAME,
    EXTRACTOR_MANIFEST_NAME,
    EXTRACTORS_DIR,
    SEMANTIC_BINDING_EVALUATION_JSON_NAME,
    SEMANTIC_BINDING_DIR,
    SEMANTIC_BOUND_QUESTIONS_NAME,
)


@pytest.mark.parametrize(
    "module_name",
    [
        "meritranker_data_ingestion.cli",
        "meritranker_data_ingestion.config",
        "meritranker_data_ingestion.services.extraction_package",
        "meritranker_data_ingestion.services.azure_document_intelligence_adapter",
        "meritranker_data_ingestion.services.document_evidence_normalizer",
        "meritranker_data_ingestion.services.semantic_binder",
        "meritranker_data_ingestion.services.llm_provider",
        "meritranker_data_ingestion.env_loader",
    ],
)
def test_module_imports_cleanly(module_name: str) -> None:
    importlib.import_module(module_name)


def test_azure_di_artifact_constants() -> None:
    assert AZURE_DI_CONTENT_MD_NAME == "content.md"
    assert AZURE_DI_LAYOUT_RESPONSE_NAME == "layout-response.json"
    assert AZURE_DI_PAGES_NAME == "pages.json"
    assert AZURE_DI_LINES_NAME == "lines.json"
    assert AZURE_DI_TABLES_NAME == "tables.json"
    assert AZURE_DI_FIGURES_NAME == "figures.json"
    assert AZURE_DI_PARAGRAPHS_NAME == "paragraphs.json"
    assert AZURE_DI_EXTRACTION_LOG_NAME == "extraction-log.json"
    assert EXTRACTOR_MANIFEST_NAME == "extractor-manifest.json"
    assert AZURE_DI_DIR == "azure-di"
    assert EXTRACTORS_DIR == "extractors"


def test_semantic_binding_constants() -> None:
    assert SEMANTIC_BINDING_DIR == "semantic-binding"
    assert SEMANTIC_BOUND_QUESTIONS_NAME == "semantic-bound-questions.json"
    assert SEMANTIC_BINDING_EVALUATION_JSON_NAME == "semantic-binding-evaluation.json"


def test_binder_env_constants_exist() -> None:
    assert BINDER_PROVIDER_ENV
    assert BINDER_API_KEY_ENV
    assert DOCUMENT_EVIDENCE_JSON_NAME == "document-evidence.json"


@pytest.mark.parametrize(
    "snippet",
    [
        "import meritranker_data_ingestion.cli; print('cli import ok')",
        (
            "import meritranker_data_ingestion.services.extraction_package; "
            "print('extraction_package import ok')"
        ),
        "import meritranker_data_ingestion.services.semantic_binder; print('semantic_binder import ok')",
    ],
)
def test_cli_import_subprocess(snippet: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
