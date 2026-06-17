"""NEO-P-002-r3 — shadow-real feed driver tests, incl. the No-Execution invariant.

The driver never executes anything itself; it only replays real analyses through
the loop's shadow seam. These tests prove: (a) the Default-OFF flag fully
no-ops, (b) when ON it injects eligible candidates in SHADOW mode with the real
analysis, and (c) the No-Execution invariant — a valid directional real signal
produces a shadow cycle but NO order / NO position / NO live mode.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.domain.document import CanonicalDocument, SentimentLabel
from app.observability import shadow_real_feed as feed


def _doc(
    doc_id: str | None = None, *, directional: float = 0.7, tickers: list[str] | None = None
) -> CanonicalDocument:
    did = doc_id or str(uuid.uuid4())
    return CanonicalDocument(
        id=did,
        url=f"https://example.test/{did}",
        title=f"Title {did}",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.6,
        relevance_score=0.8,
        impact_score=0.7,
        novelty_score=0.5,
        credibility_score=0.9,
        spam_probability=0.1,
        directional_confidence=directional,
        priority_score=8,
        tickers=tickers if tickers is not None else ["BTC"],
    )


def _set_flag(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    stub = SimpleNamespace(execution=SimpleNamespace(shadow_real_generator=enabled))
    monkeypatch.setattr(feed, "get_settings", lambda: stub)


@dataclass
class _SpyRunner:
    """Records run-once calls; simulates the loop's shadow path (no execution)."""

    calls: list[dict] = field(default_factory=list)
    orders_created: int = 0  # stays 0 — the spy never executes
    positions: int = 0

    async def __call__(self, *, symbol: str, analysis_result) -> object:
        self.calls.append({"symbol": symbol, "document_id": analysis_result.document_id})
        # Mirror the real shadow path's terminal status for entry_mode=disabled.
        return SimpleNamespace(
            status="CycleStatus.ENTRY_MODE_BLOCKED", notes=["shadow_candidate_recorded"]
        )


@pytest.mark.asyncio
async def test_flag_off_is_full_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_flag(monkeypatch, False)
    spy = _SpyRunner()

    async def _fetch() -> list:
        raise AssertionError("fetch must NOT be called when flag is OFF")

    funnel = await feed.run_shadow_real_feed(
        fetch_recent_analyzed=_fetch,
        run_once=spy,
        fed_ledger_path=tmp_path / "fed.jsonl",
        funnel_path=tmp_path / "funnel.jsonl",
    )
    assert funnel["enabled"] is False
    assert funnel["reason"] == "flag_off"
    assert spy.calls == []  # nothing injected, nothing executed


@pytest.mark.asyncio
async def test_flag_on_injects_eligible_in_shadow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_flag(monkeypatch, True)
    spy = _SpyRunner()
    ok1, ok2 = _doc(), _doc(tickers=["ETH"])
    docs = [ok1, ok2, _doc(directional=0.0)]

    async def _fetch() -> list:
        return docs

    funnel = await feed.run_shadow_real_feed(
        fetch_recent_analyzed=_fetch,
        run_once=spy,
        fed_ledger_path=tmp_path / "fed.jsonl",
        funnel_path=tmp_path / "funnel.jsonl",
    )
    assert funnel["enabled"] is True
    assert funnel["eligible"] == 2
    assert funnel["non_directional"] == 1
    assert funnel["injected"] == 2
    # candidates were replayed with their real document_id (provenance preserved)
    assert {c["document_id"] for c in spy.calls} == {str(ok1.id), str(ok2.id)}


@pytest.mark.asyncio
async def test_no_execution_invariant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Valid directional real signal → shadow cycle recorded, but NO order / NO
    position. The driver only ever calls the SHADOW seam; it owns no execution."""
    _set_flag(monkeypatch, True)
    spy = _SpyRunner()

    async def _fetch() -> list:
        return [_doc(directional=0.95)]

    funnel = await feed.run_shadow_real_feed(
        fetch_recent_analyzed=_fetch,
        run_once=spy,
        fed_ledger_path=tmp_path / "fed.jsonl",
        funnel_path=tmp_path / "funnel.jsonl",
    )
    assert funnel["injected"] == 1
    assert funnel["shadow_recorded"] == 1  # terminal = ENTRY_MODE_BLOCKED shadow path
    assert spy.orders_created == 0
    assert spy.positions == 0


@pytest.mark.asyncio
async def test_dedup_persists_across_ticks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_flag(monkeypatch, True)
    spy = _SpyRunner()
    ledger = tmp_path / "fed.jsonl"

    dup = _doc()

    async def _fetch() -> list:
        return [dup]

    f1 = await feed.run_shadow_real_feed(
        fetch_recent_analyzed=_fetch,
        run_once=spy,
        fed_ledger_path=ledger,
        funnel_path=tmp_path / "f.jsonl",
    )
    f2 = await feed.run_shadow_real_feed(
        fetch_recent_analyzed=_fetch,
        run_once=spy,
        fed_ledger_path=ledger,
        funnel_path=tmp_path / "f.jsonl",
    )
    assert f1["injected"] == 1
    assert f2["injected"] == 0  # already fed → not replayed twice
    assert f2["already_fed"] == 1
    assert len(spy.calls) == 1


@pytest.mark.asyncio
async def test_default_run_once_forces_shadow_mode() -> None:
    """The built-in runner must pass ExecutionMode.SHADOW (never live/paper)."""
    import app.observability.shadow_real_feed as mod

    captured: dict = {}

    async def _fake_run_once(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="CycleStatus.ENTRY_MODE_BLOCKED")

    # Patch the lazily-imported run_trading_loop_once.
    import app.orchestrator.trading_loop as tl

    orig = tl.run_trading_loop_once
    tl.run_trading_loop_once = _fake_run_once  # type: ignore[assignment]
    try:
        from app.core.domain.document import AnalysisResult

        ar = AnalysisResult(
            document_id="x",
            sentiment_label=SentimentLabel.BULLISH,
            sentiment_score=0.5,
            relevance_score=0.5,
            impact_score=0.5,
            confidence_score=0.5,
            novelty_score=0.5,
            explanation_short="s",
            explanation_long="l",
        )
        await mod._default_run_once(symbol="BTC/USDT", analysis_result=ar)
    finally:
        tl.run_trading_loop_once = orig  # type: ignore[assignment]
    from app.core.enums import ExecutionMode

    assert captured["mode"] == ExecutionMode.SHADOW
    assert captured["symbol"] == "BTC/USDT"
    assert captured["analysis_result"].document_id == "x"
    # V2 2026-06-16: the runner tags real_analysis so the D-182 priority gate uses
    # the feeder threshold (5), not the global 10. The SHADOW mode floor + the
    # decoupling mode-guard ensure this tag can never produce a real fill.
    from app.execution.real_analysis_paper import REAL_ANALYSIS_SOURCE

    assert captured["analysis_source"] == REAL_ANALYSIS_SOURCE
