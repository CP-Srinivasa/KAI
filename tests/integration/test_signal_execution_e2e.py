"""KAI CORE v1 — Use Case B: Signal-Execution End-to-End (Mission §10).

Kette: externes Ereignis → Normalisierung → Bewertung → Policy → Paper-Execution
→ Persistenz → Auswertung → Audit. Zwei Eingänge:

Pfad A — TradingView-Webhook (eine Kette in EINEM Test):
    ``POST /tradingview/webhook`` (echte App aus ``create_app()``, HMAC-signiert)
    → ``tradingview_pending_signals.jsonl`` (Normalisierung, row-HMAC)
    → ``persist_tv_events_as_alert_audits`` (Persistenz als AlertAudit mit Provenance)
    → ``auto_promote_pending`` (Bewertung: Eligibility-Gate, Decision-Log)
    → ``feed_tv_paper`` (Envelope-Bildung, Preis aus dem Alert — kein Netz)
    → ``bridge.run_tick`` (Policy: Allowlist/TTL/Geometrie/Risk/Entry-Mode → Paper-Fill)
    → ``PaperExecutionEngine.monitor_positions`` (Auswertung: TP-Tick schließt)
    → ``build_signal_execution_status`` (read-only Status-Konsument)
    → Audit-Asserts über alle Streams (correlation_id, Lifecycle-Reihenfolge,
      keine Secrets im Audit).

Pfad B — Telegram-Premium: erweitert den bestehenden Fill
(``test_premium_telegram_approved_signal_reaches_paper_fill``) um Teil-TP,
Stop-Close, PnL-Vorzeichen und den Trail-Verdict (``build_trail``).

Was der Test NICHT fälscht: Webhook-Auth, Replay-Cache, Normalizer, Feeder,
Bridge, RiskEngine, PaperExecutionEngine, Audit-Writer, Status-/Trail-Konsumenten.
Einziger Ersatz: die Marktdaten-Quelle (``price_provider`` deterministisch,
``bridge._fetch_price`` verboten) — der Suite-Vertrag aus
``test_premium_pipeline_e2e``.

Bekannte Befunde (heutige Wahrheit, hier dokumentiert statt umgangen):
1. Der Webhook-Router ist „audit-only" (``app/api/routers/tradingview.py:1-7``):
   ohne ``TRADINGVIEW_WEBHOOK_SIGNAL_ROUTING_ENABLED`` landet kein Event im
   Pending-Log. Der Test schaltet das Flag explizit ein.
2. Pending → Envelope ist KEIN automatischer Pfad: Promotion (``auto_promote``)
   und Paper-Feeder (``feed_tv_paper``) sind zwei unverbundene Konsumenten des
   Pending-Logs; die promoted Candidates haben keinen Execution-Konsumenten
   (``promoted_consumer_enabled`` ist ohne Referenz in ``app/``).
3. Alerts OHNE ``price`` werden heute zu 100 % als ``unsupported_event``
   abgelehnt (``tradingview_auto_promote.py:88``) und vom Feeder nur mit
   Live-OHLCV-Adapter (Netz) verarbeitet
   → ``test_tradingview_alert_without_price_is_rejected_today``.
4. ``run_position_monitor_once`` (``app/orchestrator/trading_loop.py:2085``)
   hat keine Preisquellen-Injektion — der Test tritt eine Ebene tiefer bei
   ``engine.monitor_positions`` ein (derselbe Aufruf wie ``trading_loop.py:835``).
5. ``paper_execution_audit.jsonl`` trägt keine Hash-Kette (``paper_engine.py:1836``
   ``_append_audit``: kein ``prev_hash``) — Chain-Asserts sind hier nicht möglich,
   nur correlation_id-Kontinuität.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.alerts.tv_bridge import persist_tv_events_as_alert_audits
from app.api.main import create_app
from app.api.routers import tradingview as tv_router
from app.core.settings import get_settings
from app.execution import envelope_to_paper_bridge as bridge
from app.execution.paper_engine_singleton import get_paper_engine, reset_paper_engine_cache
from app.execution.signal_execution_status import build_signal_execution_status
from app.observability.premium_signal_trail import build_trail
from app.observability.tradingview_auto_promote import auto_promote_pending
from app.observability.tradingview_paper_feeder import SOURCE as TV_SOURCE
from app.observability.tradingview_paper_feeder import feed_tv_paper
from app.signals.tradingview_event import TV_ROW_HMAC_FIELD, verify_row_hmac
from app.signals.tradingview_promotion import load_pending_events
from tests.integration.test_premium_pipeline_e2e import (
    PREMIUM_SOL_LONG,
    _emit_and_approve,
    _fixed_price_provider,
    _FixedBridgeDatetime,
    _forbid_live_market_data,
    _read_jsonl,
    isolated_premium_artifacts,  # noqa: F401 — Fixture-Re-Export für Pfad B
)

_WEBHOOK_SECRET = "e2e-webhook-secret-never-in-audit-32b!!"
_BRIDGE_ROW_SECRET = "e2e-bridge-row-hmac-secret-never-in-audit"
_SIGNATURE_HEADER = "X-KAI-Signature"
_SYMBOL = "BTC/USDT"
_ALERT_PRICE = 65_000.0


def _sign(body: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post_alert(client: TestClient, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body), "Content-Type": "application/json"},
    )


def _events(records: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [rec for rec in records if rec.get("event_type") == event_type]


@pytest.fixture
def tv_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolierte Artefakt-Pfade + reale Settings via Env für die ganze TV-Kette.

    Muster ``isolated_premium_artifacts``: cwd → tmp_path, Bridge-/Paper-Streams
    auf tmp umgebogen (der Wächter ``_paper_audit_stream_untouched`` bleibt scharf).
    Webhook, Routing, Pending-/Decision-/Promoted-Logs und Bridge-Flags kommen als
    Env, damit App, Router und Bridge dieselben ``get_settings()`` sehen.
    """
    reset_paper_engine_cache()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bridge, "_ENVELOPE_LOG", artifacts / "telegram_message_envelope.jsonl")
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", artifacts / "bridge_pending_orders.jsonl")
    monkeypatch.setattr(bridge, "_PAPER_AUDIT_LOG", artifacts / "paper_execution_audit.jsonl")
    monkeypatch.setattr(bridge, "_fetch_price", _forbid_live_market_data)

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("APP_API_KEY", "")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_AUTH_MODE", "hmac")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SHARED_TOKEN", "")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_REPLAY_CACHE_PERSISTENT", "false")
    monkeypatch.setenv(
        "TRADINGVIEW_WEBHOOK_AUDIT_LOG", str(artifacts / "tradingview_webhook_audit.jsonl")
    )
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SIGNAL_ROUTING_ENABLED", "true")
    monkeypatch.setenv(
        "TRADINGVIEW_WEBHOOK_PENDING_SIGNALS_LOG",
        str(artifacts / "tradingview_pending_signals.jsonl"),
    )
    monkeypatch.setenv(
        "TRADINGVIEW_PENDING_DECISIONS_LOG", str(artifacts / "tradingview_pending_decisions.jsonl")
    )
    monkeypatch.setenv(
        "TRADINGVIEW_PROMOTED_SIGNALS_LOG", str(artifacts / "tradingview_promoted_signals.jsonl")
    )
    monkeypatch.setenv("TRADINGVIEW_BRIDGE_HMAC_SECRET", _BRIDGE_ROW_SECRET)

    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", TV_SOURCE)
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "24")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_ENTRY_TOLERANCE_PCT", "0.5")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "paper")
    get_settings.cache_clear()

    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()
    tv_router.reset_pending_signal_writer()
    yield artifacts
    tv_router._reset_replay_cache_for_tests()
    reset_paper_engine_cache()


