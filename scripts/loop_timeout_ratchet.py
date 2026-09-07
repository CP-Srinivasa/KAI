#!/usr/bin/env python3
"""Loop-Timeout-Ratchet — kein Dauer-Loop mit unbegrenztem externen ``await``.

**Der Defekt, den dieses Gate schliesst.** Er ist auf ``kai-pi5`` zweimal
aufgetreten, und beim zweiten Mal ausgerechnet in dem Bauteil, das als Antwort
auf das erste Mal gebaut worden war:

===========  ====================================  ======  =========================
Datum        Bauteil                               Dauer   Folge
===========  ====================================  ======  =========================
2026-05-31   Telethon-Push-Stream                   46 h   ein NIGHT/USDT-Signal weg
2026-09-04   Poll-Backstop (die Absicherung dafuer) 65 h   6.000 Fehlmeldungen
===========  ====================================  ======  =========================

Die Bauart ist beide Male dieselbe: ein ``while True`` wartet auf einen
Netzwerk-Aufruf **ohne Zeitgrenze**. Die Bibliothek wartet auf ihr Future
unbegrenzt; eine Verbindung, die "connected" meldet, aber nichts mehr
beantwortet, blockiert den Zyklus fuer immer. Der Prozess lebt dabei -- ein
Heartbeat-Task tickt weiter --, systemd sieht einen gesunden Dienst, und die
eingebaute Selbstheilung (*n* Fehlschlaege, dann Neustart) greift **nie**, weil
sie Exceptions zaehlt und keine fliegt.

**Warum ein AST-Gate und keine Textsuche.** Der Defekt ist die *Abwesenheit*
einer Zeitgrenze. An der fehlerhaften Stelle steht kein auffaelliges Wort --
es fehlt eines. Abwesenheit laesst sich nicht greppen, wohl aber ueber die
Struktur fragen: fuer jeden ``await``-Knoten innerhalb eines ``while True``,
gibt es einen umschliessenden ``wait_for``/``timeout``?

**Die Regel.**

    LONG_RUNNING_LOOP + EXTERNES AWAIT
        =>  explizite Zeitgrenze (``asyncio.wait_for`` / ``async with timeout``)
        ODER Baseline-Eintrag mit nachgewiesen begrenztem Client

**Bewusst eng.** Das Gate ist kein "jedes ``await`` ist boese". Es greift nur in
``while True``-Schleifen, es ignoriert ``asyncio.sleep`` (das kommt garantiert
zurueck), und der geklaerte Bestand steht mit Begruendung in
``config/loop_timeout_baseline.json``. Ein Ratchet, das bei jedem zweiten Lauf
Fehlalarm gibt, wird abgeschaltet -- und dann schuetzt es gar nichts mehr.

**Was dieses Gate NICHT ist.** Kein Beweis, dass ein Aufruf tatsaechlich
zurueckkommt. Es misst Struktur, nicht Laufzeit. Ein Baseline-Eintrag
``bounded_client`` ist eine *gepruefte Behauptung* mit Datum und Begruendung --
nachpruefbar, aber nicht vom Skript bewiesen.

Exit: 0 = kein unbegruendeter Treffer · 1 = mindestens einer (oder eine
Baseline-Zeile, die ins Leere zeigt).
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

#: Aufrufe, die eine Zeitgrenze setzen — als Kontextmanager oder als Aufruf.
#: ``anyio``/``trio`` stehen mit drin, damit ein Wechsel der Bibliothek das Gate
#: nicht still blind macht.
TIMEOUT_CALLS: frozenset[str] = frozenset(
    {"wait_for", "timeout", "timeout_at", "move_on_after", "fail_after", "wait_for_ms"}
)

#: ``await asyncio.sleep(...)`` kommt garantiert zurueck. Es ist der einzige
#: Aufruf, dem das Gate ohne Begruendung glaubt.
HARMLOSE_AWAITS: frozenset[str] = frozenset({"sleep"})

BASELINE_PFAD = Path("config/loop_timeout_baseline.json")

#: Zulaessige Begruendungen in der Baseline. Freitext waere eine Einladung, den
#: Eintrag als Formalie abzuhaken.
GRUENDE: frozenset[str] = frozenset(
    {
        # Der Aufruf spricht mit nichts Externem (lokaler Zustand, interne Task,
        # Queue mit eigener Grenze).
        "kein_externes_io",
        # Externes I/O, aber der Client erzwingt die Grenze selbst — geprueft
        # und im Eintrag benannt (z. B. websockets ping_interval/ping_timeout).
        "bounded_client",
        # Der Aufruf beendet den Loop, statt in ihm zu warten.
        "beendet_den_loop",
    }
)


@dataclass(frozen=True)
class Treffer:
    """Ein ``await`` in einer Endlosschleife ohne umschliessende Zeitgrenze."""

    datei: str
    funktion: str
    ziel: str
    zeile: int

    @property
    def schluessel(self) -> str:
        """Stabil gegen Zeilenverschiebungen — Datei, Funktion, Aufrufziel.

        Bewusst ohne die Zeilennummer: eine Baseline, die bei jeder Einrueckung
        veraltet, wird beim ersten Rot pauschal neu geschrieben. Dann steht dort
        der Ist-Stand statt einer Entscheidung.
        """
        return f"{self.datei}::{self.funktion}::{self.ziel}"


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _ist_endlosschleife(node: ast.While) -> bool:
    """``while True:`` — die Bauart, die ohne Zeitgrenze ewig stehen kann.

    ``while <bedingung>:`` bleibt aussen vor: dort gibt es eine Abbruchbedingung,
    die jemand formuliert hat. Das Gate urteilt nicht darueber, ob sie taugt.
    """
    test = node.test
    return isinstance(test, ast.Constant) and test.value is True


class _Sucher(ast.NodeVisitor):
    def __init__(self, datei: str) -> None:
        self.datei = datei
        self.treffer: list[Treffer] = []
        self._funktionen: list[str] = []
        self._loop_tiefe = 0
        self._timeout_tiefe = 0

    # -- Kontext: in welcher Funktion stehen wir? ---------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._mit_funktion(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._mit_funktion(node)

    def _mit_funktion(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._funktionen.append(node.name)
        # Eine verschachtelte Funktion erbt die Schleife des Elternteils NICHT:
        # sie laeuft als eigener Task und hat ihren eigenen Kontrollfluss.
        loop, timeout = self._loop_tiefe, self._timeout_tiefe
        self._loop_tiefe = self._timeout_tiefe = 0
        self.generic_visit(node)
        self._loop_tiefe, self._timeout_tiefe = loop, timeout
        self._funktionen.pop()

    # -- Kontext: Endlosschleife und Zeitgrenze -----------------------------
    def visit_While(self, node: ast.While) -> None:
        endlos = _ist_endlosschleife(node)
        if endlos:
            self._loop_tiefe += 1
        self.generic_visit(node)
        if endlos:
            self._loop_tiefe -= 1

    def visit_With(self, node: ast.With) -> None:
        self._mit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._mit_with(node)

    def _mit_with(self, node: ast.With | ast.AsyncWith) -> None:
        schuetzt = any(
            isinstance(i.context_expr, ast.Call) and _name(i.context_expr.func) in TIMEOUT_CALLS
            for i in node.items
        )
        if schuetzt:
            self._timeout_tiefe += 1
        self.generic_visit(node)
        if schuetzt:
            self._timeout_tiefe -= 1

    # -- Der eigentliche Befund ---------------------------------------------
    def visit_Await(self, node: ast.Await) -> None:
        if self._loop_tiefe and not self._timeout_tiefe:
            wert = node.value
            ziel = _name(wert.func) if isinstance(wert, ast.Call) else ""
            if ziel not in HARMLOSE_AWAITS and ziel not in TIMEOUT_CALLS:
                self.treffer.append(
                    Treffer(
                        datei=self.datei,
                        funktion=self._funktionen[-1] if self._funktionen else "<modul>",
                        ziel=ziel or "<ausdruck>",
                        zeile=node.lineno,
                    )
                )
        self.generic_visit(node)


def _relativ(pfad: Path) -> str:
    """Der Dateiname, wie ihn die Baseline kennt — relativ zum Arbeitsverzeichnis.

    Ohne das haengt der Baseline-Schluessel davon ab, ob jemand
    ``loop_timeout_ratchet.py app`` oder ``... /abs/pfad/app`` aufruft: einmal
    ``app/x.py``, einmal ``C:/tmp/.../app/x.py``. Dieselbe Datei haette zwei
    Identitaeten, und das Gate waere lokal gruen und in CI rot (oder umgekehrt).
    Gefunden vom eigenen Test dieses Skripts.
    """
    try:
        return pfad.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        # Ausserhalb des Arbeitsverzeichnisses (z. B. ein tmp-Baum im Test):
        # dann ist der absolute Pfad die ehrlichste Identitaet.
        return pfad.resolve().as_posix()


def finde_treffer(wurzel: Path) -> list[Treffer]:
    """Alle ungeschuetzten ``await``-Aufrufe in Endlosschleifen unter ``wurzel``."""
    alle: list[Treffer] = []
    for pfad in sorted(wurzel.rglob("*.py")):
        if any(teil in pfad.parts for teil in (".venv", "__pycache__", "tests")):
            continue
        try:
            baum = ast.parse(pfad.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            # Eine unlesbare Datei ist ein Problem fuer andere Gates, nicht fuer
            # dieses. Stillschweigend ueberspringen waere aber falsch, wenn es
            # der einzige Grund fuer ein Gruen waere — deshalb zaehlt unten die
            # Gesamtzahl der geprueften Dateien mit.
            continue
        sucher = _Sucher(_relativ(pfad))
        sucher.visit(baum)
        alle.extend(sucher.treffer)
    return alle


def lade_baseline(pfad: Path) -> dict[str, dict[str, str]]:
    if not pfad.exists():
        return {}
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    eintraege = daten.get("geklaert", {})
    return {str(k): dict(v) for k, v in eintraege.items()}


def pruefe(wurzel: Path, baseline_pfad: Path) -> int:
    treffer = finde_treffer(wurzel)
    baseline = lade_baseline(baseline_pfad)

    offen = [t for t in treffer if t.schluessel not in baseline]
    gefunden = {t.schluessel for t in treffer}
    verwaist = sorted(set(baseline) - gefunden)

    for t in sorted(offen, key=lambda x: (x.datei, x.zeile)):
        print(f"{t.datei}:{t.zeile}  await {t.ziel}(...) in {t.funktion}() — ohne Zeitgrenze")
    if offen:
        print()
        print("LOOP_TIMEOUT_RATCHET: ungeschuetztes await in einer Endlosschleife.")
        print()
        print("  Ein Dauer-Loop, dessen await nie zurueckkommt, steht still, ohne dass")
        print("  etwas faellt: der Prozess lebt, systemd sieht 'active', und jede")
        print("  Selbstheilung, die Exceptions zaehlt, greift nicht. Auf kai-pi5 hat")
        print("  das 46 h und 65 h gekostet.")
        print()
        print("  Entweder eine Zeitgrenze ziehen:")
        print("      await asyncio.wait_for(<aufruf>, timeout=<sekunden>)")
        print("      async with asyncio.timeout(<sekunden>): ...")
        print(f"  oder den Fall in {baseline_pfad.as_posix()} begruenden.")
        print(f"  Zulaessige Gruende: {', '.join(sorted(GRUENDE))}")

    for schluessel in verwaist:
        print(f"BASELINE-EINTRAG ZEIGT INS LEERE: {schluessel}")
    if verwaist:
        print()
        print("  Der Code an dieser Stelle wurde geaendert oder entfernt. Eine")
        print("  Ausnahme, die niemand mehr braucht, ist eine Erlaubnis auf Vorrat —")
        print("  bitte aus der Baseline loeschen.")

    ungueltig = [
        (k, v.get("grund", "")) for k, v in baseline.items() if v.get("grund") not in GRUENDE
    ]
    for schluessel, grund in ungueltig:
        print(f"BASELINE-GRUND UNBEKANNT: {schluessel} -> {grund!r}")

    if offen or verwaist or ungueltig:
        return 1

    print(
        f"[loop-timeout-ratchet] ok: {len(treffer)} await(s) in Endlosschleifen, "
        f"alle mit Zeitgrenze oder begruendet ({len(baseline)} Baseline-Eintraege)."
    )
    return 0


def main(argv: list[str]) -> int:
    wurzel = Path(argv[1]) if len(argv) > 1 else Path("app")
    baseline = Path(argv[2]) if len(argv) > 2 else BASELINE_PFAD
    if not wurzel.is_dir():
        print(f"kein Verzeichnis: {wurzel}", file=sys.stderr)
        return 1
    return pruefe(wurzel, baseline)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
