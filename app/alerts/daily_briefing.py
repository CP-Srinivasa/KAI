"""Daily Operator Briefing — aggregates system state into a concise summary.

Pulls data from all audit trails (alerts, trading loop, portfolio,
outcome annotations) and produces a structured text report suitable
for Telegram or console display.

Usage:
    report = await build_daily_briefing(artifacts_dir)
    print(report.to_text())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.audit import (
    load_alert_audits,
    load_outcome_annotations,
)
from app.orchestrator.trading_loop import load_trading_loop_cycles

_ARTIFACTS = Path("artifacts")


@dataclass
class BriefingData:
    """Raw data for the daily briefing."""

    # Alert stats (24h window)
    alerts_dispatched: int = 0
    alerts_directional: int = 0
    alerts_blocked: int = 0
    block_reasons: dict[str, int] = field(default_factory=dict)
    top_assets: list[str] = field(default_factory=list)

    # Outcome annotation stats (all time)
    total_annotations: int = 0
    hits: int = 0
    misses: int = 0
    inconclusive: int = 0
    precision_pct: float | None = None

    # Trading loop stats (24h window)
    cycles_total: int = 0
    cycles_completed: int = 0
    cycles_no_signal: int = 0
    cycles_consensus_rejected: int = 0
    cycles_risk_rejected: int = 0
    cycles_no_market_data: int = 0
    fills: int = 0

    # Portfolio (live mark-to-market)
    portfolio_available: bool = False
    portfolio_cash_usd: float = 0.0
    portfolio_market_value_usd: float = 0.0
    portfolio_equity_usd: float = 0.0
    portfolio_unrealized_pnl_usd: float = 0.0
    portfolio_realized_pnl_usd: float = 0.0
    portfolio_position_count: int = 0
    portfolio_positions: list[dict[str, object]] = field(default_factory=list)

    # System health
    generated_at: str = ""
    lookback_hours: int = 24

    def to_text(self) -> str:
        """Render as compact operator-readable text."""
        lines: list[str] = []
        lines.append(f"KAI Daily Briefing ({self.generated_at[:10]})")
        lines.append("=" * 42)

        # Alerts
        lines.append("")
        lines.append(f"Alerts (last {self.lookback_hours}h)")
        lines.append(f"  Dispatched:   {self.alerts_dispatched}")
        lines.append(f"  Directional:  {self.alerts_directional}")
        lines.append(f"  Blocked:      {self.alerts_blocked}")
        if self.block_reasons:
            for reason, count in sorted(
                self.block_reasons.items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                lines.append(f"    {reason}: {count}")
        if self.top_assets:
            lines.append(f"  Top assets:   {', '.join(self.top_assets[:5])}")

        # Precision
        lines.append("")
        lines.append("Directional Precision (all time)")
        lines.append(f"  Annotations:  {self.total_annotations}")
        lines.append(f"  Hits:         {self.hits}")
        lines.append(f"  Misses:       {self.misses}")
        lines.append(f"  Inconclusive: {self.inconclusive}")
        if self.precision_pct is not None:
            lines.append(f"  Precision:    {self.precision_pct:.1f}%")

        # Trading loop
        lines.append("")
        lines.append(f"Trading Loop (last {self.lookback_hours}h)")
        lines.append(f"  Cycles:       {self.cycles_total}")
        lines.append(f"  Completed:    {self.cycles_completed}")
        lines.append(f"  Fills:        {self.fills}")
        lines.append(f"  No signal:    {self.cycles_no_signal}")
        if self.cycles_consensus_rejected:
            lines.append(
                f"  Consensus rej: {self.cycles_consensus_rejected}"
            )
        if self.cycles_risk_rejected:
            lines.append(f"  Risk rej:     {self.cycles_risk_rejected}")
        if self.cycles_no_market_data:
            lines.append(f"  No mkt data:  {self.cycles_no_market_data}")

        # Portfolio
        lines.append("")
        if self.portfolio_available:
            lines.append("Paper Portfolio (live)")
            lines.append(f"  Equity:       ${self.portfolio_equity_usd:,.2f}")
            lines.append(f"  Positions:    {self.portfolio_position_count}")
            lines.append(f"  Market Value: ${self.portfolio_market_value_usd:,.2f}")
            lines.append(f"  Cash:         ${self.portfolio_cash_usd:,.2f}")
            pnl = self.portfolio_unrealized_pnl_usd
            sign = "+" if pnl >= 0 else ""
            lines.append(f"  Unrealized:   {sign}${pnl:,.2f}")
            if self.portfolio_realized_pnl_usd != 0.0:
                rpnl = self.portfolio_realized_pnl_usd
                rsign = "+" if rpnl >= 0 else ""
                lines.append(f"  Realized:     {rsign}${rpnl:,.2f}")
            for pos in self.portfolio_positions:
                sym = pos.get("symbol", "?")
                pnl_pos = pos.get("unrealized_pnl_usd")
                price = pos.get("market_price")
                if pnl_pos is not None and price is not None:
                    s = "+" if float(pnl_pos) >= 0 else ""
                    lines.append(f"    {sym}: ${float(price):,.2f} ({s}${float(pnl_pos):,.2f})")
        else:
            lines.append("Paper Portfolio: nicht verfuegbar")

        return "\n".join(lines)


def build_daily_briefing(
    artifacts_dir: Path | None = None,
    lookback_hours: int = 24,
) -> BriefingData:
    """Build a daily briefing from all audit trails (sync, no DB)."""
    adir = artifacts_dir or _ARTIFACTS
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=lookback_hours)
    data = BriefingData(
        generated_at=now.isoformat(),
        lookback_hours=lookback_hours,
    )

    # ── Alert audit ──────────────────────────────────────────────────
    try:
        audits = load_alert_audits(adir)
    except Exception:
        audits = []

    asset_counter: dict[str, int] = {}
    for rec in audits:
        try:
            ts = datetime.fromisoformat(
                rec.dispatched_at.replace("Z", "+00:00"),
            )
        except (ValueError, AttributeError):
            continue
        if ts < cutoff:
            continue

        data.alerts_dispatched += 1
        if rec.directional_eligible is True:
            data.alerts_directional += 1
        if rec.directional_eligible is False:
            data.alerts_blocked += 1
            reason = rec.directional_block_reason or "unknown"
            data.block_reasons[reason] = (
                data.block_reasons.get(reason, 0) + 1
            )
        for asset in rec.affected_assets:
            asset_counter[asset] = asset_counter.get(asset, 0) + 1

    data.top_assets = sorted(
        asset_counter, key=asset_counter.get, reverse=True,  # type: ignore[arg-type]
    )[:5]

    # ── Outcome annotations ──────────────────────────────────────────
    try:
        annotations = load_outcome_annotations(adir)
    except Exception:
        annotations = []

    data.total_annotations = len(annotations)
    data.hits = sum(1 for a in annotations if a.outcome == "hit")
    data.misses = sum(1 for a in annotations if a.outcome == "miss")
    data.inconclusive = sum(
        1 for a in annotations if a.outcome == "inconclusive"
    )
    resolved = data.hits + data.misses
    if resolved > 0:
        data.precision_pct = data.hits / resolved * 100

    # ── Trading loop cycles ──────────────────────────────────────────
    try:
        cycles = load_trading_loop_cycles(adir / "trading_loop_audit.jsonl")
    except Exception:
        cycles = []

    for c in cycles:
        ts_str = c.get("started_at", "")
        try:
            ts = datetime.fromisoformat(
                str(ts_str).replace("Z", "+00:00"),
            )
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue

        data.cycles_total += 1
        status = str(c.get("status", ""))
        if status == "completed":
            data.cycles_completed += 1
        elif status == "no_signal":
            data.cycles_no_signal += 1
        elif status == "consensus_rejected":
            data.cycles_consensus_rejected += 1
        elif status == "risk_rejected":
            data.cycles_risk_rejected += 1
        elif status == "no_market_data":
            data.cycles_no_market_data += 1

        if c.get("fill_simulated"):
            data.fills += 1

    return data


async def build_daily_briefing_with_portfolio(
    artifacts_dir: Path | None = None,
    lookback_hours: int = 24,
) -> BriefingData:
    """Build daily briefing including live portfolio snapshot (async)."""
    data = build_daily_briefing(artifacts_dir=artifacts_dir, lookback_hours=lookback_hours)

    try:
        from app.execution.portfolio_read import build_portfolio_snapshot

        snapshot = await build_portfolio_snapshot()
        if snapshot.available:
            data.portfolio_available = True
            data.portfolio_cash_usd = snapshot.cash_usd
            data.portfolio_market_value_usd = snapshot.total_market_value_usd
            data.portfolio_equity_usd = snapshot.total_equity_usd
            data.portfolio_realized_pnl_usd = snapshot.realized_pnl_usd
            data.portfolio_position_count = snapshot.position_count
            data.portfolio_unrealized_pnl_usd = sum(
                p.unrealized_pnl_usd or 0.0 for p in snapshot.positions
            )
            data.portfolio_positions = [p.to_json_dict() for p in snapshot.positions]
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("portfolio_snapshot_failed: %s", exc)

    return data