def _tv_paths(artifacts: Path) -> dict[str, Path]:
    return {
        "webhook_audit": artifacts / "tradingview_webhook_audit.jsonl",
        "pending": artifacts / "tradingview_pending_signals.jsonl",
        "decisions": artifacts / "tradingview_pending_decisions.jsonl",
        "promoted": artifacts / "tradingview_promoted_signals.jsonl",
        "alert_audit": artifacts / "alert_audit.jsonl",
        "envelope": artifacts / "telegram_message_envelope.jsonl",
        "bridge": artifacts / "bridge_pending_orders.jsonl",
        "paper": artifacts / "paper_execution_audit.jsonl",
    }


async def _webhook_to_envelope(
    artifacts: Path, payload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, object]]:
    """Webhook → Pending → Envelope. Gibt (Webhook-Response, Envelope|None, Feeder-Summary)."""
    p = _tv_paths(artifacts)
    # Ohne Lifespan (Muster ``tests/conftest.py::client``): Router + Middleware-Stack sind echt,
    # Scheduler/DB-Boot gehören zu ``test_startup_minimal_env``.
    client = TestClient(create_app())
    resp = _post_alert(client, payload)
    assert resp.status_code == 202, resp.text
    events = load_pending_events(p["pending"])
    summary = await feed_tv_paper(
        events=events,
        adapter=None,  # kein OHLCV-Fallback → kein Netz; Preis muss aus dem Alert kommen
        consumed_ids=set(),
        envelope_log=bridge._ENVELOPE_LOG,
    )
    envelopes = _read_jsonl(p["envelope"])
    return resp.json(), (envelopes[-1] if envelopes else None), summary


