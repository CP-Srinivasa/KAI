"""Der Health-Check muss melden, BEVOR er wegen veralteter Daten aussteigt.

Der Alarmpfad lag hinter dem Stale-Abbruch:

    if exit_on_stale and ... (report.data_sources_stale or not report.runs_on_pi):
        raise typer.Exit(code=2)      # <- hier war Schluss
    ...
    if notify or telegram_on_issue:   # <- Versand, nie erreicht

Die Unit läuft mit ``--exit-on-stale --telegram-on-issue``. Das heißt: der
Health-Check meldete nur, solange die Daten frisch waren — und schwieg genau
dann, wenn ein Schreiber gestorben war, also im einzigen Fall, für den er
existiert. Der Befund wurde korrekt berechnet und verließ die Maschine nie.

Das ist die zweite, tiefere Ursache des TV-Ingest-Ausfalls (02.–08.08.): selbst
mit dem Eingangsstrom in ``files_to_check`` hätte der Alarm nicht gefeuert,
weil Staleness ihn unterdrückte statt auslöste.

Der Fix ist eine Reihenfolge, keine neue Mechanik: senden, dann aussteigen.
"""

from __future__ import annotations

import inspect

from app.cli import main as cli_main


def _source() -> str:
    """Quelltext OHNE Kommentarzeilen.

    Struktur-Tests, die über Textpositionen argumentieren, müssen Kommentare
    ausblenden: die Kommentare beschreiben hier ausgerechnet die Regel, die
    geprüft wird (»vorher stand ``raise typer.Exit(code=2)`` an dieser
    Stelle«) — eine reine Textsuche zählt sie als Verstoß gegen sich selbst.
    """
    raw = inspect.getsource(cli_main.alerts_health_check)
    return "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))


def test_versand_steht_vor_dem_stale_exit() -> None:
    """Struktur-Kontrakt: der Abbruch wird vorgemerkt, der Versand geht zuerst raus.

    Verglichen werden AUFRUFE, nicht Definitionen — die Closure ``_stale_exit_now``
    steht naturgemäß weit oben, ausgeführt wird sie zuletzt.
    """
    src = _source()

    send_pos = src.find("dispatch_health_notification(\n")
    # Der letzte Aufruf der Abbruch-Closure ist der auf dem Issue-Pfad.
    exit_call_pos = src.rfind("_stale_exit_now()")

    assert send_pos != -1, "Versand-Aufruf nicht gefunden — Test veraltet?"
    assert exit_call_pos != -1, "Abbruch-Aufruf nicht gefunden — Test veraltet?"
    assert send_pos < exit_call_pos, (
        "Der Telegram-Versand liegt wieder HINTER dem Stale-Exit. Damit meldet "
        "der Health-Check nur, solange die Daten frisch sind — und schweigt im "
        "Ausfall, fuer den er gebaut ist."
    )


def test_abbruch_wird_vorgemerkt_statt_sofort_geworfen() -> None:
    """Kein nacktes ``raise`` mehr vor dem Versand — nur noch in der Closure."""
    src = _source()
    raise_pos = src.find("raise typer.Exit(code=2)")
    send_pos = src.find("dispatch_health_notification(\n")
    closure_pos = src.find("def _stale_exit_now()")

    # Das einzige verbliebene ``raise`` gehoert zur Closure, die spaeter
    # aufgerufen wird — nicht zum linearen Ablauf vor dem Versand.
    assert closure_pos < raise_pos < send_pos
    assert src.count("raise typer.Exit(code=2)") == 1


def test_stale_exit_bleibt_erhalten() -> None:
    """Der Exit-Code 2 ist die Off-Pi-Schutzsemantik und muss bleiben."""
    src = _source()
    assert "raise typer.Exit(code=2)" in src


def test_dispatch_ist_ein_eigenes_modul() -> None:
    """Der Versand liegt ausserhalb des God-Files und ist ohne CLI-Rahmen testbar."""
    from app.alerts.health_notify import dispatch_health_notification

    assert callable(dispatch_health_notification)


def test_cli_haelt_keine_eigene_versandkopie_mehr() -> None:
    assert not hasattr(cli_main, "_dispatch_health_notification")
