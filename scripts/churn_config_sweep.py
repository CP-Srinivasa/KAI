#!/usr/bin/env python3
"""Churn-Cooldown-Sweep — read-only Gegenprobe: würde ein STRENGERES Re-Entry-
Cooldown das realisierte Netto verbessern? (Plan ``encapsulated-brewing-kahn`` PR B.)

Hintergrund: Die canonical-Edge ist belastbar widerlegt (gross ≈ 0, sogar
−4,4 bps), der Verlust ist überwiegend Fee/Churn. Ein naheliegender Hebel wäre,
das Churn-Cooldown (``app/risk/churn_killer.py``) härter zu stellen. ABER:
Min-Hold + Frequenz-Cap wurden bereits datenbelegt verworfen
(memory kai_churn_no_gate_fee_visibility_20260625). Dieses Skript RE-VERIFIZIERT
das auf aktuellem n, statt blind zu härten.

Methode (ehrlich, gegenfaktisch, KEINE Modell-Fees — echtes ``trade_pnl_usd``):
  * Round-Trips aus dem Audit per ZEITSORTIERTEM FIFO bilden (Entry-Fills →
    Closes, je Symbol). Jeder Round-Trip trägt seinen Entry-Zeitpunkt und seinen
    Close-Zeitpunkt + realisiertes Netto (``trade_pnl_usd``) + Brutto
    (``trade_pnl + close_fee``). Forensik-konsistent zu ``churn_report``:
    Implausibilitäts-Guard (>40 % Move) raus, Orphan-Closes ohne Open raus.
  * Je Kandidat-Cooldown C (Minuten) GREEDY replayen: pro Symbol nach Entry-Zeit
    ordnen; ersten Trade behalten, jeden weiteren nur behalten, wenn sein Entry
    ≥ C Minuten NACH dem Close des zuletzt behaltenen Trades liegt — sonst ist es
    ein „gechurnter" Re-Entry und wird GESCHNITTEN.
  * Kernzahl je C: das Netto der GESCHNITTENEN Trades.
      cut_net > 0  → Schneiden VERWIRFT Gewinner → härten SCHADET.
      cut_net < 0  → Schneiden entfernt Verlierer → härten HILFT.

READ-ONLY: lädt nur den Audit, schreibt nichts, ändert KEINE Config. Eine
Config-Änderung ist erst gerechtfertigt, wenn ein Kandidat ein klar besseres
Netto zeigt (sonst bleibt der Prior-Befund „kein Härten" stehen).

Usage:
    python scripts/churn_config_sweep.py
    python scripts/churn_config_sweep.py --since 2026-06-11 --cooldowns 60,120,240,480
    python scripts/churn_config_sweep.py --audit artifacts/paper_execution_audit.jsonl
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.observability.churn_report import (  # noqa: E402
    CONTAMINATION_CUTOFF_DATE,
    DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD,
    _f,
    _is_entry_fill,
    _parse_ts,
    load_audit_events,
)

_REALIZE_EVENTS = ("position_closed", "position_partial_closed")


@dataclass(frozen=True)
class RoundTrip:
    """One realised round-trip with its entry+close time and real net/gross USD."""

    symbol: str
    entry_ts: datetime
    close_ts: datetime
    net_usd: float  # trade_pnl_usd (net of the close-leg fee, as persisted)
    gross_usd: float  # trade_pnl_usd + close_fee (pre-close-fee price move)
    notional_usd: float


@dataclass(frozen=True)
class CooldownResult:
    """Counterfactual outcome of enforcing one re-entry cooldown over the window."""

    cooldown_min: float
    n_total: int
    n_cut: int
    net_total_usd: float  # net of ALL round-trips (the realised baseline)
    net_kept_usd: float  # net of the trades that survive the cooldown
    net_cut_usd: float  # net of the trades the cooldown would have removed
    cut_mean_net_usd: float  # mean net of the cut set (per-trade)

    @property
    def helps(self) -> bool:
        """True iff cutting these trades improves net (cut set is net-negative)."""
        return self.net_cut_usd < 0.0


def build_round_trips(
    events: list[dict],
    *,
    since: str | None = None,
    implausible_move_threshold: float = DEFAULT_IMPLAUSIBLE_MOVE_THRESHOLD,
) -> list[RoundTrip]:
    """FIFO-pair entry fills to closes per symbol → timed round-trips.

    Entry time of a round-trip = the timestamp of the FIRST (earliest) entry fill
    matched by FIFO; close time = the realising event's timestamp. Orphan closes
    with no matching open (contaminated legacy) and implausible >threshold moves
    are dropped — byte-consistent with ``churn_report``. ``since`` filters by the
    CLOSE date (opens before the cutoff stay in the FIFO so a held-over trade
    still matches).
    """
    stream: list[tuple[datetime, str, dict]] = []
    for ev in events:
        if _is_entry_fill(ev):
            ts = _parse_ts(ev.get("filled_at") or ev.get("timestamp_utc"))
            if ts is not None:
                stream.append((ts, "open", ev))
        elif ev.get("event_type") in _REALIZE_EVENTS:
            ts = _parse_ts(ev.get("timestamp_utc"))
            if ts is not None:
                stream.append((ts, "close", ev))
    stream.sort(key=lambda x: x[0])

    guard_active = implausible_move_threshold > 0
    # sym -> deque[[entry_ts, qty_remaining]]
    opens: dict[str, deque[list]] = defaultdict(deque)
    out: list[RoundTrip] = []

    for ts, kind, ev in stream:
        sym = str(ev.get("symbol", "?"))
        if kind == "open":
            qty = _f(ev.get("filled_quantity")) or _f(ev.get("quantity")) or 0.0
            if qty > 0:
                opens[sym].append([ts, qty])
            continue

        # --- close / partial_close ---
        entry = _f(ev.get("entry_price"))
        exit_px = _f(ev.get("exit_price"))
        if not entry or not exit_px or entry <= 0 or exit_px <= 0:
            continue
        if guard_active and abs(exit_px / entry - 1.0) > implausible_move_threshold:
            continue
        trade_pnl = _f(ev.get("trade_pnl_usd")) or 0.0
        close_fee = _f(ev.get("fee_usd")) or 0.0
        side = str(ev.get("position_side", "long")).lower()
        qty = _f(ev.get("quantity")) or 0.0
        if qty <= 0:  # partial_closed carries no qty → derive from the price move
            price_move = (exit_px - entry) if side != "short" else (entry - exit_px)
            if abs(price_move) > 1e-12:
                qty = (trade_pnl + close_fee) / price_move
        if qty <= 0:
            continue

        day = ts.date().isoformat()
        in_window = since is None or day >= since

        dq = opens[sym]
        need, matched = qty, 0.0
        entry_ts: datetime | None = None
        while need > 1e-9 and dq:
            o_ts, o_qty = dq[0]
            if entry_ts is None:
                entry_ts = o_ts
            take = min(need, o_qty)
            matched += take
            need -= take
            o_qty -= take
            if o_qty <= 1e-9:
                dq.popleft()
            else:
                dq[0][1] = o_qty
        if matched <= 1e-9 or entry_ts is None or not in_window:
            continue  # orphan (no open) or outside window

        out.append(
            RoundTrip(
                symbol=sym,
                entry_ts=entry_ts,
                close_ts=ts,
                net_usd=trade_pnl,
                gross_usd=trade_pnl + close_fee,
                notional_usd=abs(entry * qty),
            )
        )
    return out


def sweep_cooldowns(
    round_trips: list[RoundTrip], cooldowns_min: list[float]
) -> list[CooldownResult]:
    """For each candidate cooldown, greedily replay the re-entry gate per symbol.

    Greedy keeps the first round-trip of a symbol; a later round-trip survives
    only if its entry is >= cooldown minutes AFTER the close of the last KEPT
    round-trip of that symbol (anchoring on accepted closes, exactly how the
    live churn-killer cooldown re-anchors). Otherwise it is a cut re-entry.
    """
    net_total = sum(rt.net_usd for rt in round_trips)
    by_symbol: dict[str, list[RoundTrip]] = defaultdict(list)
    for rt in round_trips:
        by_symbol[rt.symbol].append(rt)
    for rts in by_symbol.values():
        rts.sort(key=lambda r: r.entry_ts)

    results: list[CooldownResult] = []
    for cd in cooldowns_min:
        cut: list[RoundTrip] = []
        kept_net = 0.0
        for rts in by_symbol.values():
            last_kept_close: datetime | None = None
            for rt in rts:
                gap_min = (
                    None
                    if last_kept_close is None
                    else (rt.entry_ts - last_kept_close).total_seconds() / 60.0
                )
                if last_kept_close is not None and gap_min is not None and gap_min < cd:
                    cut.append(rt)  # churned re-entry → removed
                else:
                    kept_net += rt.net_usd
                    last_kept_close = rt.close_ts
        net_cut = sum(rt.net_usd for rt in cut)
        results.append(
            CooldownResult(
                cooldown_min=cd,
                n_total=len(round_trips),
                n_cut=len(cut),
                net_total_usd=net_total,
                net_kept_usd=kept_net,
                net_cut_usd=net_cut,
                cut_mean_net_usd=(net_cut / len(cut)) if cut else 0.0,
            )
        )
    return results


def render(results: list[CooldownResult], *, since: str | None) -> str:
    lines: list[str] = []
    window = since or "voller Stream"
    if not results or results[0].n_total == 0:
        return f"Churn-Cooldown-Sweep ({window}): keine Round-Trips im Fenster."
    base = results[0]
    lines.append(f"Churn-Cooldown-Sweep ({window}) — {base.n_total} Round-Trips")
    lines.append(f"  Realisiertes Netto (Baseline): {base.net_total_usd:+.2f} USD")
    lines.append("")
    lines.append("  Cooldown  geschnitten   Netto-geschnitten   Netto-behalten   Verdikt")
    for r in results:
        verdict = "HÄRTEN HILFT" if r.helps and r.n_cut > 0 else "härten schadet/neutral"
        lines.append(
            f"  {r.cooldown_min:>6.0f}m  "
            f"{r.n_cut:>4d}/{r.n_total:<4d}    "
            f"{r.net_cut_usd:>+10.2f} USD     "
            f"{r.net_kept_usd:>+10.2f} USD    "
            f"{verdict}"
        )
    lines.append("")
    helpful = [r for r in results if r.helps and r.n_cut > 0]
    if helpful:
        best = min(helpful, key=lambda r: r.net_cut_usd)
        lines.append(
            f"  → Datenbeleg: Cooldown {best.cooldown_min:.0f}m schneidet "
            f"{best.net_cut_usd:+.2f} USD netto-negative Churn-Trades — Härten erwägen."
        )
    else:
        lines.append(
            "  → Kein Kandidat verbessert das Netto (geschnittene Trades sind im "
            "Schnitt nicht netto-negativ). Härten NICHT datenjustifiziert — "
            "bestätigt den Prior-Befund (kai_churn_no_gate_fee_visibility)."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only churn-cooldown counterfactual sweep.")
    ap.add_argument("--audit", default="artifacts/paper_execution_audit.jsonl")
    ap.add_argument(
        "--since",
        default=CONTAMINATION_CUTOFF_DATE,
        help="ISO date (YYYY-MM-DD); closes before it are ignored. '' = full stream.",
    )
    ap.add_argument(
        "--cooldowns",
        default="60,120,240,480",
        help="comma-separated candidate cooldowns in minutes (current live = 60).",
    )
    args = ap.parse_args(argv)

    since = args.since or None
    cooldowns = [float(x) for x in str(args.cooldowns).split(",") if x.strip()]
    events = load_audit_events(args.audit)
    round_trips = build_round_trips(events, since=since)
    results = sweep_cooldowns(round_trips, cooldowns)
    print(render(results, since=since))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
