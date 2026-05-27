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


def _blocked_alerts_summary(
    today: date,
    lookback_hours: int = 24,
    *,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """Summarize blocked_alerts.jsonl for the last *lookback_hours*.

    F4 (Dispatch-Observability) — Operator-Pflicht-Sektion in der Daily-
    Strategy: zeigt was der Eligibility-Gate aussortiert hat, damit
    Re-Calibration-Decisions auf Empirie statt auf alert_audit-Tunnelblick
    aufsetzen. Sentiment-Drift-Forensik 2026-05-24: alert_audit zeigt nur
    ~4% des Klassifikator-Outputs; ohne diese Sicht ist der Operator blind
    auf 96% des Material-Flows.

    The window anchors at the actual run-time (``now_utc`` arg or
    ``datetime.now(UTC)``), NOT at today's end-of-day. Anchoring at
    end-of-day made the window extend into the future, so a run at
    08:34 UTC missed every block written between yesterday 23:59 and
    08:34 today — exactly the rolling slice the operator needs to see.
    ``now_utc`` is exposed for tests; the ``today`` arg is kept for the
    legacy call-site and the file-date suffix in the section header.

    Returns {
        'total': int,
        'top_reasons': [(reason, count), ...],   # max 3
        'top_blocked': [{...}, ...],             # max 3 raw records
        'window_start': iso str,
        'window_end': iso str,
    }
    """
    window_end = now_utc if now_utc is not None else datetime.now(UTC)
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


def _build_skeleton(
    today: date,
    sync_success: int | None = None,
    sync_total: int | None = None,
) -> str:
    res = _resolved_directional_count()
    directional = res["hit"] + res["miss"]
    precision_pct: float | None = None
    if directional > 0:
        precision_pct = 100.0 * res["hit"] / directional

    # Operator pin 2026-05-26: the raw alert_outcomes count buries
    # resolved outcomes under repeated inconclusive rows from the
    # Multi-Window annotator. Show both raw and latest-per-document
    # so re-entry decisions use the deduped baseline.
    try:
        from app.observability.outcome_dedupe_report import build_outcome_dedupe_report

        dedupe_report = build_outcome_dedupe_report()
    except Exception:  # pragma: no cover — best-effort metric
        dedupe_report = None

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

    deduped_line = "—"
    if dedupe_report is not None:
        deduped_line = (
            f"{dedupe_report.deduped_precision_str} "
            f"(raw {dedupe_report.raw_precision_str}; "
            f"dropped {dedupe_report.dropped_inconclusive_dupes} duplicate inconclusives)"
        )

    # Sync-Health-Banner: prominent at the top when the operator MUST know that
    # Live-Metriken are based on stale Workstation snapshots. Skipped entirely
    # when sync was not attempted (sync_total is None) or fully successful.
    stale_banner = ""
    if sync_total is not None and sync_success is not None and sync_success < sync_total:
        stale_banner = (
            f"\n> ⚠️ **STALE-DATA-WARNING** (sync {sync_success}/{sync_total}): Pi-Sync ist heute"
            " (zumindest teilweise) fehlgeschlagen. Die untenstehenden Live-Metriken stammen aus"
            f" Workstation-Artifacts (Stand: lokales mtime der JSONLs, nicht heute). **Vor jeder"
            " Decision auf Pi-Side verifizieren.** Fehlerbehebung: `daily-strategy sync` manuell"
            " mit Verbose-Logs, ggf. SSH-Pfad prüfen.\n"
        )

    return f"""# KAI Daily Strategy Review — {today.isoformat()}

Erstellt: {today.isoformat()} · automatischer Skelett-Bootstrap (CLI: `daily-strategy bootstrap`)
Format: 6 Pflicht-Sektionen nach CLAUDE.md §7.
Horizont-Anker: {horizon_line}
{stale_banner}
---

## Live-Metriken (deterministisch, Stand {datetime.now(UTC).isoformat()})

| Metrik | Wert |
|---|---|
| Resolved directional alerts | {directional} (hit {res["hit"]} / miss {res["miss"]}) |
| Baseline-Precision | {precision_line} |
| Deduped Precision (latest per document_id) | {deduped_line} |
| TV pending events (unpromoted) | {tv_pending} |
| Paper-Trading abgeschlossene Trades | {paper_fills} |
| Tage bis TV-Pivot Re-Entry | {d_reentry} |
| Tage bis Pi-Migration | {d_pi} |
| Tage bis Multi-Agent-Gate | {d_gate} |

**Re-Entry-Gate (2026-05-16):** ≥200 resolved directional alerts ODER ≥10 Paper-Fills mit PnL.
Aktueller Status: {_gate_status_line(directional, paper_fills)}

---

{_format_dispatch_health_section(_blocked_alerts_summary(today, now_utc=datetime.now(UTC)))}
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


_SYNC_FILES = (
    "artifacts/alert_outcomes.jsonl",
    "artifacts/paper_execution_audit.jsonl",
    "artifacts/alert_audit.jsonl",
    "artifacts/tradingview_pending_signals.jsonl",
    "artifacts/trading_loop_audit.jsonl",
    "artifacts/blocked_alerts.jsonl",
    # Regime snapshots — without these the workstation lookup returns
    # `no_snapshot_file` for every cycle while the Pi has hourly
    # snapshots. Daily-strategy needs them for the regime-context audit.
    "artifacts/regime_state/btc_regime.jsonl",
    "artifacts/regime_state/eth_regime.jsonl",
)

_SYNC_DEFAULT_REMOTE = "ubuntu@192.168.178.23"
_SYNC_DEFAULT_REMOTE_DIR = "/home/kai/ai_analyst_trading_bot"


def _sync_artifacts(remote: str, remote_dir: str) -> tuple[int, int]:
    """Run scp for every file in _SYNC_FILES; return (success, total).

    Returns the count of successfully transferred files and the total count.
    Used by both ``cmd_sync`` (CLI) and ``cmd_bootstrap`` (for STALE-banner
    detection). All output goes through ``typer.echo`` so cron/tee captures it.
    """
    import os
    import subprocess

    typer.echo(f"Syncing artifacts from Pi ({remote}) to mitigate sync-lag...")

    # On Windows Git-Bash / MSYS the absolute remote path is otherwise rewritten
    # to a Windows path (e.g. /home/kai/... -> C:/Program Files/Git/home/kai/...).
    # Setting MSYS_NO_PATHCONV=1 disables this conversion. Harmless on Linux.
    sync_env = {**os.environ, "MSYS_NO_PATHCONV": "1"}

    scp_cmd = "scp"
    success = 0
    for f in _SYNC_FILES:
        local_path = Path(f)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        remote_src = f"{remote}:{remote_dir}/{f}"

        typer.echo(f"  Fetching {f}...")
        try:
            res = subprocess.run(
                [scp_cmd, remote_src, str(local_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                env=sync_env,
            )
            if res.returncode == 0:
                typer.echo(f"    [OK] {f} synced successfully.")
                success += 1
            else:
                typer.echo(f"    [SKIP] Could not sync {f}: {res.stderr.strip()}")
        except Exception as exc:
            typer.echo(f"    [SKIP] Failed to fetch {f}: {exc}")

    typer.echo(f"\nSync complete. {success}/{len(_SYNC_FILES)} files updated from Pi.")
    return success, len(_SYNC_FILES)


@daily_strategy_app.command("sync")
def cmd_sync(
    remote: str = typer.Option(
        _SYNC_DEFAULT_REMOTE,
        "--remote",
        help=(
            "SSH connection string for the remote Pi. Default is the LAN path"
            " (ubuntu@192.168.178.23). Cloudflare-Tunnel hostname kai-trader.org"
            " has only HTTPS ingress, no SSH/TCP route — scp via Tunnel will time"
            " out on banner exchange."
        ),
    ),
    remote_dir: str = typer.Option(
        _SYNC_DEFAULT_REMOTE_DIR,
        "--remote-dir",
        help="Remote directory path on the Pi (/home/kai is a symlink to /home/ubuntu).",
    ),
) -> None:
    """Sync operational artifacts (JSONLs) from Pi to laptop to mitigate sync-lag.

    Exit codes:
      0 — all files synced successfully
      2 — at least one file failed (operator should investigate before
          treating downstream metrics as live)
    """
    success, total = _sync_artifacts(remote, remote_dir)
    if success < total:
        raise typer.Exit(code=2)


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
        _SYNC_DEFAULT_REMOTE,
        "--remote",
        help="SSH connection string for the remote Pi.",
    ),
    remote_dir: str = typer.Option(
        _SYNC_DEFAULT_REMOTE_DIR,
        "--remote-dir",
        help="Remote directory path on the Pi.",
    ),
) -> None:
    """Write today's skeleton if missing. Idempotent by default.

    Exit codes:
      0 — skeleton present (already or freshly written) and sync was healthy
      2 — sync failed wholesale (0/N files) — skeleton was still written but
          carries a STALE-DATA banner; downstream metrics are NOT live
    """
    sync_success: int | None = None
    sync_total: int | None = None
    if sync:
        try:
            sync_success, sync_total = _sync_artifacts(remote, remote_dir)
        except Exception as exc:
            typer.echo(f"Warning: Sync failed, continuing with local data. ({exc})")
            sync_success, sync_total = 0, len(_SYNC_FILES)

    today = datetime.now(UTC).date()
    path = _today_path(today)
    _daily_dir().mkdir(parents=True, exist_ok=True)

    if path.exists() and not force:
        typer.echo(f"already present: {path}")
        if sync_success is not None and sync_total and sync_success < sync_total:
            typer.echo(
                f"WARNING: sync was {sync_success}/{sync_total} — existing skeleton may be"
                " based on stale data; check banner inside the file."
            )
            raise typer.Exit(code=2)
        raise typer.Exit(code=0)

    content = _build_skeleton(today, sync_success=sync_success, sync_total=sync_total)
    path.write_text(content, encoding="utf-8")
    typer.echo(f"wrote skeleton: {path}")

    if notify:
        try:
            ok = asyncio.run(_ping_operator(today, path, sync_success, sync_total))
            typer.echo(f"telegram ping: {'ok' if ok else 'disabled_or_failed'}")
        except Exception as exc:  # pragma: no cover — ping is best-effort
            typer.echo(f"telegram ping: error ({exc})")

    if sync_success is not None and sync_total and sync_success < sync_total:
        raise typer.Exit(code=2)


async def _ping_operator(
    today: date,
    path: Path,
    sync_success: int | None = None,
    sync_total: int | None = None,
) -> bool:
    """Notify the operator that today's skeleton was created.

    When sync was partially or fully unhealthy, the message includes a STALE
    marker so the operator does not silently consume yesterday's metrics.
    """
    from app.alerts.notify import send_operator_notification

    stale_line = ""
    if sync_total is not None and sync_success is not None and sync_success < sync_total:
        stale_line = (
            f"\n⚠️ STALE: Pi-Sync {sync_success}/{sync_total} — Metriken sind nicht live."
        )

    msg = (
        "📋 KAI Daily Strategy Review — Skelett für heute angelegt."
        f"{stale_line}\n"
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
