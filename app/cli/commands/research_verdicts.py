"""Operator CLI: mechanical prereg verdicts, maturity tracking, verdict anchoring.

Registers on the existing ``trading`` sub-app (imported at the bottom of
``trading.py``, same pattern as ``truth_compliance``) so the CLI god-file stays
untouched. Everything here is read/append-only research tooling — no order, no
capital movement, no gate is weakened.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from app.cli.commands.trading import console, trading_app
from app.research.prereg_ledger import DEFAULT_PREREG_LEDGER_PATH


def _render_maturity_state(row: dict[str, Any]) -> str:
    """Render a maturity state without echoing untrusted verdict prose."""
    raw = str(row.get("state"))
    if raw == "RESOLVED":
        resolution = row.get("resolution")
        safe = resolution if isinstance(resolution, dict) else {}
        verdict_class = str(safe.get("verdict_class") or "TERMINAL")
        seq = safe.get("seq")
        seq_render = f", Truth-seq {seq}" if isinstance(seq, int) else ""
        if verdict_class == "CLOSED_NO_VERDICT":
            # Beendet ist nicht dasselbe wie beurteilt. Ohne diesen Zusatz liest
            # sich ein Abschluss aus Unmessbarkeit oder Fristablauf wie ein
            # Ergebnis — der Fehler, den die Prä-Reg-Doktrin gerade verhindert.
            return (
                f"[cyan]ABGESCHLOSSEN OHNE SACHVERDIKT{seq_render}; "
                "weder bestanden noch widerlegt, keine neue Auswertung[/cyan]"
            )
        return f"[cyan]ABGESCHLOSSEN — {verdict_class}{seq_render}; keine neue Auswertung[/cyan]"
    if raw == "RESOLUTION_HOLD":
        resolution = row.get("resolution")
        safe = resolution if isinstance(resolution, dict) else {}
        status = str(safe.get("status") or "unknown")
        return (
            "[red]HOLD — Resolution-Evidenz "
            f"{status}; Truth-Kette prüfen, keine neue Auswertung[/red]"
        )
    renders = {
        "NOT_DUE": "reift",
        "VERDICT_UNATTESTED": (
            "[yellow]VERDIKT OFF-CHAIN — in die Truth-Kette attestieren; "
            "KEINE neue Auswertung[/yellow]"
        ),
        "EVAL_CHECK_DUE": (
            "[yellow]PROXY-ZIEL ERREICHT — exakten Eval fahren; KEIN Verdikt aus Proxy[/yellow]"
        ),
        "JUDGEABLE": "[green]URTEILBAR — Verdikt-Kette fahren (prereg-check --report)[/green]",
        # Kein Zaehler und kein Verdikt, aber ein Eigentuemer mit Termin. Ohne
        # eigene Zeile las sich das wie ein roher Zustandsname.
        "SUPERVISED": (
            "[cyan]UNTER OPERATOR-AUFSICHT — Termin im Register "
            "(config/prereg_supervision.json); KEINE Aufsichtsluecke[/cyan]"
        ),
    }
    return renders.get(raw, raw)


@trading_app.command("prereg-check")
def trading_prereg_check(
    prereg_id: str = typer.Option(..., "--prereg-id", help="Registered claim to judge"),
    from_json: str = typer.Option(
        ..., "--from-json", help="Evaluator JSON output file (news-eval --json > f.json)"
    ),
    report: bool = typer.Option(
        False, "--report", help="Also write the attested verdict report (recommended)"
    ),
    out_dir: str = typer.Option(
        "artifacts/research/verdicts", "--out-dir", help="Report output directory"
    ),
    ledger_path: str = typer.Option(
        str(DEFAULT_PREREG_LEDGER_PATH), "--ledger-path", help="Pre-registration ledger JSONL"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
) -> None:
    """Mechanically judge an evaluator result against a REGISTERED gate (ADR 0012).

    Closes the human-transcription gap: the pass bar comes from the ledger (fixed
    before data, hashed into the ``prereg_id``), the numbers come from the
    evaluator's JSON, PASS/FAIL is computed — never read off a terminal. With
    ``--report`` the machine verdict is written as an attested report in one step.
    Exit 0 = judged (PASS or FAIL), 2 = not judgeable (unknown id / no gate /
    malformed input).
    """
    from datetime import UTC, datetime

    from app.research.prereg_gate import check_gate
    from app.research.prereg_ledger import PreRegistrationLedger
    from app.research.verdict_report import (
        build_verdict_report,
        resolve_code_version,
        write_verdict_report,
    )

    entries = [
        e for e in PreRegistrationLedger(Path(ledger_path)).entries() if e.prereg_id == prereg_id
    ]
    if not entries:
        console.print(f"[red]prereg-check:[/red] unknown prereg_id {prereg_id!r}")
        raise typer.Exit(2)
    entry = entries[-1]
    if not entry.gate:
        console.print(
            f"[red]prereg-check:[/red] claim {prereg_id!r} ({entry.name}) is a "
            "free-text-era registration without a machine-readable gate — judge it "
            "manually against its success_criteria and use `trading verdict-report`."
        )
        raise typer.Exit(2)

    src = Path(from_json)
    if not src.is_file():
        console.print(f"[red]prereg-check:[/red] no such file: {from_json}")
        raise typer.Exit(2)
    try:
        eval_result = json.loads(src.read_text(encoding="utf-8"))
    except ValueError as exc:
        console.print(f"[red]prereg-check:[/red] invalid JSON: {exc}")
        raise typer.Exit(2) from exc

    outcome = check_gate(entry.gate, eval_result)

    report_paths: dict[str, str] = {}
    if report:
        rep = build_verdict_report(
            eval_result,
            hypothesis=entry.name,
            prereg_id=entry.prereg_id,
            verdict=outcome["verdict"],
            params={"gate": entry.gate, "checks": outcome["checks"], "source": str(src)},
            code_version=resolve_code_version(),
            generated_at=datetime.now(UTC),
        )
        json_path, md_path = write_verdict_report(rep, Path(out_dir))
        report_paths = {
            "report_json": str(json_path),
            "report_md": str(md_path),
            "attestation_hash": rep["attestation"]["hash"],
        }

    if as_json:
        print(json.dumps({**outcome, "prereg_id": prereg_id, **report_paths}, indent=2))
        return

    color = "green" if outcome["passed"] else "red"
    console.print(f"[{color}]{outcome['verdict']}[/{color}]  ({entry.name} / {prereg_id})")
    for c in outcome["checks"]:
        mark = "✅" if c["ok"] else "❌"
        console.print(f"  {mark} {c['name']}: required={c['required']} actual={c['actual']}")
    for k, v in report_paths.items():
        console.print(f"{k}: {v}")


@trading_app.command("prereg-maturity")
def trading_prereg_maturity(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
    notify: bool = typer.Option(
        False,
        "--notify",
        help="Fällige Prä-Regs zusätzlich an den Operator-Kanal senden (Telegram).",
    ),
) -> None:
    """Count out-of-sample cohorts of open pre-registrations; flag DUE ones.

    Read-only (document store only); the count is an upper-bound proxy — DUE means
    "run the eval now", never "the claim passed". Wired to a weekly systemd timer
    (``kai-prereg-maturity.timer``) so maturation is infrastructure, not memory.

    ``--notify`` schickt fällige Zeilen an den Operator-Kanal. Ohne das Flag
    landete die Fälligkeit ausschließlich im Journal — korrekt berechnet und
    trotzdem wirkungslos, solange niemand hineinsieht.
    """
    import asyncio

    from app.core.settings import get_settings
    from app.research.prereg_maturity import build_maturity_alert, compute_maturity
    from app.storage.db.session import build_session_factory

    async def _run() -> list[dict[str, Any]]:
        factory = build_session_factory(get_settings().db)
        async with factory() as session:
            return await compute_maturity(session)

    rows = asyncio.run(_run())

    if notify:
        # Vor dem Rendern senden: ein Renderfehler darf die Frist nicht
        # verschlucken. Ein Versandfehler wird sichtbar gemeldet, faerbt die
        # Unit aber nicht rot — sonst wuerde ein Netzausfall wie eine
        # verpasste Frist aussehen.
        alert = build_maturity_alert(rows)
        if alert:
            from app.alerts.notify import send_operator_notification

            if not asyncio.run(send_operator_notification(alert)):
                console.print(
                    "[yellow]Prä-Reg fällig, aber Operator-Kanal nicht erreicht "
                    "— Zeilen stehen unten im Journal.[/yellow]"
                )

    if as_json:
        print(json.dumps(rows, indent=2))
        return
    for r in rows:
        state = _render_maturity_state(r)
        pid = r.get("prereg_id") or "ohne-prereg-id"
        # Woher der Zustand stammt, gehört SICHTBAR in die Zeile: eine exakte
        # Messung schlägt den Proxy, und nur sie darf ein Verdikt tragen.
        n_exact = r.get("n_exact")
        if r.get("kind") == "deadline":
            # Fensterbasierte Prä-Reg: Reife ist ein Datum, kein n.
            ps = r.get("per_source") or {}
            n_render = f"Fenster bis {ps.get('window_end_utc')} (Rest {ps.get('days_remaining')}d)"
        elif n_exact is None:
            n_render = f"n≈{r['n_proxy']}/{r['n_target']} (Proxy, Obergrenze)"
        else:
            n_render = f"n={n_exact}/{r['n_target']} (EXAKT; Proxy≈{r['n_proxy']})"
        # Vermerk (z. B. Confounder-Pflicht) gehört in die Zeile, nicht ins
        # Operator-Gedächtnis.
        note = r.get("note")
        note_render = f" — {note}" if note else ""
        # Runde Klammern: eckige wuerde rich als Markup-Tag schlucken —
        # die versiegelte ID verschwand dann aus der Konsole (Smoke 07-30).
        console.print(
            f"{r['name']} ({pid}): {n_render} (seit {r['since_utc']}) {r['per_source']}"
            f" → {state}{note_render}"
        )


def _reconciliation_root_for(ledger_file: Path) -> Path:
    """Artefakt-Wurzel zu einem abweichenden ``--ledger-path`` ableiten.

    Kanonisch liegt das Ledger unter ``<artifacts>/research/prereg_ledger.jsonl``;
    dann ist die Wurzel zwei Ebenen hoeher. Jede andere Ablage (Tests, Kopien)
    bekommt ihr Elternverzeichnis — Truth-Kette und Seitenablagen fehlen dort
    dann schlicht, und der Zustand faellt ehrlich auf WATCHED/UNWATCHED zurueck.
    """
    if ledger_file.parent.name == "research":
        return ledger_file.parent.parent
    return ledger_file.parent


@trading_app.command("k1-inbox-count")
def trading_k1_inbox_count(
    rows_file: str = typer.Option(
        ..., "--rows", help="Anonymisierte Posteingangszeilen (pipe-getrennt, siehe Modul-Doku)"
    ),
    json_out: bool = typer.Option(False, "--json", help="Rohreport als JSON"),
) -> None:
    """K1 00c75a76 mechanisch auszaehlen — der Evaluator entscheidet, nicht das Lesen.

    Erwartet je Zeile:
    ``datum_utc | richtung | absenderklasse | betreff | antwort | thread_id | qualifiziert``
    plus optional eine achte Spalte ``zahlungsabsicht``. Keine Mailtexte, keine
    Adressen, keine Namen — der Zaehler braucht sie nicht.

    Rechnet BEIDE belegbaren Seal-Fenster getrennt und meldet, ob sie im Verdikt
    uebereinstimmen. Faellt eine Zeile durch, bricht der Lauf ab, statt sie als
    "zaehlt nicht" zu verbuchen.
    """
    from app.research.k1_inbox_count import K1CountError, count_rows, parse_rows

    path = Path(rows_file)
    if not path.exists():
        console.print(f"[red]k1-inbox-count:[/red] keine Datei: {rows_file}")
        raise typer.Exit(code=2)
    try:
        report = count_rows(parse_rows(path.read_text(encoding="utf-8")))
    except K1CountError as exc:
        console.print(f"[red]k1-inbox-count:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if json_out:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    for key, win in sorted(report["windows"].items()):
        color = "green" if win["VERDICT"] == "MET" else "red"
        console.print(
            f"Fenster ab {key}: SEALED_COUNT={win['SEALED_COUNT']}/{win['THRESHOLD']} "
            f"-> [{color}]{win['VERDICT']}[/{color}]  "
            f"(inbound {win['INBOUND_MESSAGES_TOTAL']}, Threads "
            f"{win['DISTINCT_QUALIFIED_THREADS']}, davon Antwort auf eigene Ansprache "
            f"{win['QUALIFIED_RESPONSES_TO_OWN_OUTREACH']})"
        )
    if not report["windows_agree_on_verdict"]:
        console.print(
            "[yellow]Die beiden Seal-Fenster kommen zu VERSCHIEDENEN Verdikten — "
            "die Fensterlesart ist damit eine Entscheidung und gehoert dokumentiert, "
            "bevor ein Verdikt attestiert wird.[/yellow]"
        )
    for conflict in report["conflicts"]:
        console.print(f"[yellow]Widerspruch:[/yellow] {conflict}")
    console.print(f"[dim]{report['distinct_contacts_note']}[/dim]")


@trading_app.command("runtime-provenance")
def trading_runtime_provenance(
    expected_sha: str = typer.Option(
        "", "--expected-sha", help="Erwartete Revision (leer: HEAD des Checkouts)"
    ),
    repo: str = typer.Option(".", "--repo", help="Checkout-Wurzel"),
    json_out: bool = typer.Option(False, "--json", help="Rohurteil als JSON"),
) -> None:
    """ONE_PRODUCTION_REVISION: laeuft jeder Dienst auf dem behaupteten Stand?

    Misst vom laufenden PROZESS aus (``/proc/<pid>``), nicht von der Unit-Datei:
    ``ExecStart`` sagt, womit gestartet werden SOLLTE, ``/proc`` sagt, was
    laeuft — inklusive aufgeloester Symlinks.

    Exit 0 = OK · Exit 10 = HOLD (Code- oder Abhaengigkeits-Drift).
    """
    import subprocess

    from app.observability.runtime_provenance import (
        DEFAULT_MARKER_RELPATH,
        collect_runtime_services,
        evaluate_provenance,
        read_marker,
        render_verdict,
        sha256_of,
    )

    root = Path(repo).resolve()
    head = (
        expected_sha
        or subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    )

    verdict = evaluate_provenance(
        collect_runtime_services(),
        expected_sha=head,
        marker=read_marker(root / DEFAULT_MARKER_RELPATH),
        checkout_sha=head,
        checkout_lock_sha256=sha256_of(root / "requirements.lock"),
    )

    if json_out:
        from dataclasses import asdict

        print(json.dumps(asdict(verdict), indent=2, ensure_ascii=False))
    else:
        color = "green" if verdict.ok else "red"
        console.print(f"[{color}]{render_verdict(verdict)}[/{color}]")
    raise typer.Exit(code=0 if verdict.ok else 10)


@trading_app.command("prereg-list")
def trading_prereg_list(
    ledger_path: str = typer.Option(
        str(DEFAULT_PREREG_LEDGER_PATH), "--ledger-path", help="Pre-registration ledger JSONL"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of the table"),
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Artefakt-Wurzel fuer den Abgleich (Truth-Kette, Seitenablagen, Wachliste)",
    ),
) -> None:
    """List pre-registered hypotheses (read-only) mit Abgleichszustand.

    ``state`` je Zeile: RESOLVED (Truth-Kette) · VERDICT_UNATTESTED (Verdikt nur
    in der Seitenablage) · WATCHED (Reife-Spec) · UNWATCHED (Aufsichtsluecke).
    Dieselbe Zustandsfunktion wie Reifeblick und Health-Check — die Ledger-Sicht
    darf nie etwas anderes behaupten als der Reifeblick.
    """
    from app.research.prereg_ledger import PreRegistrationLedger
    from app.research.prereg_reconciliation import classify_ledger_entries

    ledger = PreRegistrationLedger(Path(ledger_path))
    entries = ledger.entries()
    # Der Abgleich liest das Ledger ueber die Artefakt-Wurzel; zeigt
    # --ledger-path woandershin, gilt die Zustandsfunktion fuer DIESE Datei.
    adir = Path(artifacts_dir)
    ledger_file = Path(ledger_path)
    if ledger_file.resolve() != (adir / "research" / "prereg_ledger.jsonl").resolve():
        adir = _reconciliation_root_for(ledger_file)
    states = {
        row["prereg_id"]: row
        for row in classify_ledger_entries(adir)
        if isinstance(row.get("prereg_id"), str)
    }

    def _with_state(e: Any) -> dict[str, Any]:
        raw: dict[str, Any] = json.loads(e.to_json())
        recon = states.get(e.prereg_id) or {}
        raw["state"] = recon.get("state", "UNWATCHED")
        raw["watched"] = bool(recon.get("watched", False))
        raw["verdict_class"] = recon.get("verdict_class")
        raw["offchain_verdicts"] = list(recon.get("offchain_verdicts") or [])
        return raw

    if as_json:
        print(json.dumps([_with_state(e) for e in entries], indent=2))
        if not entries:
            raise typer.Exit(1)
        return

    if not entries:
        console.print("[yellow]no pre-registrations recorded[/yellow]")
        raise typer.Exit(1)

    table = Table(
        title=f"Pre-registered hypotheses ({len(entries)} rows, {ledger.count()} distinct)"
    )
    table.add_column("prereg_id")
    table.add_column("state")
    table.add_column("name")
    table.add_column("dir")
    table.add_column("horizon")
    table.add_column("n_target", justify="right")
    table.add_column("created_at_utc")
    for e in entries:
        table.add_row(
            e.prereg_id,
            str((states.get(e.prereg_id) or {}).get("state", "UNWATCHED")),
            e.name,
            e.direction,
            e.horizon,
            str(e.sample_size_target),
            e.created_at_utc,
        )
    console.print(table)


@trading_app.command("alpha-budget")
def trading_alpha_budget(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
    ledger_path: str = typer.Option(
        str(DEFAULT_PREREG_LEDGER_PATH), "--ledger-path", help="Pre-registration ledger JSONL"
    ),
) -> None:
    """Familienweites alpha-Budget ueber alle registrierten Claims (NICHT gatend).

    ``prereg-check`` urteilt je Claim isoliert gegen dessen versiegeltes
    ``p_min`` - richtig, aber es beantwortet eine engere Frage als beim Lesen
    entsteht. Laufen m Claims parallel, ist die Chance auf mindestens einen
    Zufalls-PASS deutlich hoeher als das einzelne alpha. Diese Zahl stand
    nirgends.

    Aendert kein versiegeltes Kriterium und kippt kein Verdikt.
    """
    from app.research.alpha_budget import family_alpha_budget
    from app.research.prereg_maturity import load_attested_resolutions

    path = Path(ledger_path)
    claims: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except ValueError:
                continue  # eine kaputte Zeile darf die Auskunft nie sprengen
            if isinstance(parsed, dict):
                claims.append(parsed)

    # Terminal entschiedene Claims zählen in die Gesamtfamilie, nicht mehr ins
    # offene Budget. Fehlt/bricht die Truth-Kette, wird fail-closed NICHTS als
    # aufgelöst behandelt — das offene Budget ist dann die konservative Sicht.
    resolutions, error = load_attested_resolutions(path.parent.parent)
    resolved_ids = (
        set()
        if error
        else {pid for pid, res in resolutions.items() if res.get("status") == "resolved"}
    )

    report = family_alpha_budget(claims, resolved_ids=resolved_ids)
    if error:
        report["truth_ledger_error"] = error.get("status")

    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    console.print(
        f"registriert {report['m_registered']} · maschinell gatebar "
        f"{report['m_machine_gated']} · offen {report['m_open']}"
    )
    console.print(
        f"P(>=1 Falsch-PASS unter H0): gesamt "
        f"[yellow]{report['familywise_error_upper_bound']:.1%}[/yellow] · "
        f"offen [yellow]{report['familywise_error_open']:.1%}[/yellow]"
    )
    bar = report["bh_p_positive_for_next_pass"]
    if bar is not None:
        console.print(
            f"BH-Schranke für den ERSTEN PASS: p_positive >= [cyan]{bar:.4f}[/cyan] "
            f"(alpha {report['bh_threshold_for_next_pass']:.4f})"
        )
    if report["n_without_machine_gate"]:
        console.print(
            f"[dim]{report['n_without_machine_gate']} Claims ohne maschinelles p_min "
            f"(nicht BH-fähig, getrennt geführt): "
            f"{', '.join(report['claims_without_machine_gate'][:6])}"
            f"{' …' if report['n_without_machine_gate'] > 6 else ''}[/dim]"
        )
    console.print(f"[dim]{report['note']}[/dim]")


@trading_app.command("verdict-anchor")
def trading_verdict_anchor(
    json_path: str = typer.Option(
        ..., "--json-path", help="Attested verdict report (.json) to anchor"
    ),
) -> None:
    """Anchor a verdict report's attestation hash via the configured stamper (OTS).

    Verifies the report's attestation first (tamper check), then hands the hash to
    :func:`app.integrity.anchor.anchor_record_digest` (respects
    ``APP_INTEGRITY_ENABLED``; fail-soft). Exit 0 on anchored/recorded/disabled,
    1 on a verification failure or anchor error.
    """
    from app.core.integrity_settings import IntegritySettings
    from app.integrity.anchor import anchor_record_digest
    from app.truth.attestation import verify_attestation

    src = Path(json_path)
    if not src.is_file():
        console.print(f"[red]verdict-anchor:[/red] no such file: {json_path}")
        raise typer.Exit(1)
    try:
        report = json.loads(src.read_text(encoding="utf-8"))
        payload, attestation = report["payload"], report["attestation"]
    except (ValueError, KeyError) as exc:
        console.print(f"[red]verdict-anchor:[/red] not a verdict report: {exc}")
        raise typer.Exit(1) from exc
    if not verify_attestation(payload, attestation):
        console.print("[red]verdict-anchor:[/red] attestation does NOT verify — refusing")
        raise typer.Exit(1)

    result = anchor_record_digest(
        str(attestation["hash"]), settings=IntegritySettings(), prefix="newsverdict"
    )
    console.print(
        f"anchor state={result.state} digest={result.digest} "
        f"proof={getattr(result, 'proof_path', None)}"
    )
    if result.state == "error":
        raise typer.Exit(1)


@trading_app.command("runtime-marker-write")
def trading_runtime_marker_write(
    unit: str = typer.Option(
        ..., "--unit", help="Vollstaendiger Unit-Name, z. B. kai-server.service"
    ),
    repo: str = typer.Option(".", "--repo", help="Checkout-Wurzel"),
    json_out: bool = typer.Option(False, "--json", help="Marker als JSON ausgeben"),
) -> None:
    """Der Dienst bezeugt beim Start, welchen Code er geladen hat.

    Gedacht fuer ``ExecStartPost=`` — **nicht** ``ExecStartPre``: dort existiert
    die MainPID des Dienstes noch nicht, und ein Marker mit der PID des
    Vorbereitungsprozesses waere schlimmer als keiner, weil er wie ein Beweis
    aussaehe.

    ⚠ Ehrliche Grenze: die Revision wird hier gelesen, nicht aus dem Speicher des
    Hauptprozesses extrahiert. ``ExecStartPost`` laeuft Millisekunden nach dem
    Fork, also ist der Checkout-Stand praktisch sicher der geladene. Das
    Risikofenster sind Millisekunden statt — wie am 2026-09-01 — Stunden.

    Exit 0 = geschrieben · Exit 1 = MainPID nicht ermittelbar (kein Marker).
    """
    import json as _json
    import subprocess

    from app.observability.process_runtime_marker import (
        build_process_marker,
        current_boot_id,
        proc_start_ticks,
        write_process_marker,
    )
    from app.observability.runtime_provenance import sha256_of

    root = Path(repo).resolve()
    head = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    raw_pid = subprocess.run(  # noqa: S603
        ["systemctl", "show", unit, "-p", "MainPID", "--value"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    try:
        pid = int(raw_pid or 0)
    except ValueError:
        pid = 0
    if pid <= 0 or not head:
        console.print(
            f"[red]kein Marker fuer {unit}[/red]: MainPID={raw_pid!r} HEAD={head[:8]!r} — "
            "der Zustand bleibt UNKNOWN, und das ist die richtige Folge."
        )
        raise typer.Exit(code=1)

    import sys as _sys

    marker = build_process_marker(
        unit=unit,
        pid=pid,
        proc_start_ticks=proc_start_ticks(pid),
        boot_id=current_boot_id(),
        repo_root=str(root),
        runtime_code_sha=head,
        python_executable=_sys.executable,
        requirements_lock_sha256=sha256_of(root / "requirements.lock"),
        started_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    path = write_process_marker(marker, root=root)
    if json_out:
        console.print(_json.dumps(marker, indent=2, sort_keys=True))
    else:
        console.print(f"[green]{unit}[/green] bezeugt {head[:8]} (pid {pid}) -> {path}")
