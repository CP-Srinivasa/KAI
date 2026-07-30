"""Auto-Annotation Agent for directional alerts.

Compares the price at alert dispatch time with the price after a
configurable evaluation window.  Writes hit / miss / inconclusive
annotations to the outcomes JSONL file so the hold-metrics report
can compute precision automatically.

Tuning (D-132):
- Volatility-adaptive thresholds scale with 24h market volatility
- Re-evaluates prior inconclusive annotations after 24h
- API delay reduced to 5s (CoinGecko free tier ~10/min)
- Window: min 4h, max 72h for fresh alerts

D-138: Stale inconclusive re-evaluation
- Inconclusives older than 72h are re-evaluated with a fixed 7-day
  attribution window (dispatch_time → dispatch_time + 7d).
- No max_age limit for inconclusive re-evaluation.
- Batch-size limit prevents CoinGecko rate exhaustion in cron.

Usage (programmatic):
    results = await auto_annotate_pending(audit_dir)

Usage (CLI):
    python -m app.cli.main alerts auto-annotate
    python -m app.cli.main alerts auto-annotate --backfill-batch 200
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from app.alerts.audit import (
    AlertAuditRecord,
    AlertOutcomeAnnotation,
    append_outcome_annotation,
    load_alert_audits,
    load_outcome_annotations,
)
from app.alerts.eligibility import evaluate_directional_eligibility
from app.market_data.binance_adapter import BinanceAdapter
from app.market_data.coingecko_adapter import CoinGeckoAdapter
from app.signals.models import SignalProvenance

log = structlog.get_logger(__name__)

# Minimum age before we evaluate an alert (hours).
_DEFAULT_MIN_AGE_HOURS = 4.0

# Maximum age — alerts older than this are too stale for reliable evaluation.
_DEFAULT_MAX_AGE_HOURS = 72.0

# Price-move threshold in percent (base, before volatility scaling).
# Scales with evaluation window and market volatility.
_DEFAULT_MOVE_THRESHOLD = 1.0

# Delay between CoinGecko API calls to respect rate limits.
# CoinGecko free tier: ~10-30 req/min. 5s = 12/min (safe margin).
_API_DELAY_SECONDS = 5

# Re-evaluate inconclusive annotations older than this many hours.
_REEVAL_MIN_AGE_HOURS = 24.0

# Fixed attribution window for stale inconclusive re-evaluation.
# Alerts older than max_age use dispatch_time + this window instead
# of dispatch_time → now.  7 days is the longest reasonable window
# for attributing a price move to a specific news event.
_STALE_REEVAL_WINDOW_HOURS = 168.0  # 7 days

# 2026-05-28 DS-V3: cap on unbounded inconclusive re-evaluation.
# Once the longest window (168h) has fully elapsed, the evaluation interval
# (dispatch → dispatch + N) is a fixed historical range, so re-evaluating an
# inconclusive yields the same result deterministically. Without a cap a
# perpetually-inconclusive doc is re-annotated every run forever (observed
# ~256x/doc on 2026-05-28 → ~8950 duplicate rows + wasted CoinGecko calls).
# After this many confirming inconclusive attempts on a fully-elapsed doc we
# stop re-queuing it. Does not affect precision: the last inconclusive stays
# the latest-per-doc outcome.
_MAX_INCONCLUSIVE_REEVAL_ATTEMPTS = 3

# 2026-05-25 DS-V-MW: Multi-Window-Outcome sub-windows (hours).
# Replaces single-window evaluation. An alert is "hit" if the predicted
# direction crosses the scaled threshold in ANY of these windows. Iteration
# is shortest→longest with early-exit on first hit (saves API calls in the
# common case where news triggers an intraday move).
# Diagnostic for choice: in calm markets (BTC <1%/24h) the 168h-window
# threshold scales to ~1.5% — 99.6% of 7d-samples ended inconclusive on
# 2026-05-18..25. Adding 1h/4h captures intraday spikes; 24h/72h cover
# normal news-decay; 168h remains as the legacy long-horizon fallback.
_MULTI_WINDOW_HOURS: tuple[float, ...] = (1.0, 4.0, 24.0, 72.0, 168.0)


def _window_label(window_hours: float) -> str:
    """Return canonical short label for a sub-window."""
    return f"{int(window_hours)}h"


# Default batch size for stale inconclusive backfill.
# Limits API calls per run to avoid rate exhaustion in cron.
_DEFAULT_BACKFILL_BATCH = 30

# ── W2 Quoten-Sprint (2026-07-29) ────────────────────────────────────────
# Preisquelle der Outcome-Aufloesung. Default "binance": eigene OHLCV-Basis
# (public, kein Key, gleiche Preisbasis wie die Paper-Execution) statt des
# CoinGecko-Free-Tiers, dessen Rate-Limit die 5-s-Delays, Batch-Caps und den
# Annotations-Backlog erzwang. CoinGecko bleibt Fallback je Fenster-Aufruf.
_PRICE_SOURCE_ENV = "ALERTS_OUTCOME_PRICE_SOURCE"

# Befund 2026-07-29: 62 eigene technical_paper-Fills hatten NULL Outcome-
# Annotationen — gehandelte und gemessene Population waren disjunkt. Eigene
# Signale werden deshalb als synthetische Pendings aus dem Paper-Audit
# gespeist (in-memory; alert_audit.jsonl bleibt unveraendert die reine
# Dispatch-Historie). SIG-TVP-* haengt am tv:-Alert (keine Doppel-Messung),
# fremde UUID-Feeds bleiben bewusst aussen vor (eigener Folgeschritt).
_PAPER_AUDIT_FILENAME = "paper_execution_audit.jsonl"
_TECHNICAL_DOC_PREFIX = "technical_paper"
_TECHNICAL_PROVENANCE = SignalProvenance(
    source="technical_paper",
    version="paper-fill-v1",
    signal_path_id="technical_paper_v1",
)


def _load_technical_paper_pendings(audit_dir: Path) -> list[AlertAuditRecord]:
    """Synthetische AlertAuditRecords aus eigenen technical_paper-Fills.

    Nur der ERSTE eroeffnende Fill je Dokument zaehlt (buy+long / sell+short);
    Schliess-Fills derselben document_id werden ignoriert. Richtung → Sentiment:
    long=bullish, short=bearish. Fehlerhafte Zeilen werden uebersprungen —
    das Audit darf die Annotation nie blocken.
    """
    path = audit_dir / _PAPER_AUDIT_FILENAME
    if not path.exists():
        return []
    records: list[AlertAuditRecord] = []
    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("event_type") != "order_filled":
                    continue
                doc = row.get("document_id")
                if not isinstance(doc, str) or not doc.startswith(_TECHNICAL_DOC_PREFIX):
                    continue
                if doc in seen:
                    continue
                side = row.get("side")
                position_side = row.get("position_side")
                is_opening = (side == "buy" and position_side == "long") or (
                    side == "sell" and position_side == "short"
                )
                if not is_opening:
                    continue
                symbol = row.get("symbol")
                ts = row.get("timestamp_utc")
                if not isinstance(symbol, str) or not isinstance(ts, str):
                    continue
                seen.add(doc)
                records.append(
                    AlertAuditRecord(
                        document_id=doc,
                        channel="paper",
                        message_id=None,
                        is_digest=False,
                        dispatched_at=ts,
                        sentiment_label=("bullish" if position_side == "long" else "bearish"),
                        affected_assets=[symbol],
                        directional_eligible=True,
                        source_name="technical_paper",
                        provenance=_TECHNICAL_PROVENANCE,
                    )
                )
    except OSError:
        return []
    return records


def _scaled_threshold(
    elapsed_hours: float,
    base_threshold: float,
    volatility_24h: float | None = None,
) -> float:
    """Return a move threshold that scales with window and volatility.

    Base scaling by window size:
      <=8h  -> base * 0.7  (short window, small moves matter)
      <=12h -> base * 1.0
      <=24h -> base * 1.5
      <=48h -> base * 2.0
      >48h  -> base * 2.5

    Volatility adjustment: if 24h vol is available, scale the
    threshold down in low-vol markets and up in high-vol markets.
    This prevents too many inconclusives during calm markets.
    """
    # Window scaling
    if elapsed_hours <= 8.0:
        window_factor = 0.7
    elif elapsed_hours <= 12.0:
        window_factor = 1.0
    elif elapsed_hours <= 24.0:
        window_factor = 1.5
    elif elapsed_hours <= 48.0:
        window_factor = 2.0
    else:
        window_factor = 2.5

    threshold = base_threshold * window_factor

    # Volatility scaling: use abs(24h change) as proxy for volatility.
    # Low vol (<1%): scale down to 60% of threshold.
    # Normal vol (1-3%): keep threshold.
    # High vol (>3%): scale up to 150% of threshold.
    if volatility_24h is not None:
        abs_vol = abs(volatility_24h)
        if abs_vol < 1.0:
            vol_factor = 0.6
        elif abs_vol < 3.0:
            vol_factor = 0.6 + (abs_vol - 1.0) * 0.2  # 0.6..1.0
        else:
            vol_factor = min(1.0 + (abs_vol - 3.0) * 0.1, 1.5)
        threshold *= vol_factor

    return max(threshold, 0.3)  # floor: never below 0.3%


def _parse_dispatch_time(record: AlertAuditRecord) -> datetime | None:
    """Parse dispatched_at to a timezone-aware datetime, or None."""
    try:
        return datetime.fromisoformat(
            record.dispatched_at.replace("Z", "+00:00"),
        )
    except (ValueError, AttributeError):
        return None


def _primary_symbol(record: AlertAuditRecord) -> str | None:
    """Return the first affected asset as a tradeable symbol."""
    if not record.affected_assets:
        return None
    raw = record.affected_assets[0].upper()
    if "/" in raw:
        return raw
    return f"{raw}/USDT"


_LOCK_FILE_NAME = ".auto_annotate.lock"
_LOCK_STALE_SECONDS = 1800  # 30 min — laenger als jeder normale Run


def _acquire_run_lock(lock_path: Path) -> bool:
    """V-DB5 Calibration 2026-05-08 (audit S-B1/H-1):
    Datei-basierter Lock gegen parallele Runs (6h-Timer ↔ manueller --catchup).

    Returns True wenn lock erworben, False wenn ein anderer Run aktiv ist.
    Stale-Lock (älter als 30 min) wird automatisch geräumt.
    """
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"pid={os.getpid()} ts={int(time.time())}\n".encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age > _LOCK_STALE_SECONDS:
                lock_path.unlink(missing_ok=True)
                log.warning("auto_annotate.stale_lock_cleared", age_seconds=int(age))
                return _acquire_run_lock(lock_path)
        except OSError:
            pass
        return False


def _release_run_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


async def auto_annotate_pending(
    audit_dir: Path,
    *,
    min_age_hours: float = _DEFAULT_MIN_AGE_HOURS,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    move_threshold: float = _DEFAULT_MOVE_THRESHOLD,
    reeval_inconclusive: bool = True,
    backfill_batch: int = _DEFAULT_BACKFILL_BATCH,
    catchup_unannotated: bool = False,
    catchup_batch: int = 50,
    dry_run: bool = False,
) -> list[AlertOutcomeAnnotation]:
    """Annotate all eligible directional alerts that are old enough.

    When ``reeval_inconclusive`` is True, alerts that were previously
    annotated as ``inconclusive`` get re-evaluated:
    - Within the normal window (4h–72h): compared to current price.
    - Beyond the normal window (>72h): compared to dispatch + 7d price
      using a fixed attribution window. No max_age limit — even very
      old inconclusives are re-evaluated (D-138).

    ``backfill_batch`` limits how many stale (>72h) inconclusives are
    processed per run to avoid CoinGecko rate exhaustion.

    V-DB5: File-Lock verhindert parallele Runs (Timer ↔ manueller --catchup),
    die sonst CoinGecko-Quota verdoppeln und doppelte Annotations schreiben.

    Returns the list of newly created annotations.
    """
    import asyncio

    # V-DB5 audit S-B1/H-1: File-lock acquire (skipped during dry-run for tests).
    # Lock wird am Funktionsende manuell released; bei Exception via try/finally
    # weiter unten (siehe ResultsRunner-Wrap).
    lock_path = audit_dir / _LOCK_FILE_NAME
    have_lock = False
    if not dry_run:
        have_lock = _acquire_run_lock(lock_path)
        if not have_lock:
            log.warning(
                "auto_annotate.lock_held",
                lock_path=str(lock_path),
                msg="another run is in progress; skip",
            )
            return []

    audits = load_alert_audits(audit_dir)
    # W2: eigene technical_paper-Signale mitmessen. Ein echter Audit-Record
    # gewinnt immer — synthetisiert wird nur, was dort fehlt.
    known_docs = {a.document_id for a in audits}
    audits.extend(
        p for p in _load_technical_paper_pendings(audit_dir) if p.document_id not in known_docs
    )
    existing = load_outcome_annotations(audit_dir)

    # Latest annotation per document_id (last entry wins).
    latest_by_doc: dict[str, str] = {}
    # DS-V3: count prior inconclusive annotations per doc to cap re-spin.
    inconclusive_attempts: dict[str, int] = {}
    for a in existing:
        latest_by_doc[a.document_id] = a.outcome
        if a.outcome == "inconclusive":
            inconclusive_attempts[a.document_id] = inconclusive_attempts.get(a.document_id, 0) + 1
            # Quoten-Sprint W1 (2026-07-29): Seit write-on-change werden
            # bestaetigende Wiederholungen nicht mehr als eigene Zeile
            # geschrieben — die reine Zeilen-Zaehlung oben wuerde den Terminal-
            # Cap unten also nie mehr erreichen (Endlos-Re-Eval + unbegrenzte
            # CoinGecko-Calls). Der explizite Zaehler ist die neue Wahrheit;
            # max() haelt Altzeilen ohne das Feld korrekt.
            if a.reeval_attempt is not None:
                inconclusive_attempts[a.document_id] = max(
                    inconclusive_attempts[a.document_id], a.reeval_attempt
                )

    now = datetime.now(UTC)
    min_cutoff = now - timedelta(hours=min_age_hours)
    max_cutoff = now - timedelta(hours=max_age_hours)
    reeval_cutoff = now - timedelta(hours=_REEVAL_MIN_AGE_HOURS)

    # Filter to actionable candidates.
    # Two pools: fresh (within normal window) and stale (beyond, inconclusives only).
    pending: list[tuple[AlertAuditRecord, datetime, bool]] = []  # (rec, dt, is_stale)
    seen_doc_ids: set[str] = set()
    stale_count = 0
    catchup_count = 0
    for rec in audits:
        if rec.directional_eligible is False:
            continue
        if rec.directional_eligible is None:
            # V-DB5 Calibration 2026-05-08 (audit F-001/B-B2):
            # Legacy record without eligibility field — recompute MIT allen
            # verfuegbaren Feldern. Vorher nur sentiment+assets → V-DB4-Gates
            # (PROMO_PATTERN, LOW_PRECISION_SOURCE, NOT_ACTIONABLE, LOW_PRIORITY,
            # BEARISH_DISABLED) wurden uebergangen, Legacy-Records mit blocked
            # Promo-Headlines konnten als hits/misses ins forward_precision
            # eingerechnet werden.
            legacy = evaluate_directional_eligibility(
                sentiment_label=rec.sentiment_label,
                affected_assets=list(rec.affected_assets or []),
                priority=rec.priority,
                source_name=rec.source_name,
                title=rec.normalized_title,
                actionable=rec.actionable,
            )
            if legacy.directional_eligible is not True:
                continue
        dt = _parse_dispatch_time(rec)
        if dt is None or dt > min_cutoff:
            continue
        if rec.document_id in seen_doc_ids:
            continue

        current_outcome = latest_by_doc.get(rec.document_id)
        is_stale = dt < max_cutoff

        if current_outcome is None:
            # Never annotated — within normal window OR catchup-mode for stale.
            if is_stale:
                # V-DB4d 2026-05-08: Backlog-Catchup-Mode.
                # Standard-Verhalten: stale + nie-annotiert wird verworfen — das
                # produziert den 423-Backlog wenn der Timer ueber Tage ausfaellt.
                # Mit catchup_unannotated=True werden bis zu catchup_batch alte
                # unannotated Records mit fixed 7d-window (wie D-138 stale-reeval)
                # nachgezogen, bevor sie endgueltig verloren sind.
                if not catchup_unannotated:
                    continue
                if catchup_count >= catchup_batch:
                    continue
                catchup_count += 1
        elif current_outcome == "inconclusive" and reeval_inconclusive:
            # Re-evaluate if old enough (24h+ since dispatch).
            if dt > reeval_cutoff:
                continue
            # DS-V3: terminal cap. Once the 168h window has fully elapsed the
            # result is deterministic; stop re-queuing after a few confirming
            # inconclusive attempts to bound JSONL inflation + CoinGecko calls.
            fully_elapsed = dt < (now - timedelta(hours=_STALE_REEVAL_WINDOW_HOURS))
            if (
                fully_elapsed
                and inconclusive_attempts.get(rec.document_id, 0)
                >= _MAX_INCONCLUSIVE_REEVAL_ATTEMPTS
            ):
                continue
            # D-138: stale inconclusives use fixed 7d window, batch-limited.
            if is_stale and stale_count >= backfill_batch:
                continue
        else:
            # Already annotated with hit/miss — skip.
            continue

        seen_doc_ids.add(rec.document_id)
        if is_stale:
            stale_count += 1
        pending.append((rec, dt, is_stale))

    if not pending:
        log.info("auto_annotate.nothing_pending")
        if have_lock:
            _release_run_lock(lock_path)
        return []

    # V-DB5 Calibration 2026-05-08 (audit H-2):
    # Sortiere pending — fresh-Records zuerst (is_stale=False), dann stale.
    # Innerhalb beider Gruppen: jüngste zuerst (höchste Aussagekraft).
    # Damit wird CoinGecko-Quota auf hot-records investiert; bei Quota-Hit
    # bleibt nur das Catchup-Tail unannotiert (akzeptabel, wird nächsten Lauf
    # wieder aufgenommen).
    pending.sort(key=lambda x: (x[2], -x[1].timestamp()))

    fresh_count = sum(1 for _, _, s in pending if not s)
    log.info(
        "auto_annotate.start",
        pending_count=len(pending),
        fresh=fresh_count,
        stale_backfill=stale_count,
        catchup_unannotated=catchup_count,
    )

    from app.core.settings import get_settings

    no_price_symbols: dict[str, int] = {}
    too_young = 0
    # W1: seit write-on-change ist "evaluiert" != "geschrieben". Beide Zahlen
    # gehoeren ins done-Log, sonst wirkt die geschrumpfte JSONL wie Datenverlust.
    written = 0
    coingecko = CoinGeckoAdapter(
        timeout_seconds=15,
        api_key=get_settings().coingecko_api_key or None,
    )
    # W2: Binance ist Default-Preisquelle (eigene OHLCV-Basis, kein Rate-
    # Limit-Delay); CoinGecko bleibt Fallback je Aufruf. Env zur LAUFZEIT
    # gelesen, damit Tests/Deploys ohne Restart-Semantik umschalten koennen.
    price_source = os.getenv(_PRICE_SOURCE_ENV, "binance").strip().lower()
    adapter = coingecko if price_source == "coingecko" else BinanceAdapter(timeout_seconds=15)
    log.info("auto_annotate.price_source", source=price_source)

    # Fetch current volatility for threshold scaling.
    volatility_24h: float | None = None
    try:
        btc_ticker = await adapter.get_ticker("BTC/USDT")
        if btc_ticker is not None:
            volatility_24h = btc_ticker.change_pct_24h
            log.info(
                "auto_annotate.volatility",
                btc_24h_change=f"{volatility_24h:+.2f}%",
            )
    except Exception:  # noqa: BLE001
        log.warning("auto_annotate.volatility_fetch_failed")

    results: list[AlertOutcomeAnnotation] = []

    for rec, dispatch_time, is_stale_reeval in pending:
        symbol = _primary_symbol(rec)
        if symbol is None:
            continue

        sentiment = (rec.sentiment_label or "").lower()

        # 2026-05-25 DS-V-MW: Multi-Window-Outcome evaluation.
        # Iterate sub-windows shortest→longest. Hit on first window that
        # crosses scaled_threshold in expected direction (early-exit saves
        # API calls). Track opposite-direction cross as miss-candidate.
        # Windows beyond `now - dispatch_time` are future — skip without
        # data (and without API call). Stale-reeval is now structurally
        # the same as fresh: each sub-window uses dispatch + N hours.
        hit_at_window: str | None = None
        hit_pct_change: float | None = None
        hit_threshold: float | None = None
        hit_start_price: float | None = None
        hit_end_price: float | None = None
        last_pct_change: float | None = None
        last_threshold: float | None = None
        last_window_h: float | None = None
        last_start_price: float | None = None
        last_end_price: float | None = None
        any_data_seen = False
        any_opposite_cross = False
        api_calls_this_alert = 0
        # Quoten-Sprint 07-30: welche Quelle(n) die tatsächlich genutzten
        # Fenster-Daten lieferten — wandert als price_source in die Annotation.
        sources_used: set[str] = set()

        for window_h in _MULTI_WINDOW_HOURS:
            eval_end = dispatch_time + timedelta(hours=window_h)
            if eval_end > now:
                # Window not yet elapsed — skip without API call.
                continue

            api_calls_this_alert += 1
            price_data = await adapter.get_price_change_between(
                symbol,
                start_utc=dispatch_time,
                end_utc=eval_end,
            )
            # W2: CoinGecko-Fallback, wenn Binance nichts liefert (Symbol
            # nicht gelistet, Luecke, Transportfehler). Das Free-Tier-Delay
            # gilt NUR fuer CoinGecko-Aufrufe — der Binance-Pfad laeuft ohne
            # kuenstliche Bremse, was den Annotations-Backlog abbaut.
            used_coingecko = adapter is coingecko
            if price_data is None and not used_coingecko:
                api_calls_this_alert += 1
                price_data = await coingecko.get_price_change_between(
                    symbol,
                    start_utc=dispatch_time,
                    end_utc=eval_end,
                )
                used_coingecko = True
            if used_coingecko:
                await asyncio.sleep(_API_DELAY_SECONDS)

            if price_data is None:
                continue

            any_data_seen = True
            sources_used.add("coingecko" if used_coingecko else "binance")
            start_price, end_price, pct_change = price_data
            threshold = _scaled_threshold(
                window_h,
                move_threshold,
                volatility_24h,
            )
            last_pct_change = pct_change
            last_threshold = threshold
            last_window_h = window_h
            last_start_price = start_price
            last_end_price = end_price

            if sentiment == "bullish" and pct_change >= threshold:
                hit_at_window = _window_label(window_h)
                hit_pct_change = pct_change
                hit_threshold = threshold
                hit_start_price = start_price
                hit_end_price = end_price
                break
            if sentiment == "bearish" and pct_change <= -threshold:
                hit_at_window = _window_label(window_h)
                hit_pct_change = pct_change
                hit_threshold = threshold
                hit_start_price = start_price
                hit_end_price = end_price
                break

            if sentiment == "bullish" and pct_change <= -threshold:
                any_opposite_cross = True
            elif sentiment == "bearish" and pct_change >= threshold:
                any_opposite_cross = True

        if not any_data_seen:
            if api_calls_this_alert:
                # Preisquelle liefert fuer dieses Symbol nichts (Exoten wie
                # CASHCAT sind auf CoinGecko unpreisbar) — diese Alerts bleiben
                # STRUKTURELL unannotiert und duerfen den Backlog-Zaehler nicht
                # als "Annotator kaputt" einfaerben (Daily 07-12 V2).
                no_price_symbols[symbol] = no_price_symbols.get(symbol, 0) + 1
            else:
                too_young += 1
            log.warning(
                "auto_annotate.price_unavailable",
                document_id=rec.document_id,
                symbol=symbol,
                stale=is_stale_reeval,
                api_calls=api_calls_this_alert,
            )
            continue

        chosen_pct: float | None
        chosen_thr: float | None
        chosen_start: float | None
        chosen_end: float | None
        chosen_window_h: float | None
        if hit_at_window is not None:
            outcome: str = "hit"
            chosen_pct = hit_pct_change
            chosen_thr = hit_threshold
            chosen_start = hit_start_price
            chosen_end = hit_end_price
            chosen_window_h = float(hit_at_window.rstrip("h"))
        elif any_opposite_cross:
            outcome = "miss"
            chosen_pct = last_pct_change
            chosen_thr = last_threshold
            chosen_start = last_start_price
            chosen_end = last_end_price
            chosen_window_h = last_window_h
        else:
            outcome = "inconclusive"
            chosen_pct = last_pct_change
            chosen_thr = last_threshold
            chosen_start = last_start_price
            chosen_end = last_end_price
            chosen_window_h = last_window_h

        is_reeval = rec.document_id in latest_by_doc
        # V-DB5 Calibration 2026-05-08 (audit B-B3):
        # Catchup-Records (stale + nie-annotiert) bekommen "catchup"-Tag —
        # Forensik kann sie von normalen "auto"/"reeval"/"backfill" trennen.
        if is_stale_reeval and not is_reeval:
            tag = "catchup"
        elif is_stale_reeval:
            tag = "backfill"
        elif is_reeval:
            tag = "reeval"
        else:
            tag = "auto"
        window_note = f"@{_window_label(chosen_window_h)}" if chosen_window_h else ""
        if hit_at_window is not None:
            window_note = f"@{hit_at_window}"
        note = (
            f"{tag}{window_note}: {sentiment} {symbol} "
            f"${(chosen_start or 0):,.2f}->${(chosen_end or 0):,.2f} "
            f"({(chosen_pct or 0):+.2f}% over {(chosen_window_h or 0):.1f}h, "
            f"thr={(chosen_thr or 0):.2f}%)"
        )

        # ── Quoten-Sprint W1 (2026-07-29) ────────────────────────────────
        # Q6-Befund: 9,15 Zeilen pro Dokument, 85,1 % aller Zeilen im obersten
        # Dezil, ein Dokument mit 840 Zeilen — weil hier bisher bei JEDEM Lauf
        # geschrieben wurde, auch wenn der Re-Eval denselben Outcome bestaetigte.
        prior_outcome = latest_by_doc.get(rec.document_id)
        outcome_changed = prior_outcome != outcome
        fully_elapsed_now = dispatch_time < (now - timedelta(hours=_STALE_REEVAL_WINDOW_HOURS))
        next_attempt: int | None = None
        if outcome == "inconclusive":
            next_attempt = inconclusive_attempts.get(rec.document_id, 0) + 1

        annotation = AlertOutcomeAnnotation(
            document_id=rec.document_id,
            outcome=outcome,  # type: ignore[arg-type]
            asset=symbol,
            note=note,
            provenance=rec.provenance,
            hit_at_window=hit_at_window,
            # Q8: erst dieser Zeitstempel macht die Resolutions-Kadenz messbar.
            resolved_at=(datetime.now(UTC).isoformat() if outcome in ("hit", "miss") else None),
            # Q3-Gegenprobe: beide Felder liegen im AlertAuditRecord bereits vor.
            directional_confidence=rec.directional_confidence,
            priority=rec.priority,
            reeval_attempt=next_attempt,
            price_source=("mixed" if len(sources_used) > 1 else next(iter(sources_used), None)),
        )

        log.info(
            "auto_annotate.result",
            document_id=rec.document_id,
            outcome=outcome,
            symbol=symbol,
            hit_at_window=hit_at_window,
            pct_change=f"{(chosen_pct or 0):+.2f}%",
            threshold=f"{(chosen_thr or 0):.2f}%",
            api_calls=api_calls_this_alert,
            reeval=is_reeval,
        )

        # write-on-change: nur echte Zustandswechsel werden persistiert. Ausnahme
        # sind vollstaendig abgelaufene Dokumente — deren bestaetigende Zeilen
        # sind cap-relevant (siehe _MAX_INCONCLUSIVE_REEVAL_ATTEMPTS oben) und
        # duerfen NICHT gespart werden, sonst terminiert der Re-Eval nie.
        should_write = outcome_changed or fully_elapsed_now
        if not dry_run and should_write:
            append_outcome_annotation(annotation, audit_dir)
            written += 1

        results.append(annotation)

    log.info(
        "auto_annotate.done",
        total=len(results),
        # W1: geschriebene Zeilen (Zustandswechsel + cap-relevante Bestaetigungen).
        # Differenz zu `total` = eingesparte Wiederholungen.
        written=written,
        hits=sum(1 for a in results if a.outcome == "hit"),
        misses=sum(1 for a in results if a.outcome == "miss"),
        inconclusive=sum(1 for a in results if a.outcome == "inconclusive"),
        # V2 (Daily 07-12): Rest-Backlog aufschluesseln — strukturell
        # unpreisbare Symbole sichtbar machen statt Dauerrauschen.
        no_price=sum(no_price_symbols.values()),
        no_price_symbols=dict(sorted(no_price_symbols.items(), key=lambda kv: -kv[1])[:5]),
        too_young_windows=too_young,
    )
    # V-DB5 audit S-B1/H-1: Lock release am Ende des Run.
    # Bei Exception in der CoinGecko-Loop bleibt Lock liegen — wird bei
    # nächstem Run nach _LOCK_STALE_SECONDS=30min automatisch geräumt.
    # Operator-eindeutige Outcome ist wichtiger als idealer Cleanup.
    if have_lock:
        _release_run_lock(lock_path)
    return results
