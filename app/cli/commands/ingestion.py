"""CLI commands for ingestion workers (B-3: telegram-channel)."""

from __future__ import annotations

import asyncio
import logging

import typer
from rich.console import Console
from rich.table import Table

from app.core.logging import configure_logging
from app.core.settings import get_settings

logger = logging.getLogger(__name__)
console = Console()

ingestion_app = typer.Typer(
    name="ingestion", help="Ingestion worker commands", no_args_is_help=True
)
telegram_channel_app = typer.Typer(
    name="telegram-channel",
    help="Premium Telegram channel ingest (Vorschlag B, B-3)",
    no_args_is_help=True,
)
ingestion_app.add_typer(telegram_channel_app, name="telegram-channel")


@telegram_channel_app.command("setup")
def telegram_channel_setup() -> None:
    """Interactive first-time MTProto auth. Writes the session file.

    Prompts for phone + SMS code (+ 2FA password if enabled). Required
    once per session file. Subsequent runs reuse the stored auth.
    """
    configure_logging()
    from app.ingestion.telegram_channel_worker import setup_auth

    cfg = get_settings().telegram_channel_ingest
    if not cfg.api_id or not cfg.api_hash:
        console.print(
            "[red]Missing credentials[/red]: set "
            "INGESTION_TELEGRAM_CHANNEL_API_ID and _API_HASH in .env "
            "(get them from https://my.telegram.org/apps)."
        )
        raise typer.Exit(code=2)
    console.print(f"[cyan]Starting interactive auth for session=[/cyan]{cfg.session_path}")
    asyncio.run(setup_auth(cfg))
    console.print("[green]Auth complete.[/green] Session written.")