# ── Pfad A: TradingView → Paper-Fill → TP-Close → Audit ──────────────────────


@pytest.mark.asyncio
async def test_tradingview_alert_fills_paper_and_closes_at_take_profit_e2e(
    tv_artifacts: Path,
) -> None:
    p = _tv_paths(tv_artifacts)
    payload = {
        "ticker": "BTCUSDT",
        "action": "buy",
        "price": _ALERT_PRICE,
        "event_id": "tv-e2e-btc-001",
        "strategy": "e2e_breakout",
    }

    # 1) Externes Ereignis: HMAC-signierter Webhook gegen die echte App (Middleware-Stack inkl.).
    client = TestClient(create_app())
    resp = _post_alert(client, payload)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["request_id"].startswith("tvwh_")
    assert body["event_id"].startswith("tvsig_")
    assert body["signal_path_id"] == "tvpath_webhook_v1"

    # Replay-Schutz gehört zur Kette: identischer Body → 409, kein zweites Pending-Event.
    replay = _post_alert(client, payload)
    assert replay.status_code == 409

    webhook_audit = _read_jsonl(p["webhook_audit"])
    accepted = [rec for rec in webhook_audit if rec.get("outcome") == "accepted"]
    assert len(accepted) == 1
    assert accepted[0]["request_id"] == body["request_id"]
    assert accepted[0]["provenance"] == {
        "source": TV_SOURCE,
        "version": "tv-3",
        "signal_path_id": "tvpath_webhook_v1",
        "auth_method": "hmac",
    }
    assert accepted[0]["routing"]["status"] == "emitted"
    assert accepted[0]["routing"]["event_id"] == body["event_id"]
    assert webhook_audit[-1]["reason"] == "replay"

    # 2) Normalisierung: genau ein Pending-Event, row-HMAC gültig (SENTR-F-004).
    pending_rows = _read_jsonl(p["pending"])
    assert len(pending_rows) == 1
    assert verify_row_hmac(pending_rows[0], _BRIDGE_ROW_SECRET) is True
    events = load_pending_events(p["pending"])
    assert len(events) == 1
    event = events[0]
    assert event.event_id == body["event_id"]
    assert (event.ticker, event.action, event.price) == ("BTCUSDT", "buy", _ALERT_PRICE)
    assert event.source_request_id == body["request_id"]
    assert event.provenance.source == TV_SOURCE

    # 3) Persistenz: TV-Event als AlertAuditRecord mit Provenance (HMAC-verifiziert gelesen).
    persisted = persist_tv_events_as_alert_audits(
        tv_pending_path=p["pending"],
        alert_audit_path=p["alert_audit"],
        hmac_secret=_BRIDGE_ROW_SECRET,
    )
    assert persisted["written"] == 1, persisted
    assert persisted["skipped_tampered"] == 0 and persisted["skipped_unsigned"] == 0
    alert_rows = _read_jsonl(p["alert_audit"])
    assert alert_rows[-1]["document_id"] == f"tv:{event.event_id}"
    assert alert_rows[-1]["provenance"]["source"] == TV_SOURCE
    assert alert_rows[-1]["provenance"]["signal_path_id"] == "tvpath_webhook_v1"

    # 4) Bewertung: Eligibility-Gate (technical, long) → promoted + Decision-Log.
    promotion = auto_promote_pending(
        pending_path=p["pending"],
        decisions_path=p["decisions"],
        promoted_path=p["promoted"],
        now_iso=datetime.now(UTC).isoformat(),
    )
    assert promotion["promoted"] == 1 and promotion["rejected"] == 0, promotion
    decisions = _read_jsonl(p["decisions"])
    assert decisions[-1]["event_id"] == event.event_id
    assert decisions[-1]["decision"] == "promoted"
    promoted = _read_jsonl(p["promoted"])
    assert promoted[-1]["symbol"] == "BTCUSDT" and promoted[-1]["direction"] == "long"
    assert promoted[-1]["entry_price"] == _ALERT_PRICE

    # 5) Envelope-Bildung: Preis aus dem Alert, kein OHLCV-Adapter (Netz verboten).
    feed = await feed_tv_paper(
        events=events,
        adapter=None,
        consumed_ids=set(),
        envelope_log=bridge._ENVELOPE_LOG,
    )
    assert feed["emitted"] == 1 and feed["no_price"] == 0, feed
    envelope = _read_jsonl(p["envelope"])[-1]
    envelope_id = str(envelope["envelope_id"])
    assert envelope_id.startswith("ENV-TVP-")
    assert envelope["source"] == TV_SOURCE and envelope["stage"] == "accepted"
    env_payload = envelope["payload"]
    assert env_payload["display_symbol"] == _SYMBOL
    assert env_payload["entry_value"] == _ALERT_PRICE
    assert env_payload["stop_loss"] < _ALERT_PRICE < env_payload["targets"][0]
    assert env_payload["strategy"] == "e2e_breakout"
    take_profit = float(env_payload["targets"][0])

    # 6) Policy + Paper-Execution: echte Bridge, echter RiskEngine, echte PaperExecutionEngine.
    tick = await bridge.run_tick(price_provider=_fixed_price_provider(_SYMBOL, _ALERT_PRICE))
    assert tick.enabled is True
    assert tick.filled == 1, tick.to_dict()
    assert tick.rejected_entry_mode == 0 and tick.rejected_risk == 0, tick.to_dict()

    bridge_rows = _read_jsonl(p["bridge"])
    filled = [rec for rec in bridge_rows if rec.get("stage") == "filled"]
    assert len(filled) == 1
    bridge_fill = filled[0]
    assert bridge_fill["envelope_id"] == envelope_id
    assert bridge_fill["correlation_id"] == envelope_id  # TV: kein Approval-Re-Emit → cid == env
    assert bridge_fill["source"] == TV_SOURCE
    assert bridge_fill["symbol"] == _SYMBOL
    assert bridge_fill["lifecycle_state"] == "POSITION_OPEN"
    assert bridge_fill["take_profit"] == pytest.approx(take_profit)
    assert bridge_fill["take_profit_tiers"] == []  # ein Target → Legacy-TP-Pfad (kein Ladder)
    assert bridge_fill["order_intent"]["correlation_id"] == envelope_id

    engine = get_paper_engine()
    position = engine.portfolio.positions[_SYMBOL]
    assert position.correlation_id == envelope_id
    assert position.source == TV_SOURCE
    assert position.take_profit == pytest.approx(take_profit)
    assert position.quantity > 0
    entry_fill_price = position.avg_entry_price

    paper_rows = _read_jsonl(p["paper"])
    order_fills = _events(paper_rows, "order_filled")
    assert len(order_fills) == 1
    assert order_fills[0]["correlation_id"] == envelope_id
    assert order_fills[0]["symbol"] == _SYMBOL

    # 7) Auswertung: Monitor-Tick über dem Take-Profit schließt die Position (reason=take).
    #    Gleicher Engine-Aufruf wie trading_loop.run_position_monitor (trading_loop.py:835);
    #    die Preisquelle ist deterministisch statt Marktdaten-Adapter (Befund 4 im Modul-Doc).
    exit_price = take_profit * 1.001
    close_fills = engine.monitor_positions({_SYMBOL: exit_price}, {_SYMBOL: "e2e_fixed"})
    assert len(close_fills) == 1
    assert _SYMBOL not in engine.portfolio.positions
    assert engine.portfolio.realized_pnl_usd > 0

    paper_rows = _read_jsonl(p["paper"])
    closed = _events(paper_rows, "position_closed")
    assert len(closed) == 1
    close = closed[0]
    assert close["correlation_id"] == envelope_id
    assert close["reason"] == "take"
    assert close["signal_source"] == TV_SOURCE
    assert close["price_source"] == "e2e_fixed"
    assert close["entry_price"] == pytest.approx(entry_fill_price)
    assert close["exit_price"] > close["entry_price"]  # long, Preis über Entry
    assert close["trade_pnl_usd"] > 0  # Vorzeichen korrekt (netto, nach Fee/Slippage)
    assert close["trade_pnl_usd"] == pytest.approx(close_fills[0].pnl_usd)
    assert close["realized_pnl_usd"] == pytest.approx(engine.portfolio.realized_pnl_usd)

    # 8) Audit-Kontinuität: EINE correlation_id durch Envelope → Bridge → Paper;
    #    Lifecycle- und Signal-State-Reihenfolge.
    lifecycle = _events(paper_rows, "lifecycle_transition")
    assert [rec["to_state"] for rec in lifecycle] == [
        "ORDER_SUBMITTED",
        "ORDER_ACCEPTED",
        "POSITION_OPEN",
        "TP_HIT",
    ]
    assert {rec["correlation_id"] for rec in lifecycle} == {envelope_id}
    assert {rec["correlation_id"] for rec in bridge_rows} == {envelope_id}
    signal_states = _events(paper_rows, "signal_state_transition")
    assert [(rec["from_state"], rec["to_state"]) for rec in signal_states] == [
        ("approved", "executed"),
        ("executed", "closed"),
    ]
    assert {rec["decision_id"] for rec in signal_states} == {envelope_id}
    assert all(rec.get("schema_version") == "v2" for rec in paper_rows)

    status = build_signal_execution_status(
        bridge_log_path=p["bridge"],
        paper_audit_log_path=p["paper"],
        entry_watcher_log_path=tv_artifacts / "entry_watcher_audit.jsonl",
    )
    assert status["filled"] == 1 and status["total_correlations"] == 1
    assert status["lifecycle_state_counts"] == {"TP_HIT": 1}
    assert status["recent"][0]["correlation_id"] == envelope_id

    # Kein Secret in irgendeinem Audit-Stream (Webhook-Secret, Row-HMAC-Secret, Signatur-Header).
    signature = _sign(json.dumps(payload).encode("utf-8"))
    for path in sorted(tv_artifacts.glob("*.jsonl")):
        text = path.read_text(encoding="utf-8")
        assert _WEBHOOK_SECRET not in text, path.name
        assert _BRIDGE_ROW_SECRET not in text, path.name
        assert signature not in text, path.name
    # Der Row-HMAC selbst steht nur im Pending-Log — nie in einem Downstream-Stream.
    row_sig = str(pending_rows[0][TV_ROW_HMAC_FIELD])
    for name in ("alert_audit", "envelope", "bridge", "paper"):
        assert row_sig not in p[name].read_text(encoding="utf-8"), name

    observed_chain = {
        "webhook_202": body["status"] == "accepted",
        "pending": event.event_id == body["event_id"],
        "alert_audit": persisted["written"] == 1,
        "promoted": promotion["promoted"] == 1,
        "envelope": envelope["source"] == TV_SOURCE,
        "bridge_filled": bridge_fill["stage"] == "filled",
        "paper_filled": bool(order_fills),
        "position_closed": close["reason"] == "take",
        "status_consumer": status["filled"] == 1,
    }
    assert all(observed_chain.values()), observed_chain


