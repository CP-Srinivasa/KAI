"""Counterfactual Live∥Replay drift logger (ADR 0010, #318 Phase 1).

Read-only diagnostic: für jeden Shadow-Kandidaten (den die Loop in Echtzeit
aufgenommen hat) vergleicht dieser Pass den **Live**-Entry-Preis (was KAI im
Moment der Entscheidung sah) gegen die **Replay**-Sicht — die gesettelte 1m-Kline
DERSELBEN Minute, später erneut von Binance gezogen (gleiche Quelle wie der
Shadow-Resolver). Kernfrage: lag KAIs Live-Preis überhaupt im [low, high] der
gesettelten Kline? Liegt er ausserhalb, ist das ein echter Daten-Drift (KAI hat
auf einen Preis reagiert, den der gesettelte Marktrecord nie handelte).

Strikt sicher (ADR 0010 Phase 1): KEIN Live-/Paper-Pfad, KEIN Order/Fill, kein
Eintrag in paper_execution_audit. Schreibt nur ``artifacts/counterfactual_
comparison.jsonl``. Flag-gated im Skript (``EXECUTION_DUAL_STREAM_DIAGNOSTICS``,
default off). Die compute-Funktionen sind pur/IO-frei (offline testbar); der
Kline-Fetch wird injiziert (wiederverwendet ``binance_kline_fetcher``).

Konservative Defaults (gekennzeichnet, ADR-Entscheidung „Binance-Klines re-fetch"):
Replay-Quelle = gesettelte 1m-Kline der Entry-Minute; Drift-Schwelle = 30 bps,
env-konfigurierbar.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.observability.shadow_candidate_ledger import (
    LEDGER_PATH,
    RESOLVABLE_CANDIDATE_KINDS,
    Bar,
)

logger = logging.getLogger(__name__)

OUTPUT_PATH = Path("artifacts/counterfactual_comparison.jsonl")
# Konservativer Default-Schwellwert (ADR 0010): out-of-settled-range-Drift über
# diesem bps-Betrag gilt als auffällig. Env: EXECUTION_DUAL_STREAM_DRIFT_BPS.
DEFAULT_DRIFT_BPS = 30.0
# Plausibilitäts-Grenze (Datenqualität): liegt der Live-Entry-Preis weiter als
# dies AUSSERHALB der gesettelten 1m-Range, ist das kein Markt-Drift, sondern ein
# Feed-/Einheiten-/Symbol-Glitch (z. B. der technical_screener loggte für gate-
# rejected Nicht-Krypto-Symbole einen normalisierten ~100-Indexwert statt des echten
# Preises; auch vereinzelt Exoten/Delisted wie FTT/BAR). 3000 bps = 30 % ausserhalb
# der Minutenkline — physisch unmöglich als echter 1m-Drift (die Kline-Range selbst
# fasst jede real gehandelte Bewegung der Minute). Solche Records werden als
# ``data_quality_suspect`` markiert und NICHT als ``drift_exceeded`` gezählt, damit
# der Glitch die Drift-Statistik nicht verzerrt. Begründete physische Konstante
# (kein Runtime-Tuning nötig); per Funktionsparameter überschreibbar/testbar.
SUSPECT_RANGE_BPS = 3000.0
# Entry-Minute muss gesettelt (Kline geschlossen) sein, bevor verglichen wird.
_MIN_AGE_S = 120

KlineFetcher = Callable[[str, int, int], Sequence[Bar] | None]
_BAR_MS = 60_000


# --------------------------------------------------------------------------- #
# Pure compute
# --------------------------------------------------------------------------- #


def bar_covering(entry_ts_ms: int, bars: Sequence[Bar]) -> Bar | None:
    """Die 1m-Kline, deren Minute ``entry_ts_ms`` enthält (open ≤ ts < open+60s)."""
    covering: Bar | None = None
    for bar in bars:
        open_ms = bar[0]
        if open_ms <= entry_ts_ms < open_ms + _BAR_MS:
            covering = bar
            break
        if open_ms > entry_ts_ms:
            break
    return covering


def _parse_ts_ms(ts_utc: str) -> int | None:
    try:
        return int(datetime.fromisoformat(ts_utc.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, AttributeError):
        return None


def build_comparison(
    candidate: dict[str, object],
    bars: Sequence[Bar],
    *,
    threshold_bps: float = DEFAULT_DRIFT_BPS,
    suspect_range_bps: float = SUSPECT_RANGE_BPS,
) -> dict[str, object] | None:
    """Live∥Replay-Vergleichszeile für einen Kandidaten (pur). None wenn nicht
    vergleichbar (kein Entry-Preis / keine deckende Kline).

    Markiert physisch unmögliche Abweichungen (> ``suspect_range_bps`` ausserhalb
    der gesettelten Range) als ``data_quality_suspect`` und zählt sie NICHT als
    ``drift_exceeded`` — sie sind Feed-/Einheiten-Glitches, kein Markt-Drift.
    Gibt ``gate_would_reject`` des Kandidaten durch (None falls unbekannt), damit
    Konsumenten eine executable-only-Sicht bilden können."""
    entry_live = candidate.get("entry_price")
    if not isinstance(entry_live, (int, float)) or entry_live <= 0:
        return None
    entry_ms = _parse_ts_ms(str(candidate.get("ts_utc", "")))
    if entry_ms is None:
        return None
    bar = bar_covering(entry_ms, bars)
    if bar is None:
        return None
    open_ms, high, low, close = bar
    live = float(entry_live)
    in_range = low <= live <= high
    drift_to_close_bps = round((live - close) / close * 1e4, 2) if close > 0 else None
    if in_range:
        drift_to_range_bps = 0.0
    else:
        nearest = high if live > high else low
        drift_to_range_bps = round((live - nearest) / nearest * 1e4, 2) if nearest > 0 else 0.0
    suspect = abs(drift_to_range_bps) > suspect_range_bps
    gate_would_reject = candidate.get("gate_would_reject")
    return {
        "candidate_id": candidate.get("candidate_id"),
        "symbol": candidate.get("symbol"),
        "side": candidate.get("side"),
        "source": candidate.get("source"),
        "ts_utc": candidate.get("ts_utc"),
        "entry_live": round(live, 8),
        "settled_open_ms": open_ms,
        "settled_high": high,
        "settled_low": low,
        "settled_close": close,
        "in_settled_range": in_range,
        "drift_to_close_bps": drift_to_close_bps,
        "drift_to_range_bps": drift_to_range_bps,
        # Glitch zählt nicht als Drift; echter Out-of-Range-Drift schon.
        "drift_exceeded": (not suspect) and abs(drift_to_range_bps) > threshold_bps,
        "data_quality_suspect": suspect,
        "gate_would_reject": bool(gate_would_reject) if gate_would_reject is not None else None,
        "threshold_bps": threshold_bps,
        "suspect_range_bps": suspect_range_bps,
        "schema_version": "v2",
    }


# --------------------------------------------------------------------------- #
# Runner (thin IO shell)
# --------------------------------------------------------------------------- #


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except json.JSONDecodeError:
            continue
    return out


def _is_comparable_kind(c: dict[str, object]) -> bool:
    """Nur echte Entry-Kandidaten (wie der Shadow-Resolver), keine
    canary/synthetic/scan-Zeilen."""
    kind = c.get("candidate_kind")
    if kind is not None and str(kind) not in RESOLVABLE_CANDIDATE_KINDS:
        return False
    if str(c.get("source")) in ("canary_probe", "real_analysis"):
        return False
    return not bool(c.get("is_synthetic_default"))


def run_counterfactual_pass(
    *,
    fetch_klines: KlineFetcher,
    threshold_bps: float = DEFAULT_DRIFT_BPS,
    suspect_range_bps: float = SUSPECT_RANGE_BPS,
    now: datetime | None = None,
    ledger_path: Path = LEDGER_PATH,
    output_path: Path = OUTPUT_PATH,
    min_age_s: int = _MIN_AGE_S,
) -> dict[str, int]:
    """Vergleiche Live vs. gesettelte Kline pro Kandidat (idempotent, fail-soft).

    Schreibt NUR nach ``output_path``. Mutiert keinen Live-/Paper-Zustand.
    Returns counts {compared, exceeded, suspect, already, skipped_kind,
    skipped_recent, no_data}. ``suspect`` zählt als Datenqualitäts-Glitch
    erkannte Records (in ``compared`` enthalten, aber NICHT in ``exceeded``).
    """
    now = now or datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    candidates = _read_jsonl(ledger_path)
    done = {r.get("candidate_id") for r in _read_jsonl(output_path)}
    counts = {
        "compared": 0,
        "exceeded": 0,
        "suspect": 0,
        "already": 0,
        "skipped_kind": 0,
        "skipped_recent": 0,
        "no_data": 0,
    }
    for c in candidates:
        cid = c.get("candidate_id")
        if cid in done:
            counts["already"] += 1
            continue
        if not _is_comparable_kind(c):
            counts["skipped_kind"] += 1
            continue
        entry_ms = _parse_ts_ms(str(c.get("ts_utc", "")))
        if entry_ms is None:
            counts["no_data"] += 1
            continue
        if now_ms < entry_ms + min_age_s * 1000:
            counts["skipped_recent"] += 1
            continue
        bars = fetch_klines(str(c.get("symbol", "")), entry_ms - _BAR_MS, entry_ms + 2 * _BAR_MS)
        if not bars:
            counts["no_data"] += 1
            continue
        record = build_comparison(
            c, bars, threshold_bps=threshold_bps, suspect_range_bps=suspect_range_bps
        )
        if record is None:
            counts["no_data"] += 1
            continue
        record["compared_at_utc"] = now.isoformat()
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            counts["compared"] += 1
            if record["drift_exceeded"]:
                counts["exceeded"] += 1
            if record["data_quality_suspect"]:
                counts["suspect"] += 1
        except OSError as exc:
            logger.warning("[counterfactual] write failed: %s", exc)
    return counts


__all__ = [
    "DEFAULT_DRIFT_BPS",
    "OUTPUT_PATH",
    "SUSPECT_RANGE_BPS",
    "bar_covering",
    "build_comparison",
    "run_counterfactual_pass",
]
