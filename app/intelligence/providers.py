"""Providers behind the LLMProvider seam (ADR 0015 §2.3).

Failure semantics are identical everywhere: return ``LLMResult(ok=False,
fallback_reason=...)`` — never raise into the caller, never switch provider.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.intelligence.core import (
    FALLBACK_DISABLED,
    FALLBACK_MALFORMED_JSON,
    FALLBACK_NO_MODEL,
    FALLBACK_TIMEOUT,
    FALLBACK_UNAVAILABLE,
    LLMRequest,
    LLMResult,
)

_JSON_INSTRUCTION = (
    "Antworte AUSSCHLIESSLICH mit einem einzelnen JSON-Objekt, das exakt dem "
    "folgenden JSON-Schema entspricht. Kein Markdown, kein Text davor oder danach.\n"
    "SCHEMA: {schema}\n"
)


def _fail(provider: str, model: str, started: float, reason: str) -> LLMResult:
    return LLMResult(
        ok=False,
        data=None,
        provider=provider,
        model=model,
        latency_ms=(time.monotonic() - started) * 1000.0,
        fallback_reason=reason,
    )


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`\n")
        if text.startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _ok(provider: str, model: str, started: float, data: dict[str, Any]) -> LLMResult:
    confidence = data.get("confidence")
    return LLMResult(
        ok=True,
        data=data,
        provider=provider,
        model=model,
        latency_ms=(time.monotonic() - started) * 1000.0,
        confidence=confidence if isinstance(confidence, int | float) else None,
        evidence=tuple(str(e) for e in data.get("evidence", ())),
    )


class NoOpProvider:
    """Always present, never errs, never calls anything."""

    name = "noop"

    def complete(self, request: LLMRequest) -> LLMResult:
        return LLMResult(
            ok=False,
            data=None,
            provider=self.name,
            model="",
            latency_ms=0.0,
            fallback_reason=FALLBACK_DISABLED,
        )

    def available(self) -> bool:
        return True


class MockProvider:
    """Deterministic fixture provider for tests: returns the canned payload
    registered for the task_type, else fails closed."""

    name = "mock"

    def __init__(self, fixtures: dict[str, dict[str, Any]] | None = None) -> None:
        self._fixtures = fixtures or {}

    def complete(self, request: LLMRequest) -> LLMResult:
        started = time.monotonic()
        data = self._fixtures.get(request.task_type)
        if data is None:
            return _fail(self.name, "mock-fixture", started, FALLBACK_UNAVAILABLE)
        return _ok(self.name, "mock-fixture", started, data)

    def available(self) -> bool:
        return True


class OllamaProvider:
    """Local Ollama via its OpenAI-compatible /v1/chat/completions endpoint."""

    name = "ollama"

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    def available(self) -> bool:
        if not self._model:
            return False
        try:
            resp = httpx.get(f"{self._base_url}/api/version", timeout=2.0)
        except Exception:  # noqa: BLE001 — availability probe is best-effort
            return False
        return resp.status_code == 200

    def complete(self, request: LLMRequest) -> LLMResult:
        started = time.monotonic()
        if not self._model:
            return _fail(self.name, "", started, FALLBACK_NO_MODEL)
        payload = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": _JSON_INSTRUCTION.format(
                        schema=json.dumps(request.schema, sort_keys=True)
                    ),
                },
                {"role": "user", "content": request.prompt},
            ],
        }
        try:
            resp = httpx.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                timeout=request.timeout_s,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
        except httpx.TimeoutException:
            return _fail(self.name, self._model, started, FALLBACK_TIMEOUT)
        except Exception:  # noqa: BLE001 — any transport/shape error is fail-closed
            return _fail(self.name, self._model, started, FALLBACK_UNAVAILABLE)
        data = _parse_json_object(raw)
        if data is None:
            return _fail(self.name, self._model, started, FALLBACK_MALFORMED_JSON)
        return _ok(self.name, self._model, started, data)


class ClaudeProvider:
    """Anthropic API behind the same seam. Only constructed on explicit
    KAI_LLM_PROVIDER=claude — never as a fallback (ADR 0015 §3)."""

    name = "claude"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def available(self) -> bool:
        return bool(self._api_key and self._model)

    def complete(self, request: LLMRequest) -> LLMResult:
        started = time.monotonic()
        if not self.available():
            return _fail(self.name, self._model, started, FALLBACK_NO_MODEL)
        try:
            import anthropic  # lazy: optional path, dependency already in pyproject

            client = anthropic.Anthropic(api_key=self._api_key, timeout=request.timeout_s)
            resp = client.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=_JSON_INSTRUCTION.format(schema=json.dumps(request.schema, sort_keys=True)),
                messages=[{"role": "user", "content": request.prompt}],
            )
            raw = "".join(
                str(getattr(b, "text", ""))
                for b in resp.content
                if getattr(b, "type", "") == "text"
            )
        except Exception:  # noqa: BLE001 — fail-closed, incl. auth/rate/network
            return _fail(self.name, self._model, started, FALLBACK_UNAVAILABLE)
        data = _parse_json_object(raw)
        if data is None:
            return _fail(self.name, self._model, started, FALLBACK_MALFORMED_JSON)
        return _ok(self.name, self._model, started, data)
