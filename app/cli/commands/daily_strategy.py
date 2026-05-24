"""Daily-Strategy CLI — bootstraps today's strategy-review skeleton.

Two subcommands (`trading-bot daily-strategy <cmd>`):

    check      — prints whether today's review file exists (exit 0/1)
    bootstrap  — if today's file is missing, writes a skeleton with live
                 metrics and (optionally) pings the operator via Telegram

Why a skeleton and not the full review: the qualitative sections (Lagebild,
Verbesserungen, neue Quellen, Priorisierung) require an LLM session. The
CLI prefills the *deterministic* metrics so that when the operator opens
the next session, Claude sees the file, fills the placeholders, and the
existing session-start memory rule keeps the loop closed.

The bootstrap command is idempotent: running it twice on the same day
does nothing the second time — the skeleton is only written if missing.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import typer

daily_strategy_app = typer.Typer(
    name="daily-strategy",
    help="Daily strategy review skeleton + operator reminder.",
    no_args_is_help=True,
)

# Anchor dates from CLAUDE.md § active project state.
PI_MIGRATION_DATE = date(2026, 5, 1)
TV_REENTRY_DATE = date(2026, 5, 16)
MULTIAGENT_GATE_DATE = date(2026, 4, 23)


def _daily_dir() -> Path:
    return Path("artifacts/daily_strategy")


def _today_path(today: date | None = None) -> Path:
    target = today or datetime.now(UTC).date()
    return _daily_dir() / f"{target.isoformat()}.md"


def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _resolved_directional_count() -> dict[str, int]:
    """Return {'total': N, 'hit': H, 'miss': M} from alert_outcomes.jsonl."""
    path = Path("artifacts/alert_outcomes.jsonl")
    counts = {"total": 0, "hit": 0, "miss": 0, "inconclusive": 0}
    if not path.exists():
        return counts
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            outcome = rec.get("outcome")
            if outcome in counts:
                counts[outcome] += 1
            counts["total"] += 1
    return counts


def _tv_pending_count() -> int:
    return _count_jsonl_lines(Path("artifacts/tradingview_pending_signals.jsonl"))


def _paper_fills_count() -> int:
    # NEO-P-103: Re-Entry-Gate verlangt "≥10 paper fills mit PnL" — d.h. echte
    # geschlossene Trades. Zählung der gesamten JSONL ist falsch (mischt
    # order_created + order_filled + position_closed + position_adjusted).
    # Pro Trade wird genau ein 'position_closed'-Event emittiert.
    path = Path("artifacts/paper_execution_audit.jsonl")
    if not path.exists():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event_type") in ("position_closed", "position_partial_closed"):
                n += 1
    return n


def _days_until(target: date, today: date) -> int:
    return (target - today).days


def _blocked_alerts_summary(today: date, lookback_hours: int = 24) -> dict[str, object]:
    """Summarize blocked_alerts.jsonl for the last *lookback_hours*.

    F4 (Dispatch-Observability) — Operator-Pflicht-Sektion in der Daily-
    Strategy: zeigt was der Eligibility-Gate aussortiert hat, damit
    Re-Calibration-Decisions auf Empirie statt auf alert_audit-Tunnelblick
    aufsetzen. Sentiment-Drift-Forensik 2026-05-24: alert_audit zeigt nur
    ~4% des Klassifikator-Outputs; ohne diese Sicht ist der Operator blind
    auf 96% des Material-Flows.

    Returns {
        'total': int,
        'top_reasons': [(reason, count), ...],   # max 3
        'top_blocked': [{...}, ...],             # max 3 raw records
        'window_start': iso str,
        'window_end': iso str,
    }
    """
    # Window: rolling lookback (default 24h) anchored at today's *end-of-day UTC*
    # so a bootstrap run at 06:00 still captures yesterday-evening blocks.
    window_end = datetime.combine(today, datetime.max.time()).replace(tzinfo=UTC)
    window_start = window_end - timedelta(hours=lookback_hours)

    path = Path("artifacts/blocked_alerts.jsonl")
    if not path.exists():
        return {
            "total": 0,
            "top_reasons": [],
            "top_blocked": [],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }

    reasons: Counter[str] = Counter()
    records: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = rec.get("blocked_at")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if not (window_start <= ts <= window_end):
                continue
            reason = rec.get("block_reason")
            if isinstance(reason, str):
                reasons[reason] += 1
            records.append(rec)

    # Top-3 records: prefer high priority + directional sentiment so Operator
    # sees the most material misses first.
    def _priority_key(r: dict[str, object]) -> int:
        p = r.get("priority")
        return p if isinstance(p, int) else -1

    records.sort(key=_priority_key, reverse=True)
    top_blocked = records[:3]

    return {
        "total": len(records),
        "top_reasons": reasons.most_common(3),
        "top_blocked": top_blocked,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


def _format_dispatch_health_section(summary: dict[str, object]) -> str:
    """Render the Dispatch-Health markdown section from the summary dict."""
    total = summary["total"]
    if not isinstance(total, int) or total == 0:
        return (
            "## Dispatch-Health 24h\n\n"
            "Keine geblockten direktionalen Alerts im letzten 24h-Fenster — "
            "Eligibility-Gate hat nichts ausgesondert.\n"
        )

    top_reasons = summary["top_reasons"]
    top_blocked = summary["top_blocked"]
    assert isinstance(top_reasons, list)
    assert isinstance(top_blocked, list)

    reason_lines: list[str] = []
    for reason, count in top_reasons:
        pct = 100.0 * count / total if total else 0.0
        reason_lines.append(f"- `{reason}`: {count} ({pct:.0f}%)")

    blocked_lines: list[str] = []
    for rec in top_blocked:
        assert isinstance(rec, dict)
        prio = rec.get("priority", "?")
        label = rec.get("sentiment_label", "?")
        src = rec.get("source_name", "?")
        title = rec.get("normalized_title") or ""
        if isinstance(title, str) and len(title) > 100:
            title = title[:97] + "..."
        reason = rec.get("block_reason", "?")
        blocked_lines.append(f"- p={prio} `{label}` src=`{src}` → `{reason}`\n  > {title}")

    window_start = str(summary["window_start"])
    window_end = str(summary["window_end"])
    return (
        "## Dispatch-Health 24h\n\n"
        f"**{total}** direktionale Alerts geblockt (Eligibility-Gate, Fenster "
        f"{window_start[:10]} → {window_end[:10]}).\n\n"
        "**Top-Block-Reasons:**\n" + "\n".join(reason_lines) + "\n\n"
        "**Top-3 geblockte Headlines (höchste Priority):**\n" + "\n".join(blocked_lines) + "\n\n"
        "→ Re-Calibration-Befund: [[kai-dispatch-filter-root-befund-20260524]]. "
        "Sprint F1+F2+F3 betreffen diese Filter-Stelle.\n"
    )


def _gate_status_line(directional: int, paper_fills: int) -> str:
    alert_gate = (
        "✅ Gate ≥200 erreicht"
        if directional >= 200
        else f"❌ {directional}/200 resolved (noch {200 - directional})"
    )
    fill_gate = (
        "✅ Gate ≥10 Fills erreicht" if paper_fills >= 10 else f"❌ {paper_fills}/10 paper-fills"
    )
    return f"{alert_gate} · {fill_gate}"


def _build_skeleton(today: date) -> str:
    res = _resolved_directional_count()
    directional = res["hit"] + res["miss"]
    precision_pct: float | None = None
    if directional > 0:
        precision_pct = 100.0 * res["hit"] / directional

    tv_pending = _tv_pending_count()
    paper_fills = _paper_fills_count()

    d_pi = _days_until(PI_MIGRATION_DATE, today)
    d_reentry = _days_until(TV_REENTRY_DATE, today)
    d_gate = _days_until(MULTIAGENT_GATE_DATE, today)

    horizon_line = (
        f"**D-{d_gate}** Multi-Agent-Gate ({MULTIAGENT_GATE_DATE.isoformat()}) · "
        f"**D-{d_pi}** Pi-Migration ({PI_MIGRATION_DATE.isoformat()}) · "
        f"**D-{d_reentry}** TV-Pivot Re-Entry ({TV_REENTRY_DATE.isoformat()})"
    )

    precision_line = (
        f"{precision_pct:.1f}% ({res['hit']}/{directional})" if precision_pct is not None else "—"
    )

    return f"""# KAI Daily Strategy Review — {today.isoformat()}

