#!/usr/bin/env python3
"""V1(b) — offline Momentum-Universe FALSIFIER (read-only, no sizing, no gates).

Red-Team-Auftrag 2026-06-29: resolve the accumulated momentum-evidence shadow
measurements DIRECTLY against forward OHLCV returns — instead of waiting months
for the fill-dependent cohort-outcome harvest (n=8). Cost-netted via the SAME
``CostModel`` round-trip SSOT the research runner uses; P(mean>0) via the SAME
autocorrelation-robust moving-block bootstrap as the L2/momentum evaluator.

Resolves BOTH directions (the LONG-only feeder assumption is itself untested —
if LONG is deeply negative and SHORT symmetric-positive, the signal is a
REVERSAL, not momentum, and must be re-registered as its own hypothesis).

Pure analysis. Writes one outcomes JSONL (signaled direction) for the existing
``evaluate_momentum_evidence.py`` and prints a per-horizon verdict. Touches no
live state.
"""

from __future__ import annotations

import asyncio
import json
import statistics
from datetime import datetime
from pathlib import Path

from app.market_data.binance_adapter import BinanceAdapter
from app.market_data.history_loader import load_ohlcv_history
from app.observability.l2_evidence_eval import moving_block_bootstrap_p_mean_positive
from app.research.runner import _resolve_cost_bps, build_fetch

SHADOW = Path("artifacts/momentum_evidence_shadow.jsonl")
OUT = Path("artifacts/momentum_resolved_outcomes.jsonl")
TIMEFRAME = "1h"
HORIZONS = [1, 4, 12, 24]  # bars == hours
MIN_SAMPLE = 20
SEED = 12345
INTERVAL_MS = 3_600_000


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _ts_ms(iso: str) -> int | None:
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _p(vals: list[float]) -> float | None:
    return moving_block_bootstrap_p_mean_positive(vals, min_sample=MIN_SAMPLE, seed=SEED)


async def main() -> int:
    measurements = _read_jsonl(SHADOW)
    print(f"falsify: {len(measurements)} shadow measurements")
    cost = _resolve_cost_bps()
    print(
        f"falsify: round-trip cost = {cost:.1f} bps (CostModel SSOT); timeframe={TIMEFRAME}; horizons(h)={HORIZONS}"
    )

    fetch = build_fetch(BinanceAdapter().get_ohlcv)

    # group measurements by symbol; fetch one OHLCV window per symbol covering all
    by_symbol: dict[str, list[dict]] = {}
    for m in measurements:
        sym = m.get("symbol")
        if sym:
            by_symbol.setdefault(sym, []).append(m)

    now_ms = int(datetime.now().timestamp() * 1000)
    # per (horizon, mode) accumulator of net_bps
    acc: dict[tuple[int, str], list[float]] = {}
    outcomes: list[dict] = []
    resolved = unresolved = no_data = 0
    per_symbol_signaled: dict[str, list[float]] = {}

    for sym, ms in sorted(by_symbol.items()):
        ts_list = [t for t in (_ts_ms(m.get("ts", "")) for m in ms) if t is not None]
        if not ts_list:
            continue
        start = min(ts_list) - 2 * INTERVAL_MS
        end = now_ms
        try:
            hist = await load_ohlcv_history(sym, TIMEFRAME, start, end, fetch)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym}: fetch failed ({exc}) — {len(ms)} measurements unresolved")
            no_data += len(ms)
            continue
        candles = hist.candles
        if not candles:
            print(f"  {sym}: 0 candles (not on Binance spot?) — {len(ms)} unresolved")
            no_data += len(ms)
            continue
        opens = [_ts_ms(c.timestamp_utc) or 0 for c in candles]
        closes = [c.close for c in candles]

        for m in ms:
            mts = _ts_ms(m.get("ts", ""))
            if mts is None:
                unresolved += 1
                continue
            # entry = first candle whose open >= measurement ts
            idx = next((i for i, o in enumerate(opens) if o >= mts), None)
            if idx is None:
                unresolved += 1
                continue
            direction = str(m.get("direction", "long")).lower()
            entry_close = closes[idx]
            got_any = False
            for h in HORIZONS:
                j = idx + h
                if j >= len(closes):
                    continue
                gross = (closes[j] - entry_close) / entry_close * 10000.0
                net_long = gross - cost
                net_short = -gross - cost
                net_signaled = net_long if direction != "short" else net_short
                acc.setdefault((h, "signaled"), []).append(net_signaled)
                acc.setdefault((h, "long_always"), []).append(net_long)
                acc.setdefault((h, "short_always"), []).append(net_short)
                got_any = True
                if h == 4:  # canonical outcome horizon for the evaluator join
                    outcomes.append(
                        {"symbol": sym, "entry_ts": m.get("ts"), "net_bps": round(net_signaled, 3)}
                    )
                    per_symbol_signaled.setdefault(sym, []).append(net_signaled)
            resolved += 1 if got_any else 0
            unresolved += 0 if got_any else 1

    OUT.write_text("\n".join(json.dumps(o) for o in outcomes) + "\n", encoding="utf-8")
    print(
        f"falsify: resolved={resolved} unresolved={unresolved} no_data(symbol off-Binance)={no_data}"
    )
    print(f"falsify: wrote {len(outcomes)} outcomes (signaled dir, h=4) -> {OUT}")

    print("\n================ PER-HORIZON NET-BPS (cost-netted) ================")
    print(
        f"{'horizon':>8} {'mode':>13} {'n':>5} {'mean':>9} {'median':>9} {'P(mean>0)':>10}  verdict"
    )
    for h in HORIZONS:
        for mode in ("signaled", "long_always", "short_always"):
            vals = acc.get((h, mode), [])
            if not vals:
                continue
            n = len(vals)
            mean = statistics.fmean(vals)
            med = statistics.median(vals)
            p = _p(vals)
            ps = f"{p:.3f}" if p is not None else "n<min"
            if p is None:
                verdict = "insufficient"
            elif p > 0.95 and mean > 0:
                verdict = "EDGE?"
            elif p < 0.05:
                verdict = "neg-confirmed"
            else:
                verdict = "no-edge"
            print(f"{h:>7}h {mode:>13} {n:>5} {mean:>9.1f} {med:>9.1f} {ps:>10}  {verdict}")
        print()

    print("================ PER-SYMBOL (signaled dir, h=4) ================")
    rows = []
    for sym, vals in per_symbol_signaled.items():
        rows.append((sym, len(vals), statistics.fmean(vals)))
    for sym, n, mean in sorted(rows, key=lambda r: r[2]):
        print(f"  {sym:>14} n={n:>3} mean_net={mean:>9.1f} bps")

    print(
        f"\nfalsify: cost hurdle = {cost:.1f} bps. An EDGE needs mean_net clearing 0 "
        f"AND P(mean>0)>0.95 on the SIGNALED direction at a stable horizon."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
