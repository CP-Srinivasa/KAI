"""Contract + security tests for the Local Intelligence Layer (ADR 0015)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.intelligence.context import ContextBuilder, ContextRefusedError
from app.intelligence.core import LLMRequest, LLMResult
from app.intelligence.providers import (
    MockProvider,
    NoOpProvider,
    OllamaProvider,
    _parse_json_object,
)
from app.intelligence.router import SHADOW_RESULT_SCHEMA, TaskRouter
from app.intelligence.settings import (
    LlmConfigRefusedError,
    LlmSettings,
    get_llm_settings,
)

_REQ = LLMRequest(task_type="doc_qa", prompt="p", schema=SHADOW_RESULT_SCHEMA)
_VALID = {"summary": "s", "evidence": ["docs/adr/0015.md"], "confidence": 0.5}


# --- settings: fail-closed defaults + boot refuse -------------------------------


def test_settings_default_off() -> None:
    s = LlmSettings(_env_file=None)
    assert (s.enabled, s.mode, s.provider, s.influences_execution) == (
        False,
        "disabled",
        "none",
        False,
    )


def test_influences_execution_true_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAI_LLM_INFLUENCES_EXECUTION", "true")
    get_llm_settings.cache_clear()
    with pytest.raises(LlmConfigRefusedError):
        get_llm_settings()
    get_llm_settings.cache_clear()


# --- providers -------------------------------------------------------------------


def test_noop_never_errs_and_reports_disabled() -> None:
    result = NoOpProvider().complete(_REQ)
    assert not result.ok and result.fallback_reason == "disabled"


def test_mock_is_deterministic() -> None:
    provider = MockProvider({"doc_qa": _VALID})
    a, b = provider.complete(_REQ), provider.complete(_REQ)
    assert a.ok and a.data == b.data == _VALID


def test_ollama_unreachable_fails_closed_without_provider_switch() -> None:
    provider = OllamaProvider(base_url="http://127.0.0.1:1", model="m")
    result = provider.complete(LLMRequest(task_type="doc_qa", prompt="p", schema={}, timeout_s=0.5))
    assert not result.ok
    assert result.provider == "ollama"  # no silent fallback to any cloud provider
    assert result.fallback_reason in ("unavailable", "timeout")
    assert provider.available() is False


def test_empty_model_means_unavailable_no_autoinstall() -> None:
    provider = OllamaProvider(base_url="http://127.0.0.1:1", model="")
    result = provider.complete(_REQ)
    assert not result.ok and result.fallback_reason == "no_model_configured"


def test_malformed_json_is_rejected() -> None:
    assert _parse_json_object("not json at all") is None
    assert _parse_json_object('["a","list"]') is None
    assert _parse_json_object('{"summary": "ok"}') == {"summary": "ok"}
    assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


# --- router: gates, schema enforcement, audit ------------------------------------


def _router(tmp_path: Path, provider, **overrides) -> TaskRouter:
    values = {"enabled": True, "mode": "shadow", "provider": "mock"}
    values.update(overrides)
    return TaskRouter(
        settings=LlmSettings(_env_file=None, **values),
        provider=provider,
        audit_path=tmp_path / "audit.jsonl",
    )


def test_router_disabled_returns_noop_and_audits(tmp_path: Path) -> None:
    router = _router(tmp_path, MockProvider({"doc_qa": _VALID}), enabled=False)
    result = router.run("doc_qa", "p")
    assert not result.ok and result.fallback_reason == "disabled"
    rows = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["influences_execution"] is False
    assert rows[0]["prompt_hash"] and rows[0]["ok"] is False


def test_router_schema_violation_discards_payload(tmp_path: Path) -> None:
    bad = {"summary": "s", "evidence": [], "confidence": 0.5, "verdeckt": "x"}
    router = _router(tmp_path, MockProvider({"doc_qa": bad}))
    result = router.run("doc_qa", "p")
    assert not result.ok and result.fallback_reason == "schema_violation"
    assert result.data is None


def test_router_valid_payload_passes_and_audits_ok(tmp_path: Path) -> None:
    router = _router(tmp_path, MockProvider({"doc_qa": _VALID}))
    result = router.run("doc_qa", "p", input_refs=("docs/adr/0015.md",))
    assert result.ok and result.data == _VALID and result.confidence == 0.5
    row = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[0])
    assert row["ok"] is True and row["input_refs"] == ["docs/adr/0015.md"]


def test_router_unknown_task_type_fails_closed(tmp_path: Path) -> None:
    router = _router(tmp_path, MockProvider({"doc_qa": _VALID}))
    assert not router.run("execute_trade", "p").ok


# --- context builder: allowlist, traversal, denylist, redaction -------------------


def _builder(tmp_path: Path) -> ContextBuilder:
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "a.md").write_text("hallo", encoding="utf-8")
    return ContextBuilder(tmp_path, ("docs/adr",))


def test_context_reads_allowlisted_file(tmp_path: Path) -> None:
    ctx = _builder(tmp_path).build(["docs/adr/a.md"])
    assert "hallo" in ctx.text and ctx.input_refs == ("docs/adr/a.md",)


def test_context_refuses_traversal_and_outside_paths(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ContextRefusedError):
        builder.build(["docs/adr/../../secret.txt"])
    with pytest.raises(ContextRefusedError):
        builder.build(["secret.txt"])


def test_context_denylist_beats_allowlist(tmp_path: Path) -> None:
    builder = ContextBuilder(tmp_path, ("",))  # allow everything -> denylist must hold
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-abc", encoding="utf-8")
    with pytest.raises(ContextRefusedError):
        builder.build([".env"])


def test_context_redacts_planted_secret(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    planted = "key sk-" + "a1b2c3d4e5f6a1b2c3d4e5f6"
    (tmp_path / "docs" / "adr" / "leak.md").write_text(planted, encoding="utf-8")
    ctx = builder.build(["docs/adr/leak.md"])
    assert "a1b2c3d4e5f6a1b2c3d4e5f6" not in ctx.text
    assert ctx.redaction_count >= 1


# --- import invariant: no path between intelligence and execution stack -----------

_FORBIDDEN = ("app.execution", "app.risk", "app.orchestrator", "app.signals", "app.trading")


def test_intelligence_never_imports_execution_stack() -> None:
    import ast

    pkg = Path(__file__).resolve().parents[2] / "app" / "intelligence"
    for py in pkg.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(_FORBIDDEN), f"{py.name} imports {name}"


def test_execution_stack_never_imports_intelligence() -> None:
    root = Path(__file__).resolve().parents[2] / "app"
    for sub in ("execution", "risk", "orchestrator", "signals", "trading"):
        for py in (root / sub).rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            assert "app.intelligence" not in text, f"{sub}/{py.name} references intelligence"


def test_llmresult_defaults_are_fail_closed() -> None:
    result = LLMResult(ok=False, data=None, provider="noop", model="", latency_ms=0.0)
    assert result.confidence is None and result.evidence == ()
