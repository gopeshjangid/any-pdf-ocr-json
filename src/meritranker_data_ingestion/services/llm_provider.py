"""LLM provider abstraction for semantic binding (mockable, env-configured)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from meritranker_data_ingestion.config import (
    BINDER_API_KEY_ENV,
    BINDER_API_VERSION_ENV,
    BINDER_CHAT_COMPLETIONS_URL_ENV,
    BINDER_ENDPOINT_ENV,
    BINDER_MODEL_ENV,
    BINDER_PROVIDER_ENV,
    BINDER_PROVIDER_AZURE_OPENAI,
    BINDER_PROVIDER_MOCK,
    BINDER_PROVIDER_OPENAI_COMPATIBLE,
    DEFAULT_BINDER_MAX_RETRIES,
    DEFAULT_BINDER_MODEL,
    DEFAULT_BINDER_PROVIDER,
    DEFAULT_BINDER_TIMEOUT_SECONDS,
)


class LlmProviderError(Exception):
    """Raised when LLM provider configuration or API calls fail."""


class LlmProviderPreflightError(LlmProviderError):
    """Raised when provider preflight fails before semantic binding chunks run."""

    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class LlmProvider(Protocol):
    """Protocol for structured JSON generation."""

    @property
    def provider_name(self) -> str:
        """Provider identifier for manifests."""

    @property
    def model_name(self) -> str:
        """Model identifier for manifests."""

    def generate_json(self, prompt: str, schema_name: str) -> dict[str, Any]:
        """Generate a JSON object from prompt."""

    def describe_sanitized(self) -> dict[str, Any]:
        """Return redacted provider diagnostics."""

    def preflight(self) -> dict[str, Any]:
        """Run a tiny connectivity check."""


def _normalize_azure_resource_endpoint(endpoint: str) -> str:
    """Strip accidental deployment paths from Azure resource endpoint."""
    base = endpoint.strip().rstrip("/")
    if "/openai/deployments/" in base:
        base = base.split("/openai/deployments/", maxsplit=1)[0]
    if base.endswith("/openai"):
        base = base[: -len("/openai")]
    return base.rstrip("/")


def _url_path_preview(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _sanitize_error_text(text: str, api_key: str) -> str:
    if not api_key:
        return text
    return text.replace(api_key, "***")


def _troubleshooting_for_status(
    status_code: int,
    *,
    provider_name: str,
    url_mode: str,
) -> str:
    if status_code in {401, 403}:
        return (
            "Check API key validity and deployment access permissions. "
            "For azure-openai, MERITRANKER_BINDER_MODEL must be the Azure deployment name."
        )
    if status_code == 404:
        return (
            "Resource not found. Check: (1) endpoint resource host is correct "
            "(https://<resource>.openai.azure.com), (2) deployment name matches Azure portal, "
            "(3) API version is supported, (4) deployment is in the same resource. "
            "Alternatively set MERITRANKER_BINDER_CHAT_COMPLETIONS_URL to the exact chat URL."
        )
    if status_code == 429:
        return "Rate limit or quota exceeded. Retry later or reduce chunk count."
    if status_code >= 500:
        return "Provider server error. Retry later."
    if provider_name == BINDER_PROVIDER_AZURE_OPENAI and url_mode == "azure_deployment":
        return (
            "For azure-openai, --model / MERITRANKER_BINDER_MODEL must be the Azure "
            "deployment name, not necessarily the base model name."
        )
    return "Verify provider configuration and network connectivity."


@dataclass
class MockLlmProvider:
    """Injected/mock provider for tests and offline runs."""

    provider_name: str = BINDER_PROVIDER_MOCK
    model_name: str = "mock-binder-v1"
    responses: list[dict[str, Any]] = field(default_factory=list)
    fail_on_call: Exception | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)
    last_request_body: dict[str, Any] | None = field(default=None, repr=False)
    last_request_url: str | None = field(default=None, repr=False)
    url_mode: str = "mock"

    def generate_json(self, prompt: str, schema_name: str) -> dict[str, Any]:
        self.calls.append((prompt, schema_name))
        if self.fail_on_call is not None:
            raise self.fail_on_call
        if self.responses:
            return self.responses.pop(0)
        return _rule_based_extract_from_prompt(prompt)

    def describe_sanitized(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "mode": self.url_mode,
            "deployment_or_model": self.model_name,
            "timeout_seconds": DEFAULT_BINDER_TIMEOUT_SECONDS,
            "max_retries": 0,
        }

    def preflight(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.provider_name, "mode": self.url_mode}


@dataclass
class _HttpJsonLlmProvider:
    """Shared HTTP request logic for OpenAI-compatible and Azure providers."""

    endpoint: str
    api_key: str
    model: str
    provider_name: str = ""
    timeout_seconds: int = DEFAULT_BINDER_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_BINDER_MAX_RETRIES
    url_mode: str = "openai_compatible"
    chat_completions_url: str | None = None
    api_version: str | None = None
    deployment: str | None = None
    last_request_body: dict[str, Any] | None = field(default=None, repr=False)
    last_request_url: str | None = field(default=None, repr=False)

    @property
    def model_name(self) -> str:
        return self.model

    def generate_json(self, prompt: str, schema_name: str) -> dict[str, Any]:
        url = self._build_url()
        self.last_request_url = url
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a source-grounded exam PDF semantic binder. "
                        "Return strict JSON only. Never invent text. Never solve questions."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        self.last_request_body = body
        payload = self._request_with_retries(url, body, self._auth_headers())
        content = _extract_message_content(payload)
        return _parse_json_content(content)

    def preflight(self) -> dict[str, Any]:
        url = self._build_url()
        self.last_request_url = url
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": 'Return {"ok": true}.'},
            ],
            "response_format": {"type": "json_object"},
        }
        self.last_request_body = body
        try:
            payload = self._request_with_retries(url, body, self._auth_headers(), preflight=True)
        except LlmProviderError as exc:
            raise LlmProviderPreflightError(
                str(exc),
                diagnostics=self.describe_sanitized(),
            ) from exc
        content = _extract_message_content(payload)
        parsed = _parse_json_content(content)
        return {"ok": True, "response": parsed, "diagnostics": self.describe_sanitized()}

    def describe_sanitized(self) -> dict[str, Any]:
        url = self._build_url()
        host = urlparse(self.endpoint).netloc or self.endpoint
        info: dict[str, Any] = {
            "provider": self.provider_name,
            "mode": self.url_mode,
            "endpoint_host": host,
            "deployment_or_model": self.deployment or self.model,
            "api_version": self.api_version,
            "url_path_preview": _url_path_preview(url),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
        }
        if self.url_mode == "full_url":
            info["chat_completions_url_set"] = True
        return info

    def _auth_headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _build_url(self) -> str:
        raise NotImplementedError

    def _request_with_retries(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str],
        *,
        preflight: bool = False,
    ) -> dict[str, Any]:
        import time
        import urllib.error
        import urllib.request

        last_error: Exception | None = None
        non_retryable = {401, 403, 404}
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = _sanitize_error_text(
                    exc.read().decode("utf-8", errors="replace")[:500],
                    self.api_key,
                )
                hints = _troubleshooting_for_status(
                    exc.code,
                    provider_name=self.provider_name,
                    url_mode=self.url_mode,
                )
                diag = json.dumps(self.describe_sanitized(), ensure_ascii=False)
                last_error = LlmProviderError(
                    f"LLM API HTTP {exc.code}: {detail}. "
                    f"Diagnostics: {diag}. {hints}",
                )
                if exc.code in non_retryable or attempt >= self.max_retries:
                    raise last_error from exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise last_error from exc
                time.sleep(min(2 ** attempt, 8))
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionResetError,
            ) as exc:
                reason = getattr(exc, "reason", str(exc))
                last_error = LlmProviderError(
                    f"LLM API connection failed: {reason}. "
                    f"Diagnostics: {json.dumps(self.describe_sanitized(), ensure_ascii=False)}",
                )
                if attempt >= self.max_retries:
                    raise last_error from exc
                time.sleep(min(2 ** attempt, 8))
            except Exception as exc:
                exc_name = type(exc).__name__
                if exc_name in {"RemoteDisconnected", "IncompleteRead"}:
                    last_error = LlmProviderError(
                        f"LLM API connection dropped: {exc_name}. "
                        f"Diagnostics: {json.dumps(self.describe_sanitized(), ensure_ascii=False)}",
                    )
                    if attempt >= self.max_retries:
                        raise last_error from exc
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise LlmProviderError("LLM API request failed after retries.")


@dataclass
class OpenAICompatibleProvider(_HttpJsonLlmProvider):
    """OpenAI-compatible chat completions endpoint."""

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _build_url(self) -> str:
        if self.chat_completions_url:
            return self.chat_completions_url.strip()
        base = self.endpoint.rstrip("/")
        if "chat/completions" in base:
            return base
        return f"{base}/v1/chat/completions"


@dataclass
class AzureOpenAIProvider(_HttpJsonLlmProvider):
    """Azure OpenAI deployment endpoint with api-key auth."""

    def __post_init__(self) -> None:
        if not self.deployment:
            self.deployment = self.model
        self.endpoint = _normalize_azure_resource_endpoint(self.endpoint)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

    def _build_url(self) -> str:
        if self.chat_completions_url:
            url = self.chat_completions_url.strip()
            if self.api_version and "api-version=" not in url:
                sep = "&" if "?" in url else "?"
                return f"{url}{sep}api-version={self.api_version}"
            return url
        deployment = self.deployment or self.model
        base = _normalize_azure_resource_endpoint(self.endpoint)
        return (
            f"{base}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={self.api_version}"
        )


def resolve_llm_provider(
    *,
    provider: str | None = None,
    model: str | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
    api_version: str | None = None,
    chat_completions_url: str | None = None,
    timeout_seconds: int = DEFAULT_BINDER_TIMEOUT_SECONDS,
) -> LlmProvider:
    """Resolve LLM provider from CLI args and environment."""
    resolved_provider = (
        provider or os.environ.get(BINDER_PROVIDER_ENV) or DEFAULT_BINDER_PROVIDER
    ).strip()
    resolved_model = (model or os.environ.get(BINDER_MODEL_ENV) or DEFAULT_BINDER_MODEL).strip()

    if resolved_provider == BINDER_PROVIDER_MOCK:
        return MockLlmProvider(model_name=resolved_model or "mock-binder-v1")

    resolved_endpoint = (endpoint or os.environ.get(BINDER_ENDPOINT_ENV) or "").strip()
    resolved_key = (api_key or os.environ.get(BINDER_API_KEY_ENV) or "").strip()
    resolved_version = (
        api_version or os.environ.get(BINDER_API_VERSION_ENV) or ""
    ).strip() or None
    resolved_full_url = (
        chat_completions_url or os.environ.get(BINDER_CHAT_COMPLETIONS_URL_ENV) or ""
    ).strip() or None

    missing: list[str] = []
    if not resolved_full_url and not resolved_endpoint:
        missing.append(BINDER_ENDPOINT_ENV)
    if not resolved_key:
        missing.append(BINDER_API_KEY_ENV)
    if not resolved_model:
        missing.append(BINDER_MODEL_ENV)
    if resolved_provider == BINDER_PROVIDER_AZURE_OPENAI and not resolved_version:
        missing.append(BINDER_API_VERSION_ENV)
    if missing:
        hint = (
            " For azure-openai, MERITRANKER_BINDER_MODEL must be the Azure deployment name."
            if resolved_provider == BINDER_PROVIDER_AZURE_OPENAI
            else ""
        )
        raise LlmProviderError(
            "Missing semantic binder LLM configuration. "
            f"Set environment variables: {', '.join(missing)} "
            f"or use --provider mock for tests.{hint}",
        )

    if resolved_full_url and not resolved_endpoint:
        parsed = urlparse(resolved_full_url)
        if parsed.scheme and parsed.netloc:
            resolved_endpoint = f"{parsed.scheme}://{parsed.netloc}"

    if resolved_provider == BINDER_PROVIDER_AZURE_OPENAI:
        url_mode = "full_url" if resolved_full_url else "azure_deployment"
        return AzureOpenAIProvider(
            endpoint=resolved_endpoint or "",
            api_key=resolved_key,
            model=resolved_model,
            deployment=resolved_model,
            api_version=resolved_version or "",
            provider_name=BINDER_PROVIDER_AZURE_OPENAI,
            timeout_seconds=timeout_seconds,
            url_mode=url_mode,
            chat_completions_url=resolved_full_url,
        )

    url_mode = "full_url" if (
        resolved_full_url or "chat/completions" in resolved_endpoint
    ) else "openai_compatible"
    return OpenAICompatibleProvider(
        endpoint=resolved_endpoint,
        api_key=resolved_key,
        model=resolved_model,
        api_version=resolved_version,
        provider_name=BINDER_PROVIDER_OPENAI_COMPATIBLE,
        timeout_seconds=timeout_seconds,
        url_mode=url_mode,
        chat_completions_url=resolved_full_url,
    )


def probe_llm_provider(
    *,
    provider: str | None = None,
    model: str | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
    api_version: str | None = None,
    chat_completions_url: str | None = None,
    timeout_seconds: int = DEFAULT_BINDER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Resolve provider, return diagnostics, and run preflight."""
    impl = resolve_llm_provider(
        provider=provider,
        model=model,
        endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
        chat_completions_url=chat_completions_url,
        timeout_seconds=timeout_seconds,
    )
    diagnostics = impl.describe_sanitized()
    result = impl.preflight()
    return {
        "status": "succeeded",
        "diagnostics": diagnostics,
        "preflight": result,
    }


