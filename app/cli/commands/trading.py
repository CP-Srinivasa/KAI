"""Trading-specific CLI commands.

Provides the ``trading`` command group (``trading-bot trading <cmd>``).
All commands are read-only or guarded-write (paper/shadow only).

"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

console = Console()

trading_app = typer.Typer(
    name="trading",
    help="Market-data, paper-portfolio, trading-loop and backtest commands",
    no_args_is_help=True,
)


def get_registered_trading_command_names() -> set[str]:
    """Return all registered trading sub-command names."""
    names: set[str] = set()
    for command in trading_app.registered_commands:
        name = getattr(command, "name", None)
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def get_invalid_trading_command_refs(refs: list[str]) -> list[str]:
    """Return command refs that are not valid `trading <command>` entries."""
    registered = get_registered_trading_command_names()
    invalid_refs: list[str] = []
    for ref in refs:
        parts = ref.strip().split()
        if len(parts) != 2 or parts[0] != "trading" or parts[1] not in registered:
            invalid_refs.append(ref)
    return invalid_refs


# -- market-data --


@trading_app.command("market-data-quote")
def trading_market_data_quote(
    symbol: str = typer.Argument(..., help="Market symbol, e.g. BTC/USDT"),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only provider: coingecko or mock",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Fetch one read-only market quote snapshot from the canonical adapter path."""
    import asyncio

    from app.market_data.service import get_market_data_snapshot

    snapshot = asyncio.run(
        get_market_data_snapshot(
            symbol=symbol,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )

    console.print("[bold]Market Data Quote[/bold]")
    console.print(f"symbol={snapshot.symbol}")
    console.print(f"provider={snapshot.provider}")
    console.print(f"retrieved_at={snapshot.retrieved_at_utc}")
    console.print(f"source_timestamp={snapshot.source_timestamp_utc}")
    console.print(f"price={snapshot.price}")
    console.print(f"is_stale={snapshot.is_stale}")
    console.print(f"freshness_seconds={snapshot.freshness_seconds}")
    console.print(f"available={snapshot.available}")
    console.print(f"error={snapshot.error}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")

    if not snapshot.available:
        raise typer.Exit(1)


@trading_app.command("market-data-snapshot")
def trading_market_data_snapshot(
    symbol: str = typer.Argument(..., help="Market symbol, e.g. BTC/USDT"),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only provider: coingecko or mock",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Print the full read-only market data snapshot payload as JSON."""
    import asyncio
    import json as _json

    from app.market_data.service import get_market_data_snapshot

    snapshot = asyncio.run(
        get_market_data_snapshot(
            symbol=symbol,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    print(_json.dumps(snapshot.to_json_dict(), indent=2))

    if not snapshot.available:
        raise typer.Exit(1)


# -- paper-portfolio --


@trading_app.command("paper-realized-by-asset")
def trading_paper_realized_by_asset(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
) -> None:
    """Print realized PnL per asset from paper execution audit (JSON).

    2026-05-25 Forensik-CLI: belegt dass Top-/Worst-Performer ohne Live-Mode
    und ohne Backtest-Endpoint ableitbar sind. Reine Aggregation über
    position_closed + position_partial_closed events.
    """
    import json as _json
    from pathlib import Path

    from app.execution.portfolio_read import compute_realized_by_asset

    result = compute_realized_by_asset(Path(audit_path))
    print(_json.dumps(result, indent=2))
    if not result["available"]:
        raise typer.Exit(1)


@trading_app.command("edge-report")
def trading_edge_report(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    venue: str = typer.Option("paper", "--venue", help="CostModel venue key"),
    safety_margin_bps: float = typer.Option(
        0.0, "--safety-margin-bps", help="Extra bps subtracted in net_edge (Sprint D margin)"
    ),
    min_sample: int = typer.Option(
        8, "--min-sample", help="Min closed trades before P(mu_net>0) is computed"
    ),
    implausible_threshold: float = typer.Option(
        0.40,
        "--implausible-threshold",
        help="Exclude closes with |exit/entry-1| above this as off-market (0=off; default 0.40)",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of the table"),
) -> None:
    """Cohort- and forward-edge diagnostics (Sprint C).

    Read-only. Reports cost-adjusted realised edge per symbol/regime/day,
    P(mu_net>0) via bootstrap (winrate alone is not the verdict), churn, and
    honest forward-return coverage. Open positions are marked-to-market in a
    SEPARATE bucket — never summed with closed realised PnL.
    """
    import json as _json

    from app.observability.edge_report import build_report_from_audit, render_report

    report = build_report_from_audit(
        audit_path,
        venue=venue,
        safety_margin_bps=safety_margin_bps,
        min_sample=min_sample,
        implausible_move_threshold=implausible_threshold,
    )
    if as_json:
        print(_json.dumps(report.to_dict(), indent=2))
    else:
        console.print(render_report(report))
    if report.closed_trade_count == 0:
        # Keep stdout single-document valid JSON under --json (Blocker #5: the
        # nightly artifact must always parse). The human note goes to stderr.
        if not as_json:
            console.print("[yellow]No closed trades in audit stream.[/yellow]")
        raise typer.Exit(1)


@trading_app.command("generator-edge")
def trading_generator_edge(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    cohort_type: str = typer.Option(
        "generator",
        "--cohort-type",
        help="Grouping key: generator (signal_source) | regime | symbol",
    ),
    venue: str = typer.Option("paper", "--venue", help="CostModel venue key"),
    min_resolved: int = typer.Option(
        30,
        "--min-resolved",
        help="Below this many resolved trades the verdict is INSUFFICIENT, not NO_GO",
    ),
    implausible_threshold: float = typer.Option(
        0.40,
        "--implausible-threshold",
        help="Exclude closes with |exit/entry-1| above this as off-market (0=off)",
    ),
) -> None:
    """Generator edge measurement (NEO /goal) — prove/disprove tradeable edge.

    READ-ONLY. Groups resolved trades by generator/regime/symbol and emits a
    förderfähig Go/No-Go verdict per cohort with EV-after-costs, P(mu_net>0),
    Sharpe/Sortino/MDD, cohort tail-CVaR and overtrading. Emits valid JSON on
    stdout always. INSUFFICIENT (too few resolved) is reported distinctly from
    NO_GO — the instrument never invents a verdict. IC-by-horizon and Brier/ECE
    require side-channel inputs not present in the audit stream alone and are
    honestly ``None`` here until a feeder supplies them.
    """
    import json as _json

    from app.observability.edge_report import (
        load_audit_events,
        parse_closed_trades_with_exclusions,
    )
    from app.observability.generator_edge import (
        EdgeGateConfig,
        build_generator_edge_report,
    )

    events = load_audit_events(audit_path)
    trades, _exclusions = parse_closed_trades_with_exclusions(
        events, implausible_move_threshold=implausible_threshold
    )
    report = build_generator_edge_report(
        trades,
        cohort_type=cohort_type,
        venue=venue,
        config=EdgeGateConfig(min_resolved=min_resolved),
    )
    print(_json.dumps(report.to_dict(), indent=2))
    if report.total_resolved == 0:
        raise typer.Exit(1)


@trading_app.command("edge-gate")
def trading_edge_gate(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    venue: str = typer.Option("paper", "--venue", help="CostModel venue key"),
    safety_margin_bps: float = typer.Option(
        0.0,
        "--safety-margin-bps",
        help="Min net bps/notional a cohort must clear before any live recommendation",
    ),
    min_n: int = typer.Option(
        20,
        "--min-n",
        help="Min closed round-trips before a release decision is defensible",
    ),
    oos_min_days: int = typer.Option(
        2,
        "--oos-min-days",
        help="Disjoint qualifying day-cohorts required for out-of-sample stability",
    ),
    implausible_threshold: float = typer.Option(
        0.40,
        "--implausible-threshold",
        help="Exclude closes with |exit/entry-1| above this as off-market (0=off; default 0.40)",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of the verdict"),
) -> None:
    """Edge Release Policy decision (Sprint D) — periodic, NOT a runtime gate.

    READ-ONLY. Consumes the Sprint-C edge report and EMITS a recommended
    ``entry_mode`` (DISABLED / PAPER / PROBE / LIVE_LIMITED / LIVE_NORMAL) with a
    human-readable reasoning. It does NOT change ``execution.entry_mode``; the
    runtime kill-switch is that setting (Sprint A). Live recommendations always
    require explicit operator sign-off; LIVE_NORMAL is never auto-promoted.
    """
    import json as _json

    from app.core.settings import get_settings
    from app.observability.edge_report import build_report_from_audit
    from app.risk.edge_release_policy import decide_from_report, render_decision

    try:
        current_mode = get_settings().execution.entry_mode
    except Exception:  # pragma: no cover - settings unavailable in some envs
        current_mode = None

    # min_sample for the bootstrap mirrors the release min_n so the overall
    # cohort's posterior is computed on the same sample-size contract.
    report = build_report_from_audit(
        audit_path,
        venue=venue,
        safety_margin_bps=safety_margin_bps,
        min_sample=min(min_n, 8),
        implausible_move_threshold=implausible_threshold,
    )
    decision = decide_from_report(
        report,
        current_mode=current_mode,
        min_n=min_n,
        safety_margin_bps=safety_margin_bps,
        oos_min_disjoint_days=oos_min_days,
    )
    if as_json:
        print(_json.dumps(decision.to_dict(), indent=2))
    else:
        console.print(render_decision(decision))
    if report.closed_trade_count == 0 and not as_json:
        console.print("[yellow]No closed trades in audit stream -> DISABLED.[/yellow]")


@trading_app.command("evidence-window")
def trading_evidence_window(
    loop_audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--loop-audit-path",
        help="Append-only trading-loop cycle audit JSONL path (counts + safety)",
    ),
    exec_audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--exec-audit-path",
        help="Append-only paper execution audit JSONL path (fills + closes)",
    ),
    since_days: float = typer.Option(
        0.0,
        "--since-days",
        help="Window = the last N days (0 = full streams; overridden by --from/--to)",
    ),
    from_iso: str = typer.Option(
        "", "--from", help="Window start (ISO-8601 UTC, e.g. 2026-06-01T00:00:00+00:00)"
    ),
    to_iso: str = typer.Option("", "--to", help="Window end (ISO-8601 UTC)"),
    venue: str = typer.Option("paper", "--venue", help="CostModel venue key"),
    p_threshold_bps: float = typer.Option(
        0.0, "--p-threshold-bps", help="Threshold T for the P(mu_net > T) figure"
    ),
    safety_margin_bps: float = typer.Option(
        0.0, "--safety-margin-bps", help="Extra bps subtracted in net_edge"
    ),
    min_sample: int = typer.Option(
        8, "--min-sample", help="Min closed trades before probabilities are computed"
    ),
    implausible_threshold: float = typer.Option(
        0.40,
        "--implausible-threshold",
        help="Exclude closes with |exit/entry-1| above this as off-market (0=off; default 0.40)",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of the table"),
) -> None:
    """Evidence-Window report (Goal 2026-06-01) — one defensible edge answer.

    READ-ONLY. Joins the trading-loop status distribution (counts) and the paper
    execution stream (fills + closes) into ONE typed window: counts, hard safety
    assertions (live_orders_attempted MUST be 0, auto_promotions 0), and a
    cost-adjusted, quarantine-cleaned edge with OUTLIER ROBUSTNESS
    (result_without_best/worst trade, trimmed mean, bootstrap CI, P(mu>T)).

    It DECIDES nothing — it is the evidence base on which a later probe/live
    conversation happens. Forward returns are honestly marked
    'pending prospective capture' (an explicit follow-up sprint).
    """
    import json as _json
    from datetime import UTC, datetime, timedelta

    from app.observability.evidence_window import build_window_from_audit, render_window

    def _parse_iso(value: str) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise typer.BadParameter(f"invalid ISO-8601 datetime: {value!r}") from exc
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    since = _parse_iso(from_iso)
    until = _parse_iso(to_iso)
    if since is None and until is None and since_days > 0:
        since = datetime.now(UTC) - timedelta(days=since_days)

    report = build_window_from_audit(
        loop_audit_path=loop_audit_path,
        exec_audit_path=exec_audit_path,
        since=since,
        until=until,
        venue=venue,
        safety_margin_bps=safety_margin_bps,
        p_threshold_bps=p_threshold_bps,
        min_sample=min_sample,
        implausible_move_threshold=implausible_threshold,
    )
    if as_json:
        print(_json.dumps(report.to_dict(), indent=2))
    else:
        console.print(render_window(report))

    # Honest exit signal: a non-paper fill in a paper window is an integrity
    # alarm. Edge-emptiness is informational (exit 0) — absence of evidence is
    # not a failure of the report.
    if report.safety.live_orders_attempted > 0:
        raise typer.Exit(2)


@trading_app.command("paper-portfolio-snapshot")
def trading_paper_portfolio_snapshot(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market data provider for mark-to-market",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Market data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Print canonical read-only paper portfolio snapshot as JSON."""
    import asyncio
    import json as _json

    from app.execution.portfolio_read import build_portfolio_snapshot

    snapshot = asyncio.run(
        build_portfolio_snapshot(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    print(_json.dumps(snapshot.to_json_dict(), indent=2))

    if not snapshot.available:
        raise typer.Exit(1)


@trading_app.command("paper-positions-summary")
def trading_paper_positions_summary(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market data provider for mark-to-market",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Market data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Print canonical read-only paper positions summary."""
    import asyncio

    from app.execution.portfolio_read import (
        build_portfolio_snapshot,
        build_positions_summary,
    )

    snapshot = asyncio.run(
        build_portfolio_snapshot(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    payload = build_positions_summary(snapshot)

    console.print("[bold]Paper Positions Summary[/bold]")
    console.print(f"position_count={payload['position_count']}")
    console.print(f"mark_to_market_status={payload['mark_to_market_status']}")
    console.print(f"available={payload['available']}")
    console.print(f"error={payload['error']}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")

    raw_positions = payload.get("positions", [])
    positions = raw_positions if isinstance(raw_positions, list) else []
    for position in positions:
        if not isinstance(position, dict):
            continue
        console.print(
            " | ".join(
                [
                    f"symbol={position.get('symbol')}",
                    f"qty={position.get('quantity')}",
                    f"avg={position.get('avg_entry_price')}",
                    f"price={position.get('market_price')}",
                    f"stale={position.get('market_data_is_stale')}",
                    f"available={position.get('market_data_available')}",
                ]
            )
        )

    if not snapshot.available:
        raise typer.Exit(1)


@trading_app.command("positions-risk-snapshot")
def trading_positions_risk_snapshot(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market data provider for mark-to-market",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Market data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
    loss_threshold_pct: float = typer.Option(
        1.0,
        "--loss-threshold-pct",
        help="Open-loss magnitude (percent) at which a position counts as risk_open",
    ),
    out: str = typer.Option(
        "artifacts/open_positions_snapshot.json",
        "--out",
        help="Path for the persisted JSON risk-snapshot artifact",
    ),
) -> None:
    """Read-only open-position risk snapshot (Blocker #3 + bleed detection).

    Computes per-position unrealized PnL and a risk-status
    (no_risk / risk_open / data_unknown), persists a JSON artifact and prints a
    loud warning if any position is bleeding beyond ``--loss-threshold-pct``.
    Never trades, never changes execution state.
    """
    import asyncio
    import json
    from pathlib import Path

    from app.core.settings import get_settings
    from app.execution.portfolio_read import build_portfolio_snapshot
    from app.observability.position_risk import (
        RISK_OPEN,
        RISK_UNKNOWN,
        build_positions_risk_snapshot,
    )

    snapshot = asyncio.run(
        build_portfolio_snapshot(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )

    try:
        entry_mode = get_settings().execution.entry_mode.value
    except Exception:  # pragma: no cover - settings should always load
        entry_mode = "unknown"

    report = build_positions_risk_snapshot(
        snapshot,
        entry_mode=entry_mode,
        loss_threshold_pct=loss_threshold_pct,
    )

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    console.print("[bold]Open Positions Risk Snapshot[/bold]")
    console.print(
        f"entry_mode={report['entry_mode']} execution_enabled={report['execution_enabled']}"
    )
    console.print(
        f"position_count={report['position_count']} "
        f"risk_open={report['risk_open_count']} data_unknown={report['data_unknown_count']}"
    )
    console.print(f"total_unrealized_pnl_usd={report['total_unrealized_pnl_usd']}")
    console.print(f"overall_risk_status={report['overall_risk_status']}")
    for position in report["positions"]:
        console.print(
            " | ".join(
                [
                    f"symbol={position['symbol']}",
                    f"side={position['side']}",
                    f"size={position['size']}",
                    f"entry={position['entry']}",
                    f"current={position['current']}",
                    f"uPnL={position['unrealized_pnl_usd']}",
                    f"uPnL%={position['unrealized_pnl_pct']}",
                    f"source={position['source']}",
                    f"risk={position['risk_status']}",
                ]
            )
        )
    console.print(f"artifact={out_path}")

    if report["overall_risk_status"] == RISK_OPEN:
        console.print(
            "[bold red]WARN: offener Verlust erkannt (risk_open) — vor paper/probe/Re-Enable "
            "Operator-Review erforderlich.[/bold red]"
        )
    elif report["overall_risk_status"] == RISK_UNKNOWN:
        console.print(
            "[bold yellow]WARN: Positionsdaten unbekannt (data_unknown) — Risiko nicht "
            "bewertbar, kein Re-Enable ohne frische Preise.[/bold yellow]"
        )


@trading_app.command("promotion-check")
def trading_promotion_check(
    target: str = typer.Option(
        ...,
        "--target",
        help="Target EntryMode to promote to (disabled/paper/probe/live_limited/live_normal)",
    ),
    current: str = typer.Option(
        "",
        "--current",
        help="Current EntryMode; defaults to the configured EXECUTION_ENTRY_MODE",
    ),
    bleed_usd_threshold: float = typer.Option(
        0.0,
        "--bleed-usd-threshold",
        help="Aggregate unrealized-loss magnitude (USD) that trips an UNREALIZED_BLEED block",
    ),
    loss_threshold_pct: float = typer.Option(
        1.0,
        "--loss-threshold-pct",
        help="Per-position open-loss percent at which a position counts as risk_open",
    ),
    audit_path: str = typer.Option("artifacts/paper_execution_audit.jsonl", "--audit-path"),
    provider: str = typer.Option("coingecko", "--provider"),
    freshness_threshold_seconds: float = typer.Option(120.0, "--freshness-threshold-seconds"),
    timeout_seconds: int = typer.Option(10, "--timeout-seconds"),
    out: str = typer.Option(
        "artifacts/promotion_gate_decision.json",
        "--out",
        help="Path for the persisted JSON promotion-gate decision",
    ),
) -> None:
    """Fail-closed bleed-breaker: block a risk-increasing EntryMode promotion.

    Builds the open-position risk snapshot and evaluates the promotion gate. Exits
    non-zero (fail-closed) when promotion is NOT allowed, so a promotion script
    refuses to proceed. Read-only: never trades, never changes execution state.
    Exits and de-risking transitions are never gated.
    """
    import asyncio
    import json
    from pathlib import Path

    from app.core.enums import EntryMode
    from app.core.settings import get_settings
    from app.execution.portfolio_read import build_portfolio_snapshot
    from app.observability.position_risk import build_positions_risk_snapshot
    from app.risk.promotion_gate import STATUS_ALLOWED, evaluate_promotion

    def _coerce_mode(raw: str) -> EntryMode:
        try:
            return EntryMode(raw.strip().lower())
        except ValueError as exc:
            valid = ", ".join(m.value for m in EntryMode)
            raise typer.BadParameter(f"invalid EntryMode '{raw}'; valid: {valid}") from exc

    target_mode = _coerce_mode(target)
    if current.strip():
        current_mode = _coerce_mode(current)
    else:
        try:
            current_mode = get_settings().execution.entry_mode
        except Exception:  # pragma: no cover - settings should always load
            current_mode = EntryMode.DISABLED

    snapshot = asyncio.run(
        build_portfolio_snapshot(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    risk_report = build_positions_risk_snapshot(
        snapshot,
        entry_mode=current_mode.value,
        loss_threshold_pct=loss_threshold_pct,
    )

    decision = evaluate_promotion(
        current_mode,
        target_mode,
        risk_report,
        bleed_usd_threshold=bleed_usd_threshold,
    )

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = decision.to_dict()
    payload["risk_snapshot"] = risk_report
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    console.print("[bold]Promotion Gate (Bleed-Breaker)[/bold]")
    console.print(
        f"current={decision.current_mode.value} -> target={decision.target_mode.value} "
        f"(risk_increasing={decision.risk_increasing})"
    )
    console.print(f"status={decision.status} allowed={decision.allowed}")
    console.print(f"reason_codes={decision.reason_codes}")
    console.print(f"artifact={out_path}")

    if not decision.allowed:
        console.print(
            "[bold red]BLOCKED: risk-increasing Promotion abgelehnt — manual_review_required. "
            "Exits/De-Risking bleiben erlaubt; entry_mode bleibt unverändert.[/bold red]"
        )
        raise typer.Exit(1)
    if decision.status == STATUS_ALLOWED and decision.risk_increasing:
        console.print(
            "[bold green]Promotion gate clear — Edge-Gate + Operator-Sign-off bleiben separat "
            "erforderlich.[/bold green]"
        )


@trading_app.command("paper-exposure-summary")
def trading_paper_exposure_summary(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market data provider for mark-to-market",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Market data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Print canonical read-only paper exposure summary."""
    import asyncio

    from app.execution.portfolio_read import (
        build_exposure_summary,
        build_portfolio_snapshot,
    )

    snapshot = asyncio.run(
        build_portfolio_snapshot(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    payload = build_exposure_summary(snapshot)

    console.print("[bold]Paper Exposure Summary[/bold]")
    console.print(f"mark_to_market_status={payload['mark_to_market_status']}")
    console.print(f"gross_exposure_usd={payload['gross_exposure_usd']}")
    console.print(f"net_exposure_usd={payload['net_exposure_usd']}")
    console.print(f"priced_position_count={payload['priced_position_count']}")
    console.print(f"stale_position_count={payload['stale_position_count']}")
    console.print(f"unavailable_price_count={payload['unavailable_price_count']}")
    console.print(f"largest_position_symbol={payload['largest_position_symbol']}")
    console.print(f"largest_position_weight_pct={payload['largest_position_weight_pct']}")
    console.print(f"available={payload['available']}")
    console.print(f"error={payload['error']}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")

    if not snapshot.available:
        raise typer.Exit(1)


@trading_app.command("diversification-report")
def trading_diversification_report(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="Read-only market data provider (defaults to APP_MARKET_DATA_PROVIDER)",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit full JSON payload"),
) -> None:
    """Print the diversification / concentration overview for the paper book."""
    import asyncio
    import json as _json
    from typing import Any, cast

    from app.trading.diversification_service import build_diversification_overview

    overview = asyncio.run(build_diversification_overview(audit_path=audit_path, provider=provider))
    if as_json:
        print(_json.dumps(overview, indent=2))
        return

    conc = cast(dict[str, Any], overview.get("concentration") or {})
    console.print("[bold]Diversification Overview[/bold]")
    _ge = overview.get("guard_enabled")
    _gm = overview.get("guard_mode")
    console.print(f"guard_enabled={_ge} mode={_gm}")
    console.print(f"short_term_gross_usd={conc.get('short_term_gross_usd')}")
    console.print(f"reserve_gross_usd={conc.get('reserve_gross_usd')}")
    console.print(f"btc_eth_short_term_pct={conc.get('btc_eth_short_term_pct')}")
    console.print("[bold]Asset distribution (short-term sleeve)[/bold]")
    for row in cast(list[dict[str, Any]], overview.get("asset_distribution") or []):
        console.print(
            f"  {row['symbol']:12} {row['weight_pct']}%  "
            f"horizon={row['position_horizon']}  group={row['correlation_group']}"
        )
    warnings = cast(list[str], overview.get("cluster_warnings") or [])
    if warnings:
        console.print("[bold yellow]Cluster warnings[/bold yellow]")
        for w in warnings:
            console.print(f"  - {w}")
    else:
        console.print("[green]No cluster warnings[/green]")
    console.print("[bold]Diversified scan candidates[/bold]")
    for c in cast(list[dict[str, Any]], overview.get("candidates") or []):
        if c["included"]:
            console.print(
                f"  {c['symbol']:12} adj_score={c['adjusted_score']} group={c['correlation_group']}"
            )


@trading_app.command("scan-candidates")
def trading_scan_candidates(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="Read-only market data provider (defaults to APP_MARKET_DATA_PROVIDER)",
    ),
    limit: int = typer.Option(6, "--limit", help="Number of diversified candidates"),
    symbols_only: bool = typer.Option(
        False, "--symbols-only", help="Print only the selected scan symbols (one per line)"
    ),
) -> None:
    """Rank diversified short-term scan candidates that broaden beyond BTC/ETH.

    Read-only. ``--symbols-only`` output is consumable by the paper cron when
    APP_DIVERSIFICATION_UNIVERSE_SCAN_ENABLED=true.
    """
    import asyncio

    from app.execution.portfolio_read import build_portfolio_snapshot
    from app.trading.candidate_selector import select_short_term_candidates
    from app.trading.diversification import exposures_from_snapshot

    resolved_provider = provider if provider is not None else None
    snapshot = asyncio.run(
        build_portfolio_snapshot(audit_path=audit_path, provider=resolved_provider or "coingecko")
    )
    rankings = select_short_term_candidates(
        positions=exposures_from_snapshot(snapshot), limit=limit
    )
    if symbols_only:
        for c in rankings:
            if c.included:
                console.print(c.symbol)
        return

    console.print("[bold]Diversified scan candidates[/bold]")
    for c in rankings:
        flag = "[green]PICK[/green]" if c.included else "[dim]skip[/dim]"
        console.print(
            f"  {flag} {c.symbol:12} adj={c.adjusted_score} "
            f"group={c.correlation_group} sector={c.sector}"
        )


@trading_app.command("signals")
def trading_signals(
    watchlist: str | None = typer.Option(None, "--watchlist", help="Filter by watchlist"),
    min_priority: int = typer.Option(8, "--min-priority", help="Minimum priority"),
    limit: int = typer.Option(50, "--limit", help="Max results"),
) -> None:
    """Generate actionable signal candidates (read-only)."""
    from app.cli.commands.research_core import research_signals

    research_signals(watchlist=watchlist, min_priority=min_priority, limit=limit)


# -- trading-loop --


@trading_app.command("loop-status")
def trading_loop_status(
    audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--audit-path",
        help="Trading loop JSONL audit path",
    ),
    mode: str = typer.Option(
        "paper",
        "--mode",
        help="Execution mode hint for run-once guard evaluation",
    ),
) -> None:
    """Print canonical read-only trading-loop status and run-once guard state."""
    from app.orchestrator.trading_loop import build_loop_status_summary

    try:
        summary = build_loop_status_summary(audit_path=audit_path, mode=mode)
    except ValueError as exc:
        console.print(f"[red]Trading loop status failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    payload = summary.to_json_dict()
    console.print("[bold]Trading Loop Status[/bold]")
    console.print(f"mode={payload['mode']}")
    console.print(f"run_once_allowed={payload['run_once_allowed']}")
    console.print(f"run_once_block_reason={payload['run_once_block_reason']}")
    console.print(f"total_cycles={payload['total_cycles']}")
    console.print(f"last_cycle_id={payload['last_cycle_id']}")
    console.print(f"last_cycle_status={payload['last_cycle_status']}")
    console.print(f"last_cycle_symbol={payload['last_cycle_symbol']}")
    console.print(f"last_cycle_completed_at={payload['last_cycle_completed_at']}")
    console.print(f"audit_path={payload['audit_path']}")
    console.print("auto_loop_enabled=False")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@trading_app.command("recent-cycles")
def trading_recent_cycles(
    audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--audit-path",
        help="Trading loop JSONL audit path",
    ),
    last_n: int = typer.Option(20, "--last-n", help="Show last N cycle records"),
) -> None:
    """Print canonical read-only summary of recent trading loop cycles."""
    from app.orchestrator.trading_loop import build_recent_cycles_summary

    summary = build_recent_cycles_summary(audit_path=audit_path, last_n=last_n)
    payload = summary.to_json_dict()

    console.print(f"[bold]Trading Loop Recent Cycles[/bold] ({payload['total_cycles']} total)")
    console.print(f"status_counts={payload['status_counts']}")
    console.print(
        f"showing last {len(payload['recent_cycles'])} of {payload['total_cycles']} cycles:"  # type: ignore[arg-type]
    )

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("cycle_id", width=16)
    table.add_column("status", width=16)
    table.add_column("symbol", width=12)
    table.add_column("sig", width=4)
    table.add_column("risk", width=4)
    table.add_column("fill", width=4)

    raw_recent_cycles = payload.get("recent_cycles", [])
    recent_cycles = raw_recent_cycles if isinstance(raw_recent_cycles, list) else []
    for rec in recent_cycles:
        if not isinstance(rec, dict):
            continue
        table.add_row(
            str(rec.get("cycle_id", "-"))[:16],
            str(rec.get("status", "-")),
            str(rec.get("symbol", "-")),
            "Y" if rec.get("signal_generated") else "N",
            "Y" if rec.get("risk_approved") else "N",
            "Y" if rec.get("fill_simulated") else "N",
        )

    console.print(table)
    console.print("audit_path=" + str(payload.get("audit_path")))
    console.print("auto_loop_enabled=False")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@trading_app.command("run-once")
def trading_run_once(
    extra_positional: Annotated[
        list[str] | None,
        typer.Argument(
            hidden=True,
            metavar="",
            help=(
                "INTERNAL: captures stray positional args so a mistaken "
                "`run-once BTC/USDT` fails with an actionable message instead of a "
                "generic Click error. There is NO positional symbol argument."
            ),
        ),
    ] = None,
    symbol: str = typer.Option("BTC/USDT", "--symbol", help="Trading symbol (use this flag)"),
    mode: str = typer.Option(
        "paper",
        "--mode",
        help="Allowed run modes: paper or shadow (live fails closed)",
    ),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market-data provider: coingecko (default, real data) or mock (dev/test)",
    ),
    analysis_profile: str = typer.Option(
        "conservative",
        "--analysis-profile",
        help="conservative, bullish, or bearish control profile",
    ),
    loop_audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--loop-audit-path",
        help="Append-only loop cycle audit path",
    ),
    execution_audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--execution-audit-path",
        help="Append-only paper execution audit path",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Market data stale threshold",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Market data request timeout",
    ),
    consensus: bool = typer.Option(
        False,
        "--consensus",
        help="Enable multi-model consensus gate (uses all configured LLM keys)",
    ),
    consensus_model: str = typer.Option(
        "gpt-4o-mini",
        "--consensus-model",
        help="LLM model for consensus validation",
    ),
) -> None:
    """Run one guarded paper/shadow cycle and append cycle audit output.

    There is NO positional symbol argument. Pass a symbol with ``--symbol``,
    e.g. ``trading run-once --symbol ETH/USDT``. A stray positional arg
    (``run-once ETH/USDT``) is rejected with an actionable error rather than
    silently swallowed — a positional that looked like it ran a tick but did
    not has caused operator mis-diagnosis (Goal 2026-06-01, NEO-F-411).
    """
    import asyncio

    # DX hard-stop: a stray positional symbol must NEVER look like a tick.
    if extra_positional:
        stray = " ".join(extra_positional)
        raise typer.BadParameter(
            f"no positional symbol argument: run-once does not take a positional "
            f"symbol (got '{stray}'). Use the --symbol flag for a symbol-specific "
            f"tick, e.g. 'trading run-once --symbol {extra_positional[0]}', or use "
            f"'trading monitor-positions' to watch open positions. This tick did "
            f"NOT run.",
        )

    from app.orchestrator.trading_loop import run_trading_loop_once

    try:
        cycle = asyncio.run(
            run_trading_loop_once(
                symbol=symbol,
                mode=mode,
                provider=provider,
                analysis_profile=analysis_profile,
                loop_audit_path=loop_audit_path,
                execution_audit_path=execution_audit_path,
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
                enable_consensus=consensus,
                consensus_model=consensus_model,
            )
        )
    except ValueError as exc:
        console.print(f"[red]Trading loop run-once blocked:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print("[bold]Trading Loop Run Once[/bold]")
    console.print(f"cycle_id={cycle.cycle_id}")
    console.print(f"status={cycle.status.value}")
    console.print(f"symbol={cycle.symbol}")
    console.print(f"mode={mode}")
    console.print(f"provider={provider}")
    console.print(f"analysis_profile={analysis_profile}")
    console.print(f"market_data_fetched={cycle.market_data_fetched}")
    console.print(f"signal_generated={cycle.signal_generated}")
    console.print(f"risk_approved={cycle.risk_approved}")
    console.print(f"order_created={cycle.order_created}")
    console.print(f"fill_simulated={cycle.fill_simulated}")
    console.print(f"notes={list(cycle.notes)}")
    console.print("auto_loop_enabled=False")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@trading_app.command("monitor-positions")
def trading_monitor_positions(
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market-data provider for SL/TP price checks",
    ),
    loop_audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--loop-audit-path",
        help="Trading-loop audit JSONL path",
    ),
    execution_audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--execution-audit-path",
        help="Paper execution audit JSONL path (used for portfolio rehydration)",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Stale-data threshold; stale prices are skipped, not force-closed",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Market data request timeout",
    ),
) -> None:
    """Check SL/TP on every open paper position and close those that triggered.

    Designed for cron invocation. Rehydrates the paper engine from the audit
    JSONL, fetches a live price per open symbol, and closes positions whose
    stop-loss or take-profit level was crossed. Positions with stale or missing
    market data are skipped — never force-closed on bad data.
    """
    import asyncio

    from app.orchestrator.trading_loop import run_position_monitor_once

    summary = asyncio.run(
        run_position_monitor_once(
            provider=provider,
            loop_audit_path=loop_audit_path,
            execution_audit_path=execution_audit_path,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )

    console.print("[bold]Paper Position Monitor[/bold]")
    console.print(f"checked={summary.get('checked')}")
    console.print(f"no_market_data={summary.get('no_market_data')}")
    console.print(f"triggered={summary.get('triggered')}")
    closes = summary.get("closes")
    if isinstance(closes, list):
        for close in closes:
            if not isinstance(close, dict):
                continue
            console.print(
                " | ".join(
                    [
                        f"symbol={close.get('symbol')}",
                        f"qty={close.get('quantity')}",
                        f"fill_price={close.get('fill_price')}",
                        f"realized_pnl_usd={close.get('realized_pnl_usd')}",
                    ]
                )
            )
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@trading_app.command("operator-signal-bridge-tick")
def trading_operator_signal_bridge_tick() -> None:
    """Route accepted signal envelopes (dashboard/telegram) to paper fills.

    Fail-closed: no-op unless EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED=true.
    Designed for cron invocation. Reads artifacts/telegram_message_envelope.jsonl,
    filters source allowlist, applies risk gates, and fills operator-supplied
    entry/SL/TP1 via the paper engine. Non-fillable signals stay pending until
    the configured TTL elapses.
    """
    import asyncio

    from app.execution.envelope_to_paper_bridge import run_tick

    result = asyncio.run(run_tick())
    data = result.to_dict()

    console.print("[bold]Operator-Signal-Bridge[/bold]")
    if not data["enabled"]:
        console.print("enabled=False (fail-closed) — no action taken")
        return
    for k, v in data.items():
        console.print(f"{k}={v}")


@trading_app.command("operator-signal-entry-watch")
def trading_operator_signal_entry_watch(
    duration_seconds: float = typer.Option(
        55.0,
        "--duration-seconds",
        min=0.0,
        help="How long to watch pending operator entries before exiting.",
    ),
    poll_interval_seconds: float = typer.Option(
        5.0,
        "--poll-interval-seconds",
        min=0.1,
        help="Seconds between market-data polls while watching.",
    ),
) -> None:
    """High-frequency EntryRangeWatcher loop for pending operator signals.

    On entry hit, this invokes the existing bridge immediately. The bridge still
    owns risk gates, idempotency, paper order creation and audit.
    """
    import asyncio

    from app.execution.operator_entry_watch import run_watch_loop

    result = asyncio.run(
        run_watch_loop(
            duration_seconds=duration_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    )
    data = result.to_dict()

    console.print("[bold]Operator-Signal-Entry-Watch[/bold]")
    if not data["enabled"]:
        console.print("enabled=False (fail-closed) — no action taken")
        return
    for k, v in data.items():
        console.print(f"{k}={v}")


# -- backtest --


@trading_app.command("backtest-run")
def trading_backtest_run(
    signals_path: str = typer.Option(
        "artifacts/signal_candidates.jsonl",
        "--signals-path",
        help="JSONL file with signal candidates (one JSON dict per line)",
    ),
    out: str = typer.Option(
        "artifacts/backtest_result.json",
        "--out",
        help="Output path for backtest result JSON",
    ),
    initial_equity: float = typer.Option(10_000.0, "--initial-equity"),
    stop_loss_pct: float = typer.Option(2.0, "--stop-loss-pct"),
    take_profit_mult: float = typer.Option(2.0, "--take-profit-mult"),
    min_confidence: float = typer.Option(0.7, "--min-confidence"),
    max_positions: int = typer.Option(5, "--max-positions"),
    max_risk_pct: float = typer.Option(2.0, "--max-risk-pct"),
    long_only: bool = typer.Option(True, "--long-only/--no-long-only"),
    audit_path: str = typer.Option("artifacts/backtest_audit.jsonl", "--audit-path"),
) -> None:
    """Run a paper backtest from a signal candidate JSONL file."""
    import asyncio
    import json as _json
    from pathlib import Path as _Path

    from app.core.signals import SignalCandidate
    from app.execution.backtest_engine import BacktestConfig, BacktestEngine
    from app.market_data.mock_adapter import MockMarketDataAdapter

    sp = _Path(signals_path)
    if not sp.exists():
        console.print(f"[red]Signals file not found: {signals_path}[/red]")
        raise typer.Exit(1)

    signals: list[SignalCandidate] = []
    for raw in sp.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            signals.append(SignalCandidate.model_validate(_json.loads(raw), strict=False))
        except Exception:
            pass

    if not signals:
        console.print("[yellow]No valid signals found. Exiting.[/yellow]")
        raise typer.Exit(0)

    adapter = MockMarketDataAdapter()
    unique_assets = {s.target_asset for s in signals}
    prices: dict[str, float] = {}
    for asset in unique_assets:
        p = asyncio.run(adapter.get_price(asset))
        if p is None:
            p = asyncio.run(adapter.get_price(f"{asset}/USDT"))
        if p:
            prices[asset] = p
            prices[f"{asset}/USDT"] = p

    cfg = BacktestConfig(
        initial_equity=initial_equity,
        stop_loss_pct=stop_loss_pct,
        take_profit_multiplier=take_profit_mult,
        min_signal_confidence=min_confidence,
        max_open_positions=max_positions,
        max_risk_per_trade_pct=max_risk_pct,
        long_only=long_only,
        audit_log_path=audit_path,
    )
    engine = BacktestEngine(cfg)
    result = engine.run(signals, prices)

    out_path = _Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_json.dumps(result.to_json_dict(), indent=2), encoding="utf-8")

    console.print("[bold]Backtest Result[/bold]")
    console.print(f"signals_received={result.signals_received}")
    console.print(f"signals_executed={result.signals_executed}")
    console.print(f"signals_skipped={result.signals_skipped}")
    console.print(f"trade_count={result.trade_count}")
    console.print(f"final_equity={result.final_equity:.4f}")
    console.print(f"total_return_pct={result.total_return_pct:.4f}")
    console.print(f"max_drawdown_pct={result.max_drawdown_pct:.4f}")
    console.print(f"realized_pnl_usd={result.realized_pnl_usd:.4f}")
    console.print(f"kill_switch_triggered={result.kill_switch_triggered}")
    console.print(f"result_written={out}")


# -- decision-journal --


@trading_app.command("decision-journal-append")
def trading_decision_journal_append(
    symbol: str = typer.Argument(..., help="Trading symbol (e.g. BTC/USDT)"),
    thesis: str = typer.Option(..., "--thesis", help="Trading thesis (min 10 chars)"),
    market: str = typer.Option("crypto", "--market"),
    venue: str = typer.Option("paper", "--venue"),
    mode: str = typer.Option(
        "research",
        "--mode",
        help="One of: research, backtest, paper, shadow, live",
    ),
    confidence: float = typer.Option(0.5, "--confidence", help="Confidence 0.0-1.0"),
    supporting: Annotated[
        list[str] | None,
        typer.Option("--supporting", help="Supporting factor; repeat for multiple"),
    ] = None,
    contradictory: Annotated[
        list[str] | None,
        typer.Option("--contradictory", help="Contradictory factor; repeat for multiple"),
    ] = None,
    entry_logic: str = typer.Option("manual_entry", "--entry-logic"),
    exit_logic: str = typer.Option("manual_exit", "--exit-logic"),
    stop_loss: float = typer.Option(0.0, "--stop-loss"),
    invalidation: str = typer.Option("thesis_invalidated", "--invalidation"),
    model_version: str = typer.Option("manual", "--model-version"),
    prompt_version: str = typer.Option("v0", "--prompt-version"),
    data_source: Annotated[
        list[str] | None,
        typer.Option("--data-source", help="Data source; repeat for multiple"),
    ] = None,
    journal_path: str = typer.Option(
        "artifacts/decision_journal.jsonl",
        "--journal-path",
        help="Append-only decision journal JSONL path",
    ),
) -> None:
    """Append a validated decision instance to the decision journal."""
    from app.decisions.journal import (
        RiskAssessment,
        append_decision_jsonl,
        create_decision_instance,
    )

    try:
        risk = RiskAssessment(
            risk_level="unassessed",
            max_position_pct=0.0,
            drawdown_remaining_pct=100.0,
        )
        decision = create_decision_instance(
            symbol=symbol,
            market=market,
            venue=venue,
            mode=mode,
            thesis=thesis,
            supporting_factors=list(supporting or ["manual_observation"]),
            contradictory_factors=list(contradictory or []),
            confidence_score=confidence,
            market_regime="unknown",
            volatility_state="unknown",
            liquidity_state="unknown",
            risk_assessment=risk,
            entry_logic=entry_logic,
            exit_logic=exit_logic,
            stop_loss=stop_loss,
            invalidation_condition=invalidation,
            position_size_rationale="manual sizing",
            max_loss_estimate=0.0,
            data_sources_used=list(data_source or ["operator_input"]),
            model_version=model_version,
            prompt_version=prompt_version,
        )
        out_path = Path(journal_path)
        append_decision_jsonl(decision, out_path)
    except ValueError as exc:
        console.print(f"[red]Decision journal append failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]Decision appended to {out_path.resolve()}[/green]")
    console.print(f"decision_id={decision.decision_id}")
    console.print(f"mode={decision.mode.value}")
    console.print(f"approval_state={decision.approval_state.value}")
    console.print(f"execution_state={decision.execution_state.value}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@trading_app.command("decision-journal-summary")
def trading_decision_journal_summary(
    journal_path: str = typer.Option(
        "artifacts/decision_journal.jsonl",
        "--journal-path",
        help="Append-only decision journal JSONL path",
    ),
) -> None:
    """Print a read-only summary of the decision journal."""
    from app.decisions.journal import (
        build_decision_journal_summary,
        load_decision_journal,
    )

    path = Path(journal_path)
    try:
        entries = load_decision_journal(path)
        summary = build_decision_journal_summary(entries, journal_path=path)
    except ValueError as exc:
        console.print(f"[red]Decision journal summary failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print("[bold]Decision Journal Summary[/bold]")
    console.print(f"total_count={summary.total_count}")
    console.print(f"symbols={summary.symbols}")
    console.print(f"by_mode={summary.by_mode}")
    console.print(f"by_approval={summary.by_approval}")
    console.print(f"by_execution={summary.by_execution}")
    if summary.avg_confidence is not None:
        console.print(f"avg_confidence={summary.avg_confidence}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


# -- loop-idle / quality / outcome / tv / audit-rauschen (Goal-Sprint 2026-05-26) --


@trading_app.command("loop-idle-check")
def trading_loop_idle_check(
    audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--audit-path",
        help="Trading-loop audit JSONL",
    ),
    window_hours: int = typer.Option(
        24, "--window-hours", help="Rolling window length (default: 24h)"
    ),
    idle_fraction: float = typer.Option(
        0.95,
        "--idle-fraction",
        help="priority_rejected / total_cycles ratio that counts as idle",
    ),
    min_cycles: int = typer.Option(
        6,
        "--min-cycles",
        help="Minimum cycles in window before a decision can be made",
    ),
    notify: bool = typer.Option(
        False,
        "--notify/--no-notify",
        help="Send a Telegram alert when status=idle (best-effort)",
    ),
) -> None:
    """Emit a loop-idle signal from the trading-loop audit.

    Exit codes:
      0 — healthy
      1 — insufficient_data (not enough cycles to decide)
      2 — idle (every threshold tripped; operator action needed)

    Designed for cron use:
        trading-bot trading loop-idle-check --notify || true
    """
    import asyncio

    from app.observability.loop_idle_signal import compute_loop_idle_signal

    signal = compute_loop_idle_signal(
        audit_path=audit_path,
        window_hours=window_hours,
        idle_fraction=idle_fraction,
        min_cycles=min_cycles,
    )

    payload = signal.to_dict()
    color = {"healthy": "green", "insufficient_data": "yellow", "idle": "red"}.get(
        signal.status, "white"
    )
    console.print(f"[bold {color}]loop_status={signal.status}[/bold {color}]")
    console.print(f"reason={signal.reason}")
    console.print(
        f"window={signal.window_hours}h "
        f"total={signal.total_cycles} "
        f"completed={signal.completed} "
        f"priority_rejected={signal.priority_rejected} "
        f"other_rejected={signal.other_rejected}"
    )
    if signal.rejection_fraction is not None:
        console.print(f"rejection_fraction={signal.rejection_fraction:.3f}")
    console.print(f"audit_path={payload['audit_path']}")

    if signal.status == "idle" and notify:
        try:
            from app.alerts.notify import send_operator_notification

            msg = (
                "🟥 KAI Loop-Idle\n"
                f"Window: {signal.window_hours}h\n"
                f"Cycles: {signal.total_cycles} "
                f"(priority_rejected={signal.priority_rejected}, completed=0)\n"
                f"Reason: {signal.reason}\n"
                f"Audit: {payload['audit_path']}\n"
                "Check EXECUTION_PAPER_MIN_PRIORITY / signal-pipeline upstream."
            )
            ok = asyncio.run(send_operator_notification(msg))
            console.print(f"telegram: {'ok' if ok else 'disabled_or_failed'}")
        except Exception as exc:  # pragma: no cover — best-effort
            console.print(f"telegram: error ({exc})")

    if signal.status == "healthy":
        raise typer.Exit(0)
    if signal.status == "insufficient_data":
        raise typer.Exit(1)
    raise typer.Exit(2)


@trading_app.command("paper-quality-snapshot")
def trading_paper_quality_snapshot(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Paper-execution audit JSONL",
    ),
    last_n: int = typer.Option(
        25,
        "--last-n",
        help="Show the last N position_closed events (and aggregate them)",
    ),
) -> None:
    """Summarize paper-fill quality from position_closed events.

    Read-only, fixture-friendly. Used to couple a green ≥10-fill gate
    with an honest PnL/win-rate snapshot — 11 closures all green
    against a -349.79 USD realized PnL would otherwise pass the
    re-entry-gate without anyone noticing the negative quality.
    """
    from app.observability.paper_quality_snapshot import build_paper_quality_snapshot

    snapshot = build_paper_quality_snapshot(audit_path=audit_path, last_n=last_n)

    console.print("[bold]Paper Quality Snapshot[/bold]")
    console.print(
        f"closures_total={snapshot.closures_total} "
        f"window_last_n={snapshot.window_last_n} "
        f"shown={len(snapshot.window_closures)}"
    )
    console.print(
        f"win_rate={snapshot.win_rate:.3f} "
        f"sum_trade_pnl_usd={snapshot.sum_trade_pnl_usd:.2f} "
        f"avg_trade_pnl_usd={snapshot.avg_trade_pnl_usd:.2f} "
        f"latest_realized_pnl_usd={snapshot.latest_realized_pnl_usd}"
    )
    if snapshot.by_symbol:
        sym_table = Table(title="by_symbol", show_header=True, header_style="bold cyan")
        sym_table.add_column("symbol", width=14)
        sym_table.add_column("n", justify="right")
        sym_table.add_column("wins", justify="right")
        sym_table.add_column("losses", justify="right")
        sym_table.add_column("sum_pnl_usd", justify="right")
        for sym in sorted(snapshot.by_symbol):
            row = snapshot.by_symbol[sym]
            sym_table.add_row(
                sym,
                str(row["count"]),
                str(row["wins"]),
                str(row["losses"]),
                f"{row['sum_pnl_usd']:.2f}",
            )
        console.print(sym_table)
    if snapshot.by_reason:
        rsn_table = Table(title="by_reason", show_header=True, header_style="bold cyan")
        rsn_table.add_column("reason", width=14)
        rsn_table.add_column("n", justify="right")
        rsn_table.add_column("wins", justify="right")
        rsn_table.add_column("losses", justify="right")
        rsn_table.add_column("sum_pnl_usd", justify="right")
        for reason in sorted(snapshot.by_reason):
            row = snapshot.by_reason[reason]
            rsn_table.add_row(
                reason,
                str(row["count"]),
                str(row["wins"]),
                str(row["losses"]),
                f"{row['sum_pnl_usd']:.2f}",
            )
        console.print(rsn_table)
    console.print(f"audit_path={snapshot.audit_path}")


@trading_app.command("outcome-dedupe-report")
def trading_outcome_dedupe_report(
    audit_path: str = typer.Option(
        "artifacts/alert_outcomes.jsonl",
        "--audit-path",
        help="Alert-outcomes audit JSONL",
    ),
) -> None:
    """Compare raw vs latest-per-document_id outcome counts.

    The raw JSONL accumulates inconclusive rows from every annotator
    pass — 4409 raw rows / 3981 inconclusive on the 2026-05-26 baseline,
    but only 410 unique documents and 35 inconclusive after dedupe.
    Re-entry-decisions and precision metrics must use the deduped view.
    """
    from app.observability.outcome_dedupe_report import build_outcome_dedupe_report

    report = build_outcome_dedupe_report(audit_path=audit_path)
    console.print("[bold]Outcome Dedupe Report[/bold]")
    console.print(
        f"raw: rows={report.raw_total} "
        f"hit={report.raw_hit} miss={report.raw_miss} "
        f"inconclusive={report.raw_inconclusive} "
        f"precision_directional={report.raw_precision_str}"
    )
    console.print(
        f"deduped(latest_per_document_id): "
        f"documents={report.deduped_total} "
        f"hit={report.deduped_hit} miss={report.deduped_miss} "
        f"inconclusive={report.deduped_inconclusive} "
        f"precision_directional={report.deduped_precision_str}"
    )
    if report.dropped_inconclusive_dupes:
        console.print(
            f"dropped_inconclusive_dupes={report.dropped_inconclusive_dupes} "
            "(redundant inconclusive rows replaced by a later resolved outcome)"
        )
    console.print(f"audit_path={report.audit_path}")


@trading_app.command("tv-pending-classify")
def trading_tv_pending_classify(
    audit_path: str = typer.Option(
        "artifacts/tradingview_pending_signals.jsonl",
        "--audit-path",
        help="TradingView pending-signals JSONL",
    ),
) -> None:
    """Classify TV-pending events by age and ticker (read-only).

    The 75-event backlog (tail 2026-05-10/11) was opaque from the
    Daily-Strategy bootstrap. This split shows operators *what* sits
    in the queue before they decide to reject/archive in bulk.
    """
    from app.observability.tv_pending_classifier import build_tv_pending_breakdown

    breakdown = build_tv_pending_breakdown(audit_path=audit_path)
    console.print("[bold]TradingView Pending Breakdown[/bold]")
    console.print(f"total={breakdown.total} audit_path={breakdown.audit_path}")
    if breakdown.by_age_bucket:
        tbl = Table(title="by_age_bucket", show_header=True, header_style="bold cyan")
        tbl.add_column("age", width=8)
        tbl.add_column("count", justify="right")
        for bucket in ("<1d", "1-7d", "7-14d", ">14d", "unknown"):
            if bucket in breakdown.by_age_bucket:
                tbl.add_row(bucket, str(breakdown.by_age_bucket[bucket]))
        console.print(tbl)
    if breakdown.by_ticker:
        tt = Table(title="top_tickers", show_header=True, header_style="bold cyan")
        tt.add_column("ticker", width=12)
        tt.add_column("count", justify="right")
        for ticker, count in breakdown.by_ticker[:10]:
            tt.add_row(ticker, str(count))
        console.print(tt)
    if breakdown.by_external_event_id:
        ee = Table(title="top_external_event_ids", show_header=True, header_style="bold cyan")
        ee.add_column("external_event_id", width=30)
        ee.add_column("count", justify="right")
        for ext, count in breakdown.by_external_event_id[:10]:
            ee.add_row(ext, str(count))
        console.print(ee)


@trading_app.command("paper-duplicate-rejections")
def trading_paper_duplicate_rejections(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Paper-execution audit JSONL",
    ),
) -> None:
    """Aggregate order_created_rejected_duplicate events.

    Surfaces forensic patterns (idempotency_key replays) without
    forcing the operator to grep. The replay-guard itself stays
    intact — this CLI only summarizes its audit footprint.
    """
    from app.observability.paper_duplicate_rejections import (
        build_paper_duplicate_rejection_summary,
    )

    summary = build_paper_duplicate_rejection_summary(audit_path=audit_path)
    console.print("[bold]Paper Duplicate-Rejection Summary[/bold]")
    console.print(f"total_rejections={summary.total} audit_path={summary.audit_path}")
    if summary.first_rejected_at or summary.last_rejected_at:
        console.print(
            f"first_rejected_at={summary.first_rejected_at} "
            f"last_rejected_at={summary.last_rejected_at}"
        )
    if summary.by_idempotency_key:
        tbl = Table(
            title="by_idempotency_key (top 10)",
            show_header=True,
            header_style="bold cyan",
        )
        tbl.add_column("idempotency_key", width=46)
        tbl.add_column("count", justify="right")
        tbl.add_column("first_seen", width=26)
        tbl.add_column("last_seen", width=26)
        for entry in summary.by_idempotency_key[:10]:
            tbl.add_row(
                str(entry["idempotency_key"])[:46],
                str(entry["count"]),
                str(entry["first_seen"]),
                str(entry["last_seen"]),
            )
        console.print(tbl)


@trading_app.command("shadow-resolve")
def trading_shadow_resolve(
    include_canary: bool = typer.Option(
        False,
        "--include-canary",
        help="Diagnostic: also resolve canary_probe / non-resolvable kinds (default off)",
    ),
) -> None:
    """Resolve pending shadow candidates from Binance 1m klines (Phase B).

    Read-only diagnostics. Pulls prices for candidates whose MAE/MFE window has
    elapsed, computes forward returns (1m/5m/15m/60m) + MAE/MFE, and appends a
    resolved record. Idempotent. NEVER touches paper_engine / orders / positions
    — it only reads market data and writes diagnostic metrics. A failed fetch
    leaves the candidate pending (counted as no_data). NEO-P-002: by default
    canary_probe / raw_scan / synthetic-default rows are skipped (skipped_kind);
    pass --include-canary to resolve them too.
    """
    from app.observability.shadow_resolver import resolve_with_binance

    counts = resolve_with_binance(include_canary=include_canary)
    console.print("[bold]Shadow Candidate Resolve[/bold]")
    console.print(
        f"resolved={counts['resolved']} skipped_recent={counts['skipped_recent']} "
        f"skipped_kind={counts.get('skipped_kind', 0)} "
        f"already={counts['already']} no_data={counts['no_data']}"
    )


@trading_app.command("shadow-report")
def trading_shadow_report(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of the table"),
    include_legacy: bool = typer.Option(
        False,
        "--include-legacy",
        help="Diagnostic: include the 644 legacy v1 rows in the by_source split (default off)",
    ),
) -> None:
    """Root-cause classification over resolved shadow candidates (Phase B).

    Read-only. Aggregates the resolved shadow ledger into MAE/MFE +
    forward-return distributions and a heuristic primary class
    (ADVERSE_SELECTION / STOP_IN_NOISE_BAND / TP_UNREACHABLE /
    PROFIT_NOT_HARVESTED / INSUFFICIENT_DATA). The class is a HINT — the raw
    distribution it carries is the actual evidence. NEO-P-002: headline +
    primary_class are computed ONLY over real v2 rows (canary + legacy fenced
    off); pass --include-legacy to fold the 644 legacy rows into the by_source
    split for diagnostics.
    """
    import json as _json

    from app.observability.shadow_candidate_ledger import (
        LEDGER_PATH,
        RESOLVED_PATH,
        build_shadow_report,
    )

    def _read(path: Path) -> list[dict[str, object]]:
        if not path.exists():
            return []
        return [
            _json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    resolved = _read(RESOLVED_PATH)
    total = len(_read(LEDGER_PATH))
    report = build_shadow_report(resolved, total_candidates=total, include_legacy=include_legacy)

    if as_json:
        print(_json.dumps(report, indent=2, default=str))
        return

    console.print("[bold]Shadow Candidate Report[/bold]")
    console.print(
        f"resolved={report['n_resolved']} (real) total={report['total_candidates']} "
        f"pending={report['pending']} coverage={report['resolution_coverage_pct']}%"
    )
    legacy_counts = report.get("legacy_counts")
    if isinstance(legacy_counts, dict):
        console.print(
            f"real_resolved={report.get('real_resolved')} "
            f"canary_probe_resolved={report.get('canary_probe_resolved')} "
            f"legacy_unattributed={legacy_counts.get('legacy_unattributed')} "
            f"legacy_canary_suspect={legacy_counts.get('legacy_canary_suspect')}"
        )
    console.print(f"confidence_analysis_status={report.get('confidence_analysis_status')}")
    console.print(f"[bold cyan]primary_class={report['primary_class']}[/bold cyan]")
    console.print(
        f"mfe_before_mae_rate={report['mfe_before_mae_rate']} "
        f"reached_take_rate={report['reached_take_rate']} "
        f"reached_stop_rate={report['reached_stop_rate']}"
    )
    console.print(
        f"median_mfe={report['median_mfe_bps']}bps median_mae={report['median_mae_bps']}bps "
        f"median_stop_dist={report['median_stop_dist_bps']}bps "
        f"median_take_dist={report['median_take_dist_bps']}bps"
    )
    by_regime = report.get("by_regime")
    if isinstance(by_regime, dict) and by_regime:
        tbl = Table(title="by_regime", show_header=True, header_style="bold cyan")
        tbl.add_column("regime", width=22)
        tbl.add_column("n", justify="right")
        tbl.add_column("take_rate", justify="right")
        tbl.add_column("stop_rate", justify="right")
        tbl.add_column("median_mfe", justify="right")
        for regime, s in by_regime.items():
            tbl.add_row(
                regime,
                str(s.get("count")),
                str(s.get("reached_take_rate")),
                str(s.get("reached_stop_rate")),
                str(s.get("median_mfe_bps")),
            )
        console.print(tbl)
    by_source = report.get("by_source")
    if isinstance(by_source, dict) and by_source:
        tbl_src = Table(title="by_source", show_header=True, header_style="bold magenta")
        tbl_src.add_column("source", width=22)
        tbl_src.add_column("n", justify="right")
        tbl_src.add_column("take_rate", justify="right")
        tbl_src.add_column("stop_rate", justify="right")
        tbl_src.add_column("median_mfe", justify="right")
        for src, s in by_source.items():
            tbl_src.add_row(
                src,
                str(s.get("count")),
                str(s.get("reached_take_rate")),
                str(s.get("reached_stop_rate")),
                str(s.get("median_mfe_bps")),
            )
        console.print(tbl_src)
    if report["n_resolved"] == 0:
        console.print("[yellow]No resolved real shadow candidates yet.[/yellow]")


@trading_app.command("shadow-drift-check")
def trading_shadow_drift_check(
    ledger_path: str = typer.Option(
        "artifacts/shadow_candidate_ledger.jsonl",
        "--ledger-path",
        help="Append-only shadow candidate ledger JSONL path",
    ),
    window_hours: float = typer.Option(
        24.0,
        "--window-hours",
        help="Window for growth and feature-variance checks",
    ),
    min_rows: int = typer.Option(
        1,
        "--min-rows",
        help="Minimum rows required in the window before warning",
    ),
    min_variance_samples: int = typer.Option(
        5,
        "--min-variance-samples",
        help="Minimum numeric samples before a feature can be called degenerate",
    ),
    variance_epsilon: float = typer.Option(
        1e-9,
        "--variance-epsilon",
        help="Variance at or below this value is treated as degenerate",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of the table"),
) -> None:
    """Read-only health check for the shadow learning stream.

    Warns when the ledger stopped growing or core features are constant in the
    selected window. It never changes entry mode, orders, positions, or the
    ledger; a non-zero exit means "manual review required".
    """
    import json as _json

    from app.observability.shadow_drift import STATUS_WARN, build_shadow_drift_report

    report = build_shadow_drift_report(
        ledger_path=Path(ledger_path),
        window_hours=window_hours,
        min_rows=min_rows,
        min_variance_samples=min_variance_samples,
        variance_epsilon=variance_epsilon,
    )

    if as_json:
        print(_json.dumps(report.to_dict(), indent=2))
    else:
        console.print("[bold]Shadow Drift Check[/bold]")
        console.print(
            f"status={report.status} rows_in_window={report.rows_in_window} "
            f"total_rows={report.total_rows} latest_ts_utc={report.latest_ts_utc}"
        )
        if report.reasons:
            console.print(f"reasons={','.join(report.reasons)}")
        tbl = Table(title="feature_variance", show_header=True, header_style="bold cyan")
        tbl.add_column("field")
        tbl.add_column("samples", justify="right")
        tbl.add_column("variance", justify="right")
        tbl.add_column("distinct", justify="right")
        tbl.add_column("degenerate", justify="right")
        for var in report.feature_variance:
            tbl.add_row(
                var.field,
                str(var.sample_count),
                "None" if var.variance is None else str(var.variance),
                "None" if var.distinct_count is None else str(var.distinct_count),
                str(var.is_degenerate),
            )
        console.print(tbl)

    if report.status == STATUS_WARN:
        raise typer.Exit(1)
