"""Tests for the AI call-audit scope (NEO-P-001).

Covers the closed error taxonomy, the one-row-per-scope guarantee (success and
failure), re-raise semantics, correlation-id handling and the secret-leak gate.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app.ai.audit import classify_error, http_status, is_retryable_error, llm_call_scope
from app.observability.llm_telemetry import llm_telemetry_summary


class _StatusError(Exception):
    """Duck-typed SDK error: carries .status_code like openai.APIStatusError."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"http {status}")
        self.status_code = status


class _NestedStatusError(Exception):
    """Duck-typed httpx.HTTPStatusError: status lives on .response."""

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code = status

    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.response = _NestedStatusError._Resp(status)


def _validation_error() -> ValidationError:
    class _M(BaseModel):
        x: int

    try:
        _M(x="not-an-int")  # type: ignore[arg-type]
    except ValidationError as exc:
        return exc
    raise AssertionError("pydantic did not raise")


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


# ── classify_error matrix ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "auth"),
        (403, "auth"),
        (402, "quota"),
        (429, "rate_limit"),
        (500, "server"),
        (502, "server"),
        (503, "server"),
        (400, "unknown"),
        (404, "unknown"),
    ],
)
def test_classify_error_by_http_status(status: int, expected: str) -> None:
    assert classify_error(_StatusError(status)) == expected
    assert classify_error(_NestedStatusError(status)) == expected


def test_classify_error_timeout() -> None:
    assert classify_error(TimeoutError("slow")) == "timeout"
    # The asyncio alias is asserted deliberately: it must resolve onto the
    # same branch as the builtin (suppression below is intentional).
    assert classify_error(asyncio.TimeoutError()) == "timeout"  # noqa: UP041


def test_classify_error_cancelled() -> None:
    assert classify_error(asyncio.CancelledError()) == "cancelled"


def test_classify_error_schema() -> None:
    assert classify_error(_validation_error()) == "schema"


def test_classify_error_unknown_fallback() -> None:
    assert classify_error(ValueError("refusal-ish")) == "unknown"
    assert classify_error(RuntimeError("boom")) == "unknown"


def test_classify_error_by_class_name() -> None:
    rate = type("RateLimitError", (Exception,), {})
    conn = type("APIConnectionError", (Exception,), {})
    auth = type("AuthenticationError", (Exception,), {})
    assert classify_error(rate()) == "rate_limit"
    assert classify_error(conn()) == "transport"
    assert classify_error(auth()) == "auth"


def test_classify_error_never_raises_on_hostile_object() -> None:
    class _HostileError(Exception):
        @property
        def status_code(self) -> int:
            raise RuntimeError("nope")

    assert classify_error(_HostileError()) == "unknown"
    assert http_status(_HostileError()) is None


def test_http_status_absent() -> None:
    assert http_status(ValueError("x")) is None


# ── retry predicate (feeds the tenacity filters, NEO-P-004) ──────────────────


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_is_retryable_false_for_client_errors(status: int) -> None:
    assert is_retryable_error(_StatusError(status)) is False


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_is_retryable_true_for_transient(status: int) -> None:
    assert is_retryable_error(_StatusError(status)) is True


def test_is_retryable_false_for_validation_error() -> None:
    assert is_retryable_error(_validation_error()) is False


def test_is_retryable_true_for_unclassified() -> None:
    # Deny-list semantics: unknown errors keep the pre-existing retry behaviour.
    assert is_retryable_error(ValueError("empty completion")) is True
    assert is_retryable_error(TimeoutError()) is True


# ── scope: exactly one row, success ──────────────────────────────────────────