@pytest.mark.asyncio
async def test_tradingview_alert_without_price_is_rejected_today(tv_artifacts: Path) -> None:
    """Heutige Wahrheit (Befund 3): Operator-Alerts ohne ``price`` sind nicht ausführbar.

    Der Webhook nimmt sie an (202, Pending-Event mit ``price=None``), aber
    ``auto_promote_pending`` lehnt sie als ``unsupported_event`` ab und
    ``feed_tv_paper`` erzeugt ohne OHLCV-Adapter (= ohne Netz) keinen Envelope
    (``no_price``, Event bleibt unkonsumiert). Die Bridge sieht nichts.
    """
    p = _tv_paths(tv_artifacts)
    payload = {"ticker": "ETHUSDT", "action": "buy", "event_id": "tv-e2e-eth-noprice"}

    body, envelope, feed = await _webhook_to_envelope(tv_artifacts, payload)
    assert body["status"] == "accepted"
    events = load_pending_events(p["pending"])
    assert len(events) == 1 and events[0].price is None

    promotion = auto_promote_pending(
        pending_path=p["pending"],
        decisions_path=p["decisions"],
        promoted_path=p["promoted"],
        now_iso=datetime.now(UTC).isoformat(),
    )
    assert promotion == {"enabled": True, "open_events": 1, "promoted": 0, "rejected": 1}
    decision = _read_jsonl(p["decisions"])[-1]
    assert decision["decision"] == "rejected"
    assert decision["operator_reason"] == "auto_promote:unsupported_event"
    assert not p["promoted"].exists()

    assert envelope is None
    assert feed["emitted"] == 0 and feed["no_price"] == 1, feed

    tick = await bridge.run_tick(price_provider=_fixed_price_provider("ETH/USDT", 3000.0))
    assert tick.enabled is True and tick.envelopes_scanned == 0, tick.to_dict()
    assert get_paper_engine().portfolio.positions == {}
    assert not p["paper"].exists() or _events(_read_jsonl(p["paper"]), "order_filled") == []


