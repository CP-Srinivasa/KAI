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
from pathlib import Path

import structlog

from app.alerts.audit import (
    AlertAuditRecord,
    append_alert_audit,
    iter_alert_audit_document_ids,
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

# SENTR-F-006: log-hygiene — strip newlines/CR/tabs (log-injection guard)
# and cap length so an attacker-controlled `note` can't forge fake log
# lines or blow up line-based log shippers.
_LOG_NOTE_MAX_LEN: int = 200


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
    HMAC when secret active), ``skipped_tampered`` (HMAC mismatch).
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
    }
    if not tv_pending_path.exists():
        return counts

    # NEO-F-002: stream document_ids instead of allocating AlertAuditRecord
    # per audit row — bridge only needs the dedup-key set.
    existing_ids = iter_alert_audit_document_ids(alert_audit_path)

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
            log.info(
                "tv_bridge.skip_smoke",
                event_id=event_id,
                note=_sanitize_for_log(event.get("note")),
            )
            continue

        doc_id = f"tv:{event_id}"
        if doc_id in existing_ids:
            counts["skipped_existing"] += 1
            continue

        # SENTR-F-005: cap writes per tick. Remaining rows that would
        # otherwise be written contribute to skipped_overflow and are
        # picked up next tick.
        if counts["written"] >= max_events_per_tick:
            counts["skipped_overflow"] += 1
            continue

        split = _split_ticker(ticker)
        if split is None:
            log.info("tv_bridge.skip_unsupported_quote", ticker=ticker, event_id=event_id)
            counts["skipped_unsupported"] += 1
            continue
        base, _quote = split
        if base not in _BASE_ASSET_TO_COINGECKO:
            log.info("tv_bridge.skip_unsupported_base", base=base, event_id=event_id)
            counts["skipped_unsupported"] += 1
            continue

        sentiment = _ACTION_TO_SENTIMENT.get(action)
        if sentiment is None:
            log.info("tv_bridge.skip_invalid_action", action=action, event_id=event_id)
            counts["skipped_invalid"] += 1
            continue

        note = event.get("note")
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
