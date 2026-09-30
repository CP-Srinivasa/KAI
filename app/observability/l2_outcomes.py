"""L2-Ergebnisse: Rendite jeder L2-Messung in Signalrichtung, getrennt vom Kandidaten-Ledger.

**Warum (Operator-Entscheid 2026-09-30, „Weg 2“).** Eine L2-Messung wird erst
verwertbar, wenn zu ihrem Signal ein Ergebnis vorliegt. Die Ergebnisse des
gemeinsamen Pools entstehen nur für Einträge im Shadow-Ledger, und dort schreibt der
Loop nur im reinen Shadow-Modus. Im Paper-Modus stoppt das Risiko-Gate fast jedes
Signal (30.09.: 38 von 40, ``max_open_positions_reached``); gestoppte Signale
verschwinden ohne Ergebnis. Seit dem 22.06. hat keine L2-Messung je ein Ergebnis
bekommen. Die gestoppten Signale ins gemeinsame Ledger zu schreiben, würde die
Edge-Kennzahlen des autonomen Generators verändern — deshalb ein eigener Rechner,
der NUR die L2-Messungen auflöst und in eine eigene Datei schreibt.

**Was aufgelöst wird.** Messzeilen mit Kandidatenkontext (``candidate_id`` und
Zyklusbeginn: ``cycle_started_at`` ab 30.09., ``decision_ts`` für 25.–30.09.), sobald
der längste Horizont plus Puffer verstrichen ist. Der Anker ist der Zyklusbeginn —
derselbe, den ``pit_join`` vom Outcome verlangt.

**Einstiegskurs.** Neue Messungen tragen den Kurs, den das Signal gesehen hat
(``reference_price``). Älteren Zeilen fehlt er; dann gilt der Schlusskurs der letzten
VOLLSTÄNDIGEN Minute vor dem Zyklusbeginn (``entry_price_basis`` sagt, welcher).

Nur lesend gegenüber Markt und Ledger; kein Einfluss auf Handel, Sizing oder andere
Auswertungen. Idempotent: jede ``candidate_id`` wird genau einmal geschrieben.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.file_lock import append_lock
from app.observability.shadow_candidate_ledger import (
    HORIZONS_S,
    Bar,
    KlineFetcher,
    compute_forward_returns,
)

logger = logging.getLogger(__name__)

SHADOW_PATH = Path("artifacts/l2_evidence_shadow.jsonl")
OUTCOMES_PATH = Path("artifacts/l2_outcomes.jsonl")
PRIMARY_HORIZON_S = 3600
# Puffer nach dem Horizont, bis die Minutenkerze sicher abgeschlossen und geliefert ist.
SETTLE_S = 180
# Aeltere Messungen werden nicht mehr versucht (und vom Waechter nicht gezaehlt).
MAX_AGE = timedelta(days=14)
# Ab diesem Alter wird eine Messung ohne vollstaendige Kerzenreihe als ``no_data``
# abgeschlossen, statt jede halbe Stunde neu versucht zu werden.
GIVE_UP_AFTER = timedelta(days=2)


def _parse(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def anchor_of(measurement: dict[str, Any]) -> datetime | None:
    """Zyklusbeginn der Messung (neue Form ``cycle_started_at``, Übergangsform ``decision_ts``)."""
    return _parse(measurement.get("cycle_started_at") or measurement.get("decision_ts"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def resolvable(measurements: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Messungen mit eindeutiger ``candidate_id``, Anker, Symbol und Richtung."""
    counts = Counter(m.get("candidate_id") for m in measurements if m.get("candidate_id"))
    out: dict[str, dict[str, Any]] = {}
    for m in measurements:
        cid = m.get("candidate_id")
        if not isinstance(cid, str) or not cid or counts[cid] != 1:
            continue  # ohne ID nicht joinbar, doppelte ID mehrdeutig (wie pit_join)
        if (
            anchor_of(m) is None
            or not m.get("symbol")
            or m.get("direction") not in ("long", "short")
        ):
            continue
        out[cid] = m
    return out


def due(measurement: dict[str, Any], now: datetime) -> bool:
    anchor = anchor_of(measurement)
    if anchor is None:
        return False
    return (
        anchor + timedelta(seconds=PRIMARY_HORIZON_S + SETTLE_S) <= now and now - anchor <= MAX_AGE
    )


def _entry_price(
    measurement: dict[str, Any], anchor_ms: int, bars: Sequence[Bar]
) -> tuple[float | None, str]:
    ref = measurement.get("reference_price")
    if isinstance(ref, (int, float)) and not isinstance(ref, bool) and ref > 0:
        return float(ref), "reference_price"
    # Letzte vollstaendige Minute VOR dem Anker: ihr Schluss lag zum Anker schon vor.
    prev = [close for open_ms, _hi, _lo, close in bars if open_ms + 60_000 <= anchor_ms]
    if prev and prev[-1] > 0:
        return float(prev[-1]), "kline_prev_close"
    return None, "none"