Erstellt: {today.isoformat()} · automatischer Skelett-Bootstrap (CLI: `daily-strategy bootstrap`)
Format: 6 Pflicht-Sektionen nach CLAUDE.md §7.
Horizont-Anker: {horizon_line}

---

## Live-Metriken (deterministisch, Stand {datetime.now(UTC).isoformat()})

| Metrik | Wert |
|---|---|
| Resolved directional alerts | {directional} (hit {res["hit"]} / miss {res["miss"]}) |
| Baseline-Precision | {precision_line} |
| TV pending events (unpromoted) | {tv_pending} |
| Paper-Trading abgeschlossene Trades | {paper_fills} |
| Tage bis TV-Pivot Re-Entry | {d_reentry} |
| Tage bis Pi-Migration | {d_pi} |
| Tage bis Multi-Agent-Gate | {d_gate} |

**Re-Entry-Gate (2026-05-16):** ≥200 resolved directional alerts ODER ≥10 Paper-Fills mit PnL.
Aktueller Status: {_gate_status_line(directional, paper_fills)}

---

{_format_dispatch_health_section(_blocked_alerts_summary(today))}
---

## 1. Lagebild

_Bitte Claude füllen — aktuelle Stärken/Schwächen, offene Baustellen, was Potenzial verschenkt._

