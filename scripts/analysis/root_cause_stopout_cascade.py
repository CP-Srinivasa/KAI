#!/usr/bin/env python3
"""Phase-A forensic reconstruction of the frozen forward-edge loss cohort.

Stop-the-line context: EXECUTION_ENTRY_MODE was flipped paper->disabled on
2026-06-02 after build_edge_report showed gross_bps_mean=-35.3 over 105 closes
(negative *before* costs). This tool does NOT decide entry vs geometry — it
produces the evidence to classify the loss cohort into:

    ENTRY_BAD | STOP_IN_NOISE_BAND | TP_UNREACHABLE | REGIME_MISMATCH | ADVERSE_SELECTION

It is read-only: it never writes trading state, never places orders.

Sources (paper_execution_audit.jsonl, schema_version v2):
  - position_closed : entry_price, exit_price, reason, trade_pnl_usd, fee_usd, order_id, side
  - order_filled    : filled_at (= entry time), order_id
  - order_created   : stop_loss, take_profit, limit_price, order_id  (geometry)
  - trading_loop_audit.jsonl : regime per cycle (joined by order_id when present)

MAE/MFE: not persisted intra-trade. Optionally reconstructed from Binance 1m
klines for liquid symbols (best-effort, network-gated). Everything else is
marked not_reconstructable with a reason.
"""
from __future__ import annotations

import json
import statistics
import sys
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "artifacts" / "paper_execution_audit.jsonl"
LOOP = ROOT / "artifacts" / "trading_loop_audit.jsonl"
OUT = ROOT / "artifacts" / "root_cause_stopout_cascade_20260602.json"

LIQUID = {"BTC", "ETH", "XRP", "SOL", "ADA", "LINK", "DOGE", "DOT", "AVAX", "BNB", "LTC", "TRX"}
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"


def _ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def side_adjusted_bps(entry: float, exit_: float, side: str) -> float:
    if entry <= 0:
        return 0.0
    raw = (exit_ - entry) / entry * 1e4
    return raw if side == "long" else -raw


@dataclass
class Trade:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    stop_price: float | None
    take_price: float | None
    entry_time: datetime | None
    exit_time: datetime | None
    reason: str
    trade_pnl_usd: float
    fee_usd: float
    order_id: str
    regime: str | None = None
    # derived
    gross_bps: float = 0.0
    stop_dist_bps: float | None = None
    take_dist_bps: float | None = None
    rr: float | None = None
    holding_s: float | None = None
    # mae/mfe (optional)
    mae_bps: float | None = None
    mfe_bps: float | None = None
    mfe_before_mae: bool | None = None
    reached_take: bool | None = None
    reached_stop: bool | None = None
    mae_mfe_status: str = "not_attempted"


def load_events(path: Path) -> list[dict]:
    out = []
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _entry_index(events: list[dict]) -> dict:
    """Map (symbol, rounded entry fill_price) -> {filled_at, stop, take, limit}.

    The entry order is linked to a position_closed by matching the close's
    entry_price to the buy fill's fill_price (position_closed.order_id is the
    EXIT order, so order_id cannot be used to recover entry geometry).
    """
    created_by_oid: dict[str, dict] = {}
    for o in events:
        if o.get("event_type") == "order_created" and o.get("order_id"):
            created_by_oid[o["order_id"]] = o
    idx: dict = {}
    for o in events:
        if o.get("event_type") != "order_filled":
            continue
        if (o.get("side") or "").lower() != "buy":
            continue
        sym = o.get("symbol", "?")
        fp = o.get("fill_price")
        if fp is None:
            continue
        key = (sym, round(float(fp), 10))
        c = created_by_oid.get(o.get("order_id", ""), {})
        idx.setdefault(key, {
            "filled_at": o.get("filled_at") or o.get("timestamp_utc"),
            "stop": c.get("stop_loss"),
            "take": c.get("take_profit"),
            "limit": c.get("limit_price"),
            "order_id": o.get("order_id", ""),
        })
    return idx


