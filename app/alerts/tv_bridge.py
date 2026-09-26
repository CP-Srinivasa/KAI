"""TV-Webhook -> AlertAudit Bridge.

Bridges the TradingView-webhook pipeline (``artifacts/tradingview_pending_signals.jsonl``)
into the alert-audit system (``artifacts/alert_audit.jsonl``) so the existing
auto-annotator (``app/alerts/auto_annotator.py``) can compute hit/miss outcomes
for TV events the same way it does for RSS-sourced directional alerts.

Design:
- Append-only, idempotent. ``document_id=f"tv:{event_id}"`` is the dedup key.
- Skips events whose ticker base asset is not in the CoinGecko-supported
  set (``_BASE_ASSET_TO_COINGECKO``), because the auto-annotator would be
  unable to resolve a market price for them.
- Does NOT modify the TV-pipeline files. One-way fan-out from TV -> audit.
- Reversible: delete ``tv:*`` rows from ``alert_audit.jsonl`` to undo.

Rationale: D-125 TV-Pivot requires TV-precision to be measurable against
RSS-precision. The TV-pipeline (events/decisions/promoted/consumed) lives on
``event_id``/``decision_id`` keys; the alert-outcome system lives on
``document_id`` keys. Without this bridge there is no structural path for
TV events to ever be annotated hit/miss and the Wilson-CI split verdict
``insufficient_sample_for_split_comparison`` cannot change.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import structlog

from app.alerts.alert_debounce import (
    DEBOUNCE_BLOCK_REASON,
    AlertDebouncer,
    debounce_window_from_env,
    seed_debouncer_from_audit_rows,
)
from app.alerts.audit import (
    AlertAuditRecord,
    append_alert_audit,
    iter_alert_audit_rows,
)
from app.alerts.blocked_audit import (
    BlockedAlertRecord,
    append_blocked_alert,
    load_blocked_alerts,
)
from app.market_data.coingecko_adapter import _BASE_ASSET_TO_COINGECKO
from app.signals.models import SignalProvenance
from app.signals.tradingview_event import TV_ROW_HMAC_FIELD, verify_row_hmac

log = structlog.get_logger(__name__)

_KNOWN_QUOTES: tuple[str, ...] = ("USDT", "USDC", "BUSD", "FDUSD", "USD")
_ACTION_TO_SENTIMENT: dict[str, str] = {"buy": "bullish", "sell": "bearish"}
_TV_CHANNEL: str = "tradingview_webhook"
_TV_SOURCE: str = "tradingview_webhook"

# SENTR-F-005: per-tick cap. Guards against a malformed / DOS-style pending
# file (e.g. 10k rows at once) from blocking the event-loop + CoinGecko
# quota during a single bridge tick. The overflow is NOT dropped — it just
# waits for the next tick. Default 500 is ~3x the expected daily peak.
_DEFAULT_MAX_EVENTS_PER_TICK: int = 500

# Die Pending-Datei wird nie abgeraeumt (V10, Modulkopf), der Scheduler liest sie
# je Takt ganz. Ein uebersprungenes Ereignis stand deshalb bei JEDEM Takt erneut im
# Log: am 26.09.2026 316 651 Zeilen fuer 1 408 Ereignisse im server.log (~60-70 MB
# pro Tag). Jetzt einmal je Ereignis und Prozess; die Zaehler zaehlen weiter jeden
# Takt. Obergrenze, damit ein wachsender Bestand den Speicher nicht mitwachsen laesst.
_SKIPS_LOGGED: set[tuple[str, str]] = set()
_SKIPS_LOGGED_MAX: int = 100_000


def _log_skip_once(event_name: str, event_id: str, **fields: object) -> None:
    key = (event_name, event_id)
    if key in _SKIPS_LOGGED:
        return
    if len(_SKIPS_LOGGED) >= _SKIPS_LOGGED_MAX:
        _SKIPS_LOGGED.clear()
    _SKIPS_LOGGED.add(key)
    log.info(event_name, event_id=event_id, **fields)


# SENTR-F-006: log-hygiene — strip newlines/CR/tabs (log-injection guard)
# and cap length so an attacker-controlled `note` can't forge fake log
# lines or blow up line-based log shippers.
_LOG_NOTE_MAX_LEN: int = 200


def _parse_received_at(raw: object) -> datetime | None:
    """``received_at`` als Zeitpunkt, oder ``None``.

    ``None`` heisst fuer den Entpreller: nicht vergleichbar, also durchlassen.
    Ein unlesbarer Zeitstempel darf kein Ereignis verschlucken.
    """
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _sanitize_for_log(value: object) -> str | None:
    """Strip CR/LF/tab and cap length. None-safe."""
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
    if not cleaned:
        return None
    if len(cleaned) > _LOG_NOTE_MAX_LEN:
        cleaned = cleaned[:_LOG_NOTE_MAX_LEN] + "..."
    return cleaned


def _split_ticker(ticker: str) -> tuple[str, str] | None:
    up = ticker.strip().upper()
    # Normalize common TradingView chart-symbol forms so the base asset still
    # resolves when the operator alerts on a perp/exchange-prefixed chart:
    #   - exchange prefix:  "BYBIT:SOLUSDT" -> "SOLUSDT"
    #   - perp suffix:      "BTCUSD.P" / "ETHUSDT.PERP" -> "BTCUSD" / "ETHUSDT"
    # Dated-futures codes (e.g. "SOLM2026") carry no clean base/quote and stay
    # unmapped ON PURPOSE — alert on the perp/spot symbol for those instead.
    up = up.split(":", 1)[-1]
    for _suffix in (".PERP", ".P"):  # ".PERP" first so ".P" doesn't truncate it
        if up.endswith(_suffix):
            up = up[: -len(_suffix)]
            break
    for quote in _KNOWN_QUOTES:
        if up.endswith(quote) and len(up) > len(quote):
            return up[: -len(quote)], quote
    if up in _BASE_ASSET_TO_COINGECKO:
        return up, ""
    return None


def _is_smoke_event(note: object) -> bool:
    """Match the heuristic used by provenance_metrics._summarize_tv_pipeline."""
    if not isinstance(note, str):
        return False
    lowered = note.lower()
    return "smoke" in lowered or "test" in lowered


def persist_tv_events_as_alert_audits(
    *,
    tv_pending_path: Path,
    alert_audit_path: Path,
    include_smoke: bool = False,
    max_events_per_tick: int = _DEFAULT_MAX_EVENTS_PER_TICK,
    hmac_secret: str = "",
    blocked_alerts_path: Path | None = None,
    debounce_window_minutes: int | None = None,
) -> dict[str, int]:
    """Append synthetic AlertAuditRecords for TV-webhook events. Idempotent.

    When ``include_smoke`` is False (default), events whose ``note`` contains
    "smoke" or "test" are filtered out — same heuristic as
    ``provenance_metrics._summarize_tv_pipeline``. This keeps the TV precision
    bucket free of test-payload noise that carries synthetic entry prices
    disconnected from real market conditions.

    ``max_events_per_tick`` caps the number of *written* rows per call
    (SENTR-F-005). When reached, remaining events contribute to
    ``skipped_overflow`` and wait for the next tick. Default 500.

    When ``hmac_secret`` is non-empty (SENTR-F-004), each pending row must
    carry a valid ``_sig`` HMAC-SHA256 over its canonical JSON. Rows with no
    ``_sig`` are counted as ``skipped_unsigned``; rows whose ``_sig`` fails
    verification are counted as ``skipped_tampered``. Both are logged and
    not bridged into the audit — protecting the hit-rate metric from a
    local attacker who can write the file but does not hold the secret.
    Empty ``hmac_secret`` disables verification (legacy behaviour).

    Returns counts: ``written``, ``skipped_existing`` (already bridged),
    ``skipped_unsupported`` (base asset not in CoinGecko map or ticker
    unparseable), ``skipped_invalid`` (missing required event fields),
    ``skipped_smoke`` (filtered by smoke heuristic), ``skipped_overflow``
    (deferred to next tick by per-tick cap), ``skipped_unsigned`` (missing
    HMAC when secret active), ``skipped_tampered`` (HMAC mismatch),
    ``skipped_debounced`` und ``skipped_blocked`` (V10, siehe unten).

    **V10 — Entprellung (Daily Review 2026-09-16).** Gemessen ueber 09.-16.09.:
    293 der 355 dispatchten Alerts kamen aus diesem Pfad, alle bullish, auf zwei
    Basiswerten, mit einem Median-Abstand von 1,08 min. Die Trefferquote faellt
    monoton mit der Position in der Serie (erster Alert 51,9 %, ab dem 21. noch
    13,7 %). Mit ``blocked_alerts_path`` wird je ``(kanal, wert, richtung)``
    entprellt. Das Fenster kommt aus ``KAI_ALERT_DEBOUNCE_WINDOW_MIN`` (Default
    60), sofern ``debounce_window_minutes`` nichts anderes sagt; ``0`` schaltet
    die Entprellung ab, ein fehlender ``blocked_alerts_path`` ebenfalls — ohne
    den Blocked-Strom gaebe es keinen Ort, an dem eine Unterdrueckung
    nachweisbar bliebe.

    Entschieden wird in EREIGNISZEIT (``received_at``): die Bruecke raeumt die
    Pending-Datei nicht ab, also muss dasselbe Ereignis bei jedem Takt dieselbe
    Antwort bekommen. ``skipped_blocked`` zaehlt die Ereignisse, die in einem
    frueheren Takt bereits entprellt wurden.
    """
    counts = {
        "written": 0,
        "skipped_existing": 0,
        "skipped_unsupported": 0,
        "skipped_invalid": 0,
        "skipped_smoke": 0,
        "skipped_overflow": 0,
        "skipped_unsigned": 0,
        "skipped_tampered": 0,
        "skipped_debounced": 0,
        "skipped_blocked": 0,
    }
    if not tv_pending_path.exists():
        return counts

    # NEO-F-002: stream raw rows instead of allocating AlertAuditRecord per
    # audit row. Ein Durchgang, zwei Ergebnisse: die Dedup-Schluessel und der
    # Wiederanlauf des Entprellers (V10) lesen dieselben Zeilen.
    audit_rows = iter_alert_audit_rows(alert_audit_path)
    existing_ids = {
        doc_id
        for row in audit_rows
        if isinstance(row, dict) and isinstance(doc_id := row.get("document_id"), str)
    }

    # V10 (Daily Review 2026-09-16): Entprellung wiederholter Meldungen. Nur
    # aktiv, wenn ein Blocked-Strom mitgegeben wurde — ohne ihn gaebe es keinen
    # Ort, an dem eine Unterdrueckung nachweisbar bliebe, und ein Aufrufer
    # wuerde still Alerts verlieren.
    # ``debounce_window_minutes=None`` heisst "nimm die Umgebung" -- so bleibt
    # KAI_ALERT_DEBOUNCE_WINDOW_MIN der eine Schalter, den ein Operator ohne
    # Deploy umlegen kann. Ein ausdruecklich uebergebenes Fenster gewinnt.
    window = (
        debounce_window_from_env()
        if debounce_window_minutes is None
        else timedelta(minutes=debounce_window_minutes)
    )
    debouncer: AlertDebouncer | None = None
    blocked_ids: set[str] = set()
    if blocked_alerts_path is not None and window > timedelta(0):
        debouncer = AlertDebouncer(window=window)
        blocked_ids = {
            rec.document_id
            for rec in load_blocked_alerts(blocked_alerts_path)
            if rec.block_reason == DEBOUNCE_BLOCK_REASON
        }
        seed_debouncer_from_audit_rows(debouncer, audit_rows, channel=_TV_CHANNEL)

    for raw in tv_pending_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            counts["skipped_invalid"] += 1
            continue

        # SENTR-F-004: HMAC verification. Only enforced when a secret is
        # configured — keeps legacy deployments working and makes the
        # feature opt-in per-deployment.
        if hmac_secret:
            if TV_ROW_HMAC_FIELD not in event:
                counts["skipped_unsigned"] += 1
                log.warning(
                    "tv_bridge.skip_unsigned",
                    event_id=_sanitize_for_log(event.get("event_id")),
                )
                continue
            if not verify_row_hmac(event, hmac_secret):
                counts["skipped_tampered"] += 1
                log.warning(
                    "tv_bridge.skip_tampered",
                    event_id=_sanitize_for_log(event.get("event_id")),
                )
                continue

        event_id = event.get("event_id")
        ticker = event.get("ticker")
        action = (event.get("action") or "").lower()
        received_at = event.get("received_at")
        if not (
            isinstance(event_id, str)
            and isinstance(ticker, str)
            and isinstance(received_at, str)
            and action
        ):
            counts["skipped_invalid"] += 1
            continue

        if not include_smoke and _is_smoke_event(event.get("note")):
            counts["skipped_smoke"] += 1
            _log_skip_once(
                "tv_bridge.skip_smoke", event_id, note=_sanitize_for_log(event.get("note"))
            )
            continue

        doc_id = f"tv:{event_id}"
        if doc_id in existing_ids:
            counts["skipped_existing"] += 1
            continue

        # V10: Die Bruecke raeumt die Pending-Datei nicht ab (siehe Modulkopf).
        # Ein einmal entprelltes Ereignis wuerde sonst bei jedem Takt erneut
        # geprueft und spaeter, nach Ablauf des Fensters, verspaetet gesendet.
        if doc_id in blocked_ids:
            counts["skipped_blocked"] += 1
            continue

        # SENTR-F-005: cap writes per tick. Remaining rows that would
        # otherwise be written contribute to skipped_overflow and are
        # picked up next tick.
        if counts["written"] >= max_events_per_tick:
            counts["skipped_overflow"] += 1
            continue

        split = _split_ticker(ticker)
        if split is None:
            _log_skip_once("tv_bridge.skip_unsupported_quote", event_id, ticker=ticker)
            counts["skipped_unsupported"] += 1
            continue
        base, quote = split
        if base not in _BASE_ASSET_TO_COINGECKO:
            _log_skip_once("tv_bridge.skip_unsupported_base", event_id, base=base)
            counts["skipped_unsupported"] += 1
            continue

        sentiment = _ACTION_TO_SENTIMENT.get(action)
        if sentiment is None:
            _log_skip_once("tv_bridge.skip_invalid_action", event_id, action=action)
            counts["skipped_invalid"] += 1
            continue

        note = event.get("note")

        # V10: entprellen, bevor die Zeile in den Audit-Trail geht. Entschieden
        # wird in EREIGNISZEIT (``received_at``), nicht nach der Wanduhr -- so
        # faellt fuer dasselbe Ereignis bei jedem Takt dieselbe Entscheidung.
        received_moment = _parse_received_at(received_at)
        if (
            debouncer is not None
            and blocked_alerts_path is not None
            and received_moment is not None
        ):
            decision = debouncer.decide(_TV_CHANNEL, base, sentiment, now=received_moment)
            if not decision.emit:
                append_blocked_alert(
                    BlockedAlertRecord(
                        document_id=doc_id,
                        block_reason=DEBOUNCE_BLOCK_REASON,
                        blocked_at=str(received_at),
                        sentiment_label=sentiment,
                        blocked_assets=[base],
                        actionable=True,
                        normalized_title=note if isinstance(note, str) else None,
                        source_name=_TV_SOURCE,
                    ),
                    blocked_alerts_path,
                )
                blocked_ids.add(doc_id)
                counts["skipped_debounced"] += 1
                log.info(
                    "tv_bridge.debounced",
                    document_id=doc_id,
                    base=base,
                    sentiment=sentiment,
                    repeat_index=decision.repeat_index,
                    window_minutes=int(window.total_seconds() // 60),
                )
                continue

        event_prov = event.get("provenance") or {}
        prov_version = (
            event_prov.get("version") if isinstance(event_prov, dict) else None
        ) or "tv-3"
        prov_signal_path_id = (
            event_prov.get("signal_path_id") if isinstance(event_prov, dict) else None
        )
        prov_auth_method = event_prov.get("auth_method") if isinstance(event_prov, dict) else None
        from app.core.settings import get_settings as _get_settings

        provenance = SignalProvenance(
            source=_TV_SOURCE,
            version=prov_version,
            signal_path_id=prov_signal_path_id,
            auth_method=prov_auth_method,
            ingest_event_id=event_id,
        ).with_hash(_get_settings().alerts.provenance_secret)
        record = AlertAuditRecord(
            document_id=doc_id,
            channel=_TV_CHANNEL,
            message_id=None,
            is_digest=False,
            dispatched_at=received_at,
            sentiment_label=sentiment,
            affected_assets=[base],
            priority=None,
            actionable=True,
            directional_eligible=True,
            source_name=_TV_SOURCE,
            # STAB-2026-09-01 §5: the pair is known right here — it was split out
            # of the ticker a few lines above and then thrown away. Persisting it
            # is what makes future outcomes pair-attributable instead of naked.
            canonical_asset=base,
            canonical_quote=quote,
            canonical_pair=f"{base}/{quote}",
            normalized_title=note if isinstance(note, str) else None,
            provenance=provenance,
        )
        append_alert_audit(record, alert_audit_path)
        existing_ids.add(doc_id)
        counts["written"] += 1
        log.info(
            "tv_bridge.written",
            document_id=doc_id,
            base=base,
            sentiment=sentiment,
            dispatched_at=received_at,
        )

    if counts["skipped_overflow"]:
        log.warning(
            "tv_bridge.overflow",
            written=counts["written"],
            deferred=counts["skipped_overflow"],
            max_events_per_tick=max_events_per_tick,
        )

    return counts
