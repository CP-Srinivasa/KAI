"""Regression tests for NEO-P-004.

Two defects had zero coverage before this file existed:

* ``save_llm_audit`` was never exercised by any test (grep over ``tests/`` was
  empty) while the guard silently dropped every Gemini/Grok row and hardcoded
  ``model="unknown"``.
* the four ``tenacity`` decorators had no ``retry=`` filter, so a 401 cost three
  attempts plus up to 15 s of backoff.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from tenacity import wait_none

from app.analysis.pipeline import PipelineResult
from app.core.domain.document import CanonicalDocument
from tests.unit.factories import make_llm_output

# ── _persist_llm_audit ───────────────────────────────────────────────────────


class _RecordingRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def save_llm_audit(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _result(**kwargs: Any) -> PipelineResult:
    doc = CanonicalDocument(url="https://example.com/a", title="Bitcoin ETF approved")
    return PipelineResult(document=doc, **kwargs)


async def test_persist_llm_audit_skips_when_no_llm_output() -> None:
    from app.pipeline.service import _persist_llm_audit

    repo = _RecordingRepo()
    await _persist_llm_audit(repo, _result(llm_output=None))  # type: ignore[arg-type]
    assert repo.calls == []


async def test_persist_llm_audit_records_gemini_without_raw_prompt() -> None:
    """The old guard (`if res.llm_output.raw_prompt`) dropped this row entirely."""
    from app.pipeline.service import _persist_llm_audit

    out = make_llm_output()
    out.raw_prompt = None
    out.raw_response = '{"sentiment_label":"bullish"}'
    out.prompt_tokens = 311
    out.completion_tokens = 88

    repo = _RecordingRepo()
    await _persist_llm_audit(
        repo,  # type: ignore[arg-type]
        _result(llm_output=out, provider_name="gemini", model_name="gemini-2.5-flash"),
    )

    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["provider"] == "gemini"
    assert call["model"] == "gemini-2.5-flash"
    assert call["prompt_text"] == ""
    assert call["raw_response"] == '{"sentiment_label":"bullish"}'
    assert call["prompt_tokens"] == 311 and call["completion_tokens"] == 88


async def test_persist_llm_audit_uses_real_model_name() -> None:
    from app.pipeline.service import _persist_llm_audit

    out = make_llm_output()
    out.raw_prompt = "prompt"
    repo = _RecordingRepo()
    await _persist_llm_audit(
        repo,  # type: ignore[arg-type]
        _result(llm_output=out, provider_name="openai", model_name="gpt-4o"),
    )
    assert repo.calls[0]["model"] == "gpt-4o"


async def test_persist_llm_audit_falls_back_to_unknown_not_a_guess() -> None:
    """model_name unresolvable -> honest "unknown", never an invented model."""
    from app.pipeline.service import _persist_llm_audit

    out = make_llm_output()
    out.raw_prompt = "prompt"
    repo = _RecordingRepo()
    await _persist_llm_audit(
        repo,  # type: ignore[arg-type]
        _result(llm_output=out, provider_name="openai", model_name=None),
    )
    assert repo.calls[0]["model"] == "unknown"


# ── model-name resolution through the ensemble ───────────────────────────────


def test_resolve_runtime_model_name_picks_the_winner_of_the_chain() -> None:
    from app.analysis.ensemble.provider import EnsembleProvider
    from app.analysis.pipeline import _resolve_runtime_model_name

    class _P:
        def __init__(self, name: str, model: str) -> None:
            self.provider_name = name
            self.model = model

        async def analyze(self, *a: Any, **k: Any) -> Any:  # pragma: no cover
            raise AssertionError("not called")

    ensemble = EnsembleProvider([_P("openai", "gpt-4o"), _P("gemini", "gemini-2.5-flash")])  # type: ignore[list-item]
    out = make_llm_output()
    out.provider_used = "gemini"
    assert _resolve_runtime_model_name(ensemble, out) == "gemini-2.5-flash"


def test_resolve_runtime_model_name_is_none_when_winner_not_in_chain() -> None:
    from app.analysis.ensemble.provider import EnsembleProvider
    from app.analysis.pipeline import _resolve_runtime_model_name

    class _P:
        provider_name = "openai"
        model = "gpt-4o"

        async def analyze(self, *a: Any, **k: Any) -> Any:  # pragma: no cover
            raise AssertionError("not called")

    ensemble = EnsembleProvider([_P()])  # type: ignore[list-item]
    out = make_llm_output()
    out.provider_used = "internal"
    assert _resolve_runtime_model_name(ensemble, out) is None


# ── tenacity retry filter (NEO-F-006) ────────────────────────────────────────


class _SdkError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status_code = status


class _Counter:
    def __init__(self, exc: BaseException) -> None:
        self.n = 0
        self._exc = exc

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.n += 1
        raise self._exc

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        self.n += 1
        raise self._exc


def _no_wait(monkeypatch: pytest.MonkeyPatch, func: Any) -> None:
    """Neutralise the exponential backoff so the test runs in milliseconds."""
    monkeypatch.setattr(func.retry, "wait", wait_none())


def _openai_provider(counter: _Counter) -> Any:
    from app.integrations.openai.provider import OpenAIAnalysisProvider

    p = OpenAIAnalysisProvider(api_key="test-key", model="gpt-4o", timeout=1)
    p._client = SimpleNamespace(  # type: ignore[assignment]
        beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=counter.acall)))
    )
    return p


def _xai_provider(counter: _Counter) -> Any:
    from app.integrations.xai.provider import GrokAnalysisProvider

    p = GrokAnalysisProvider(api_key="test-key", model="grok-4", timeout=1)
    p._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=counter.acall))
    )
    return p


def _anthropic_provider(counter: _Counter) -> Any:
    from app.integrations.anthropic.provider import AnthropicAnalysisProvider

    p = AnthropicAnalysisProvider(api_key="test-key", model="claude-sonnet-4-6", timeout=1)
    p._client = SimpleNamespace(messages=SimpleNamespace(create=counter.acall))  # type: ignore[assignment]
    return p


def _gemini_provider(counter: _Counter) -> Any:
    from app.integrations.gemini.provider import GeminiAnalysisProvider

    p = GeminiAnalysisProvider(api_key="test-key", model="gemini-2.5-flash", timeout=5)
    p._client = SimpleNamespace(models=SimpleNamespace(generate_content=counter))  # type: ignore[assignment]
    return p


_BUILDERS = {
    "openai": _openai_provider,
    "xai": _xai_provider,
    "anthropic": _anthropic_provider,
    "gemini": _gemini_provider,
}


@pytest.mark.parametrize("name", sorted(_BUILDERS))
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, name: str, status: int
) -> None:
    counter = _Counter(_SdkError(status))
    provider = _BUILDERS[name](counter)
    _no_wait(monkeypatch, type(provider).analyze)

    with pytest.raises(_SdkError):
        await provider.analyze(title="t", text="Bitcoin " * 40)

    assert counter.n == 1


@pytest.mark.parametrize("name", sorted(_BUILDERS))
async def test_bad_request_is_not_retried(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    counter = _Counter(_SdkError(400))
    provider = _BUILDERS[name](counter)
    _no_wait(monkeypatch, type(provider).analyze)

    with pytest.raises(_SdkError):
        await provider.analyze(title="t", text="Bitcoin " * 40)

    assert counter.n == 1


@pytest.mark.parametrize("name", sorted(_BUILDERS))
async def test_validation_error_is_not_retried(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    from pydantic import BaseModel, ValidationError

    class _M(BaseModel):
        x: int

    try:
        _M(x="nope")  # type: ignore[arg-type]
    except ValidationError as exc:
        err: BaseException = exc

    counter = _Counter(err)
    provider = _BUILDERS[name](counter)
    _no_wait(monkeypatch, type(provider).analyze)

    with pytest.raises(ValidationError):
        await provider.analyze(title="t", text="Bitcoin " * 40)

    assert counter.n == 1


@pytest.mark.parametrize("name", sorted(_BUILDERS))
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_error_is_retried_three_times(
    monkeypatch: pytest.MonkeyPatch, name: str, status: int
) -> None:
    counter = _Counter(_SdkError(status))
    provider = _BUILDERS[name](counter)
    _no_wait(monkeypatch, type(provider).analyze)

    with pytest.raises(_SdkError):
        await provider.analyze(title="t", text="Bitcoin " * 40)

    assert counter.n == 3
