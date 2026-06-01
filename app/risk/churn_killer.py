"""Sprint E (Goal 2026-06-01 §5): churn-killer (read-only entry-side gate).

Root-cause: the trading loop re-enters the same loser minute-by-minute. Real
data: MATIC 4.25 re-entries/day, LINK 3.0, ETH 2.71 — each round-trip bleeds
fees with no edge. The existing `post_stop_cooldown.py` only covers stop-outs of
a single symbol; this generalises the throttle into a full churn-killer:

  §1  per-symbol cooldown after ANY risk-reducing close (stop AND take AND
      reversal), not only `stop`.
  §2  loss-streak backoff: N consecutive losing closes of the same symbol
      stretch the cooldown window by a multiplier.
  §3  global rate limits: max entries/symbol/hour and max notional turnover/hour.

HARD INVARIANT (§4): this gate evaluates ONLY risk-INCREASING entries. It is
wired into `run_cycle`/`_trade_signal` — the entry path. Exits, stop-loss,
take-profit and position reductions go through `monitor_positions`/
`close_position`, which never call this module. Additionally, when this module
reads the audit to count "trades this hour" / "turnover this hour", it counts
only ENTRY fills (long buy / short sell) — exit fills (long sell / short buy)
must never inflate the counters, otherwise de-risking would penalise itself.

No new persistence: everything is derived from the existing paper-execution
audit JSONL (`position_closed` + `order_filled` events). Fail-OPEN on any read
problem (missing file, malformed lines, bad timestamps): a transient read hiccup
must never deadlock the loop. A temporarily missing guardrail is strictly less
bad than blocking all trading. This mirrors the documented post_stop_cooldown
trade-off.

Determinism: pass `now` explicitly in tests. All windows use a strict `<` lower
bound so a boundary-aged event has elapsed.

Reuse vs new: `post_stop_cooldown.is_symbol_in_post_stop_cooldown` stays as-is
(its own gate, stop-only, backward-compatible). This module is the superset gate
with its own CycleStatus reason. The two coexist; the churn-killer covers the
stop case too, so when both are enabled the churn-killer subsumes it.

Performance: one linear scan of the audit per call yields all aggregates
(last risk-reducing close, trailing loss-streak, per-hour entry events). At
current volumes (~10^2-10^3 lines/day) this is negligible vs the per-cycle
market-data fetch. If the file grows to many MB this should be revisited
(tail-read / in-memory cache). Flagged as an open risk, not assumed away.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Any of these `position_closed` reasons is a risk-reducing exit that should
# start a per-symbol cooldown. Kept broad on purpose: the design intent is
# "after the position is reduced/closed, wait before re-entering the same name".
_RISK_REDUCING_CLOSE = frozenset(
    {"stop", "sl", "stop_loss", "take", "tp", "tp_hit", "tp_tier", "reversal"}
)


@dataclass(frozen=True)
class ChurnKillerConfig:
    """Operator-tunable churn-killer parameters. Each `<= 0` disables its gate."""

    cooldown_minutes: int
    loss_streak_threshold: int
    loss_streak_multiplier: float
    max_trades_per_symbol_per_hour: int
    max_notional_turnover_per_hour: float


@dataclass(frozen=True)
class ChurnVerdict:
    """Outcome of the churn gate for one entry attempt.

    `blocked` is the authoritative boolean. `reason` maps to a CycleStatus note
    (`post_stop_cooldown` | `churn_limit`); `detail` is a human-readable note.
    """

    blocked: bool
    reason: str | None
    detail: str


@dataclass(frozen=True)
class _ChurnScan:
    """Aggregates derived from a single audit scan, relative to `now`."""

    last_risk_reducing_close: datetime | None
    trailing_loss_streak: int  # consecutive losing closes ending at the latest close
    entries_last_hour_symbol: int  # entry fills for the target symbol within 1h
    notional_turnover_last_hour: float  # entry notional across ALL symbols within 1h


def _parse_ts(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


def _is_entry_fill(event: dict[str, object]) -> bool:
    """True iff an order_filled event is a risk-INCREASING entry.

    long entry  = side buy  + position_side long
    short entry = side sell + position_side short
    Everything else (long sell / short buy) is an exit/reduction -> NOT an entry.
    """
    side = str(event.get("side", "")).lower()
    pos_side = str(event.get("position_side", "")).lower()
    return (side == "buy" and pos_side == "long") or (side == "sell" and pos_side == "short")


def _fill_notional(event: dict[str, object]) -> float:
    price = event.get("fill_price")
    qty = event.get("filled_quantity", event.get("quantity"))
    try:
        return float(price) * float(qty)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def scan_audit(
    symbol: str,
    *,
    audit_path: Path,
    now: datetime,
) -> _ChurnScan:
    """Single linear pass over the audit. Never raises; fails open to zeros.

    Collects:
      - the most-recent risk-reducing close timestamp for `symbol`,
      - the trailing consecutive loss-streak for `symbol` (closes ordered by ts;
        a non-negative trade_pnl breaks the streak),
      - entry-fill count for `symbol` in the last hour,
      - total entry notional across all symbols in the last hour.
    """
    empty = _ChurnScan(None, 0, 0, 0.0)
    if not audit_path.exists():
        return empty

    one_hour_ago = now - timedelta(hours=1)
    last_close: datetime | None = None
    # (timestamp, is_loss) for this symbol's closes, to compute the trailing streak
    symbol_closes: list[tuple[datetime, bool]] = []
    entries_symbol = 0
    turnover = 0.0

    try:
        with audit_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                etype = event.get("event_type")
                ts = _parse_ts(event.get("timestamp_utc"))
                if ts is None:
                    continue

                if etype == "position_closed" and event.get("symbol") == symbol:
                    reason = str(event.get("reason", "")).lower()
                    if reason in _RISK_REDUCING_CLOSE:
                        if last_close is None or ts > last_close:
                            last_close = ts
                        pnl = event.get("trade_pnl_usd")
                        try:
                            is_loss = float(pnl) < 0.0  # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            is_loss = False
                        symbol_closes.append((ts, is_loss))

                elif etype == "order_filled" and ts >= one_hour_ago and _is_entry_fill(event):
                    turnover += _fill_notional(event)
                    if event.get("symbol") == symbol:
                        entries_symbol += 1
    except OSError as exc:
        logger.warning("[churn] cannot read audit %s: %s; failing open", audit_path, exc)
        return empty

    # Trailing loss-streak: order closes by time, count trailing consecutive losses.
    symbol_closes.sort(key=lambda x: x[0])
    streak = 0
    for _ts, is_loss in reversed(symbol_closes):
        if is_loss:
            streak += 1
        else:
            break

    return _ChurnScan(
        last_risk_reducing_close=last_close,
        trailing_loss_streak=streak,
        entries_last_hour_symbol=entries_symbol,
        notional_turnover_last_hour=turnover,
    )


def _effective_cooldown_minutes(config: ChurnKillerConfig, loss_streak: int) -> float:
    """Base window, stretched by the multiplier once the loss-streak threshold
    is reached. multiplier <= 1 or threshold <= 0 makes the backoff inert."""
    base = float(config.cooldown_minutes)
    if (
        config.loss_streak_threshold > 0
        and config.loss_streak_multiplier > 1.0
        and loss_streak >= config.loss_streak_threshold
    ):
        return base * config.loss_streak_multiplier
    return base


def evaluate_churn_gate(
    symbol: str,
    *,
    config: ChurnKillerConfig,
    audit_path: Path,
    now: datetime | None = None,
) -> ChurnVerdict:
    """Decide whether a NEW entry for `symbol` should be blocked.

    Evaluation order (cheapest, most-specific first):
      1. per-symbol cooldown (with loss-streak backoff)  -> reason post_stop_cooldown
      2. per-symbol entries/hour limit                   -> reason churn_limit
      3. global notional turnover/hour limit             -> reason churn_limit

    Each sub-gate independently disabled by its `<= 0` config value. Fail-open.
    """
    current = now or datetime.now(UTC)
    scan = scan_audit(symbol, audit_path=audit_path, now=current)

    # §1 + §2: per-symbol cooldown with loss-streak backoff
    if config.cooldown_minutes > 0 and scan.last_risk_reducing_close is not None:
        window = _effective_cooldown_minutes(config, scan.trailing_loss_streak)
        elapsed_min = (current - scan.last_risk_reducing_close).total_seconds() / 60.0
        if elapsed_min < window:
            detail = f"cooldown elapsed={elapsed_min:.1f}min < window={window:.1f}min"
            if scan.trailing_loss_streak >= config.loss_streak_threshold > 0:
                detail += f" (loss_streak={scan.trailing_loss_streak})"
            return ChurnVerdict(blocked=True, reason="post_stop_cooldown", detail=detail)

    # §3a: per-symbol entries/hour
    if (
        config.max_trades_per_symbol_per_hour > 0
        and scan.entries_last_hour_symbol >= config.max_trades_per_symbol_per_hour
    ):
        return ChurnVerdict(
            blocked=True,
            reason="churn_limit",
            detail=(
                f"trades_per_hour={scan.entries_last_hour_symbol}"
                f">={config.max_trades_per_symbol_per_hour}"
            ),
        )

    # §3b: global notional turnover/hour
    if (
        config.max_notional_turnover_per_hour > 0.0
        and scan.notional_turnover_last_hour >= config.max_notional_turnover_per_hour
    ):
        return ChurnVerdict(
            blocked=True,
            reason="churn_limit",
            detail=(
                f"notional_turnover={scan.notional_turnover_last_hour:.2f}"
                f">={config.max_notional_turnover_per_hour:.2f}"
            ),
        )

    return ChurnVerdict(blocked=False, reason=None, detail="")
