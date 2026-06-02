"""Shadow-Candidate-Ledger — learn the entry signal WITHOUT trading (Phase B).

Background (2026-06-02): the autonomous entry loop is ``EXECUTION_ENTRY_MODE=
disabled`` because its closed-trade cohort has negative cost-adjusted edge
(P(mu_net>0)=2.56%, gross_bps_mean=-35; root-cause report
``root_cause_stopout_cascade_20260602``). Phase-A could only *retrospectively*
reconstruct MAE/MFE for 52/110 trades (no intra-trade prices stored) and regime
per-trade was not reconstructable at all.

Phase B closes that instrumentation gap **without** re-enabling trading: for
every signal the loop WOULD have entered, we record a hypothetical candidate
(entry/SL/TP/geometry/regime/gate-decision) and later resolve forward returns +
MAE/MFE from market klines. No paper fill, no position, no order — pure
observation. This is strictly safer than ``entry_mode=paper`` and gives the same
(better: regime-tagged, MAE/MFE-complete) evidence for the next entry/exit
candidate.

Diagnose buckets the resolved data is meant to separate (see ``classify``):

    ADVERSE_SELECTION : MAE hits fast, MFE ~0, forward returns negative from t0
    STOP_IN_NOISE_BAND: MFE often > stop distance before MAE hits the stop
    TP_UNREACHABLE    : MFE rarely reaches TP, stop reached more often
    REGIME_MISMATCH   : losses concentrate in a regime / side / symbol

Design: compute functions are pure / IO-free (unit-testable with synthetic
bars). Persistence + kline fetching are thin shells around them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LEDGER_PATH = Path("artifacts/shadow_candidate_ledger.jsonl")
RESOLVED_PATH = Path("artifacts/shadow_candidate_resolved.jsonl")

# Forward-return horizons in seconds. The longest also bounds the MAE/MFE window.
HORIZONS_S: tuple[int, ...] = (60, 300, 900, 3600)

# A price bar: (open_time_ms, high, low, close). Matches the first/2nd/3rd/4th
# Binance kline fields — but the ledger never imports a network client; a fetcher
# is injected so tests stay offline and the source stays swappable.
Bar = tuple[int, float, float, float]
KlineFetcher = Callable[[str, int, int], Sequence[Bar] | None]


# --------------------------------------------------------------------------- #
# Pure compute
# --------------------------------------------------------------------------- #


def side_adjusted_bps(entry: float, price: float, side: str) -> float:
    """Return in bps from entry to ``price``, signed so positive = favourable."""
    if entry <= 0:
        return 0.0
    raw = (price - entry) / entry * 1e4
    return raw if side == "long" else -raw


def compute_forward_returns(
    *,
    entry_price: float,
    side: str,
    entry_ts_ms: int,
    bars: Sequence[Bar],
    horizons_s: Sequence[int] = HORIZONS_S,
) -> dict[str, float | None]:
    """Side-adjusted close-to-close return at each horizon (bps).

    For horizon ``h`` we take the close of the last bar whose open-time is
    <= entry_ts + h. ``None`` when no bar covers the horizon yet (not elapsed /
    no data) — never silently 0.
    """
    out: dict[str, float | None] = {}
    last_open_ms = bars[-1][0] if bars else None
    for h in horizons_s:
        target_ms = entry_ts_ms + h * 1000
        # Horizon is only resolvable if the series actually extends to it; a
        # series ending before the horizon yields None (never carry-forward the
        # last close as if it were the horizon price).
        if last_open_ms is None or last_open_ms < target_ms:
            out[f"fwd_{h}s_bps"] = None
            continue
        chosen: float | None = None
        for open_ms, _hi, _lo, close in bars:
            if open_ms <= target_ms:
                chosen = close
            else:
                break
        out[f"fwd_{h}s_bps"] = (
            None if chosen is None else round(side_adjusted_bps(entry_price, chosen, side), 2)
        )
    return out


@dataclass
class ExcursionResult:
    mae_bps: float | None = None
    mfe_bps: float | None = None
    mfe_before_mae: bool | None = None
    time_to_mae_s: float | None = None
    time_to_mfe_s: float | None = None
    bars_seen: int = 0


def compute_excursion(
    *,
    entry_price: float,
    side: str,
    entry_ts_ms: int,
    bars: Sequence[Bar],
    window_s: int = max(HORIZONS_S),
) -> ExcursionResult:
    """MAE/MFE over [entry_ts, entry_ts+window] using bar high/low extremes."""
    if entry_price <= 0:
        return ExcursionResult()
    end_ms = entry_ts_ms + window_s * 1000
    best_fav = -1e18
    worst_adv = 1e18
    fav_ms: int | None = None
    adv_ms: int | None = None
    seen = 0
    for open_ms, hi, lo, _close in bars:
        if open_ms < entry_ts_ms or open_ms > end_ms:
            continue
        seen += 1
        if side == "long":
            fav = side_adjusted_bps(entry_price, hi, "long")
            adv = side_adjusted_bps(entry_price, lo, "long")
        else:
            # short: favourable when price falls (low), adverse when it rises (high)
            fav = side_adjusted_bps(entry_price, lo, "short")
            adv = side_adjusted_bps(entry_price, hi, "short")
        if fav > best_fav:
            best_fav, fav_ms = fav, open_ms
        if adv < worst_adv:
            worst_adv, adv_ms = adv, open_ms
    if seen == 0:
        return ExcursionResult()
    return ExcursionResult(
        mae_bps=round(worst_adv, 2),
        mfe_bps=round(best_fav, 2),
        mfe_before_mae=(fav_ms is not None and adv_ms is not None and fav_ms <= adv_ms),
        time_to_mae_s=None if adv_ms is None else (adv_ms - entry_ts_ms) / 1000.0,
        time_to_mfe_s=None if fav_ms is None else (fav_ms - entry_ts_ms) / 1000.0,
        bars_seen=seen,
    )


# --------------------------------------------------------------------------- #
# Candidate record
# --------------------------------------------------------------------------- #


@dataclass
class ShadowCandidate:
    """A hypothetical entry the loop WOULD have taken (no execution)."""

    candidate_id: str
    ts_utc: str  # hypothetical entry time (= signal time)
    symbol: str
    side: str
    entry_price: float
    stop_price: float | None = None
    take_price: float | None = None
    stop_dist_bps: float | None = None
    take_dist_bps: float | None = None
    rr: float | None = None
    regime: str | None = None
    regime_vol_class: str | None = None
    cooldown_state: str | None = None
    signal_confidence: float | None = None
    recommended_priority: int | None = None
    gate_would_reject: bool | None = None
    gate_reason_codes: list[str] = field(default_factory=list)
    entry_mode: str = "disabled"
    source: str = "autonomous_loop"
    schema_version: str = "v1"

    @staticmethod
    def from_geometry(
        *,
        candidate_id: str,
        ts_utc: str,
        symbol: str,
        side: str,
        entry_price: float,
        stop_price: float | None,
        take_price: float | None,
        **extra: object,
    ) -> ShadowCandidate:
        sd = td = rr = None
        if stop_price and entry_price > 0:
            sd = round(abs(entry_price - stop_price) / entry_price * 1e4, 2)
        if take_price and entry_price > 0:
            td = round(abs(take_price - entry_price) / entry_price * 1e4, 2)
        if sd and td:
            rr = round(td / sd, 3)
        return ShadowCandidate(
            candidate_id=candidate_id,
            ts_utc=ts_utc,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_price=stop_price,
            take_price=take_price,
            stop_dist_bps=sd,
            take_dist_bps=td,
            rr=rr,
            **extra,  # type: ignore[arg-type]
        )


def record_candidate(candidate: ShadowCandidate, *, path: Path = LEDGER_PATH) -> bool:
    """Append a candidate to the ledger. Fail-soft: never raises into the loop."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(candidate), ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        logger.warning("[shadow] candidate write failed: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _as_float(v: object) -> float | None:
    """Coerce a JSON-loaded value to float, or None if not numeric."""
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _parse_ts_ms(ts_utc: str) -> int | None:
    try:
        return int(datetime.fromisoformat(ts_utc.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def resolve_pending(
    *,
    fetch_klines: KlineFetcher,
    now: datetime | None = None,
    ledger_path: Path = LEDGER_PATH,
    resolved_path: Path = RESOLVED_PATH,
    window_s: int = max(HORIZONS_S),
) -> dict[str, int]:
    """Resolve candidates whose full MAE/MFE window has elapsed.

    Idempotent: candidates already present in ``resolved_path`` are skipped. Only
    candidates older than ``window_s`` are resolved (so the excursion window is
    complete). Returns counts {resolved, skipped_recent, already, no_data}.
    """
    now = now or datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    candidates = _read_jsonl(ledger_path)
    done = {r.get("candidate_id") for r in _read_jsonl(resolved_path)}
    counts = {"resolved": 0, "skipped_recent": 0, "already": 0, "no_data": 0}

    for c in candidates:
        cid = c.get("candidate_id")
        if cid in done:
            counts["already"] += 1
            continue
        entry_ms = _parse_ts_ms(str(c.get("ts_utc", "")))
        if entry_ms is None:
            counts["no_data"] += 1
            continue
        if now_ms < entry_ms + window_s * 1000:
            counts["skipped_recent"] += 1
            continue
        bars = fetch_klines(
            str(c.get("symbol", "")), entry_ms - 60_000, entry_ms + window_s * 1000 + 60_000
        )
        if not bars:
            counts["no_data"] += 1
            continue
        entry_price = _as_float(c.get("entry_price")) or 0.0
        side = str(c.get("side") or "long")
        fwd = compute_forward_returns(
            entry_price=entry_price, side=side, entry_ts_ms=entry_ms, bars=bars
        )
        exc = compute_excursion(
            entry_price=entry_price, side=side, entry_ts_ms=entry_ms, bars=bars, window_s=window_s
        )
        sd = _as_float(c.get("stop_dist_bps"))
        td = _as_float(c.get("take_dist_bps"))
        resolution = {
            "candidate_id": cid,
            "symbol": c.get("symbol"),
            "side": side,
            "regime": c.get("regime"),
            "resolved_at_utc": now.isoformat(),
            **fwd,
            "mae_bps": exc.mae_bps,
            "mfe_bps": exc.mfe_bps,
            "mfe_before_mae": exc.mfe_before_mae,
            "time_to_mae_s": exc.time_to_mae_s,
            "time_to_mfe_s": exc.time_to_mfe_s,
            "bars_seen": exc.bars_seen,
            "reached_take": (None if (td is None or exc.mfe_bps is None) else exc.mfe_bps >= td),
            "reached_stop": (None if (sd is None or exc.mae_bps is None) else exc.mae_bps <= -sd),
            "schema_version": "v1",
        }
        try:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            with resolved_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(resolution, ensure_ascii=False) + "\n")
            counts["resolved"] += 1
        except OSError as exc_io:
            logger.warning("[shadow] resolution write failed: %s", exc_io)
    return counts


__all__ = [
    "HORIZONS_S",
    "Bar",
    "ExcursionResult",
    "ShadowCandidate",
    "compute_excursion",
    "compute_forward_returns",
    "record_candidate",
    "resolve_pending",
    "side_adjusted_bps",
]
