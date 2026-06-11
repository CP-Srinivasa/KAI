"""Feeder orchestration: fail-closed no-op when disarmed, and armed injection
with hard PAPER + real_analysis source (Goal 2026-06-10).

The feeder's DB + loop dependencies are stubbed so this stays a unit test of the
orchestration contract (not an integration test): a disarmed override must never
touch the DB or inject; an armed pass must inject every selected candidate with
mode=PAPER and analysis_source=real_analysis.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import app.observability.real_analysis_paper_feeder as feeder
from app.core.enums import ExecutionMode
from app.core.settings import (
    REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL,
    AppSettings,
    RealAnalysisPaperSettings,
)
from app.orchestrator.models import CycleStatus, LoopCycle


def _armed_settings() -> AppSettings:
    return AppSettings(
        real_analysis_paper=RealAnalysisPaperSettings(
            enabled=True,
            allow_paper_while_entry_disabled=True,
            entry_disabled_override_ack=REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL,
        )
    )


@pytest.mark.asyncio
async def test_disarmed_feeder_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ack → the pass short-circuits BEFORE any DB access or injection."""
    monkeypatch.setattr(feeder, "get_settings", lambda: AppSettings())

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("DB must not be touched when disarmed")

    monkeypatch.setattr(feeder, "build_session_factory", _boom)

    result = await feeder.run_real_analysis_paper_feed_once()
    assert result.armed is False
    assert result.refusal_code == "real_analysis_paper_disabled"
    assert result.fills == 0
    assert result.candidates_selected == 0


@pytest.mark.asyncio
async def test_armed_feeder_injects_paper_real_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Armed → each selected candidate is injected with mode=PAPER and
    analysis_source=real_analysis; fills are counted from COMPLETED+filled."""
    monkeypatch.setattr(feeder, "get_settings", lambda: _armed_settings())

    # Stub the DB layer: a fake async session-factory whose .begin() yields a
    # context manager with a repo whose .list returns nothing (the selector is
    # stubbed separately, so docs content is irrelevant here).
    class _FakeRepo:
        def __init__(self, _session):  # noqa: D401
            pass

        async def list(self, **_kw):
            return []

    class _FakeCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    class _FakeFactory:
        def begin(self):
            return _FakeCtx()

    monkeypatch.setattr(feeder, "build_session_factory", lambda _db: _FakeFactory())
    monkeypatch.setattr(feeder, "DocumentRepository", _FakeRepo)

    # Stub the selector to return two candidates (one long, one short).
    from app.observability.real_analysis_paper_selector import RealAnalysisCandidate

    cands = [
        RealAnalysisCandidate(
            document_id="doc_long", symbol="BTC/USDT", direction="long", analysis=object()
        ),
        RealAnalysisCandidate(
            document_id="doc_short", symbol="ETH/USDT", direction="short", analysis=object()
        ),
    ]
    monkeypatch.setattr(
        feeder,
        "select_real_analysis_candidates",
        lambda docs, **kw: (cands, {"eligible": 2}),
    )

    seen: list[dict] = []

    async def _fake_run_once(**kw):
        seen.append(kw)
        return LoopCycle(
            cycle_id="cyc_x",
            started_at=datetime.now(UTC).isoformat(),
            completed_at=datetime.now(UTC).isoformat(),
            symbol=kw["symbol"],
            status=CycleStatus.COMPLETED,
            fill_simulated=True,
        )

    monkeypatch.setattr(feeder, "run_trading_loop_once", _fake_run_once)

    result = await feeder.run_real_analysis_paper_feed_once()

    assert result.armed is True
    assert result.candidates_selected == 2
    assert result.fills == 2
    assert sorted(result.fill_document_ids) == ["doc_long", "doc_short"]
    # Every injection is PAPER + real_analysis (live unreachable, source hard).
    assert all(kw["mode"] == ExecutionMode.PAPER for kw in seen)
    assert all(kw["analysis_source"] == "real_analysis" for kw in seen)


@pytest.mark.asyncio
async def test_armed_feeder_counts_blocked_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-filled cycle (e.g. ENTRY_MODE_BLOCKED) counts as blocked, not a fill."""
    monkeypatch.setattr(feeder, "get_settings", lambda: _armed_settings())

    class _FakeRepo:
        def __init__(self, _session):
            pass

        async def list(self, **_kw):
            return []

    class _FakeCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        feeder,
        "build_session_factory",
        lambda _db: type("F", (), {"begin": lambda self: _FakeCtx()})(),
    )
    monkeypatch.setattr(feeder, "DocumentRepository", _FakeRepo)

    from app.observability.real_analysis_paper_selector import RealAnalysisCandidate

    monkeypatch.setattr(
        feeder,
        "select_real_analysis_candidates",
        lambda docs, **kw: (
            [
                RealAnalysisCandidate(
                    document_id="d1", symbol="BTC/USDT", direction="long", analysis=object()
                )
            ],
            {"eligible": 1},
        ),
    )

    async def _fake_run_once(**kw):
        return LoopCycle(
            cycle_id="cyc_y",
            started_at=datetime.now(UTC).isoformat(),
            completed_at=datetime.now(UTC).isoformat(),
            symbol=kw["symbol"],
            status=CycleStatus.ENTRY_MODE_BLOCKED,
            fill_simulated=False,
        )

    monkeypatch.setattr(feeder, "run_trading_loop_once", _fake_run_once)

    result = await feeder.run_real_analysis_paper_feed_once()
    assert result.armed is True
    assert result.fills == 0
    assert result.blocked == 1