@pytest.mark.asyncio
async def test_tradingview_entry_mode_disabled_blocks_paper_fill_e2e(
    tv_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kill-Switch: ``EXECUTION_ENTRY_MODE=disabled`` → Envelope entsteht, Bridge füllt nicht."""
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    get_settings.cache_clear()
    p = _tv_paths(tv_artifacts)
    payload = {"ticker": "BTCUSDT", "action": "buy", "price": _ALERT_PRICE, "event_id": "tv-ks-1"}

    body, envelope, feed = await _webhook_to_envelope(tv_artifacts, payload)
    assert body["status"] == "accepted"
    assert envelope is not None and feed["emitted"] == 1

    tick = await bridge.run_tick(price_provider=_fixed_price_provider(_SYMBOL, _ALERT_PRICE))
    assert tick.enabled is True
    assert tick.filled == 0, tick.to_dict()
    assert tick.rejected_entry_mode == 1, tick.to_dict()

    terminal = _read_jsonl(p["bridge"])[-1]
    assert terminal["envelope_id"] == envelope["envelope_id"]
    assert terminal["stage"] == "rejected_entry_mode"
    assert terminal["reason"] == "entry_mode_disabled"
    assert terminal["reason_codes"] == ["ENTRY_MODE_DISABLED"]
    assert terminal["entry_mode"] == "disabled"
    assert terminal["lifecycle_state"] == "REJECTED_INVALID_SIGNAL"
    # report-then-refuse: Risk-Diagnostik ist trotzdem da.
    assert "risk_gate_would_reject" in terminal

    assert _SYMBOL not in get_paper_engine().portfolio.positions
    assert _events(_read_jsonl(p["paper"]), "order_filled") == []


# ── Pfad B: Telegram-Premium → Fill → Teil-TP → Stop-Close → Trail-Verdict ────


@pytest.mark.asyncio
async def test_premium_telegram_fill_partial_tp_then_stop_close_with_pnl_and_trail(
    isolated_premium_artifacts: Path,  # noqa: F811 — importierte Fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erweitert ``test_premium_telegram_approved_signal_reaches_paper_fill`` um die
    Auswertung: SOL Long 84.20, Targets 85.08/86.10/87.40, SL 83.78.

    Tick 1 @ 85.50 → Tier 1 (85.08) wird konsumiert: ``position_partial_closed``
    mit positivem ``trade_pnl_usd``, Lifecycle ``PARTIAL_TP_HIT``.
    Tick 2 @ 83.70 → Stop: ``position_closed`` (reason=stop) mit negativem
    ``trade_pnl_usd``, Lifecycle ``SL_HIT``; Position weg; Trail-Verdict ``CLOSED_SL``.
    """
    artifacts = isolated_premium_artifacts
    envelope_log = artifacts / "telegram_message_envelope.jsonl"
    bridge_log = artifacts / "bridge_pending_orders.jsonl"
    paper_log = artifacts / "paper_execution_audit.jsonl"
    emitted_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(bridge, "_fetch_price", _forbid_live_market_data)
    monkeypatch.setattr(bridge, "datetime", _FixedBridgeDatetime)
    origin_id, approved_id = _emit_and_approve(
        PREMIUM_SOL_LONG,
        envelope_log=envelope_log,
        emitted_at=emitted_at,
        approved_at=emitted_at + timedelta(minutes=3),
    )

    tick = await bridge.run_tick(price_provider=_fixed_price_provider("SOL/USDT", 84.2))
    assert tick.filled == 1, tick.to_dict()
    bridge_fill = [r for r in _read_jsonl(bridge_log) if r.get("stage") == "filled"][0]
    assert bridge_fill["correlation_id"] == origin_id
    assert [t["price"] for t in bridge_fill["take_profit_tiers"]] == [85.08, 86.10, 87.40]

    engine = get_paper_engine()
    position = engine.portfolio.positions["SOL/USDT"]
    initial_qty = position.quantity
    assert position.correlation_id == origin_id
    assert [price for price, _share in position.take_profit_tiers] == [85.08, 86.10, 87.40]

    # Auswertung 1: Take-Profit-Tier 1 (85.08) getroffen, Tier 2 (86.10) nicht.
    tier_fills = engine.monitor_positions({"SOL/USDT": 85.50}, {"SOL/USDT": "e2e_fixed"})
    assert len(tier_fills) == 1
    residual = engine.portfolio.positions["SOL/USDT"]
    assert residual.quantity == pytest.approx(initial_qty * (2 / 3), rel=1e-6)
    assert [price for price, _share in residual.take_profit_tiers] == [86.10, 87.40]

    partial = _events(_read_jsonl(paper_log), "position_partial_closed")
    assert len(partial) == 1
    assert partial[0]["correlation_id"] == origin_id
    assert partial[0]["tier_price"] == pytest.approx(85.08)
    assert partial[0]["trade_pnl_usd"] > 0
    assert partial[0]["remaining_quantity"] == pytest.approx(residual.quantity)
    assert [t["price"] for t in partial[0]["remaining_tiers"]] == [86.10, 87.40]

    # Auswertung 2: Stop (83.78) unterschritten → Rest wird geschlossen, Verlust gebucht.
    stop_fills = engine.monitor_positions({"SOL/USDT": 83.70}, {"SOL/USDT": "e2e_fixed"})
    assert len(stop_fills) == 1
    assert "SOL/USDT" not in engine.portfolio.positions

    paper_rows = _read_jsonl(paper_log)
    closed = _events(paper_rows, "position_closed")
    assert len(closed) == 1
    close = closed[0]
    assert close["correlation_id"] == origin_id
    assert close["reason"] == "stop"
    assert close["quantity"] == pytest.approx(residual.quantity)
    assert close["exit_price"] < close["entry_price"]
    assert close["trade_pnl_usd"] < 0
    assert close["trade_pnl_usd"] == pytest.approx(stop_fills[0].pnl_usd)
    assert close["realized_pnl_usd"] == pytest.approx(
        partial[0]["trade_pnl_usd"] + close["trade_pnl_usd"]
    )
    assert close["realized_pnl_usd"] == pytest.approx(engine.portfolio.realized_pnl_usd)

    lifecycle = _events(paper_rows, "lifecycle_transition")
    assert [rec["to_state"] for rec in lifecycle] == [
        "ORDER_SUBMITTED",
        "ORDER_ACCEPTED",
        "POSITION_OPEN",
        "PARTIAL_TP_HIT",
        "SL_HIT",
    ]
    assert {rec["correlation_id"] for rec in lifecycle} == {origin_id}

    # Outcome-Konsument: der Premium-Trail joint alle Streams und urteilt CLOSED_SL.
    trail = build_trail(
        envelope_records=_read_jsonl(envelope_log),
        bridge_records=_read_jsonl(bridge_log),
        paper_records=paper_rows,
    )
    assert len(trail) == 1
    entry = trail[0]
    assert entry.envelope_id == origin_id
    assert entry.approved_envelope_id == approved_id
    assert entry.overall == "CLOSED_SL"
    assert entry.is_open is False
    assert entry.paper_close_reason == "stop"
    assert entry.realized_pnl_usd == pytest.approx(engine.portfolio.realized_pnl_usd)
