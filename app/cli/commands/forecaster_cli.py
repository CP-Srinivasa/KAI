"""Operator-CLI für das CORE8-Forecaster-Panel (Shadow-Epoche, #647).

Registriert auf ``trading_app`` (Side-Module-Muster wie ``quote_evals_cli``),
damit weder ``trading.py`` noch der God-File wächst. Die Engine selbst lebt in
``app/research/forecaster_panel.py`` und bleibt hier unangetastet — insbesondere
gilt weiter: ``p_kai`` ist IMMER ``null``, jeder Record trägt ``sealed: false``.

Timer-Semantik (kai-forecaster-issue.timer, ``Persistent=true``):

* Ein Doppel-Feuern desselben Tages ist ein **No-op mit Exit 0** — sonst würde
  jeder Catch-up-Lauf nach Reboot eine failed Unit produzieren.
* Ein t0 ohne abgeschlossene Tageskerze (heute/Zukunft, UTC) wird mit Exit 2
  verweigert, bevor irgendetwas geschrieben wird (fail-closed).
* Provider-/Netzfehler → Exit 1; die Engine garantiert, dass dann keine
  partielle Zeile im Store liegt (ein verpasster Slot ist auf Ops-Ebene ein
  ``MISSED_ISSUANCE`` und wird von ``forecaster-status`` als Lücke gezählt).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import typer

from app.cli.commands.trading import console, trading_app
from app.research.forecaster_panel import (
    DEFAULT_STORE_DIR,
    build_binance_daily_provider,
    issue_panel,
    panel_status,
    read_panels,
    resolve_due,
    verify_panel_chain,
)
from app.research.forecaster_resolvers import DailyKlinesProvider

_STORE_OPT = typer.Option(str(DEFAULT_STORE_DIR), "--store-dir", help="Panel-Store-Verzeichnis")


def _build_provider() -> DailyKlinesProvider:
    """Indirektion für Tests: Produktionspfad = read-only Binance-Daily-Klines."""
    return build_binance_daily_provider()


def _today_utc() -> date:
    return datetime.now(UTC).date()


@trading_app.command("forecaster-issue")
def trading_forecaster_issue(
    t0: str | None = typer.Option(
        None, "--t0", help="Anker-Tag YYYY-MM-DD (UTC-Tageskerze); Default: gestern (UTC)"
    ),
    store_dir: str = _STORE_OPT,
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
) -> None:
    """CORE8-Shadow-Panel für t0 ausgeben (append-only, hash-verkettet, p_kai=null)."""
    anchor = date.fromisoformat(t0) if t0 else _today_utc() - timedelta(days=1)
    if anchor >= _today_utc():
        console.print(
            f"[red]forecaster-issue:[/red] t0={anchor.isoformat()} hat noch keine"
            " abgeschlossene UTC-Tageskerze — nichts geschrieben (fail-closed)."
        )
        raise typer.Exit(2)
    try:
        record = issue_panel(anchor, _build_provider(), store_dir=Path(store_dir))
    except ValueError as exc:
        if "already issued" in str(exc):
            # Timer-Idempotenz: Persistent=true kann doppelt feuern; der Store
            # ist append-only und unverändert — Erfolg, kein failed-unit-Alarm.
            console.print(f"forecaster-issue: Panel für t0={anchor.isoformat()} bereits im Store.")
            return
        console.print(f"[red]forecaster-issue:[/red] Store-Integritätsfehler: {exc}")
        raise typer.Exit(2) from exc
    except Exception as exc:  # noqa: BLE001 — Provider-/Netzfehler: laut, Exit 1
        console.print(f"[red]forecaster-issue:[/red] Provider-Fehler, nichts geschrieben: {exc}")
        raise typer.Exit(1) from exc

    invalid = sum(1 for q in record.payload["questions"] if q["status"] != "ISSUED")
    if as_json:
        print(
            json.dumps(
                {
                    "panel_index": record.panel_index,
                    "reference_observation_id": record.reference_observation_id,
                    "panel_hash": record.panel_hash,
                    "invalid_at_issuance": invalid,
                    "store_dir": store_dir,
                },
                indent=2,
            )
        )
        return
    console.print(
        f"forecaster-issue: t0={record.reference_observation_id}"
        f" panel_index={record.panel_index} invalid_at_issuance={invalid}"
    )


@trading_app.command("forecaster-resolve")
def trading_forecaster_resolve(
    store_dir: str = _STORE_OPT,
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
) -> None:
    """Fällige CORE8-Fragen auflösen (idempotent; Provider-Fehler ⇒ Panel bleibt pending)."""
    try:
        written = resolve_due(datetime.now(UTC), _build_provider(), store_dir=Path(store_dir))
    except Exception as exc:  # noqa: BLE001 — defensiv: Engine ist selbst fail-closed
        console.print(f"[red]forecaster-resolve:[/red] {exc}")
        raise typer.Exit(1) from exc
    by_status: dict[str, int] = {}
    for row in written:
        key = str(row.get("status"))
        by_status[key] = by_status.get(key, 0) + 1
    if as_json:
        print(json.dumps({"written": len(written), "by_status": by_status}, indent=2))
        return
    console.print(f"forecaster-resolve: written={len(written)} by_status={by_status}")


@trading_app.command("forecaster-status")
def trading_forecaster_status(
    store_dir: str = _STORE_OPT,
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
) -> None:
    """Zähler je Frage + t0-Abdeckung (Lücken = MISSED_ISSUANCE auf Ops-Ebene)."""
    status = panel_status(store_dir=Path(store_dir))

    t0_days: list[date] = []
    for panel in read_panels(Path(store_dir)):
        ref = panel.get("reference_observation_id")
        if isinstance(ref, str):
            try:
                t0_days.append(date.fromisoformat(ref))
            except ValueError:
                continue
    if t0_days:
        first, last = min(t0_days), max(t0_days)
        expected = (last - first).days + 1
        status["t0_first"] = first.isoformat()
        status["t0_last"] = last.isoformat()
        status["t0_missing_days"] = expected - len(set(t0_days))
    else:
        status["t0_first"] = None
        status["t0_last"] = None
        status["t0_missing_days"] = 0

    if as_json:
        print(json.dumps(status, indent=2))
        return
    console.print(
        f"forecaster-status: panels={status['panels']} resolutions={status['resolutions']}"
        f" t0=[{status['t0_first']}..{status['t0_last']}]"
        f" missing_days={status['t0_missing_days']}"
    )
    for qid, row in status["questions"].items():
        console.print(f"  {qid}: {row}")


@trading_app.command("forecaster-verify")
def trading_forecaster_verify(store_dir: str = _STORE_OPT) -> None:
    """Hash-Kette + Index-Monotonie des Panel-Stores prüfen (Exit 1 bei Bruch)."""
    errors = verify_panel_chain(Path(store_dir))
    if errors:
        for err in errors:
            console.print(f"[red]forecaster-verify:[/red] {err}")
        raise typer.Exit(1)
    console.print("forecaster-verify: OK — Kette intakt.")
