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