@pytest.mark.asyncio
async def test_feeder_armed_in_paper_learning_mode_without_acks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint S3 (#181): EXECUTION_ENTRY_MODE=paper_learning arms the feeder
    WITHOUT the legacy three-arm ack (master enable suffices). The disarmed
    short-circuit contract is unchanged for every legacy mode."""
    from app.core.enums import EntryMode
    from app.core.settings import ExecutionSettings

    settings = AppSettings(
        execution=ExecutionSettings(entry_mode=EntryMode.PAPER_LEARNING),
        real_analysis_paper=RealAnalysisPaperSettings(enabled=True),
    )
    monkeypatch.setattr(feeder, "get_settings", lambda: settings)

    class _FakeRepo:
        def __init__(self, _session):
            pass

        async def list(self, **_kw):
            return []

    class _FakeCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    class _FakeFactory:
        def begin(self):
            return _FakeCtx()

    monkeypatch.setattr(feeder, "build_session_factory", lambda _db: _FakeFactory())
    monkeypatch.setattr(feeder, "DocumentRepository", _FakeRepo)
    monkeypatch.setattr(
        feeder, "select_real_analysis_candidates", lambda docs, **kw: ([], {"eligible": 0})
    )

    result = await feeder.run_real_analysis_paper_feed_once()
    assert result.armed is True
    assert result.refusal_code is None


@pytest.mark.asyncio
async def test_feeder_disarmed_in_paper_premium_limited_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint S3 (#181): paper_premium_limited keeps the learning route CLOSED
    even with the feeder master enabled — fail-closed no-op."""
    from app.core.enums import EntryMode
    from app.core.settings import ExecutionSettings

    settings = AppSettings(
        execution=ExecutionSettings(entry_mode=EntryMode.PAPER_PREMIUM_LIMITED),
        real_analysis_paper=RealAnalysisPaperSettings(enabled=True),
    )
    monkeypatch.setattr(feeder, "get_settings", lambda: settings)

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("DB must not be touched when disarmed")

    monkeypatch.setattr(feeder, "build_session_factory", _boom)

    result = await feeder.run_real_analysis_paper_feed_once()
    assert result.armed is False
    assert result.refusal_code == "learning_route_closed_in_paper_premium_limited"