## 2. Konkrete Verbesserungen

_Bitte Claude füllen — 3–10 Maßnahmen, priorisiert in V1..Vn._

## 3. Neue Quellen / Wege

_Bitte Claude füllen — fehlende Datenquellen, Crawls, Integrations._

## 4. Aufgabenverteilung

_Bitte Claude füllen — parallel/seriell, Subagent-Kandidaten, automatisierbar._

## 5. Priorisierung

_Bitte Claude füllen — P0/P1/P2/P3._

## 6. Ehrliche Aufwandsschätzung

_Bitte Claude füllen — Stunden/Tage, Blocker, Abhängigkeiten._

---

## Progress-Tabelle

| ID | Titel | Prio | Status | Ergebnis / Verweis |
|---|---|---|---|---|
| — | (Carry-over aus Vortagen bitte hier migrieren) | — | ⏳ offen | — |

**Abarbeitungsmodus:** Punkt für Punkt.
Nicht abgeschlossene Punkte wandern in das nächste Daily-File.
"""


@daily_strategy_app.command("check")
def cmd_check() -> None:
    """Print whether today's strategy-review file exists.

    Exit code 0 when present, 1 when missing — usable from scripts.
    """
    path = _today_path()
    if path.exists():
        typer.echo(f"present: {path}")
        raise typer.Exit(code=0)
    typer.echo(f"missing: {path}")
    raise typer.Exit(code=1)


# Stub-marker the bootstrap writes into placeholder sections. The reminder
# uses these to detect "skeleton present but unfilled" — Claude removes them
# when filling §1..§6 in a session-start review.
_STUB_MARKERS = (
    "_Bitte Claude füllen",
    "(Carry-over aus Vortagen bitte hier migrieren)",
)


def _reminder_marker_path(today: date) -> Path:
    return _daily_dir() / f".reminder_sent_{today.isoformat()}"


def _stub_section_count(text: str) -> int:
    return sum(text.count(marker) for marker in _STUB_MARKERS)


@daily_strategy_app.command("sync")
def cmd_sync(
    remote: str = typer.Option(
        "kai@kai-trader.org",
        "--remote",
        help="SSH connection string for the remote Pi (e.g. kai@kai-trader.org)",
    ),
    remote_dir: str = typer.Option(
        "/home/kai/ai_analyst_trading_bot",
        "--remote-dir",
        help="Remote directory path on the Pi",
    ),
) -> None:
    """Sync operational artifacts (JSONLs) from Pi to laptop to mitigate sync-lag."""
    import subprocess
    import sys

    files = [
        "artifacts/alert_outcomes.jsonl",
        "artifacts/paper_execution_audit.jsonl",
        "artifacts/alert_audit.jsonl",
        "artifacts/tradingview_pending_signals.jsonl",
    ]

    typer.echo(f"Syncing artifacts from Pi ({remote}) to mitigate sync-lag...")

    scp_cmd = "scp"
    success = 0
    for f in files:
        local_path = Path(f)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Build scp source and destination
        remote_src = f"{remote}:{remote_dir}/{f}"

        typer.echo(f"  Fetching {f}...")
        try:
            # We run scp synchronously
            res = subprocess.run(
                [scp_cmd, remote_src, str(local_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            if res.returncode == 0:
                typer.echo(f"    [OK] {f} synced successfully.")
                success += 1
            else:
                typer.echo(f"    [SKIP] Could not sync {f}: {res.stderr.strip()}")
        except Exception as exc:
            typer.echo(f"    [SKIP] Failed to fetch {f}: {exc}")

    typer.echo(f"\nSync complete. {success}/{len(files)} files updated from Pi.")


@daily_strategy_app.command("bootstrap")
def cmd_bootstrap(
    notify: bool = typer.Option(
        True,
        "--notify/--no-notify",
        help="Send a Telegram ping to the operator when a skeleton is created.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing skeleton (use with care — destroys content).",
    ),
    sync: bool = typer.Option(
        True,
        "--sync/--no-sync",
        help="Sync artifacts from Pi first to avoid sync-lag.",
    ),
    remote: str = typer.Option(
        "kai@kai-trader.org",
        "--remote",
        help="SSH connection string for the remote Pi.",
    ),
    remote_dir: str = typer.Option(
        "/home/kai/ai_analyst_trading_bot",
        "--remote-dir",
        help="Remote directory path on the Pi.",
    ),
) -> None:
    """Write today's skeleton if missing. Idempotent by default."""
    if sync:
        try:
            cmd_sync(remote=remote, remote_dir=remote_dir)
        except Exception as exc:
            typer.echo(f"Warning: Sync failed, continuing with local data. ({exc})")

    today = datetime.now(UTC).date()
    path = _today_path(today)
    _daily_dir().mkdir(parents=True, exist_ok=True)

    if path.exists() and not force:
        typer.echo(f"already present: {path}")
        raise typer.Exit(code=0)

    content = _build_skeleton(today)
    path.write_text(content, encoding="utf-8")
    typer.echo(f"wrote skeleton: {path}")

    if notify:
        try:
            ok = asyncio.run(_ping_operator(today, path))
            typer.echo(f"telegram ping: {'ok' if ok else 'disabled_or_failed'}")
        except Exception as exc:  # pragma: no cover — ping is best-effort
            typer.echo(f"telegram ping: error ({exc})")