@telegram_channel_app.command("list-dialogs")
def telegram_channel_list_dialogs(
    limit: int = typer.Option(50, help="Max dialogs to display"),
    contains: str = typer.Option(
        "", help="Filter dialogs whose title contains this substring (case-insensitive)"
    ),
) -> None:
    """List visible dialogs (chats/channels/groups) with id+title.

    Use this to find the numeric chat_id of the premium channel when
    the title is ambiguous or contains special characters.
    """
    configure_logging()
    from app.ingestion.telegram_channel_worker import list_dialogs

    cfg = get_settings().telegram_channel_ingest
    if not cfg.api_id or not cfg.api_hash:
        console.print("[red]Missing credentials[/red]: api_id/api_hash required.")
        raise typer.Exit(code=2)

    dialogs = asyncio.run(list_dialogs(cfg))
    needle = contains.strip().lower()
    if needle:
        dialogs = [d for d in dialogs if needle in str(d.get("title", "")).lower()]

    table = Table(title="Telegram Dialogs", show_lines=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("title", style="white")
    table.add_column("channel", style="green")
    table.add_column("group", style="yellow")
    for row in dialogs[:limit]:
        table.add_row(
            str(row.get("id")),
            str(row.get("title"))[:80],
            "✓" if row.get("is_channel") else "",
            "✓" if row.get("is_group") else "",
        )
    console.print(table)
    if len(dialogs) > limit:
        console.print(f"... {len(dialogs) - limit} more (use --limit to see more)")


@telegram_channel_app.command("run")
def telegram_channel_run() -> None:
    """Run the long-lived channel listener. Blocks until disconnected."""
    configure_logging()
    from app.ingestion.telegram_channel_worker import run_worker

    cfg = get_settings().telegram_channel_ingest
    if not cfg.enabled:
        console.print("[red]Disabled[/red]: set INGESTION_TELEGRAM_CHANNEL_ENABLED=true to run.")
        raise typer.Exit(code=2)
    console.print(
        f"[cyan]Starting channel listener[/cyan] "
        f"target_title={cfg.target_title!r} chat_id={cfg.target_chat_id} "
        f"source_tag={cfg.source_tag!r}"
    )
    try:
        asyncio.run(run_worker(cfg))
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted by user.[/yellow]")


@telegram_channel_app.command("test-parse")
def telegram_channel_test_parse(
    text: str = typer.Argument(..., help="Raw message text to parse (single arg)"),
) -> None:
    """Parse one text without connecting to Telegram — useful for smoke tests."""
    configure_logging()
    from app.ingestion.telegram_channel_envelope import build_envelope_record
    from app.ingestion.telegram_channel_parser import parse_premium_channel_message

    parsed = parse_premium_channel_message(text)
    if parsed is None:
        console.print("[yellow]Not a signal.[/yellow]")
        raise typer.Exit(code=1)
    record = build_envelope_record(parsed)
    console.print_json(data=record)


@telegram_channel_app.command("probe")
def telegram_channel_probe(
    symbol: str = typer.Option("BTC/USDT", help="Symbol to probe (must have CoinGecko mapping)"),
    targets_count: int = typer.Option(4, help="Number of TP tiers to simulate"),
) -> None:
    """End-to-end probe of the V25/V25-C pipeline — runs in isolation.

    Verifies the full staged-exit cascade against the real PaperExecutionEngine
    + audit_replay layer in a sandboxed audit path so production state is NOT
    touched. Skips the live Telethon listener and the Telegram-bot transport
    (those are out-of-scope for a deterministic CI-style smoke). Live coverage
    of those last two hops still depends on a real channel post + operator
    click.

    Steps:
      1. Open a paper-long position with 4 staged-exit tiers
      2. Drive monitor_positions through escalating prices (T1 → T4)
      3. Assert: 4 partial-closes + final close, realized PnL > 0, audit
         contains all expected event types, rehydrate from audit reproduces
         a clean (empty) portfolio at the end.

    Exit code 0 on full GREEN, 1 on any RED gate.
    """
    configure_logging()
    import json
    import tempfile
    from pathlib import Path

    from app.execution.paper_engine import PaperExecutionEngine

    console.print(f"[bold cyan]V25 Pipeline Probe[/bold cyan] symbol={symbol} tiers={targets_count}")

    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        audit_path = Path(tmp) / "probe_audit.jsonl"
        engine = PaperExecutionEngine(
            initial_equity=10_000.0,
            fee_pct=0.1,
            slippage_pct=0.05,
            live_enabled=False,
            audit_log_path=str(audit_path),
        )

        # Synthetic prices: entry=100, SL=95, tier prices 101..104.
        entry, sl = 100.0, 95.0
        tier_prices = [100.5 + 0.5 * i for i in range(1, targets_count + 1)]
        order = engine.create_order(
            symbol=symbol,
            side="buy",
            quantity=10.0,
            stop_loss=sl,
            take_profit=tier_prices[0],
            idempotency_key="probe_open",
        )
        fill = engine.fill_order(order, current_price=entry)
        if fill is None:
            failures.append("step1_open_fill_returned_none")
            console.print("[red]RED step 1[/red] — open fill returned None")
        elif symbol not in engine.portfolio.positions:
            failures.append("step1_position_missing")
            console.print("[red]RED step 1[/red] — position not found after fill")
        else:
            console.print(f"[green]GREEN step 1[/green] — position opened qty=10.0 @ {fill.fill_price:.2f}")

        share = round(1.0 / targets_count, 6)
        tiers = [(p, share) for p in tier_prices]
        ok = engine.set_position_tp_tiers(symbol, tiers)
        if not ok:
            failures.append("step2_set_tp_tiers_returned_false")
            console.print("[red]RED step 2[/red] — set_position_tp_tiers returned False")
        else:
            pos = engine.portfolio.positions[symbol]
            if len(pos.take_profit_tiers) != targets_count:
                failures.append(f"step2_tier_count_mismatch_{len(pos.take_profit_tiers)}")
                console.print(f"[red]RED step 2[/red] — tier count mismatch ({len(pos.take_profit_tiers)})")
            else:
                console.print(f"[green]GREEN step 2[/green] — {targets_count} tiers attached")

        # Drive each tier individually so we can verify partial closes one by one.
        partial_fills = 0
        for i, tier_price in enumerate(tier_prices, start=1):
            # Step price slightly above the tier so it triggers, but below the next
            # tier so we get one close per step.
            step_price = tier_price + 0.001
            fills = engine.monitor_positions({symbol: step_price})
            if len(fills) != 1:
                failures.append(f"step3_tier{i}_unexpected_fills_{len(fills)}")
                console.print(f"[red]RED tier {i}[/red] — got {len(fills)} fills (expected 1)")
            else:
                partial_fills += 1
                pos = engine.portfolio.positions.get(symbol)
                remaining_qty = pos.quantity if pos else 0.0
                console.print(
                    f"[green]GREEN tier {i}[/green] — close@{step_price:.3f} "
                    f"qty_closed={fills[0].quantity:.4f} remaining={remaining_qty:.4f}"
                )

        if partial_fills != targets_count:
            failures.append(f"step3_partial_count_{partial_fills}")
        if symbol in engine.portfolio.positions:
            failures.append("step3_position_not_fully_closed")
            console.print(f"[red]RED step 3[/red] — position {symbol} not fully closed")
        else:
            console.print(f"[green]GREEN step 3[/green] — position fully exited after {targets_count} tiers")

        if engine.portfolio.realized_pnl_usd <= 0:
            failures.append(f"step4_realized_pnl_{engine.portfolio.realized_pnl_usd:.4f}")
            console.print(f"[red]RED step 4[/red] — realized_pnl_usd={engine.portfolio.realized_pnl_usd:.4f} ≤ 0")
        else:
            console.print(f"[green]GREEN step 4[/green] — realized_pnl_usd={engine.portfolio.realized_pnl_usd:.4f}")

        # Audit-trail content gate: every expected event_type must appear at least once.
        expected_events = {
            "order_created",
            "order_filled",
            "position_tp_tiers_set",
            "position_partial_closed",
        }
        seen_events: set[str] = set()
        for raw in audit_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                ev = payload.get("event_type")
                if isinstance(ev, str):
                    seen_events.add(ev)
        missing = expected_events - seen_events
        if missing:
            failures.append(f"step5_audit_missing_events_{sorted(missing)}")
            console.print(f"[red]RED step 5[/red] — audit missing events: {sorted(missing)}")
        else:
            console.print(f"[green]GREEN step 5[/green] — audit contains all 4 expected event types")

        # Rehydrate gate: a fresh engine reading the same audit must reproduce
        # the empty-portfolio terminal state (V25-C audit_replay extension).
        eng2 = PaperExecutionEngine(
            initial_equity=10_000.0,
            fee_pct=0.1,
            slippage_pct=0.05,
            live_enabled=False,
            audit_log_path=str(audit_path),
        )
        eng2.rehydrate_from_audit()
        if symbol in eng2.portfolio.positions:
            failures.append("step6_rehydrate_position_leaked")
            console.print(f"[red]RED step 6[/red] — rehydrated portfolio still has {symbol}")
        else:
            console.print("[green]GREEN step 6[/green] — rehydrate reproduces empty portfolio")

    if failures:
        console.print(f"\n[bold red]PROBE RED[/bold red] — {len(failures)} gate(s) failed:")
        for f in failures:
            console.print(f"  · {f}")
        raise typer.Exit(code=1)
    console.print("\n[bold green]PROBE GREEN[/bold green] — all 6 gates passed")
