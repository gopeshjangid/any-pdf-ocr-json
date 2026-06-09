"""Tests for LLM provider URL modes, preflight, and .env loading."""

from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meritranker_data_ingestion.config import (
    BINDER_API_KEY_ENV,
    BINDER_ENDPOINT_ENV,
    BINDER_MODEL_ENV,
    BINDER_PROVIDER_ENV,
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
)
from meritranker_data_ingestion.env_loader import load_dotenv_if_present
from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceExtractionStatus,
    EvidenceLine,
    SourceSpan,
)
from meritranker_data_ingestion.services.llm_provider import (
    AzureOpenAIProvider,
    LlmProviderError,
    LlmProviderPreflightError,
    MockLlmProvider,
    OpenAICompatibleProvider,
    resolve_llm_provider,
    probe_llm_provider,
)
from meritranker_data_ingestion.services.semantic_binder import bind_semantically_package


def test_env_loader_loads_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MERITRANKER_BINDER_MODEL=from-dotenv\nMERITRANKER_BINDER_API_KEY=secret-key\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MERITRANKER_BINDER_MODEL", raising=False)
    loaded = load_dotenv_if_present()
    assert loaded == env_file.resolve()
    assert os.environ.get("MERITRANKER_BINDER_MODEL") == "from-dotenv"


def test_shell_env_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MERITRANKER_BINDER_MODEL=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MERITRANKER_BINDER_MODEL", "from-shell")
    load_dotenv_if_present()
    assert os.environ.get("MERITRANKER_BINDER_MODEL") == "from-shell"


def test_missing_dotenv_is_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_dotenv_if_present() is None


def test_azure_stable_url_construction() -> None:
    provider = AzureOpenAIProvider(
        endpoint="https://myresource.openai.azure.com",
        api_key="key",
        model="my-deployment",
        deployment="my-deployment",
        api_version="2024-10-21",
        provider_name="azure-openai",
        url_mode="azure_deployment",
    )
    assert provider._build_url() == (
        "https://myresource.openai.azure.com/openai/deployments/my-deployment/"
        "chat/completions?api-version=2024-10-21"
    )
    diag = provider.describe_sanitized()
    assert diag["mode"] == "azure_deployment"
    assert diag["deployment_or_model"] == "my-deployment"
    assert "api-key" not in json.dumps(diag)


def test_azure_endpoint_normalizes_accidental_deployment_path() -> None:
    provider = AzureOpenAIProvider(
        endpoint=(
            "https://myresource.openai.azure.com/openai/deployments/wrong-deploy/"
            "chat/completions?api-version=2024-10-21"
        ),
        api_key="key",
        model="correct-deploy",
        api_version="2024-10-21",
        provider_name="azure-openai",
        url_mode="azure_deployment",
    )
    assert "correct-deploy" in provider._build_url()
    assert "wrong-deploy" not in provider._build_url()


def test_azure_full_url_mode() -> None:
    full_url = (
        "https://myresource.openai.azure.com/openai/deployments/dep/chat/completions"
        "?api-version=2024-10-21"
    )
    provider = AzureOpenAIProvider(
        endpoint="https://myresource.openai.azure.com",
        api_key="key",
        model="dep",
        api_version="2024-10-21",
        provider_name="azure-openai",
        url_mode="full_url",
        chat_completions_url=full_url,
    )
    assert provider._build_url() == full_url
    assert provider.describe_sanitized()["mode"] == "full_url"


def test_openai_compatible_full_url_mode() -> None:
    provider = OpenAICompatibleProvider(
        endpoint="https://api.example.com/v1/chat/completions",
        api_key="key",
        model="gpt-4",
        provider_name="openai-compatible",
        url_mode="full_url",
    )
    assert provider._build_url() == "https://api.example.com/v1/chat/completions"


def test_openai_compatible_base_url_appends_v1_path() -> None:
    provider = OpenAICompatibleProvider(
        endpoint="https://api.openai.com",
        api_key="key",
        model="gpt-4",
        provider_name="openai-compatible",
        url_mode="openai_compatible",
    )
    assert provider._build_url() == "https://api.openai.com/v1/chat/completions"


