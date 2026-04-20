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
from datetime import UTC, date, datetime
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
    return _count_jsonl_lines(Path("artifacts/paper_execution_audit.jsonl"))


def _days_until(target: date, today: date) -> int:
    return (target - today).days


def _gate_status_line(directional: int, paper_fills: int) -> str:
    alert_gate = (
        "✅ Gate ≥200 erreicht"
        if directional >= 200
        else f"❌ {directional}/200 resolved (noch {200 - directional})"
    )
    fill_gate = (
        "✅ Gate ≥10 Fills erreicht"
        if paper_fills >= 10
        else f"❌ {paper_fills}/10 paper-fills"
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
        f"{precision_pct:.1f}% ({res['hit']}/{directional})"
        if precision_pct is not None
        else "—"
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
| Paper-Execution-Audit-Einträge | {paper_fills} |
| Tage bis TV-Pivot Re-Entry | {d_reentry} |
| Tage bis Pi-Migration | {d_pi} |
| Tage bis Multi-Agent-Gate | {d_gate} |

**Re-Entry-Gate (2026-05-16):** ≥200 resolved directional alerts ODER ≥10 Paper-Fills mit PnL.
Aktueller Status: {_gate_status_line(directional, paper_fills)}

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
) -> None:
    """Write today's skeleton if missing. Idempotent by default."""
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


__all__ = ["daily_strategy_app"]
