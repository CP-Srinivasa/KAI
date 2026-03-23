"""Trading and market data commands.

Covers: market data quotes, portfolio snapshots, backtesting,
decision journal, trading loop status and control.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

console = Console()
research_trading_app = typer.Typer()

@research_trading_app.command("market-data-quote")
def research_market_data_quote(
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


@research_trading_app.command("market-data-snapshot")
def research_market_data_snapshot(
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
    console.print(_json.dumps(snapshot.to_json_dict(), indent=2))

    if not snapshot.available:
        raise typer.Exit(1)


@research_trading_app.command("paper-portfolio-snapshot")
def research_paper_portfolio_snapshot(
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
    console.print(_json.dumps(snapshot.to_json_dict(), indent=2))

    if not snapshot.available:
        raise typer.Exit(1)


@research_trading_app.command("paper-positions-summary")
def research_paper_positions_summary(
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


@research_trading_app.command("paper-exposure-summary")
def research_paper_exposure_summary(
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


@research_trading_app.command("backtest-run")
def research_backtest_run(
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
    audit_path: str = typer.Option(
        "artifacts/backtest_audit.jsonl", "--audit-path"
    ),
) -> None:
    """Run a paper backtest from a signal candidate JSONL file."""
    import asyncio
    import json as _json
    from pathlib import Path as _Path

    from app.execution.backtest_engine import BacktestConfig, BacktestEngine
    from app.market_data.mock_adapter import MockMarketDataAdapter
    from app.research.signals import SignalCandidate

    # Load signals
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
            pass  # skip malformed rows

    if not signals:
        console.print("[yellow]No valid signals found. Exiting.[/yellow]")
        raise typer.Exit(0)

    # Fetch prices for unique assets via MockAdapter (A-012)
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
    out_path.write_text(
        _json.dumps(result.to_json_dict(), indent=2), encoding="utf-8"
    )

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


@research_trading_app.command("decision-journal-append")
def research_decision_journal_append(
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


@research_trading_app.command("decision-journal-summary")
def research_decision_journal_summary(
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


@research_trading_app.command("trading-loop-status")
def research_trading_loop_status(
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


@research_trading_app.command("loop-cycle-summary")
@research_trading_app.command("trading-loop-recent-cycles")
def research_trading_loop_recent_cycles(
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

    console.print(
        f"[bold]Trading Loop Recent Cycles[/bold] ({payload['total_cycles']} total)"
    )
    console.print(f"status_counts={payload['status_counts']}")
    console.print(
        "showing last "
        f"{len(payload['recent_cycles'])} of {payload['total_cycles']} cycles:"  # type: ignore[arg-type]
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
            str(rec.get("cycle_id", "—"))[:16],
            str(rec.get("status", "—")),
            str(rec.get("symbol", "—")),
            "Y" if rec.get("signal_generated") else "N",
            "Y" if rec.get("risk_approved") else "N",
            "Y" if rec.get("fill_simulated") else "N",
        )

    console.print(table)
    console.print("audit_path=" + str(payload.get("audit_path")))
    console.print("auto_loop_enabled=False")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_trading_app.command("trading-loop-run-once")
def research_trading_loop_run_once(
    symbol: str = typer.Option("BTC/USDT", "--symbol", help="Trading symbol"),
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
) -> None:
    """Run one guarded paper/shadow cycle and append cycle audit output."""
    import asyncio

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