def _http_error(code: int, body: str) -> MagicMock:
    import urllib.error

    exc = urllib.error.HTTPError(
        url="https://example.com",
        code=code,
        msg="error",
        hdrs=None,
        fp=BytesIO(body.encode("utf-8")),
    )
    return exc


def test_preflight_404_fails_with_clear_error() -> None:
    provider = AzureOpenAIProvider(
        endpoint="https://myresource.openai.azure.com",
        api_key="super-secret-key",
        model="dep",
        api_version="2024-10-21",
        provider_name="azure-openai",
        url_mode="azure_deployment",
        max_retries=0,
    )
    with patch("urllib.request.urlopen", side_effect=_http_error(404, '{"error":{"code":"404"}}')):
        with pytest.raises(LlmProviderPreflightError) as exc_info:
            provider.preflight()
    assert "404" in str(exc_info.value)
    assert "super-secret-key" not in str(exc_info.value)
    assert exc_info.value.diagnostics["mode"] == "azure_deployment"


def test_preflight_401_has_clear_error() -> None:
    provider = OpenAICompatibleProvider(
        endpoint="https://api.openai.com",
        api_key="secret",
        model="gpt-4",
        provider_name="openai-compatible",
        max_retries=0,
    )
    with patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized")):
        with pytest.raises(LlmProviderPreflightError, match="401"):
            provider.preflight()


def test_preflight_success() -> None:
    provider = MockLlmProvider()
    result = provider.preflight()
    assert result["ok"] is True


def test_probe_llm_provider_mock() -> None:
    result = probe_llm_provider(provider="mock", model="mock")
    assert result["status"] == "succeeded"
    assert "diagnostics" in result


def test_bind_semantically_preflight_404_no_chunk_calls(tmp_path: Path) -> None:
    package_dir = tmp_path / "extraction_package"
    line = EvidenceLine(
        line_id="l1",
        text_raw="**1.** Q",
        normalized_preview="**1.** Q",
        source_extractor="marker",
        source_span=SourceSpan(extractor="marker", line_id="l1"),
        role_hints=[],
    )
    evidence = DocumentEvidencePackage(
        package_version="1.0",
        source_file_name="exam.pdf",
        primary_extractor="marker",
        extractors_available=["marker"],
        extractors_used=["marker"],
        extraction_status=EvidenceExtractionStatus.SUCCEEDED,
        lines=[line],
    )
    path = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")

    provider = AzureOpenAIProvider(
        endpoint="https://myresource.openai.azure.com",
        api_key="key",
        model="dep",
        api_version="2024-10-21",
        provider_name="azure-openai",
        url_mode="azure_deployment",
        max_retries=0,
    )

    with patch.object(provider, "generate_json") as mock_generate:
        with patch.object(provider, "preflight", side_effect=LlmProviderPreflightError("HTTP 404", diagnostics={})):
            result = bind_semantically_package(
                package_dir,
                llm_provider=provider,
                force=True,
            )
    mock_generate.assert_not_called()
    assert result.package.status.value == "failed"
    assert any("provider_preflight_failed" in e for e in result.package.errors)
    assert any("executed_chunk_count:0" in w for w in result.package.warnings)


def test_missing_azure_config_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BINDER_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(BINDER_API_KEY_ENV, raising=False)
    monkeypatch.delenv(BINDER_MODEL_ENV, raising=False)
    with pytest.raises(LlmProviderError, match="Missing semantic binder LLM configuration"):
        resolve_llm_provider(provider="azure-openai")


def test_cli_test_llm_provider_mock(capsys) -> None:
    from meritranker_data_ingestion.cli import main

    code = main(["test-llm-provider", "--provider", "mock", "--model", "mock"])
    assert code == 0
    out = capsys.readouterr().out
    assert "succeeded" in out
    assert "mock" in out
    assert "secret" not in out.lower() or "diagnostics" in out
