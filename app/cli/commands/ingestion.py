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


@telegram_channel_app.command("bootstrap-checkpoint")
def telegram_channel_bootstrap_checkpoint(
    message_id: int = typer.Option(
        ...,
        "--message-id",
        "-m",
        help="last_message_id to seed the checkpoint with (must be > 0)",
    ),
    chat_id: int = typer.Option(
        0,
        "--chat-id",
        "-c",
        help=(
            "Marked chat_id (Channels: -100<peer>). Defaults to "
            "INGESTION_TELEGRAM_CHANNEL_TARGET_CHAT_ID. Both marked and "
            "unmarked forms accepted; normalised to marked before write."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt when overwriting an existing entry",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview the resulting file content without writing",
    ),
) -> None:
    """Seed the listener checkpoint manually for recovery scenarios.

    Use case: after a Pi cutover or session-rebuild that loses the
    checkpoint file, the worker would otherwise short-circuit replay
    (skipped_no_checkpoint=1, the defensive default that prevents a
    full-channel-history re-ingest on first boot). This command writes
    the file in canonical marked-form so the next boot triggers replay
    from --message-id forward.

    Risks mitigated:
    - Confirmation prompt before overwriting an existing entry (use
      --force to bypass in scripts).
    - --dry-run shows the resulting file content without modifying disk.
    - Warns + re-prompts when --message-id is lower than the stored
      value (would replay messages already processed → operator-visible
      duplicate signals + duplicate approval-sends).
    - Refuses message_id <= 0 and chat_id == 0 (both produce a no-op
      checkpoint that the worker would treat as "missing").
    """
    from datetime import UTC, datetime
    from pathlib import Path

    from app.ingestion.telegram_channel_worker import (
        _checkpoint_chat_id_marked,
        load_checkpoint,
        save_checkpoint,
    )

    configure_logging()

    if message_id <= 0:
        console.print("[red]--message-id must be > 0[/red]")
        raise typer.Exit(code=2)

    cfg = get_settings().telegram_channel_ingest
    effective_chat_id = chat_id if chat_id != 0 else cfg.target_chat_id
    if effective_chat_id == 0:
        console.print(
            "[red]No chat_id resolved.[/red] Pass --chat-id or set "
            "INGESTION_TELEGRAM_CHANNEL_TARGET_CHAT_ID in .env."
        )
        raise typer.Exit(code=2)

    canonical = _checkpoint_chat_id_marked(int(effective_chat_id))
    checkpoint_path = Path(cfg.checkpoint_path)

    existing = load_checkpoint(checkpoint_path)
    existing_entry = existing.get(str(canonical))
    existing_msg_id: int | None = None
    if existing_entry is not None:
        try:
            existing_msg_id = int(existing_entry.get("last_message_id", 0))
        except (TypeError, ValueError):
            existing_msg_id = None

    # Operator visibility before any write decision.
    console.print(
        f"[cyan]checkpoint_path[/cyan] = {checkpoint_path}\n"
        f"[cyan]chat_id[/cyan]         = {canonical} (canonical marked form)\n"
        f"[cyan]new message_id[/cyan]  = {message_id}\n"
        f"[cyan]existing[/cyan]        = "
        + (f"{existing_msg_id}" if existing_msg_id is not None else "[dim]none[/dim]")
    )

    if existing_msg_id is not None and message_id < existing_msg_id and not force:
        console.print(
            f"[yellow]WARNING:[/yellow] new message_id {message_id} is "
            f"LOWER than existing {existing_msg_id}. The next worker boot "
            f"would replay {existing_msg_id - message_id} messages already "
            f"processed — risk of duplicate operator-approval-sends. "
            f"Pass --force to proceed anyway."
        )
        raise typer.Exit(code=3)

    if existing_msg_id is not None and not force and not dry_run:
        # Interactive guard — Operator must type "yes" (or pass --force).
        confirmed = typer.confirm("Overwrite the existing checkpoint entry?", default=False)
        if not confirmed:
            console.print("[yellow]Aborted by operator.[/yellow] No write performed.")
            raise typer.Exit(code=1)

    if dry_run:
        # Compose the would-be file content without touching disk.
        preview = {
            **{k: v for k, v in existing.items() if k != str(canonical)},
            str(canonical): {
                "last_message_id": int(message_id),
                "last_seen_at": datetime.now(tz=UTC).isoformat(),
            },
        }
        console.print("[cyan]--dry-run: would write[/cyan]")
        console.print_json(data=preview)
        return

    save_checkpoint(checkpoint_path, canonical, message_id)
    console.print(
        f"[green]Checkpoint written.[/green] Next listener boot will replay "
        f"messages with id > {message_id} from chat {canonical}."
    )


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

    console.print(
        f"[bold cyan]V25 Pipeline Probe[/bold cyan] symbol={symbol} tiers={targets_count}"
    )

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
            console.print(
                f"[green]GREEN step 1[/green] — position opened qty=10.0 @ {fill.fill_price:.2f}"
            )

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
                console.print(
                    f"[red]RED step 2[/red] — tier count mismatch ({len(pos.take_profit_tiers)})"
                )
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
            console.print(
                f"[green]GREEN step 3[/green] — position fully exited after {targets_count} tiers"
            )

        if engine.portfolio.realized_pnl_usd <= 0:
            failures.append(f"step4_realized_pnl_{engine.portfolio.realized_pnl_usd:.4f}")
            console.print(
                f"[red]RED step 4[/red] — realized_pnl_usd={engine.portfolio.realized_pnl_usd:.4f} ≤ 0"
            )
        else:
            console.print(
                f"[green]GREEN step 4[/green] — realized_pnl_usd={engine.portfolio.realized_pnl_usd:.4f}"
            )

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
            console.print("[green]GREEN step 5[/green] — audit contains all 4 expected event types")

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


@ingestion_app.command("okx-announcements")
def ingestion_okx_announcements(
    source_id: str = typer.Option("okx_announcements", help="Source ID"),
    source_name: str = typer.Option("OKX Announcements", help="Source name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip DB writes"),
) -> None:
    """Fetch OKX exchange announcements (listings/delistings), analyze, and alert.

    source-scout 2026-06-14: exchange listings are the highest-impact directional
    event class — raises eligible throughput at the same quality bar. Intended to
    be driven periodically by a Pi timer (analog to the NewsData CLI+timer).
    """
    configure_logging()
    from pathlib import Path

    # Lazy import of the shared provider builders avoids a circular import
    # (app.cli.main imports ingestion_app at module load).
    from app.analysis.keywords.engine import KeywordEngine
    from app.cli.main import _build_primary_provider, _maybe_gemini_shadow
    from app.pipeline.service import run_okx_announcements_pipeline
    from app.storage.db.session import build_session_factory

    async def run() -> None:
        settings = get_settings()
        keyword_engine = KeywordEngine.from_monitor_dir(Path(settings.monitor_dir))
        session_factory = build_session_factory(settings.db)
        stats = await run_okx_announcements_pipeline(
            session_factory=session_factory,
            keyword_engine=keyword_engine,
            provider=_build_primary_provider(),
            shadow_provider=_maybe_gemini_shadow(),
            source_id=source_id,
            source_name=source_name,
            dry_run=dry_run,
        )
        console.print("\n[bold green]OKX announcements pipeline complete[/bold green]")
        console.print(f"  Fetched:   {stats.fetched_count}")
        console.print(f"  Saved:     {stats.saved_count}")
        console.print(f"  Analyzed:  {stats.analyzed_count}")
        console.print(f"  Alerts:    {stats.alerts_fired_count}")
        console.print(f"  Skipped:   {stats.skipped_count}")
        if stats.priority_distribution:
            dist = ", ".join(
                f"P{score}:{count}" for score, count in sorted(stats.priority_distribution.items())
            )
            console.print(f"  Priority:  {dist}")

    asyncio.run(run())


@ingestion_app.command("technical-screen")
def technical_screen() -> None:
    """Run the asset-agnostic technical screener (SHADOW-ONLY, default OFF).

    Gated by ``ALERT_TECHNICAL_SCREENER_ENABLED``. Fetches OHLCV for the
    configured liquid universe, ranks by relative-strength-vs-BTC, evaluates the
    WP-B technical eligibility path, and records shadow candidates — no
    execution. Intended for a systemd timer / operator invocation.
    """
    configure_logging()
    from app.observability.technical_screener_feed import run_from_settings

    summary = asyncio.run(run_from_settings())
    if not summary.get("enabled"):
        console.print(
            "[yellow]Technical screener is OFF[/yellow] "
            "(set ALERT_TECHNICAL_SCREENER_ENABLED=true to enable)."
        )
        raise typer.Exit(code=0)
    console.print("[bold green]Technical screener run complete[/bold green]")
    for key in (
        "scanned",
        "signals",
        "written",
        "non_btc_signals",
        "eligible_on_technical_path",
    ):
        console.print(f"  {key}: {summary.get(key)}")


async def run_messari_command_logic(
    source_id: str,
    source_name: str,
    limit: int,
    dry_run: bool,
) -> None:
    """Core logic to run Messari pipeline from CLI."""
    from pathlib import Path

    from app.analysis.keywords.engine import KeywordEngine
    from app.cli.main import _build_primary_provider, _maybe_gemini_shadow
    from app.pipeline.service import run_messari_pipeline
    from app.storage.db.session import build_session_factory

    settings = get_settings()
    keyword_engine = KeywordEngine.from_monitor_dir(Path(settings.monitor_dir))
    session_factory = build_session_factory(settings.db)
    stats = await run_messari_pipeline(
        session_factory=session_factory,
        keyword_engine=keyword_engine,
        provider=_build_primary_provider(),
        shadow_provider=_maybe_gemini_shadow(),
        api_key=settings.providers.messari_api_key,
        source_id=source_id,
        source_name=source_name,
        limit=limit,
        dry_run=dry_run,
    )
    console.print("\n[bold green]Messari pipeline complete[/bold green]")
    console.print(f"  Fetched:   {stats.fetched_count}")
    console.print(f"  Saved:     {stats.saved_count}")
    console.print(f"  Analyzed:  {stats.analyzed_count}")
    console.print(f"  Alerts:    {stats.alerts_fired_count}")
    console.print(f"  Skipped:   {stats.skipped_count}")
    if stats.priority_distribution:
        dist = ", ".join(
            f"P{score}:{count}" for score, count in sorted(stats.priority_distribution.items())
        )
        console.print(f"  Priority:  {dist}")


@ingestion_app.command("messari")
def ingestion_messari(
    source_id: str = typer.Option("messari", help="Source ID"),
    source_name: str = typer.Option("Messari", help="Source name"),
    limit: int = typer.Option(100, help="Max assets to fetch"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip DB writes"),
) -> None:
    """Fetch Messari asset metrics, analyze, and alert.

    Intended to be driven periodically by a timer.
    """
    configure_logging()
    asyncio.run(run_messari_command_logic(source_id, source_name, limit, dry_run))