def _regime_by_order(loop_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not loop_path.exists():
        return out
    for o in load_events(loop_path):
        oid = o.get("order_id")
        if oid and o.get("regime"):
            out[oid] = f"{o.get('regime')}/{o.get('regime_vol_class', '?')}"
    return out


def build_trades(events: list[dict], clean: list) -> list[Trade]:
    """Cohort = quarantine-clean ClosedTrade list from edge_report (has regime,
    gross via cost model). Enriched with entry-side geometry via price-join."""
    idx = _entry_index(events)
    regime_by_order = _regime_by_order(LOOP)
    trades: list[Trade] = []
    for ct in clean:
        entry = float(ct.entry_price or 0)
        exit_ = float(ct.exit_price or 0)
        side = ct.position_side or "long"
        sym = ct.symbol
        meta = idx.get((sym, round(entry, 10)), {})
        stop = meta.get("stop")
        take = meta.get("take")
        et = _ts(meta.get("filled_at") or "")
        xt = _ts(getattr(ct, "timestamp_utc", "") or "")
        regime = getattr(ct, "regime", None) or regime_by_order.get(meta.get("order_id", ""))
        t = Trade(
            symbol=sym,
            side=side,
            entry_price=entry,
            exit_price=exit_,
            stop_price=float(stop) if stop not in (None, "") else None,
            take_price=float(take) if take not in (None, "") else None,
            entry_time=et,
            exit_time=xt,
            reason=str(ct.reason or ""),
            trade_pnl_usd=float(ct.trade_pnl_usd or 0),
            fee_usd=float(getattr(ct, "fee_usd", 0) or 0),
            order_id=meta.get("order_id", ""),
            regime=regime,
        )
        t.gross_bps = side_adjusted_bps(entry, exit_, side)
        if t.stop_price and entry:
            t.stop_dist_bps = abs(entry - t.stop_price) / entry * 1e4
        if t.take_price and entry:
            t.take_dist_bps = abs(t.take_price - entry) / entry * 1e4
        if t.stop_dist_bps and t.take_dist_bps:
            t.rr = t.take_dist_bps / t.stop_dist_bps
        if et and xt:
            t.holding_s = (xt - et).total_seconds()
        trades.append(t)
    return trades


def fetch_klines(symbol: str, start: datetime, end: datetime) -> list[list] | None:
    pair = symbol.replace("/", "")
    s = int(start.timestamp() * 1000) - 60_000
    e = int(end.timestamp() * 1000) + 60_000
    url = f"{BINANCE_KLINES}?symbol={pair}&interval=1m&startTime={s}&endTime={e}&limit=1000"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def compute_mae_mfe(trades: list[Trade]) -> None:
    for t in trades:
        base = t.symbol.split("/")[0]
        if base not in LIQUID:
            t.mae_mfe_status = "not_reconstructable:exotic_symbol_no_binance"
            continue
        if not (t.entry_time and t.exit_time and t.entry_price):
            t.mae_mfe_status = "not_reconstructable:missing_timestamps"
            continue
        kl = fetch_klines(t.symbol, t.entry_time, t.exit_time)
        if not kl:
            t.mae_mfe_status = "not_reconstructable:kline_fetch_failed"
            continue
        # restrict to bars within [entry,exit]
        bars = []
        es, xs = t.entry_time.timestamp() * 1000, t.exit_time.timestamp() * 1000
        for k in kl:
            open_ms = k[0]
            if open_ms < es - 60_000 or open_ms > xs + 60_000:
                continue
            bars.append((open_ms, float(k[2]), float(k[3])))  # ts, high, low
        if not bars:
            t.mae_mfe_status = "not_reconstructable:no_bars_in_window"
            continue
        entry = t.entry_price
        fav_t = adv_t = None
        best_fav = -1e9
        worst_adv = 1e9
        for ms, hi, lo in bars:
            if t.side == "long":
                fav = (hi - entry) / entry * 1e4
                adv = (lo - entry) / entry * 1e4
            else:
                fav = (entry - lo) / entry * 1e4
                adv = (entry - hi) / entry * 1e4
            if fav > best_fav:
                best_fav, fav_t = fav, ms
            if adv < worst_adv:
                worst_adv, adv_t = adv, ms
        t.mfe_bps = round(best_fav, 1)
        t.mae_bps = round(worst_adv, 1)
        t.mfe_before_mae = (fav_t is not None and adv_t is not None and fav_t <= adv_t)
        if t.take_dist_bps is not None:
            t.reached_take = best_fav >= t.take_dist_bps
        if t.stop_dist_bps is not None:
            t.reached_stop = worst_adv <= -t.stop_dist_bps
        t.mae_mfe_status = "ok"


def agg(values: list[float]) -> dict:
    if not values:
        return {}
    vs = sorted(values)
    n = len(vs)
    trim = vs[max(1, n // 10): n - max(1, n // 10)] if n >= 10 else vs
    return {
        "n": n,
        "mean": round(statistics.fmean(vs), 1),
        "median": round(statistics.median(vs), 1),
        "trimmed_mean_10pct": round(statistics.fmean(trim), 1),
        "min": round(vs[0], 1),
        "max": round(vs[-1], 1),
    }


def split_by(trades: list[Trade], key) -> dict:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        buckets[str(key(t))].append(t)
    out = {}
    for k, ts in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        gb = [t.gross_bps for t in ts]
        wins = sum(1 for t in ts if t.gross_bps > 0)
        out[k] = {
            "count": len(ts),
            "gross_bps_mean": round(statistics.fmean(gb), 1),
            "gross_bps_median": round(statistics.median(gb), 1),
            "winrate": round(wins / len(ts), 3),
            "total_pnl_usd": round(sum(t.trade_pnl_usd for t in ts), 1),
        }
    return out


def main() -> None:
    do_mae = "--mae" in sys.argv
    events = load_events(AUDIT)
    # Cohort: quarantine-clean closed trades (same exclusions as edge_report).
    from app.observability import edge_report as er
    parsed = er.parse_closed_trades_with_exclusions(events)
    clean = parsed.trades if hasattr(parsed, "trades") else (
        parsed[0] if isinstance(parsed, tuple) else parsed)
    excluded_n = None
    for attr in ("excluded", "excluded_trades", "quarantined", "exclusions", "excluded_count"):
        v = getattr(parsed, attr, None)
        if v is not None:
            excluded_n = len(v) if hasattr(v, "__len__") else v
            break
    trades = build_trades(events, clean)
    if do_mae:
        compute_mae_mfe(trades)

    gross = [t.gross_bps for t in trades]
    by_pnl = sorted(trades, key=lambda t: t.trade_pnl_usd)
    worst, best = by_pnl[0], by_pnl[-1]
    total = sum(t.trade_pnl_usd for t in trades)
    wo_best = sum(t.trade_pnl_usd for t in trades if t is not best)
    wo_worst = sum(t.trade_pnl_usd for t in trades if t is not worst)
    stops = [t for t in trades if "stop" in t.reason]
    takes = [t for t in trades if "take" in t.reason]

    report = {
        "report_type": "root_cause_stopout_cascade_phase_a",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_experiment": "forward_edge_experiment_frozen_20260602.json",
        "cohort": "quarantine-clean (edge_report exclusions applied)",
        "n_trades": len(trades),
        "n_quarantine_excluded": excluded_n,
        "overall": {
            "gross_bps": agg(gross),
            "winrate": round(sum(1 for g in gross if g > 0) / len(gross), 3) if gross else None,
            "stop_out_rate": round(len(stops) / len(trades), 3) if trades else None,
            "take_rate": round(len(takes) / len(trades), 3) if trades else None,
            "total_pnl_usd": round(total, 1),
            "total_pnl_without_best_trade": round(wo_best, 1),
            "total_pnl_without_worst_trade": round(wo_worst, 1),
            "best_trade": {"symbol": best.symbol, "pnl": round(best.trade_pnl_usd, 1), "gross_bps": round(best.gross_bps, 1)},
            "worst_trade": {"symbol": worst.symbol, "pnl": round(worst.trade_pnl_usd, 1), "gross_bps": round(worst.gross_bps, 1)},
        },
        "geometry": {
            "stop_dist_bps": agg([t.stop_dist_bps for t in trades if t.stop_dist_bps is not None]),
            "take_dist_bps": agg([t.take_dist_bps for t in trades if t.take_dist_bps is not None]),
            "rr": agg([t.rr for t in trades if t.rr is not None]),
            "holding_seconds": agg([t.holding_s for t in trades if t.holding_s is not None]),
            "trades_with_geometry": sum(1 for t in trades if t.stop_dist_bps is not None),
        },
        "split_by_reason": split_by(trades, lambda t: t.reason),
        "split_by_side": split_by(trades, lambda t: t.side),
        "split_by_symbol": split_by(trades, lambda t: t.symbol),
        "split_by_regime": split_by(trades, lambda t: t.regime or "unknown"),
    }

    if do_mae:
        ok = [t for t in trades if t.mae_mfe_status == "ok"]
        status_counts = Counter(t.mae_mfe_status for t in trades)
        report["mae_mfe"] = {
            "status_counts": dict(status_counts),
            "n_reconstructed": len(ok),
            "mae_bps": agg([t.mae_bps for t in ok if t.mae_bps is not None]),
            "mfe_bps": agg([t.mfe_bps for t in ok if t.mfe_bps is not None]),
            "pct_mfe_reached_take": round(sum(1 for t in ok if t.reached_take) / len(ok), 3) if ok else None,
            "pct_mae_reached_stop": round(sum(1 for t in ok if t.reached_stop) / len(ok), 3) if ok else None,
            "pct_mfe_before_mae": round(sum(1 for t in ok if t.mfe_before_mae) / len(ok), 3) if ok else None,
            "median_mfe_of_stopped_trades": (
                round(statistics.median([t.mfe_bps for t in ok if "stop" in t.reason and t.mfe_bps is not None]), 1)
                if [t for t in ok if "stop" in t.reason] else None
            ),
        }

    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\n[written] {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