async def test_scope_writes_exactly_one_row_on_success(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    async with llm_call_scope(
        purpose="analysis",
        provider="openai",
        model="gpt-4o",
        correlation_id="req_abc",
        chain_position=0,
        path=p,
    ) as scope:
        scope.set_tokens(120, 45)

    rows = _rows(p)
    assert len(rows) == 1
    row = rows[0]
    assert row["schema_version"] == "v2"
    assert row["ok"] is True
    assert row["provider"] == "openai" and row["model"] == "gpt-4o"
    assert row["purpose"] == "analysis"
    assert row["correlation_id"] == "req_abc"
    assert row["outcome"] == "success"
    assert row["prompt_tokens"] == 120 and row["completion_tokens"] == 45
    assert row["error_class"] is None and row["http_status"] is None
    assert isinstance(row["latency_ms"], float) and row["latency_ms"] >= 0.0
    assert str(row["call_id"]).startswith("llmc_")


async def test_scope_writes_exactly_one_row_on_error_and_reraises(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    with pytest.raises(_StatusError):
        async with llm_call_scope(
            purpose="analysis",
            provider="openai",
            model="gpt-4o",
            correlation_id="req_abc",
            failure_outcome="fallthrough",
            path=p,
        ):
            raise _StatusError(429)

    rows = _rows(p)
    assert len(rows) == 1
    row = rows[0]
    assert row["ok"] is False
    assert row["error_class"] == "rate_limit"
    assert row["http_status"] == 429
    assert row["error_type"] == "_StatusError"
    assert row["outcome"] == "fallthrough"


async def test_scope_records_cancellation_and_reraises(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    with pytest.raises(asyncio.CancelledError):
        async with llm_call_scope(purpose="chat", provider="openai", model="gpt-4o", path=p):
            raise asyncio.CancelledError()

    rows = _rows(p)
    assert len(rows) == 1
    assert rows[0]["error_class"] == "cancelled"
    assert rows[0]["ok"] is False


async def test_scope_correlation_id_is_autogenerated(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    async with llm_call_scope(purpose="intent", provider="openai", model="gpt-4o", path=p):
        pass
    cid = _rows(p)[0]["correlation_id"]
    assert isinstance(cid, str) and cid.startswith("llm_") and len(cid) > 4


async def test_scope_set_outcome_and_set_model(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    async with llm_call_scope(purpose="analysis", provider="ensemble", model=None, path=p) as s:
        s.set_model("gemini-2.5-flash")
        s.set_outcome("skipped")
    row = _rows(p)[0]
    assert row["model"] == "gemini-2.5-flash"
    assert row["outcome"] == "skipped"


async def test_scope_set_tokens_tolerates_garbage(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    async with llm_call_scope(purpose="chat", provider="openai", model="gpt-4o", path=p) as s:
        s.set_tokens(None, "nope")  # type: ignore[arg-type]
    row = _rows(p)[0]
    assert row["prompt_tokens"] == 0 and row["completion_tokens"] == 0


async def test_scope_never_raises_from_telemetry(tmp_path: Path) -> None:
    # Unwritable sink must not turn into a caller-visible failure.
    async with llm_call_scope(
        purpose="chat",
        provider="openai",
        model="gpt-4o",
        path=Path("Z:/nonexistent/dir/t.jsonl"),
    ):
        pass


async def test_scope_does_not_leak_secrets(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    secret = "sk-live-DEADBEEF0123456789"
    with pytest.raises(RuntimeError):
        async with llm_call_scope(purpose="chat", provider="openai", model="gpt-4o", path=p):
            raise RuntimeError(f"Incorrect API key provided: {secret}")

    blob = p.read_text("utf-8")
    assert secret not in blob
    assert "sk-" not in blob
    assert "api_key" not in blob


async def test_v1_summary_reads_v2_rows(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    async with llm_call_scope(purpose="analysis", provider="openai", model="gpt-4o", path=p):
        pass
    with pytest.raises(_StatusError):
        async with llm_call_scope(purpose="analysis", provider="openai", model="gpt-4o", path=p):
            raise _StatusError(500)

    summary = llm_telemetry_summary(window_hours=1.0, path=p)
    assert summary["n"] == 2 and summary["failures"] == 1
    assert summary["failure_rate_pct"] == 50.0