def outcome_for(
    measurement: dict[str, Any], bars: Sequence[Bar], now: datetime, *, not_trading: bool = False
) -> dict[str, Any] | None:
    """Ergebniszeile für eine Messung, oder ``None``, wenn es noch nicht entscheidbar ist.

    ``not_trading``: keine Kerzen UND die Kursquelle handelt das Paar nicht (30.09.:
    COIN, NVDA, XDP unbekannt, KLAY eingestellt) — sofort ``no_data`` statt zwei Tage
    Warten und Fehlalarm des Waechters.
    """
    anchor = anchor_of(measurement)
    if anchor is None:
        return None
    anchor_ms = int(anchor.timestamp() * 1000)
    entry, basis = _entry_price(measurement, anchor_ms, bars)
    side = str(measurement["direction"])
    fwd = (
        compute_forward_returns(entry_price=entry, side=side, entry_ts_ms=anchor_ms, bars=bars)
        if entry is not None
        else {f"fwd_{h}s_bps": None for h in HORIZONS_S}
    )
    net = fwd.get(f"fwd_{PRIMARY_HORIZON_S}s_bps")
    if net is None and not not_trading and now - anchor < GIVE_UP_AFTER:
        return None  # Kerzen noch unvollstaendig: spaeter erneut versuchen
    row: dict[str, Any] = {
        "candidate_id": measurement["candidate_id"],
        "symbol": measurement["symbol"],
        "side": side,
        "entry_ts": anchor.isoformat(),
        "entry_price": entry,
        "entry_price_basis": basis,
        **fwd,
        "net_bps": net,
        "horizon_s": PRIMARY_HORIZON_S,
        "status": "resolved" if net is not None else "no_data",
        "measurement_form": "input_cutoff" if "input_cutoff_ts" in measurement else "decision_ts",
        "resolved_at": now.isoformat(),
        "source": "l2_outcome_resolver",
    }
    if net is None:
        row["no_data_reason"] = "pair_not_trading" if not_trading else "no_complete_bars"
    return row


def resolve(
    fetch_klines: KlineFetcher,
    *,
    now: datetime | None = None,
    shadow_path: Path = SHADOW_PATH,
    outcomes_path: Path = OUTCOMES_PATH,
    not_trading: Callable[[str], bool] | None = None,
) -> dict[str, int]:
    """Fällige L2-Messungen auflösen und anhängen. Ein Fehlschlag lässt die Messung offen.

    ``not_trading(symbol)`` sagt, ob die Kursquelle das Paar sicher nicht handelt; nur
    gefragt, wenn keine Kerzen kamen (ein angehaltenes Paar liefert noch Historie).
    """
    now = now or datetime.now(UTC)
    done = {r.get("candidate_id") for r in _read_jsonl(outcomes_path)}
    counts = {"resolved": 0, "no_data": 0, "pending": 0, "already": 0, "not_due": 0}
    new_rows: list[dict[str, Any]] = []
    for cid, m in resolvable(_read_jsonl(shadow_path)).items():
        if cid in done:
            counts["already"] += 1
            continue
        if not due(m, now):
            counts["not_due"] += 1
            continue
        anchor = anchor_of(m)
        assert anchor is not None  # durch resolvable() garantiert
        symbol = str(m["symbol"])
        start_ms = int((anchor - timedelta(minutes=3)).timestamp() * 1000)
        end_ms = int((anchor + timedelta(seconds=PRIMARY_HORIZON_S + 120)).timestamp() * 1000)
        try:
            bars = fetch_klines(symbol, start_ms, end_ms)
        except Exception as exc:  # noqa: BLE001 — Kursquelle darf den Lauf nicht beenden
            logger.warning("[l2-outcomes] Kerzen fuer %s nicht abrufbar: %s", symbol, exc)
            bars = None
        dead = not bars and not_trading is not None and not_trading(symbol)
        row = outcome_for(m, bars or [], now, not_trading=dead)
        if row is None:
            counts["pending"] += 1
            continue
        new_rows.append(row)
        counts[row["status"] if row["status"] == "no_data" else "resolved"] += 1
    if new_rows:
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        with append_lock(outcomes_path), outcomes_path.open("a", encoding="utf-8") as fh:
            for row in new_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return counts


def load_feature_outcomes(
    path: Path = OUTCOMES_PATH, *, horizon: int = PRIMARY_HORIZON_S
) -> list[dict[str, Any]]:
    """Ergebnisse in der Form, die ``pit_join`` erwartet (nur aufgelöste Zeilen)."""
    out = []
    for r in _read_jsonl(path):
        value = r.get(f"fwd_{horizon}s_bps")
        if value is None:
            continue
        out.append(
            {
                "candidate_id": r["candidate_id"],
                "symbol": r["symbol"],
                "side": r["side"],
                "entry_ts": r["entry_ts"],
                "net_bps": float(value),
            }
        )
    return out


def backlog(
    now: datetime, *, shadow_path: Path = SHADOW_PATH, outcomes_path: Path = OUTCOMES_PATH
) -> list[str]:
    """Fällige Messungen ohne Ergebnis, deren Anker älter als 3 h ist (für den Waechter)."""
    done = {r.get("candidate_id") for r in _read_jsonl(outcomes_path)}
    late = []
    for cid, m in resolvable(_read_jsonl(shadow_path)).items():
        anchor = anchor_of(m)
        if cid in done or anchor is None or not due(m, now):
            continue
        if now - anchor >= timedelta(hours=3):
            late.append(cid)
    return late


__all__ = [
    "OUTCOMES_PATH",
    "PRIMARY_HORIZON_S",
    "SHADOW_PATH",
    "anchor_of",
    "backlog",
    "load_feature_outcomes",
    "outcome_for",
    "resolvable",
    "resolve",
]
