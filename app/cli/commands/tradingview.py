"""TradingView CLI — TV-3.1 operator-gated promotion of pending events.

Subcommands (`trading-bot tradingview <cmd>`):

    list      — open pending events (decision log filtered)
    show      — full event dump (JSON)
    promote   — promote pending event to SignalCandidate (operator inputs)
    reject    — record rejection in decision log

All commands are read-only or append-only on JSONL artifact files.
No execution, no DB writes. See `app/signals/tradingview_promotion.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from app.core.settings import get_settings
from app.signals.tradingview_promotion import (
    DecisionRecord,
    PromotionError,
    PromotionInputs,
    append_decision,
    append_promoted_candidate,
    fetch_rsi_context_sync,
    filter_open_events,
    load_decisions,
    load_pending_events,
    promote_event,
)

console = Console()

tradingview_app = typer.Typer(
    name="tradingview",
    help="TV-3.1 operator-gated promotion of pending TradingView signal events.",
    no_args_is_help=True,
)


def _paths() -> tuple[Path, Path, Path]:
    tv = get_settings().tradingview
    return (
        Path(tv.webhook_pending_signals_log),
        Path(tv.pending_decisions_log),
        Path(tv.promoted_signals_log),
    )


@tradingview_app.command("list")
def tradingview_list(
    show_decided: bool = typer.Option(
        False, "--show-decided", help="Include events that already have a decision."
    ),
    limit: int = typer.Option(50, help="Max rows to print."),
) -> None:
    """List pending TV signal events not yet decided by operator."""
    pending_path, decisions_path, _ = _paths()
    events = load_pending_events(pending_path)
    decisions = load_decisions(decisions_path)
    rows = events if show_decided else filter_open_events(events, decisions)

    console.print(
        f"[bold]{len(rows)} events[/bold] "
        f"(total pending file rows={len(events)}, decided={len(decisions)})"
    )
    if not rows:
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Event ID", width=22)
    table.add_column("Received At", width=26)
    table.add_column("Ticker", width=12)
    table.add_column("Action", width=6)
    table.add_column("Price", justify="right", width=12)
    table.add_column("Decision", width=10)
    table.add_column("Strategy / Note")

    for ev in rows[:limit]:
        dec = decisions.get(ev.event_id)
        decision_str = dec.decision if dec else "-"
        meta = ev.strategy or (ev.note[:40] if ev.note else "-")
        price_str = f"{ev.price:,.2f}" if ev.price is not None else "-"
        table.add_row(
            ev.event_id,
            ev.received_at,
            ev.ticker,
            ev.action,
            price_str,
            decision_str,
            meta,
        )
    console.print(table)


@tradingview_app.command("show")
def tradingview_show(
    event_id: str = typer.Argument(..., help="Event ID (e.g. tvsig_xxxx)."),
) -> None:
    """Print the full pending event payload as JSON."""
    pending_path, decisions_path, _ = _paths()
    events = load_pending_events(pending_path)
    match = next((ev for ev in events if ev.event_id == event_id), None)
    if match is None:
        console.print(f"[red]Event not found:[/red] {event_id}")
        raise typer.Exit(1)

    console.print_json(
        data={
            "event_id": match.event_id,
            "received_at": match.received_at,
            "ticker": match.ticker,
            "action": match.action,
            "price": match.price,
            "note": match.note,
            "strategy": match.strategy,
            "source_request_id": match.source_request_id,
            "source_payload_hash": match.source_payload_hash,
            "provenance": {
                "source": match.provenance.source,
                "version": match.provenance.version,
                "signal_path_id": match.provenance.signal_path_id,
            },
        }
    )

    decisions = load_decisions(decisions_path)
    dec = decisions.get(event_id)
    if dec:
        console.print(
            f"\n[bold]Existing decision:[/bold] {dec.decision} "
            f"at {dec.timestamp_utc} — {dec.operator_reason or '(no reason)'}"
        )


@tradingview_app.command("promote")
def tradingview_promote(
    event_id: str = typer.Argument(..., help="Event ID to promote."),
    thesis: str = typer.Option(..., "--thesis", help="Operator thesis (mandatory)."),
    confidence: float = typer.Option(0.75, "--confidence", help="Confidence in [0.0, 1.0]."),
    stop_loss: float | None = typer.Option(None, "--stop-loss", help="Stop-loss price."),
    take_profit: float | None = typer.Option(None, "--take-profit", help="Take-profit price."),
    invalidation: str = typer.Option(
        "manual_invalidate", "--invalidation", help="Invalidation condition."
    ),
    risk_assessment: str = typer.Option(
        "operator_review", "--risk-assessment", help="Risk assessment text."
    ),
    venue: str = typer.Option("paper", "--venue", help="Execution venue (paper recommended)."),
    mode: str = typer.Option(
        "paper", "--mode", help="Mode: paper | live (live execution gated elsewhere)."
    ),
    enrich_rsi: bool = typer.Option(
        True,
        "--rsi/--no-rsi",
        help="Try to fetch Binance RSI(14) context (fail-soft).",
    ),
    reason: str = typer.Option("", "--reason", help="Operator note for decision log."),
) -> None:
    """Promote a pending TV event to a SignalCandidate (approved, pending execution)."""
    pending_path, decisions_path, promoted_path = _paths()
    events = load_pending_events(pending_path)
    decisions = load_decisions(decisions_path)

    match = next((ev for ev in events if ev.event_id == event_id), None)
    if match is None:
        console.print(f"[red]Event not found:[/red] {event_id}")
        raise typer.Exit(1)

    if event_id in decisions:
        prior = decisions[event_id]
        console.print(
            f"[red]Event already decided:[/red] {prior.decision} at {prior.timestamp_utc}. "
            "Re-deciding is rejected."
        )
        raise typer.Exit(2)

    rsi_value: float | None = None
    if enrich_rsi:
        rsi_value = fetch_rsi_context_sync(match.ticker)
        if rsi_value is None:
            console.print(
                "[yellow]RSI enrichment unavailable[/yellow] "
                "(BINANCE_ENABLED off, fetch failed, or insufficient candles)."
            )

    inputs = PromotionInputs(
        thesis=thesis,
        confidence_score=confidence,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        invalidation_condition=invalidation,
        risk_assessment=risk_assessment,
        venue=venue,
        mode=mode,
    )
    try:
        candidate = promote_event(match, inputs, rsi_value=rsi_value)
    except PromotionError as exc:
        console.print(f"[red]Promotion rejected:[/red] {exc}")
        raise typer.Exit(2) from exc

    append_promoted_candidate(promoted_path, candidate)

    record = DecisionRecord(
        event_id=event_id,
        decision="promoted",
        timestamp_utc=candidate.timestamp_utc,
        operator_reason=reason,
        promoted_decision_id=candidate.decision_id,
    )
    append_decision(decisions_path, record)

    console.print(
        f"[green]Promoted[/green] {event_id} -> {candidate.decision_id} "
        f"({candidate.symbol} {candidate.direction.value} @ {candidate.entry_price})"
    )
    console.print(f"  promoted_log: {promoted_path}")
    console.print(f"  decision_log: {decisions_path}")


@tradingview_app.command("reject")
def tradingview_reject(
    event_id: str = typer.Argument(..., help="Event ID to reject."),
    reason: str = typer.Option(..., "--reason", help="Operator reason (mandatory)."),
) -> None:
    """Record a rejection in the decision log (event stays in pending file)."""
    pending_path, decisions_path, _ = _paths()
    events = load_pending_events(pending_path)
    decisions = load_decisions(decisions_path)

    match = next((ev for ev in events if ev.event_id == event_id), None)
    if match is None:
        console.print(f"[red]Event not found:[/red] {event_id}")
        raise typer.Exit(1)

    if event_id in decisions:
        prior = decisions[event_id]
        console.print(
            f"[red]Event already decided:[/red] {prior.decision} at {prior.timestamp_utc}. "
            "Re-deciding is rejected."
        )
        raise typer.Exit(2)

    from app.signals.tradingview_promotion import _utc_now_iso  # local import: small helper

    record = DecisionRecord(
        event_id=event_id,
        decision="rejected",
        timestamp_utc=_utc_now_iso(),
        operator_reason=reason,
        promoted_decision_id=None,
    )
    append_decision(decisions_path, record)
    console.print(f"[yellow]Rejected[/yellow] {event_id} — {reason}")


@tradingview_app.command("run")
def tradingview_run(
    provider: str = typer.Option(
        "coingecko", "--provider", help="Market data provider (coingecko, mock)."
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0, "--freshness", help="Market data stale threshold."
    ),
    timeout_seconds: int = typer.Option(10, "--timeout", help="Market data request timeout."),
    consensus: bool = typer.Option(False, "--consensus", help="Enable multi-model consensus gate."),
    consensus_model: str = typer.Option(
        "gpt-4o-mini", "--consensus-model", help="LLM model for consensus."
    ),
) -> None:
    """TV-4 bridge: run all pending promoted TV signals through the paper loop."""
    import asyncio

    from app.orchestrator.trading_loop import run_promoted_signals_once

    cycles = asyncio.run(
        run_promoted_signals_once(
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
            enable_consensus=consensus,
            consensus_model=consensus_model,
        )
    )

    if not cycles:
        console.print("[yellow]No pending promoted TV signals to process.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Decision ID", width=18)
    table.add_column("Symbol", width=12)
    table.add_column("Status", width=18)
    table.add_column("Fill", width=4)

    for cycle in cycles:
        fill = "Y" if cycle.fill_simulated else "N"
        status_color = "green" if cycle.status.value == "completed" else "yellow"
        table.add_row(
            cycle.decision_id or "-",
            cycle.symbol,
            f"[{status_color}]{cycle.status.value}[/{status_color}]",
            fill,
        )

    console.print(table)
    completed = sum(1 for c in cycles if c.status.value == "completed")
    console.print(f"[bold]{len(cycles)} signals processed, {completed} completed (filled).[/bold]")


def _format_event_dict(event_dict: dict[str, Any]) -> str:
    """Stable JSON formatting for tests/diagnostics."""
    return json.dumps(event_dict, sort_keys=True, indent=2)


@tradingview_app.command("auto-promote")
def tradingview_auto_promote() -> None:
    """Auto-promote eligible accepted TV events (WP-C, default OFF).

    Gated by ``TRADINGVIEW_WEBHOOK_AUTO_PROMOTE``. Routes each event through the
    technical-path eligibility gate; bearish promotes only with
    ``ALERT_ALLOW_SHORT_TECHNICAL``. Idempotent via the decision log. Execution
    stays gated by entry_mode. Intended for a systemd timer / operator run.
    """
    from app.observability.tradingview_auto_promote import run_from_settings

    summary = run_from_settings()
    if not summary.get("enabled"):
        console.print(
            "[yellow]TV auto-promote is OFF[/yellow] "
            "(set TRADINGVIEW_WEBHOOK_AUTO_PROMOTE=true to enable)."
        )
        raise typer.Exit(code=0)
    console.print("[bold green]TV auto-promote run complete[/bold green]")
    for key in ("open_events", "promoted", "rejected"):
        console.print(f"  {key}: {summary.get(key)}")


@tradingview_app.command("datafeed-probe")
def tradingview_datafeed_probe(
    limit: int = typer.Option(10, help="How many top rows to show"),
) -> None:
    """Probe the UNOFFICIAL TradingView datafeed (WP-G, default OFF).

    Gated by ``TRADINGVIEW_DATAFEED_ENABLED``. Public scanner, no login. ToS-grey,
    isolated, fail-soft — supplements (never replaces) the sanctioned exchange data.
    """
    import asyncio

    from app.core.settings import get_settings

    tv = get_settings().tradingview
    if not tv.datafeed_enabled:
        console.print(
            "[yellow]TradingView datafeed is OFF[/yellow] "
            "(set TRADINGVIEW_DATAFEED_ENABLED=true to enable)."
        )
        raise typer.Exit(code=0)

    from app.integrations.tradingview.datafeed import TradingViewDatafeed, rating_label

    feed = TradingViewDatafeed(exchange=tv.datafeed_exchange)
    rows = asyncio.run(feed.top_rows(limit=limit))
    if not rows:
        console.print("[red]No rows returned[/red] (endpoint error or empty).")
        raise typer.Exit(code=0)
    console.print(
        f"[bold green]TradingView {tv.datafeed_exchange} top {len(rows)} by volume[/bold green]"
    )
    for r in rows:
        console.print(
            f"  {r.symbol:14} chg={r.change_pct}  rating={r.rating} ({rating_label(r.rating)})"
        )


@tradingview_app.command("technicals")
def tradingview_technicals(
    symbols: str = typer.Option(
        "BTC/USDT,ETH/USDT,SOL/USDT", help="Comma-separated canonical symbols"
    ),
) -> None:
    """Per-symbol TV technical-indicator snapshot (WP-I, default OFF).

    Gated by ``TRADINGVIEW_DATAFEED_ENABLED``. Webhook-independent data pull
    (RSI/MACD/ADX/EMAs/Recommend.*). Public scanner, no login, fail-soft.
    """
    import asyncio

    from app.core.settings import get_settings

    tv = get_settings().tradingview
    if not tv.datafeed_enabled:
        console.print(
            "[yellow]TradingView datafeed is OFF[/yellow] "
            "(set TRADINGVIEW_DATAFEED_ENABLED=true to enable)."
        )
        raise typer.Exit(code=0)

    from app.integrations.tradingview.datafeed import TradingViewDatafeed

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    feed = TradingViewDatafeed(exchange=tv.datafeed_exchange)
    snap = asyncio.run(feed.technicals(syms))
    if not snap:
        console.print("[red]No data returned[/red] (endpoint error or unknown symbols).")
        raise typer.Exit(code=0)
    for sym, cols in snap.items():
        rsi = cols.get("RSI")
        macd = cols.get("MACD.macd")
        adx = cols.get("ADX")
        rec = cols.get("Recommend.All")
        console.print(f"  {sym:12} RSI={rsi} MACD={macd} ADX={adx} Recommend.All={rec}")


@tradingview_app.command("shadow-feed")
def tradingview_shadow_feed() -> None:
    """Record open TV alerts as SHADOW candidates for forward-return measurement.

    Gated by ``ALERT_TRADINGVIEW_SHADOW_FEED_ENABLED`` (default OFF). No
    execution, no order, no capital — it only writes to the shadow ledger so the
    resolver can measure how effective the TV buy/sell alerts actually are.
    """
    import asyncio

    from app.observability.tradingview_shadow_feed import run_from_settings

    summary = asyncio.run(run_from_settings())
    if not summary.get("enabled"):
        console.print(
            "[yellow]TV shadow feed is OFF[/yellow] "
            "(set ALERT_TRADINGVIEW_SHADOW_FEED_ENABLED=true to enable)."
        )
        raise typer.Exit(code=0)
    console.print("[bold]TradingView Shadow Feed[/bold]")
    console.print(
        f"  open_events={summary.get('open_events')}  recorded={summary.get('recorded')}  "
        f"unmappable={summary.get('unmappable')}  no_price={summary.get('no_price')}  "
        f"short_skipped={summary.get('short_skipped')}  already={summary.get('already')}"
    )


@tradingview_app.command("paper-feed")
def tradingview_paper_feed() -> None:
    """Turn open TV alerts into PAPER trades via the envelope bridge.

    Gated by ``ALERT_TRADINGVIEW_PAPER_FEED_ENABLED`` (default OFF). PAPER only
    (no real capital). Requires source ``tradingview_webhook`` in
    ``EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST`` for the bridge to fill.
    """
    import asyncio

    from app.observability.tradingview_paper_feeder import run_from_settings

    summary = asyncio.run(run_from_settings())
    if not summary.get("enabled"):
        console.print(
            "[yellow]TV paper feed is OFF[/yellow] "
            "(set ALERT_TRADINGVIEW_PAPER_FEED_ENABLED=true to enable)."
        )
        raise typer.Exit(code=0)
    console.print("[bold]TradingView Paper Feed[/bold]")
    console.print(
        f"  open_events={summary.get('open_events')}  emitted={summary.get('emitted')}  "
        f"unmappable={summary.get('unmappable')}  no_price={summary.get('no_price')}  "
        f"short_skipped={summary.get('short_skipped')}  already={summary.get('already')}"
    )
