"""Track 2.3 — Source × Direction × Asset-Bucket × Horizon edge audit (READ-ONLY).

Localises WHERE forward edge is carried or destroyed, plus a bearish-invert
counterfactual. This is an *instrument*, never a runtime rule: it never boosts,
downranks, inverts, sizes, gates, or fetches prices — it only reads the resolver's
already-recorded forward returns and reports.

Data source: ``artifacts/shadow_candidate_resolved.jsonl`` (the only stream with
per-horizon forward bps). Horizons present in that stream are 1m/5m/15m/1h; 4h/24h
are NOT resolved → reported as ``bps_unavailable`` (never estimated/backfilled).
Sources that never enter this stream (e.g. news alerts: thedefiant, cointelegraph,
decrypt, …) have hit/miss only (see provenance) → ``bps_unavailable`` here.

side-adjustment: a SHORT profits when price falls, so the realised return is
``+fwd`` for long and ``-fwd`` for short. The bearish counterfactual inverts the
short cohort (``inverted = -realised``), i.e. "what if we had flipped bearish".
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.learning.source_reliability import wilson_lower_bound

DEFAULT_RESOLVED_PATH = Path("artifacts/shadow_candidate_resolved.jsonl")

# Resolver horizon → forward-bps field. 4h/24h are intentionally absent (the
# resolver stops at 1h) and surface as bps_unavailable.
HORIZONS: tuple[tuple[str, str], ...] = (
    ("1m", "fwd_60s_bps"),
    ("5m", "fwd_300s_bps"),
    ("15m", "fwd_900s_bps"),
    ("1h", "fwd_3600s_bps"),
)
UNAVAILABLE_HORIZONS: tuple[str, ...] = ("4h", "24h")

_STABLES = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "BUSD", "USDD", "PYUSD"}
_MAJORS = {"BTC", "ETH"}

# Round-trip cost assumption for EV_net (entry+exit fee + spread + slippage).
# A documented constant — NOT a runtime parameter; the audit changes nothing.
DEFAULT_COST_BPS = 20.0
MIN_N = 30  # below this a cohort is INSUFFICIENT (mirrors EdgeGateConfig.min_resolved)
TRIM = 0.10


def normalize_source(source: str | None) -> str:
    """Lowercase + collapse known casing variants (decrypt/Decrypt, …)."""
    s = (source or "unknown").strip().lower()
    return {"cointelegraph": "cointelegraph", "the_defiant": "thedefiant"}.get(s, s)


def normalize_side(side: str | None) -> str:
    s = (side or "").strip().lower()
    if s in ("long", "buy", "bullish"):
        return "long"
    if s in ("short", "sell", "bearish"):
        return "short"
    return "unknown"


def asset_bucket(symbol: str | None) -> str:
    base = (symbol or "").split("/")[0].split("-")[0].upper()
    if base in _MAJORS:
        return "major"
    if base in _STABLES:
        return "stable"  # stablecoin base → ~0 move, marked + excluded from carriers
    return "alt"


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _trimmed_mean(xs: list[float], trim: float = TRIM) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = int(len(s) * trim)
    cut = s[k : len(s) - k] if len(s) - 2 * k > 0 else s
    return _mean(cut)


def _pearson_ic(scores: list[float], returns: list[float]) -> float | None:
    """Pearson correlation of signal score vs side-adjusted return. None if the
    sample is too small or either series is constant (no aligned signal)."""
    n = len(scores)
    if n < 5:
        return None
    ms, mr = _mean(scores), _mean(returns)
    sxy = sum((s - ms) * (r - mr) for s, r in zip(scores, returns, strict=True))
    sxx = sum((s - ms) ** 2 for s in scores)
    syy = sum((r - mr) ** 2 for r in returns)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


@dataclass
class HorizonStat:
    horizon: str
    available: bool
    n: int = 0
    hit_rate: float | None = None
    wilson_lb: float | None = None
    ic: float | None = None
    median_bps: float | None = None
    mean_bps: float | None = None
    trimmed_mean_bps: float | None = None
    ev_net_bps: float | None = None  # mean-based (outlier-sensitive; for reference)
    robust_ev_bps: float | None = None  # trimmed-mean-based (drives the verdict)
    inverted_ev_bps: float | None = None  # short cohorts only (mean-based)
    robust_inverted_ev_bps: float | None = None  # short cohorts only (trimmed-based)


@dataclass
class Cohort:
    source: str
    direction: str
    asset_bucket: str
    n: int
    horizons: dict[str, HorizonStat] = field(default_factory=dict)
    positive_horizons: int = 0
    verdict: str = "INSUFFICIENT"


def _horizon_stat(
    label: str, rows: list[dict[str, Any]], field_name: str, cost_bps: float
) -> HorizonStat:
    pairs: list[tuple[float, float, float]] = []  # (score, raw_fwd, adj_fwd)
    for r in rows:
        raw = r.get(field_name)
        if not isinstance(raw, (int, float)):
            continue
        adj_val = float(raw) if r["_dir"] == "long" else -float(raw)
        score = r.get("signal_confidence")
        pairs.append(
            (float(score) if isinstance(score, (int, float)) else 0.0, float(raw), adj_val)
        )
    n = len(pairs)
    if n == 0:
        return HorizonStat(horizon=label, available=False)
    adj = [p[2] for p in pairs]
    hits = sum(1 for a in adj if a > 0)
    mean_adj = _mean(adj)
    trimmed_adj = _trimmed_mean(adj)
    is_short = rows[0]["_dir"] == "short"
    return HorizonStat(
        horizon=label,
        available=True,
        n=n,
        hit_rate=round(hits / n * 100, 1),
        wilson_lb=wilson_lower_bound(hits, n),
        ic=_pearson_ic([p[0] for p in pairs], adj),
        median_bps=round(_median(adj), 1),
        mean_bps=round(mean_adj, 1),
        trimmed_mean_bps=round(trimmed_adj, 1),
        ev_net_bps=round(mean_adj - cost_bps, 1),
        robust_ev_bps=round(trimmed_adj - cost_bps, 1),
        inverted_ev_bps=round(-mean_adj - cost_bps, 1) if is_short else None,
        robust_inverted_ev_bps=round(-trimmed_adj - cost_bps, 1) if is_short else None,
    )


def _verdict(c: Cohort) -> str:
    if c.n < MIN_N:
        return "INSUFFICIENT"
    if c.asset_bucket == "stable":
        return "NEUTRAL"  # stablecoin base → no directional edge by construction
    # Verdict uses ROBUST (trimmed-mean) net EV, not the mean-based ev_net_bps —
    # a handful of tail winners must not mint a fake "carrier".
    avail = [h for h in c.horizons.values() if h.available and h.robust_ev_bps is not None]
    if not avail:
        return "NO_EDGE"
    evs: list[float] = []
    inv: list[float] = []
    short_horizons: list[float] = []
    long_horizons: list[float] = []
    for h in avail:
        if h.robust_ev_bps is None:
            continue
        evs.append(h.robust_ev_bps)
        if h.horizon in ("1m", "5m"):
            short_horizons.append(h.robust_ev_bps)
        if h.horizon == "1h":
            long_horizons.append(h.robust_ev_bps)
        if h.robust_inverted_ev_bps is not None:
            inv.append(h.robust_inverted_ev_bps)
    best_ev = max(evs)
    worst_ev = min(evs)
    pos = sum(1 for e in evs if e > 0)
    c.positive_horizons = pos
    # bearish counterfactual: original loses but inverted wins (robust) → contrarian.
    inv_best = max(inv) if inv else None

    if pos == 0 and inv_best is not None and inv_best > 0:
        return "CONTRARIAN_CANDIDATE"
    if best_ev <= 0 and worst_ev < -2 * DEFAULT_COST_BPS:
        return "DIRECTION_POISON" if c.direction == "short" else "SOURCE_POISON"
    if pos >= 2 and best_ev > 0:
        return "CARRIER_LONG" if c.direction == "long" else "SUPPORTING"
    if long_horizons and short_horizons and max(long_horizons) > 0 and max(short_horizons) <= 0:
        return "HORIZON_MISMATCH"
    if pos == 1 and best_ev > 0:
        return "SUPPORTING"
    if abs(best_ev) <= 5 and abs(worst_ev) <= 5:
        return "NEUTRAL"
    return "NO_EDGE"


def build_audit(rows: list[dict[str, Any]], *, cost_bps: float = DEFAULT_COST_BPS) -> list[Cohort]:
    """Group resolved rows by (source, direction, asset_bucket) and compute the
    per-horizon edge stats + verdict. ``rows`` must already be canary-filtered."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = (
            normalize_source(r.get("source")),
            normalize_side(r.get("side")),
            asset_bucket(r.get("symbol")),
        )
        for row in (r,):
            row["_dir"] = key[1]
        groups.setdefault(key, []).append(r)

    cohorts: list[Cohort] = []
    for (src, direction, bucket), grp in groups.items():
        c = Cohort(source=src, direction=direction, asset_bucket=bucket, n=len(grp))
        for label, fieldname in HORIZONS:
            c.horizons[label] = _horizon_stat(label, grp, fieldname, cost_bps)
        for h in UNAVAILABLE_HORIZONS:
            c.horizons[h] = HorizonStat(horizon=h, available=False)
        c.verdict = _verdict(c)
        cohorts.append(c)
    cohorts.sort(key=lambda c: (-c.n, c.source, c.direction, c.asset_bucket))
    return cohorts


def load_resolved(path: Path = DEFAULT_RESOLVED_PATH) -> tuple[list[dict[str, Any]], int]:
    """Load + canary-filter the resolved ledger. Returns (rows, canary_skipped).
    A missing file yields ([], 0). Never raises on a malformed line."""
    if not path.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("is_canary") is True:
            skipped += 1
            continue
        rows.append(d)
    return rows, skipped
