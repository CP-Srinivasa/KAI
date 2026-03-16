"""CLI Entry Point — Typer-based CLI for the AI Analyst Trading Bot."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="trading-bot", help="AI Analyst Trading Bot CLI", no_args_is_help=True)
console = Console()

ingest_app = typer.Typer(help="Ingestion commands")
analyze_app = typer.Typer(help="Analysis commands")
alerts_app = typer.Typer(help="Alert commands")
sources_app = typer.Typer(help="Source management commands")
query_app = typer.Typer(help="Query commands")
podcasts_app = typer.Typer(help="Podcast resolver commands")
youtube_app = typer.Typer(help="YouTube channel commands")
watchlists_app = typer.Typer(help="Watchlist management commands")
research_app = typer.Typer(help="Research output commands")
signals_app = typer.Typer(help="Signal candidate commands")

app.add_typer(ingest_app, name="ingest")
app.add_typer(analyze_app, name="analyze")
app.add_typer(alerts_app, name="alerts")
app.add_typer(sources_app, name="sources")
app.add_typer(query_app, name="query")
app.add_typer(podcasts_app, name="podcasts")
app.add_typer(youtube_app, name="youtube")
app.add_typer(watchlists_app, name="watchlists")
app.add_typer(research_app, name="research")
app.add_typer(signals_app, name="signals")


@ingest_app.command("run")
def ingest_run(
    source_id: str | None = typer.Argument(None, help="Source ID or all if omitted"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Run ingestion for all or a specific source."""
    console.print(f"[bold green]Starting ingestion[/] source={source_id or 'all'} dry_run={dry_run}")
    # TODO: Initialize and run ingestion orchestrator
    console.print("[yellow]TODO: Ingestion orchestrator not yet connected[/]")


@ingest_app.command("rss")
def ingest_rss(
    source: str | None = typer.Option(None, "--source", "-s", help="Source ID to ingest (all active RSS if omitted)"),
    monitor_dir: str = typer.Option("monitor", "--monitor-dir", help="Path to monitor/ directory"),
) -> None:
    """Ingest RSS/podcast feeds from the source registry."""
    from pathlib import Path
    from app.ingestion.source_registry import build_registry
    from app.core.utils.file_loaders import load_website_sources, load_podcast_feeds_raw
    from app.ingestion.podcasts.resolver_pipeline import run_pipeline

    monitor_path = Path(monitor_dir)
    console.print(f"[bold green]RSS Ingestion[/] source={source or 'all'} monitor={monitor_dir}")

    # Build a quick in-memory registry for display
    rss_feeds_path = monitor_path / "podcast_feeds_resolved.txt"
    pipeline_result = run_pipeline(monitor_path / "podcast_feeds_raw.txt")

    table = Table(title="Active RSS Sources")
    table.add_column("Source ID")
    table.add_column("URL")
    table.add_column("Status")

    for entry in pipeline_result.resolved:
        if source and entry.source_id != source:
            continue
        table.add_row(entry.source_id, entry.url[:70], entry.status.value)

    console.print(table)
    console.print(f"[yellow]TODO: Connect to IngestionRunner for actual DB storage[/]")