def _extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmProviderError("LLM response missing choices.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise LlmProviderError("LLM response missing message.")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LlmProviderError("LLM response missing content.")
    return content


def _parse_json_content(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise LlmProviderError("LLM response was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise LlmProviderError("LLM response JSON root must be an object.")
    return parsed


RE_OPTION_BOLD = re.compile(r"^[-*+]\s+\*\*([A-Da-d])\*\*\s+(.+)$")
RE_OPTION_PLAIN = re.compile(r"^[-*+]\s+\*\*?([A-Da-d])\*\*?\s+(.+)$")
RE_OPTION_PAREN = re.compile(r"^\s*\(([A-Da-d])\)\s*(.+)$")
RE_OPTION_DOT = re.compile(r"^([A-Da-d])\.\s+(.+)$")
RE_QUESTION_BOLD = re.compile(r"^\*\*(\d+)\.\*\*\s+(.+)$")
RE_QUESTION_NUM = re.compile(r"^(\d+)\.\s+(.+)$")


def _rule_based_extract_from_prompt(prompt: str) -> dict[str, Any]:
    """Deterministic mock extraction from embedded evidence lines JSON."""
    lines = _parse_json_block(prompt, "EVIDENCE_LINES_JSON:")
    if not lines:
        return {"items": [], "metadata_candidates": []}

    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in lines:
        if not isinstance(line, dict):
            continue
        line_id = line.get("line_id")
        text_raw = str(line.get("text_raw") or "")
        if not line_id or not text_raw.strip():
            continue

        role_hints = line.get("role_hints") or []
        question = _parse_question_line(text_raw, role_hints)
        if question is not None:
            if current is not None:
                items.append(current)
            qnum, qtext, _qraw = question
            current = {
                "question_number": qnum,
                "question_number_raw": str(qnum),
                "question_text_raw": qtext,
                "raw_text": text_raw,
                "options": [],
                "answer": {"available": False},
                "solution": {"available": False},
                "visual_references": [],
                "source_spans": [{"extractor": "marker", "line_id": line_id}],
                "confidence": 0.85,
            }
            continue

        option = _parse_option_line(text_raw, role_hints)
        if option is not None and current is not None:
            key, opt_text = option
            current["options"].append(
                {
                    "key": key,
                    "key_raw": key,
                    "text_raw": opt_text,
                    "source_spans": [{"extractor": "marker", "line_id": line_id}],
                    "confidence": 0.9,
                },
            )

    if current is not None:
        items.append(current)

    _apply_answer_key_evidence(items, _parse_json_block(prompt, "ANSWER_KEY_EVIDENCE_JSON:"))
    return {"items": items, "metadata_candidates": []}


def _parse_json_block(prompt: str, marker: str) -> list:
    if marker not in prompt:
        return []
    json_text = prompt.split(marker, 1)[1].strip().split("\n", 1)[0]
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _apply_answer_key_evidence(items: list[dict[str, Any]], answer_keys: list) -> None:
    if not answer_keys:
        return
    by_qnum = {
        int(ak["question_number"]): ak
        for ak in answer_keys
        if isinstance(ak, dict) and ak.get("question_number") is not None
    }
    for item in items:
        qnum = item.get("question_number")
        if qnum is None or qnum not in by_qnum:
            continue
        ak = by_qnum[qnum]
        if item.get("answer", {}).get("available"):
            continue
        key = str(ak.get("answer_key") or "").upper()
        item["answer"] = {
            "available": True,
            "key": key,
            "key_raw": ak.get("answer_key"),
            "answer_text_raw": ak.get("source_text_raw") or key,
            "source_spans": [
                {
                    "extractor": "marker",
                    "line_id": ak.get("source_line_id"),
                },
            ],
            "confidence": ak.get("confidence", 0.85),
        }


def _parse_question_line(text_raw: str, role_hints: list[str]) -> tuple[int, str, str] | None:
    stripped = text_raw.strip()
    for pattern in (RE_QUESTION_BOLD, RE_QUESTION_NUM):
        match = pattern.match(stripped)
        if match:
            return int(match.group(1)), match.group(2).strip(), stripped
    if "question_anchor_candidate" in role_hints:
        bold = re.search(r"\*\*(\d+)\.\*\*\s*(.+)", stripped)
        if bold:
            return int(bold.group(1)), bold.group(2).strip(), stripped
        num = re.match(r"^(\d+)\.\s+(.+)", stripped)
        if num:
            return int(num.group(1)), num.group(2).strip(), stripped
    return None


def _parse_option_line(text_raw: str, role_hints: list[str]) -> tuple[str, str] | None:
    stripped = text_raw.strip()
    for pattern in (RE_OPTION_BOLD, RE_OPTION_PLAIN, RE_OPTION_PAREN, RE_OPTION_DOT):
        match = pattern.match(stripped)
        if match:
            return match.group(1).upper(), match.group(2).strip()
    if "option_label_candidate" in role_hints:
        paren = re.match(r"^\s*\(?([A-Da-d])\)?\s+(.+)$", stripped)
        if paren:
            return paren.group(1).upper(), paren.group(2).strip()
    return None
