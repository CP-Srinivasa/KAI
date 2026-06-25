"""Churn- / Fee-Effizienz-Report (Operator-/goal 2026-06-25, „Trades-pro-Edge").

READ-ONLY Mess-/Sichtbarkeits-Artefakt — entscheidet NICHTS, ändert KEIN
Handelsverhalten. Beantwortet die drei Operator-Fragen in USD aus den ECHTEN
Audit-Fees (nicht modelliert):

  1. Brutto-vor-Fees vs Netto-nach-Fees  (Preis-Bewegung gegen die Fee-Last)
  2. Fee-Drag                            (Round-Trip-Fees als % der |Brutto|)
  3. Fees / Handelstag                    (Trend, um Varianz/Tageslast zu sehen)

Warum ein eigenes Modul statt ``edge_report``:
  * ``edge_report.parse_closed_trades`` zählt NUR ``position_closed`` und nutzt
    MODELLIERTE Round-Trip-Kosten (CostModel) — gut für die bps-Edge, falsch für
    die Frage „wieviel Gebühren habe ich real bezahlt". Hier zählen wir die
    tatsächlichen ``fee_usd`` der Fills UND die ``position_partial_closed``
    (TP-Tiers, real realisierte Gewinne + reale Close-Fees) mit. Das Auslassen
    der Partials verzerrte die erste Churn-Analyse (Red-Team S-001, 2026-06-25).

Methodik (forensik-konsistent zur canonical Edge):
  * Haltedauer + Open-Fee je realisiertem Round-Trip via ZEITSORTIERTEM FIFO
    (Close kann nur gegen bereits geschehene Opens matchen → Haltedauer ≥ 0;
    Orphan-Closes ohne Open = kontaminierte Legacy fallen sauber raus).
  * ``trade_pnl_usd`` ist netto NUR der Close-Fee (am Audit verifiziert) →
    Brutto = trade_pnl + close_fee; Voll-Netto = trade_pnl − Open-Fee.
  * Quarantäne-Signaturen (MATIC-Stale-Exit, ETH-off-market) + Implausibilitäts-
    Guard schließen korrupte Closes aus (Alignment-Pops bleiben erhalten).
  * Default-Fenster der API = ab ``CONTAMINATION_CUTOFF_DATE`` (saubere Epoche).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Saubere Epoche: vor diesem Datum ist der Stream durch die Mai-Canary-Korruption
# kontaminiert (memory kai_edge_epoch_contamination_20260623). Die API misst den
# Churn defensiv ab hier; die CLI kann mit --since=... beliebig wählen.
CONTAMINATION_CUTOFF_DATE = "2026-06-11"

# Off-market/Korrupt-Print-Guard, byte-gleich zur edge_report-Konvention.
DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD = 0.40

_REALIZE_EVENTS = ("position_closed", "position_partial_closed")


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse_ts(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _event_date(ev: dict[str, Any]) -> str:
    dt = _parse_ts(ev.get("timestamp_utc") or ev.get("filled_at"))
    return dt.date().isoformat() if dt else "unknown"


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


@dataclass(frozen=True)
class DayChurn:
    """Fee-Kadenz eines Handelstags (Trend-/Varianz-Sicht)."""

    date: str
    fills: int
    realizations: int
    fee_spend_usd: float
    realized_gross_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "fills": self.fills,
            "realizations": self.realizations,
            "fee_spend_usd": round(self.fee_spend_usd, 2),
            "realized_gross_usd": round(self.realized_gross_usd, 2),
        }


@dataclass(frozen=True)
class ReasonStat:
    """Per-Exit-Grund (stop/take/manual/tp_tier): Anzahl + Netto + Win-Rate."""

    reason: str
    count: int
    net_usd: float
    winrate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "count": self.count,
            "net_usd": round(self.net_usd, 2),
            "winrate": round(self.winrate, 4),
        }


@dataclass(frozen=True)
class ChurnReport:
    """Fee-Effizienz über das Fenster. Reine Messung, keine Handlung."""

    available: bool
    since: str | None
    window_start: str | None
    window_end: str | None
    trading_days: int
    realization_count: int
    final_close_count: int
    partial_count: int
    excluded_count: int
    # Phantom = nicht-handelbare Symbole (Self-Pair / Stablecoin-Paar): nie ein
    # echter gebührenpflichtiger Markt → Fees fiktiv, aus allen ehrlichen Zahlen
    # ausgeschlossen, hier transparent ausgewiesen (Operator 2026-06-25).
    phantom_realization_count: int
    phantom_fees_usd: float
    # USD-Ökonomie (echte Audit-Fees)
    gross_usd: float
    open_fees_usd: float
    close_fees_usd: float
    round_trip_fees_usd: float
    net_usd: float
    fee_drag_pct: float | None  # RT-Fees / |Brutto| · None wenn |Brutto| < 1 USD
    gross_near_zero: bool
    # Kadenz
    trades_per_trading_day: float
    fee_spend_per_trading_day: float
    per_day: list[DayChurn] = field(default_factory=list)
    # Haltedauer (finale + partielle Realisierungen, FIFO)
    hold_minutes_median: float | None = None
    hold_minutes_p25: float | None = None
    hold_minutes_p75: float | None = None
    hold_under_15min_pct: float | None = None
    hold_under_1h_pct: float | None = None
    # Exit-Grund-Split
    by_reason: list[ReasonStat] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "since": self.since,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "trading_days": self.trading_days,
            "realization_count": self.realization_count,
            "final_close_count": self.final_close_count,
            "partial_count": self.partial_count,
            "excluded_count": self.excluded_count,
            "phantom_realization_count": self.phantom_realization_count,
            "phantom_fees_usd": round(self.phantom_fees_usd, 2),
            "gross_usd": round(self.gross_usd, 2),
            "open_fees_usd": round(self.open_fees_usd, 2),
            "close_fees_usd": round(self.close_fees_usd, 2),
            "round_trip_fees_usd": round(self.round_trip_fees_usd, 2),
            "net_usd": round(self.net_usd, 2),
            "fee_drag_pct": (None if self.fee_drag_pct is None else round(self.fee_drag_pct, 1)),
            "gross_near_zero": self.gross_near_zero,
            "trades_per_trading_day": round(self.trades_per_trading_day, 2),
            "fee_spend_per_trading_day": round(self.fee_spend_per_trading_day, 2),
            "per_day": [d.to_dict() for d in self.per_day],
            "hold_minutes_median": _round_opt(self.hold_minutes_median, 1),
            "hold_minutes_p25": _round_opt(self.hold_minutes_p25, 1),
            "hold_minutes_p75": _round_opt(self.hold_minutes_p75, 1),
            "hold_under_15min_pct": _round_opt(self.hold_under_15min_pct, 1),
            "hold_under_1h_pct": _round_opt(self.hold_under_1h_pct, 1),
            "by_reason": [r.to_dict() for r in self.by_reason],
            "note": self.note,
        }


def _round_opt(v: float | None, n: int) -> float | None:
    return None if v is None else round(v, n)


def _empty(since: str | None, note: str) -> ChurnReport:
    return ChurnReport(
        available=False,
        since=since,
        window_start=None,
        window_end=None,
        trading_days=0,
        realization_count=0,
        final_close_count=0,
        partial_count=0,
        excluded_count=0,
        phantom_realization_count=0,
        phantom_fees_usd=0.0,
        gross_usd=0.0,
        open_fees_usd=0.0,
        close_fees_usd=0.0,
        round_trip_fees_usd=0.0,
        net_usd=0.0,
        fee_drag_pct=None,
        gross_near_zero=True,
        trades_per_trading_day=0.0,
        fee_spend_per_trading_day=0.0,
        note=note,
    )


def load_audit_events(path: str | Path) -> list[dict[str, Any]]:
    """Audit-JSONL → dicts. Überspringt kaputte Zeilen. Leer wenn Datei fehlt."""
    p = Path(path)
    if not p.exists():
        logger.warning("[churn_report] audit file not found: %s", p)
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _is_entry_fill(ev: dict[str, Any]) -> bool:
    """order_filled das eine Position ÖFFNET (long-buy oder short-sell)."""
    if ev.get("event_type") != "order_filled":
        return False
    side = str(ev.get("side", "")).lower()
    pside = str(ev.get("position_side", "")).lower()
    return (side == "buy" and pside == "long") or (side == "sell" and pside == "short")


def build_churn_report(
    audit_path: str | Path,
    *,
    since: str | None = None,
    implausible_move_threshold: float = DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD,
) -> ChurnReport:
    """Fee-Effizienz über das Fenster ``[since, ∞)``.

    ``since`` ist ein ISO-Datum ``YYYY-MM-DD`` (oder None = voller Stream). Opens
    VOR ``since`` bleiben im FIFO (damit ein über die Grenze gehaltener Trade
    korrekt matcht), gezählt werden nur Realisierungen mit Datum ≥ ``since``.
    """
    # Lazy import: bayes_quarantine zieht (eager) portfolio_read → Zirkular-Risiko
    # (memory kai_bayes_circular_import_recalc). Hier lokal halten.
    # symbol_guard ist dependency-frei (kein app-Import) → SSOT für „nicht handelbar".
    from app.core.symbol_guard import untradeable_reason
    from app.learning.bayes_quarantine import quarantine_reason

    events = load_audit_events(audit_path)
    if not events:
        return _empty(since, "audit stream empty or missing")

    # Zeitsortierter Event-Strom (stabil): Opens + Realisierungen.
    stream: list[tuple[datetime, str, dict[str, Any]]] = []
    for ev in events:
        et = ev.get("event_type")
        if _is_entry_fill(ev):
            ts = _parse_ts(ev.get("filled_at") or ev.get("timestamp_utc"))
            if ts is not None:
                stream.append((ts, "open", ev))
        elif et in _REALIZE_EVENTS:
            ts = _parse_ts(ev.get("timestamp_utc"))
            if ts is not None:
                stream.append((ts, "close", ev))
    stream.sort(key=lambda x: x[0])

    guard_active = implausible_move_threshold > 0
    opens: dict[str, deque[list[float]]] = defaultdict(
        deque
    )  # sym -> [ts_epoch, qty, fee_per_unit]

    gross_usd = open_fees = close_fees = 0.0
    realization_count = final_close = partial = excluded = 0
    # Phantom = nicht-handelbares Symbol (Self-Pair / Stablecoin-Paar): nie ein
    # echter gebührenpflichtiger Markt → Fees sind fiktiv und gehören NICHT in die
    # ehrliche Rechnung (Operator 2026-06-25). Separat ausgewiesen, nicht still gedroppt.
    phantom_realization_count = 0
    phantom_fees = 0.0
    holds_min: list[float] = []
    reason_net: dict[str, float] = defaultdict(float)
    reason_count: dict[str, int] = defaultdict(int)
    reason_wins: dict[str, int] = defaultdict(int)
    window_start: str | None = None
    window_end: str | None = None

    for ts, kind, ev in stream:
        sym = str(ev.get("symbol", "?"))
        if kind == "open":
            qty = _f(ev.get("filled_quantity")) or _f(ev.get("quantity")) or 0.0
            if qty <= 0:
                continue
            fee = _f(ev.get("fee_usd")) or 0.0
            opens[sym].append([ts.timestamp(), qty, fee / qty])
            continue

        # --- close / partial_close ---
        entry = _f(ev.get("entry_price"))
        exit_px = _f(ev.get("exit_price"))
        if not entry or not exit_px or entry <= 0 or exit_px <= 0:
            continue
        trade_pnl = _f(ev.get("trade_pnl_usd")) or 0.0
        close_fee = _f(ev.get("fee_usd")) or 0.0  # == das Close-Leg-Fill-Fee (verifiziert)
        side = str(ev.get("position_side", "long")).lower()
        # position_partial_closed trägt KEIN quantity (qty=None). Da das Event-fee_usd
        # exakt das Close-Leg-Fee ist, gilt gross = trade_pnl + close_fee = price_move·qty
        # → qty arithmetisch ableiten (verifiziert gegen die Close-Leg-Fills).
        qty = _f(ev.get("quantity")) or 0.0
        if qty <= 0:
            price_move = (exit_px - entry) if side != "short" else (entry - exit_px)
            if abs(price_move) > 1e-12:
                qty = (trade_pnl + close_fee) / price_move
        if qty <= 0:
            continue
        day = ts.date().isoformat()
        in_window = since is None or day >= since

        # FIFO-Match (immer poppen → Alignment), Open-Fee + Haltedauer ableiten.
        dq = opens[sym]
        need, weighted_age, ofee, matched = qty, 0.0, 0.0, 0.0
        while need > 1e-9 and dq:
            o_ts, o_qty, o_fpu = dq[0]
            take = min(need, o_qty)
            weighted_age += take * (ts.timestamp() - o_ts)
            ofee += take * o_fpu
            matched += take
            need -= take
            o_qty -= take
            if o_qty <= 1e-9:
                dq.popleft()
            else:
                dq[0][1] = o_qty
        if matched <= 1e-9 or not in_window:
            continue  # Orphan (kontaminierte Legacy) oder außerhalb Fenster

        # Phantom-Symbol (Self-Pair / Stablecoin-Paar): nie eine echte Börsen-
        # Position → Open- + Close-Fee sind fiktiv. Aus der ehrlichen Rechnung
        # ausschließen, aber transparent ausweisen (Alignment-Pop ist schon passiert).
        if untradeable_reason(sym) is not None:
            phantom_realization_count += 1
            continue

        # Quarantäne + Implausibilitäts-Guard (Alignment-Pop ist schon passiert).
        if quarantine_reason(ev) is not None or (
            guard_active and abs(exit_px / entry - 1.0) > implausible_move_threshold
        ):
            excluded += 1
            continue

        gross = trade_pnl + close_fee  # Preis-Bewegung vor Fees
        net = trade_pnl - ofee  # voll-belastet (= gross − gematchte Open-Fee − Close-Fee)

        gross_usd += gross
        close_fees += close_fee
        open_fees += ofee
        realization_count += 1
        if ev.get("event_type") == "position_partial_closed":
            partial += 1
        else:
            final_close += 1
        holds_min.append(weighted_age / matched / 60.0)
        reason = str(ev.get("reason") or "?")
        reason_net[reason] += net
        reason_count[reason] += 1
        if net > 0:
            reason_wins[reason] += 1
        if window_start is None or day < window_start:
            window_start = day
        if window_end is None or day > window_end:
            window_end = day

    if realization_count == 0:
        return _empty(since, "no realizations in window")

    # --- Per-Tag-Fee-Kadenz (unabhängiger Pass über ALLE Fee-tragenden Events) ---
    day_fills: dict[str, int] = defaultdict(int)
    day_real: dict[str, int] = defaultdict(int)
    day_fee: dict[str, float] = defaultdict(float)
    day_gross: dict[str, float] = defaultdict(float)
    for ev in events:
        et = ev.get("event_type")
        if et not in ("order_filled", *_REALIZE_EVENTS):
            continue
        day = _event_date(ev)
        if day == "unknown" or (since is not None and day < since):
            continue
        fee = _f(ev.get("fee_usd")) or 0.0
        # Phantom-Symbol-Fills (Self-Pair / Stablecoin-Paar): fiktive Fees → aus der
        # ehrlichen Tages-Kadenz raus, separat als phantom_fees ausgewiesen. NUR aus
        # order_filled summieren (das position_(partial_)closed-Event trägt dieselbe
        # Close-Fee → sonst Doppelzählung, genau wie bei den realen Tages-Fees).
        if untradeable_reason(str(ev.get("symbol", "?"))) is not None:
            if et == "order_filled":
                phantom_fees += fee
            continue
        # Fee-SPEND nur aus order_filled (jeder Open- UND Close-Leg ist ein Fill);
        # das position_(partial_)closed-Event trägt DIESELBE Close-Fee → würde
        # sonst doppelt zählen. Realisierungen/Brutto kommen aus den Events.
        if et == "order_filled":
            day_fee[day] += fee
            day_fills[day] += 1
        else:
            day_real[day] += 1
            tp = _f(ev.get("trade_pnl_usd")) or 0.0
            day_gross[day] += tp + fee  # gross = trade_pnl + close_fee
    all_days = sorted(set(day_fee) | set(day_fills) | set(day_real) | set(day_gross))
    per_day = [
        DayChurn(
            date=d,
            fills=day_fills.get(d, 0),
            realizations=day_real.get(d, 0),
            fee_spend_usd=day_fee.get(d, 0.0),
            realized_gross_usd=day_gross.get(d, 0.0),
        )
        for d in all_days
    ]

    trading_days = len(all_days)
    rt_fees = open_fees + close_fees
    net_usd = gross_usd - rt_fees
    gross_near_zero = abs(gross_usd) < 1.0
    fee_drag = None if gross_near_zero else rt_fees / abs(gross_usd) * 100.0

    holds_sorted = sorted(holds_min)
    n_h = len(holds_sorted)
    by_reason = [
        ReasonStat(
            reason=r,
            count=reason_count[r],
            net_usd=reason_net[r],
            winrate=(reason_wins[r] / reason_count[r] if reason_count[r] else 0.0),
        )
        for r in sorted(reason_count, key=lambda k: reason_net[k])
    ]

    note = (
        "READ-ONLY Fee-Effizienz. trade_pnl_usd ist netto der Close-Fee; "
        "Brutto = +Close-Fee, Netto = −Open-Fee (FIFO). Partials inkludiert. "
        "Kein Handelseingriff."
    )
    if gross_near_zero:
        note += " Brutto ≈ 0 → Fees sind der dominante Ergebnis-Faktor (Fee-Drag instabil)."
    if phantom_realization_count or phantom_fees:
        note += (
            f" {phantom_realization_count} Phantom-Realisierung(en) (nicht-handelbare "
            f"Self-Pair/Stablecoin-Paare) mit {phantom_fees:.2f} USD fiktiven Fees aus allen "
            "Zahlen ausgeschlossen (nie eine echte gebührenpflichtige Position)."
        )

    return ChurnReport(
        available=True,
        since=since,
        window_start=window_start,
        window_end=window_end,
        trading_days=trading_days,
        realization_count=realization_count,
        final_close_count=final_close,
        partial_count=partial,
        excluded_count=excluded,
        phantom_realization_count=phantom_realization_count,
        phantom_fees_usd=phantom_fees,
        gross_usd=gross_usd,
        open_fees_usd=open_fees,
        close_fees_usd=close_fees,
        round_trip_fees_usd=rt_fees,
        net_usd=net_usd,
        fee_drag_pct=fee_drag,
        gross_near_zero=gross_near_zero,
        trades_per_trading_day=(realization_count / trading_days if trading_days else 0.0),
        fee_spend_per_trading_day=(sum(day_fee.values()) / trading_days if trading_days else 0.0),
        per_day=per_day,
        hold_minutes_median=_percentile(holds_sorted, 50),
        hold_minutes_p25=_percentile(holds_sorted, 25),
        hold_minutes_p75=_percentile(holds_sorted, 75),
        hold_under_15min_pct=(
            sum(1 for h in holds_sorted if h < 15) / n_h * 100.0 if n_h else None
        ),
        hold_under_1h_pct=(sum(1 for h in holds_sorted if h < 60) / n_h * 100.0 if n_h else None),
        by_reason=by_reason,
        note=note,
    )


def render_churn_report(r: ChurnReport) -> str:
    """Kompakte Klartext-Tabelle für die CLI."""
    if not r.available:
        return f"[churn] keine Daten: {r.note}"
    lines: list[str] = []
    win = f"{r.window_start}–{r.window_end}" if r.window_start else "?"
    lines.append(f"Churn / Fee-Effizienz  ({win}, {r.trading_days} Handelstage)")
    lines.append(
        f"  Realisierungen: {r.realization_count}  "
        f"(finale {r.final_close_count} + partielle {r.partial_count}; "
        f"{r.excluded_count} quarantäniert)  ·  {r.trades_per_trading_day:.1f}/Tag"
    )
    lines.append("  -- USD-Ökonomie (echte Fees) --")
    lines.append(f"    Brutto (vor Fees) : {r.gross_usd:+10.2f}")
    lines.append(
        f"    Round-Trip-Fees   : {r.round_trip_fees_usd:10.2f}"
        f"  (Open {r.open_fees_usd:.2f} + Close {r.close_fees_usd:.2f})"
    )
    lines.append(f"    NETTO (nach Fees) : {r.net_usd:+10.2f}")
    if r.phantom_realization_count or r.phantom_fees_usd:
        lines.append(
            f"    Phantom (excl.)   : {r.phantom_fees_usd:10.2f}  fiktive Fees "
            f"({r.phantom_realization_count} nicht-handelbare Self-Pair/Stablecoin-Realis.)"
        )
    if r.fee_drag_pct is not None:
        lines.append(f"    Fee-Drag          : {r.fee_drag_pct:.0f}% der |Brutto|")
    else:
        lines.append("    Fee-Drag          : n/a (Brutto ≈ 0 → Fees dominieren)")
    lines.append(f"    Fees/Handelstag   : {r.fee_spend_per_trading_day:.2f}")
    if r.hold_minutes_median is not None:
        lines.append(
            f"  -- Haltedauer -- median {r.hold_minutes_median:.0f}min "
            f"(p25 {r.hold_minutes_p25:.0f} / p75 {r.hold_minutes_p75:.0f}); "
            f"<15min {r.hold_under_15min_pct:.0f}% · <1h {r.hold_under_1h_pct:.0f}%"
        )
    if r.by_reason:
        lines.append("  -- Exit-Grund (Netto USD / Win) --")
        for rs in r.by_reason:
            lines.append(
                f"    {rs.reason:10s}: n={rs.count:3d}  net={rs.net_usd:+9.2f}  "
                f"win={rs.winrate * 100:3.0f}%"
            )
    return "\n".join(lines)