@analyze_app.command("pending")
def analyze_pending(
    limit: int = typer.Option(50, "--limit", "-n", help="Max documents to analyze"),
    source_id: str | None = typer.Option(None, "--source", "-s", help="Limit to one source"),
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
    llm: bool = typer.Option(False, "--llm", help="[REQUIRES: OPENAI_API_KEY] Enable LLM analysis"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be analyzed without running"),
) -> None:
    """
    Analyze pending documents (rule-based by default).
    Use --llm to enable OpenAI analysis [REQUIRES: OPENAI_API_KEY in .env].
    """
    import asyncio
    from pathlib import Path
    from app.core.utils.file_loaders import load_keywords, load_entity_aliases
    from app.analysis.keywords.matcher import KeywordMatcher

    monitor_path = Path(monitor_dir)
    keywords = load_keywords(monitor_path / "keywords.txt")
    alias_groups = load_entity_aliases(monitor_path / "entity_aliases.yml")
    matcher = KeywordMatcher(keywords=keywords, alias_groups=alias_groups)

    console.print(
        f"[bold green]Analysis pipeline[/] "
        f"limit={limit} source={source_id or 'all'} "
        f"llm={'[yellow]enabled[/]' if llm else 'disabled'} "
        f"keywords={len(keywords)}"
    )

    if dry_run:
        console.print(
            f"[yellow]DRY RUN: Would analyze up to {limit} pending documents "
            f"with {len(keywords)} keywords.[/]"
        )
        return

    console.print("[yellow]TODO: Connect async DB session to AnalysisRunner.[/]")
    console.print(
        "Run via the scheduler or use the API endpoint POST /analysis/run "
        "when DB is configured."
    )


@alerts_app.command("send-test")
def alerts_send_test(
    channel: str = typer.Option("telegram", "--channel", "-c", help="telegram or email"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Log message instead of sending"),
) -> None:
    """
    Send a test alert to verify channel configuration.
    [REQUIRES: channel settings in .env]
    """
    import asyncio
    from app.alerts.evaluator import AlertDecision, DocumentScores
    from app.core.enums import AlertType, AlertChannel, DocumentPriority

    test_scores = DocumentScores(
        document_id="cli-test",
        source_id="test",
        title="🧪 CLI Test Alert — AI Analyst Bot",
        explanation_short="Test from CLI. If you see this, your channel is configured correctly.",
        sentiment_label="positive",
        sentiment_score=0.5,
        impact_score=0.6,
        relevance_score=0.7,
        credibility_score=0.8,
        affected_assets=["BTC"],
        recommended_priority=DocumentPriority.MEDIUM,
    )

    try:
        ch = AlertChannel(channel)
    except ValueError:
        console.print(f"[red]Unknown channel: {channel}. Use: telegram, email[/]")
        raise typer.Exit(1)

    decision = AlertDecision(
        rule_name="cli_test",
        alert_type=AlertType.BREAKING,
        channels=[ch],
        should_alert=True,
        severity=DocumentPriority.MEDIUM,
        reasons=["CLI test"],
        document_scores=test_scores,
    )

    if ch == AlertChannel.TELEGRAM:
        from app.integrations.telegram.adapter import TelegramAdapter
        from app.core.settings import get_settings
        settings = get_settings()
        if not dry_run and not settings.telegram.is_configured:
            console.print("[red]Telegram not configured. Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED=true[/]")
            raise typer.Exit(1)
        adapter = TelegramAdapter(
            bot_token=settings.telegram.bot_token if not dry_run else "dry",
            chat_id=settings.telegram.chat_id if not dry_run else "0",
            dry_run=dry_run,
        )
        success = asyncio.run(adapter.send_alert(decision))

    elif ch == AlertChannel.EMAIL:
        from app.integrations.email.adapter import EmailAdapter
        from app.core.settings import get_settings
        settings = get_settings()
        if not dry_run and not settings.email.is_configured:
            console.print("[red]Email not configured. Set EMAIL_SMTP_* and EMAIL_ENABLED=true[/]")
            raise typer.Exit(1)
        adapter = EmailAdapter(
            smtp_host=settings.email.smtp_host,
            smtp_port=settings.email.smtp_port,
            smtp_user=settings.email.smtp_user if not dry_run else "",
            smtp_password=settings.email.smtp_password if not dry_run else "",
            from_address=settings.email.from_address,
            to_address=settings.email.to_address,
            dry_run=dry_run,
        )
        success = asyncio.run(adapter.send_alert(decision))
    else:
        console.print(f"[yellow]Channel {channel} not yet implemented in CLI[/]")
        return

    status = "[bold green]✓ Sent[/]" if success else "[bold red]✗ Failed[/]"
    console.print(f"{status} → {channel}" + (" (dry-run)" if dry_run else ""))


@alerts_app.command("send-digest")
def send_digest(
    channel: str = typer.Option("telegram", "--channel", "-c"),
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """
    Send a digest alert (uses sample data — connect DB for real documents).
    [REQUIRES: channel settings in .env unless --dry-run]
    """
    console.print(f"[bold green]Sending digest[/] channel={channel} dry_run={dry_run}")
    console.print("[yellow]Real digest requires DB connection. Use --dry-run for formatting test.[/]")
    if dry_run:
        from app.alerts.evaluator import DocumentScores
        from app.core.enums import DocumentPriority
        sample = [
            DocumentScores(
                document_id=f"sample-{i}",
                source_id="test",
                title=f"Sample Document {i}: Bitcoin analysis",
                explanation_short="This is a sample entry in the digest.",
                sentiment_label="positive",
                impact_score=0.7 - i * 0.1,
                relevance_score=0.8,
                recommended_priority=DocumentPriority.HIGH,
            )
            for i in range(5)
        ]
        from app.integrations.telegram.adapter import format_digest_message
        msg = format_digest_message(sample)
        console.print("\n[bold]Telegram digest preview:[/]")
        console.print(msg[:500] + ("..." if len(msg) > 500 else ""))


@alerts_app.command("rules")
def alerts_rules() -> None:
    """Show all configured alert rules."""
    from app.alerts.rules import DEFAULT_RULES
    table = Table(title="Configured Alert Rules")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Channels")
    table.add_column("Impact ≥")
    table.add_column("Relevance ≥")
    table.add_column("Entity Hit")
    table.add_column("Enabled")
    for r in DEFAULT_RULES:
        table.add_row(
            r.name,
            r.alert_type.value,
            ", ".join(c.value for c in r.channels),
            str(r.min_impact),
            str(r.min_relevance),
            "✓" if r.requires_entity_hit else "—",
            "[green]✓[/]" if r.enabled else "[red]✗[/]",
        )
    console.print(table)


@alerts_app.command("evaluate-pending")
def alerts_evaluate_pending(
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
) -> None:
    """
    Preview which pending documents would trigger alerts (offline test).
    Uses rule evaluation against sample scores — no DB required.
    """
    from app.alerts.rules import DEFAULT_RULES
    from app.alerts.evaluator import AlertEvaluator, DocumentScores
    from app.core.enums import DocumentPriority

    evaluator = AlertEvaluator(rules=DEFAULT_RULES)

    # Sample scores for preview
    sample = DocumentScores(
        document_id="sample-eval",
        source_id="coindesk_rss",
        source_type="rss_feed",
        title="Bitcoin ETF approved by SEC in landmark decision",
        explanation_short="The SEC has approved multiple spot Bitcoin ETF applications.",
        sentiment_label="positive",
        sentiment_score=0.82,
        relevance_score=0.85,
        impact_score=0.90,
        confidence_score=0.78,
        novelty_score=0.95,
        credibility_score=0.75,
        spam_probability=0.02,
        recommended_priority=DocumentPriority.CRITICAL,
        actionable=True,
        keyword_score=0.80,
        has_entity_hit=True,
        matched_entities=["Bitcoin"],
        source_credibility=0.82,
    )

    decisions = evaluator.evaluate_all(sample)
    table = Table(title="Alert Evaluation (sample document)")
    table.add_column("Rule")
    table.add_column("Type")
    table.add_column("Would Alert?")
    table.add_column("Reasons / Failures", max_width=60)

    for d in decisions:
        status = "[bold green]YES[/]" if d.should_alert else "[dim]no[/]"
        detail = "; ".join(d.reasons or d.failed_conditions)[:60]
        table.add_row(d.rule_name, d.alert_type.value, status, detail)

    console.print(table)
    if dry_run:
        console.print("[dim](dry-run mode — no alerts sent)[/]")


@sources_app.command("classify")
def sources_classify(file: str = typer.Option("monitor/podcast_feeds_raw.txt", "--file")) -> None:
    """Classify sources from a raw source file."""
    from app.ingestion.resolvers.podcast_resolver import classify_batch
    console.print(f"[bold green]Classifying sources[/] from {file}")
    try:
        with open(file) as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        results = classify_batch(urls)
        table = Table(title=f"Source Classification: {file}")
        table.add_column("URL", max_width=60)
        table.add_column("Category")
        table.add_column("Status")
        for r in results:
            table.add_row(r.url[:60], r.category.value, r.status.value)
        console.print(table)
    except FileNotFoundError:
        console.print(f"[red]File not found: {file}[/]")
        raise typer.Exit(1)


@sources_app.command("list")
def sources_list(
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
    status_filter: str | None = typer.Option(None, "--status", help="Filter by status (active, requires_api, disabled)"),
    type_filter: str | None = typer.Option(None, "--type", help="Filter by type (rss_feed, website, youtube_channel, ...)"),
) -> None:
    """List all registered sources from monitor files."""
    from pathlib import Path
    from app.core.utils.file_loaders import load_website_sources
    from app.ingestion.source_registry import build_registry
    from app.ingestion.websites.registry import build_website_registry
    from app.ingestion.youtube.registry import build_youtube_registry
    from app.ingestion.podcasts.resolver_pipeline import run_pipeline

    monitor_path = Path(monitor_dir)

    # Collect all entries from all registries
    all_entries = []
    all_entries.extend(build_website_registry(
        monitor_path / "website_sources.txt",
        monitor_path / "news_domains.txt",
    ))
    all_entries.extend(build_youtube_registry(monitor_path / "youtube_channels.txt"))
    pipeline_result = run_pipeline(monitor_path / "podcast_feeds_raw.txt")
    all_entries.extend(pipeline_result.resolved)

    # Apply filters
    if status_filter:
        all_entries = [e for e in all_entries if e.status.value == status_filter]
    if type_filter:
        all_entries = [e for e in all_entries if e.source_type.value == type_filter]

    table = Table(title=f"Registered Sources ({len(all_entries)} total)")
    table.add_column("ID", max_width=30)
    table.add_column("Name", max_width=25)
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Fetchable")
    table.add_column("URL", max_width=45)

    for e in sorted(all_entries, key=lambda x: (x.source_type.value, x.source_id)):
        table.add_row(
            e.source_id,
            e.source_name,
            e.source_type.value,
            e.status.value,
            "✓" if e.is_fetchable else "✗",
            e.url[:45],
        )
    console.print(table)


@query_app.command("validate")
def query_validate(
    query_text: str = typer.Argument(..., help="Query string to validate"),
    explain: bool = typer.Option(False, "--explain", "-e", help="Show full AST breakdown"),
) -> None:
    """Validate a Boolean query string and display its AST."""
    from app.core.query.parser import QueryParser
    from app.core.errors import QueryParseError
    try:
        ast = QueryParser().parse(query_text)
        console.print("[bold green]✓ Valid query[/]")
        if explain:
            console.print(f"\nAST:\n  {ast!r}")
            console.print(f"\nNode type: [cyan]{ast.node_type.value}[/]")
            if ast.children:
                console.print(f"Children: {len(ast.children)}")
                for i, child in enumerate(ast.children):
                    console.print(f"  [{i}] {child!r}")
        else:
            console.print(f"AST: {ast!r}")
    except QueryParseError as e:
        console.print(f"[bold red]✗ Parse error:[/] {e}")
        raise typer.Exit(1)


@query_app.command("search")
def query_search(
    query_text: str = typer.Argument(..., help="Boolean query to run against documents"),
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """
    Run a Boolean query against documents (offline keyword test only).
    This tests query parsing and matching — no DB required.
    """
    from app.core.query.parser import QueryParser
    from app.core.errors import QueryParseError

    try:
        ast = QueryParser().parse(query_text)
    except QueryParseError as e:
        console.print(f"[bold red]✗ Parse error:[/] {e}")
        raise typer.Exit(1)

    console.print(f"[bold green]Query parsed successfully.[/]")
    console.print(f"AST: {ast!r}")
    console.print(
        "\n[yellow]For live document search, use POST /documents/search via the API.[/]"
    )


@podcasts_app.command("resolve")
def podcasts_resolve(
    file: str = typer.Option("monitor/podcast_feeds_raw.txt", "--file", "-f"),
    show_unresolved: bool = typer.Option(True, "--show-unresolved/--no-show-unresolved"),
) -> None:
    """
    Classify and resolve all raw podcast URLs.
    Shows which sources were resolved to RSS and which require manual action.
    """
    from pathlib import Path
    from app.ingestion.podcasts.resolver_pipeline import run_pipeline, print_unresolved_report

    console.print(f"[bold green]Resolving podcasts[/] from {file}")
    try:
        result = run_pipeline(Path(file))
    except Exception as e:
        console.print(f"[red]Error: {e}[/]")
        raise typer.Exit(1)

    # Resolved table
    if result.resolved:
        table = Table(title=f"Resolved RSS Feeds ({result.resolved_count})")
        table.add_column("Source ID")
        table.add_column("RSS URL", max_width=70)
        for entry in result.resolved:
            table.add_row(entry.source_id, entry.url)
        console.print(table)

    # Unresolved report
    if show_unresolved and result.unresolved:
        table = Table(title=f"Unresolved Sources ({result.unresolved_count})")
        table.add_column("URL", max_width=60)
        table.add_column("Status")
        table.add_column("Required Action", max_width=50)
        for cs in result.unresolved:
            table.add_row(cs.url[:60], cs.status.value, cs.requires_action[:50] if cs.requires_action else "—")
        console.print(table)

    console.print(
        f"\n[bold]Summary:[/] {result.total_input} input → "
        f"[green]{result.resolved_count} resolved[/], "
        f"[yellow]{result.unresolved_count} unresolved[/]"
    )


@youtube_app.command("list")
def youtube_list(
    file: str = typer.Option("monitor/youtube_channels.txt", "--file", "-f"),
) -> None:
    """List all YouTube channels from the monitor file."""
    from pathlib import Path
    from app.ingestion.youtube.registry import build_youtube_registry

    entries = build_youtube_registry(Path(file))

    table = Table(title=f"YouTube Channels ({len(entries)}) — [yellow]All require YOUTUBE_API_KEY[/]")
    table.add_column("Source ID")
    table.add_column("Display Name")
    table.add_column("URL")
    table.add_column("URL Type")

    for e in entries:
        url_type = e.config.get("url_type", "?")
        table.add_row(e.source_id, e.source_name, e.url, url_type)

    console.print(table)
    console.print(f"[yellow]Note: Set YOUTUBE_API_KEY in .env to enable fetching.[/]")


# ──────────────────────────────────────────────
# Watchlists commands
# ──────────────────────────────────────────────

@watchlists_app.command("list")
def watchlists_list(
    category: str | None = typer.Option(None, "--category", "-c", help="crypto | equities | etfs | persons | topics | domains"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
) -> None:
    """List all watchlist items (optionally filtered by category or tag)."""
    from pathlib import Path
    from app.trading.watchlists.watchlist import WatchlistRegistry
    from app.core.enums import WatchlistCategory

    registry = WatchlistRegistry.from_file(Path(monitor_dir) / "watchlists.yml")
    console.print(f"[bold green]Watchlist Registry[/] — {registry.total} items total")

    if category:
        try:
            cat = WatchlistCategory(category.lower())
        except ValueError:
            console.print(f"[red]Unknown category: {category}. Use: crypto, equities, etfs, persons, topics, domains[/]")
            raise typer.Exit(1)
        categories = [cat]
    else:
        categories = list(WatchlistCategory)

    for cat in categories:
        items = registry.get_by_category(cat)
        if tag:
            items = [i for i in items if tag.lower() in [t.lower() for t in i.tags]]
        if not items:
            continue
        table = Table(title=f"{cat.value.upper()} ({len(items)})")
        table.add_column("Identifier")
        table.add_column("Display Name")
        table.add_column("Tags", max_width=50)
        for item in items:
            table.add_row(item.identifier, item.display_name, ", ".join(item.tags[:5]))
        console.print(table)


@watchlists_app.command("search")
def watchlists_search(
    text: str = typer.Argument(..., help="Text to search for watchlist hits"),
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
) -> None:
    """Find all watchlist items matching a text snippet."""
    from pathlib import Path
    from app.trading.watchlists.watchlist import WatchlistRegistry

    registry = WatchlistRegistry.from_file(Path(monitor_dir) / "watchlists.yml")
    matches = registry.find_by_text(text)

    if not matches:
        console.print("[yellow]No watchlist hits found.[/]")
        return

    table = Table(title=f"Watchlist Hits ({len(matches)})")
    table.add_column("Category")
    table.add_column("Identifier")
    table.add_column("Matched Alias")
    table.add_column("Tags", max_width=50)
    for m in matches:
        table.add_row(
            m.item.category.value,
            m.item.identifier,
            m.matched_alias,
            ", ".join(m.item.tags[:4]),
        )
    console.print(table)


@watchlists_app.command("sync")
def watchlists_sync(
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
) -> None:
    """Reload and validate watchlists.yml, print summary."""
    from pathlib import Path
    from app.trading.watchlists.watchlist import WatchlistRegistry

    path = Path(monitor_dir) / "watchlists.yml"
    if not path.exists():
        console.print(f"[red]File not found: {path}[/]")
        raise typer.Exit(1)

    registry = WatchlistRegistry.from_file(path)
    table = Table(title=f"Watchlists Summary — {path}")
    table.add_column("Category")
    table.add_column("Items")
    for cat, count in registry.summary().items():
        table.add_row(cat, str(count))
    console.print(table)
    console.print(f"[bold green]✓ {registry.total} items loaded successfully.[/]")


# ──────────────────────────────────────────────
# Research commands
# ──────────────────────────────────────────────

@research_app.command("build-brief")
def research_build_brief(
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
    date: str | None = typer.Option(None, "--date", help="Date string e.g. 2024-01-15"),
) -> None:
    """
    Build and display a daily research brief from sample data.
    For production, connect this to the analysis database.
    """
    from app.research.router_helpers import get_sample_candidates
    from app.research.builder import ResearchPackBuilder
    from datetime import datetime

    candidates = get_sample_candidates()
    builder = ResearchPackBuilder()
    brief = builder.daily_brief(candidates, date=date or datetime.utcnow().strftime("%Y-%m-%d"))

    console.print(f"\n[bold cyan]Daily Research Brief — {brief.date}[/]")
    console.print(f"Total signals: [bold]{brief.total_signals}[/]  Market sentiment: [bold]{brief.market_sentiment}[/]  Urgency: [bold]{brief.overall_urgency}[/]")

    if brief.key_themes:
        console.print(f"Key themes: {', '.join(brief.key_themes)}")

    if brief.top_assets:
        table = Table(title="Top Assets")
        table.add_column("Asset")
        table.add_column("Direction")
        table.add_column("Confidence")
        table.add_column("Urgency")
        table.add_column("Signals")
        for pack in brief.top_assets:
            table.add_row(
                pack.asset,
                pack.direction_consensus,
                f"{pack.overall_confidence:.0%}",
                pack.urgency,
                str(pack.total_documents),
            )
        console.print(table)

    if brief.active_narratives:
        table = Table(title="Active Narratives")
        table.add_column("Narrative")
        table.add_column("Assets")
        table.add_column("Direction")
        table.add_column("Confidence")
        for narrative in brief.active_narratives:
            table.add_row(
                narrative.narrative_label,
                ", ".join(narrative.affected_assets[:4]),
                narrative.dominant_direction,
                f"{narrative.overall_confidence:.0%}",
            )
        console.print(table)

    if brief.risk_summary:
        console.print("\n[bold yellow]Risk Summary:[/]")
        for risk in brief.risk_summary:
            console.print(f"  • {risk}")


@research_app.command("asset")
def research_asset(
    symbol: str = typer.Argument(..., help="Asset symbol e.g. BTC, ETH, NVDA"),
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
) -> None:
    """Show research pack for a specific asset."""
    from app.research.router_helpers import get_sample_candidates
    from app.research.builder import ResearchPackBuilder

    candidates = get_sample_candidates()
    symbol_upper = symbol.upper()
    asset_candidates = [c for c in candidates if c.asset == symbol_upper]

    if not asset_candidates:
        console.print(f"[yellow]No signal candidates found for {symbol_upper}.[/]")
        console.print(f"Available: {list({c.asset for c in candidates})}")
        raise typer.Exit(1)

    builder = ResearchPackBuilder()
    pack = builder.for_asset(symbol_upper, candidates)

    console.print(f"\n[bold cyan]{pack.asset} Research Pack[/]")
    console.print(f"Direction: [bold]{pack.direction_consensus}[/]  Confidence: [bold]{pack.overall_confidence:.0%}[/]  Urgency: [bold]{pack.urgency}[/]")

    if pack.top_supporting_evidence:
        console.print("\n[bold green]Supporting:[/]")
        for e in pack.top_supporting_evidence:
            console.print(f"  + {e}")

    if pack.top_contradicting_evidence:
        console.print("\n[bold red]Contradicting:[/]")
        for e in pack.top_contradicting_evidence:
            console.print(f"  - {e}")

    if pack.key_risk_notes:
        console.print("\n[bold yellow]Risks:[/]")
        for r in pack.key_risk_notes:
            console.print(f"  ⚠ {r}")


# ──────────────────────────────────────────────
# Signals commands
# ──────────────────────────────────────────────

@signals_app.command("generate")
def signals_generate(
    monitor_dir: str = typer.Option("monitor", "--monitor-dir"),
    min_confidence: float = typer.Option(0.50, "--min-confidence", "-c"),
    top: int = typer.Option(10, "--top", "-n", help="Show top N candidates"),
) -> None:
    """
    Generate and display signal candidates from sample data.
    Ranked by trading relevance score.
    """
    from app.research.router_helpers import get_sample_candidates
    from app.analysis.ranking.trading_ranker import TradingRelevanceRanker

    candidates = get_sample_candidates()
    ranker = TradingRelevanceRanker()
    ranked = ranker.rank(candidates)
    filtered = [(c, s) for c, s in ranked if c.confidence >= min_confidence][:top]

    if not filtered:
        console.print(f"[yellow]No candidates above confidence threshold {min_confidence}.[/]")
        return

    table = Table(title=f"Signal Candidates (top {len(filtered)}, min confidence {min_confidence})")
    table.add_column("Asset")
    table.add_column("Direction")
    table.add_column("Confidence")
    table.add_column("Urgency")
    table.add_column("Narrative")
    table.add_column("Relevance Score")
    table.add_column("Next Step", max_width=50)

    for candidate, score in filtered:
        direction_color = {
            "bullish": "[green]",
            "bearish": "[red]",
            "neutral": "[white]",
            "mixed": "[yellow]",
        }.get(candidate.direction_hint.value, "[white]")
        table.add_row(
            f"[bold]{candidate.asset}[/]",
            f"{direction_color}{candidate.direction_hint.value}[/]",
            f"{candidate.confidence:.0%}",
            candidate.urgency.value,
            candidate.narrative_label.value,
            f"{score:.2f}",
            candidate.recommended_next_step[:50],
        )
    console.print(table)
    console.print(
        "\n[dim]Note: Signal candidates are for research purposes only. "
        "No orders are placed automatically.[/]"
    )


@signals_app.command("historical")
def signals_historical(
    asset: str = typer.Argument(..., help="Asset symbol e.g. BTC"),
    event_type: str | None = typer.Option(None, "--event-type", "-e"),
    sentiment: str | None = typer.Option(None, "--sentiment", "-s"),
) -> None:
    """Find historical event analogues for an asset."""
    from app.analysis.historical.matcher import HistoricalMatcher

    matcher = HistoricalMatcher()
    analogues = matcher.find(
        assets=[asset.upper()],
        event_type=event_type,
        sentiment=sentiment,
        max_results=5,
    )

    if not analogues:
        console.print(f"[yellow]No historical analogues found for {asset.upper()}.[/]")
        return

    for i, analogue in enumerate(analogues, 1):
        event = analogue.event
        console.print(f"\n[bold cyan]{i}. {event.title}[/]  (similarity: {analogue.similarity_score:.0%})")
        console.print(f"   Occurred: {event.occurred_at.strftime('%Y-%m-%d')}  |  Type: {event.event_type}  |  Sentiment: {event.sentiment_label}")
        if analogue.matched_assets:
            console.print(f"   Matched assets: {', '.join(analogue.matched_assets)}")
        if event.outcome_summary:
            console.print(f"   Outcome: {event.outcome_summary}")
        if event.max_price_impact_pct is not None:
            color = "green" if event.max_price_impact_pct > 0 else "red"
            console.print(f"   Max price impact: [{color}]{event.max_price_impact_pct:+.1f}%[/]")
        console.print(f"   [dim]{analogue.confidence_caveat}[/]")

    console.print(
        "\n[dim]Disclaimer: Historical patterns are for context only — "
        "not predictive guidance.[/]"
    )


if __name__ == "__main__":
    app()