async def _ping_operator(today: date, path: Path) -> bool:
    """Notify the operator that today's skeleton was created."""
    from app.alerts.notify import send_operator_notification

    msg = (
        "📋 KAI Daily Strategy Review — Skelett für heute angelegt.\n"
        f"Datum: {today.isoformat()}\n"
        f"Pfad: {path.as_posix()}\n"
        "Öffne eine Claude-Session, damit die 6 Pflicht-Sektionen gefüllt werden."
    )
    return await send_operator_notification(msg)


async def _ping_reminder(today: date, path: Path, kind: str, detail: str) -> bool:
    """Send a reminder when today's review is missing or stub-only."""
    from app.alerts.notify import send_operator_notification

    msg = (
        f"⏰ KAI Daily Strategy Reminder — {kind}\n"
        f"Datum: {today.isoformat()}\n"
        f"Pfad: {path.as_posix()}\n"
        f"{detail}\n"
        "Skill `daily-strategy-review` ausführen."
    )
    return await send_operator_notification(msg)


@daily_strategy_app.command("reminder")
def cmd_reminder(
    notify: bool = typer.Option(
        True,
        "--notify/--no-notify",
        help="Send a Telegram reminder when the review is missing or stub-only.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Send the reminder even if one was already sent today.",
    ),
) -> None:
    """V2: nudge the operator if today's review is missing or unfilled.

    Idempotent per day — once a reminder has been sent, a marker file
    `artifacts/daily_strategy/.reminder_sent_<YYYY-MM-DD>` keeps subsequent
    invocations silent. Use ``--force`` to bypass the marker (e.g. after
    operator deletes the marker manually).

    Exit codes:
      0 — review present and filled, no reminder needed
      1 — review present but stub-only, reminder sent
      2 — review missing entirely, reminder sent
    """
    today = datetime.now(UTC).date()
    path = _today_path(today)
    marker = _reminder_marker_path(today)
    _daily_dir().mkdir(parents=True, exist_ok=True)

    if marker.exists() and not force:
        typer.echo(f"reminder already sent today: {marker}")
        raise typer.Exit(code=0)

    if not path.exists():
        kind = "Skeleton fehlt komplett"
        detail = "Der Bootstrap lief heute nicht. `trading-bot daily-strategy bootstrap` ausführen."
        exit_code = 2
    else:
        text = path.read_text(encoding="utf-8")
        stub_count = _stub_section_count(text)
        if stub_count == 0:
            typer.echo(f"review filled: {path} ({len(text)} bytes)")
            raise typer.Exit(code=0)
        kind = f"{stub_count} Sektion(en) leer"
        detail = (
            f"Skeleton seit Bootstrap mit {stub_count} unausgefüllten "
            f"Stub-Markern. Claude-Session öffnen."
        )
        exit_code = 1

    typer.echo(f"reminder needed: {kind}")
    if notify:
        try:
            ok = asyncio.run(_ping_reminder(today, path, kind, detail))
            typer.echo(f"telegram reminder: {'ok' if ok else 'disabled_or_failed'}")
        except Exception as exc:  # pragma: no cover — best-effort
            typer.echo(f"telegram reminder: error ({exc})")

    # Mark sent regardless of telegram outcome — dedup-by-attempt, not by
    # delivery. If telegram is broken, the operator sees CLI output anyway
    # (cron stdout / journalctl).
    marker.write_text(
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "kind": kind,
                "review_path": path.as_posix(),
                "review_exists": path.exists(),
            }
        ),
        encoding="utf-8",
    )
    raise typer.Exit(code=exit_code)


__all__ = ["daily_strategy_app"]
