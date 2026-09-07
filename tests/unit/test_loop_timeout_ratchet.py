"""Tests fuer das Loop-Timeout-Ratchet.

Der Waechter existiert wegen zweier Vorfaelle auf ``kai-pi5``: 2026-05-31 stand
der Telethon-Push-Stream 46 h still, 2026-09-04 der Poll-Backstop 65 h — der
als Antwort auf den ersten Vorfall gebaut worden war. Beide Male wartete ein
``while True`` auf einen Netzwerk-Aufruf ohne Zeitgrenze, es flog keine
Ausnahme, und jede Selbstheilung, die Exceptions zaehlt, lief ins Leere.

Ein Waechter ist nur so viel wert wie seine beiden Richtungen: er muss den
Fehler **fangen** und den korrekten Fall **durchlassen**. Faengt er zu viel,
wird er abgeschaltet; faengt er zu wenig, schuetzt er nichts. Beide Richtungen
stehen hier.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RATCHET = _ROOT / "scripts" / "loop_timeout_ratchet.py"
_BASELINE = _ROOT / "config" / "loop_timeout_baseline.json"


def _lauf(wurzel: Path, baseline: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_RATCHET), str(wurzel), str(baseline)],
        capture_output=True,
        text=True,
        check=False,
        cwd=_ROOT,
    )


def _leere_baseline(tmp_path: Path) -> Path:
    pfad = tmp_path / "baseline.json"
    pfad.write_text(json.dumps({"geklaert": {}}), encoding="utf-8")
    return pfad


def _modul(tmp_path: Path, quelle: str, name: str = "probe.py") -> Path:
    wurzel = tmp_path / "quelle"
    wurzel.mkdir(exist_ok=True)
    (wurzel / name).write_text(quelle, encoding="utf-8")
    return wurzel


# ── Richtung 1: der Fehler MUSS reissen ──────────────────────────────────────


def test_unbegrenztes_await_reisst_den_guard(tmp_path: Path) -> None:
    """Genau die Bauart der beiden Vorfaelle — sie darf nicht durchrutschen."""
    wurzel = _modul(
        tmp_path,
        """
import asyncio


async def poll_loop(client):
    while True:
        await asyncio.sleep(90)
        await client.fetch_messages()
""",
    )

    ergebnis = _lauf(wurzel, _leere_baseline(tmp_path))

    assert ergebnis.returncode == 1
    assert "fetch_messages" in ergebnis.stdout
    assert "poll_loop()" in ergebnis.stdout
    # Die Meldung muss den Weg nach draussen zeigen, nicht nur das Verbot.
    assert "asyncio.wait_for" in ergebnis.stdout


def test_baseline_eintrag_ins_leere_reisst_ebenfalls(tmp_path: Path) -> None:
    """Eine Ausnahme, die niemand mehr braucht, ist eine Erlaubnis auf Vorrat."""
    wurzel = _modul(tmp_path, "async def harmlos():\n    return 1\n")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "geklaert": {
                    "quelle/weg.py::alt::ruf": {
                        "grund": "kein_externes_io",
                        "begruendung": "existiert nicht mehr",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ergebnis = _lauf(wurzel, baseline)

    assert ergebnis.returncode == 1
    assert "ZEIGT INS LEERE" in ergebnis.stdout


def test_unbekannter_baseline_grund_reisst(tmp_path: Path) -> None:
    """Freitext als Grund waere eine Einladung, den Eintrag abzuhaken."""
    wurzel = _modul(
        tmp_path,
        "async def f(c):\n    while True:\n        await c.hol()\n",
        name="m.py",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "geklaert": {
                    f"{(wurzel / 'm.py').as_posix()}::f::hol": {
                        "grund": "passt schon",
                        "begruendung": "—",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ergebnis = _lauf(wurzel, baseline)

    assert ergebnis.returncode == 1
    assert "GRUND UNBEKANNT" in ergebnis.stdout


# ── Richtung 2: der korrekte Fall MUSS durchgehen ────────────────────────────


@pytest.mark.parametrize(
    ("bezeichnung", "quelle"),
    [
        (
            "asyncio.wait_for",
            """
import asyncio


async def poll_loop(client):
    while True:
        await asyncio.sleep(90)
        await asyncio.wait_for(client.fetch_messages(), timeout=360)
""",
        ),
        (
            "async with asyncio.timeout",
            """
import asyncio


async def poll_loop(client):
    while True:
        await asyncio.sleep(90)
        async with asyncio.timeout(360):
            await client.fetch_messages()
""",
        ),
        (
            "nur asyncio.sleep",
            """
import asyncio


async def takt():
    while True:
        await asyncio.sleep(1)
""",
        ),
        (
            "Schleife mit Abbruchbedingung statt while True",
            """
async def bis_fertig(client, fertig):
    while not fertig():
        await client.fetch_messages()
""",
        ),
        (
            "verschachtelte Funktion erbt die Schleife nicht",
            """
import asyncio


async def aussen(client):
    async def innen():
        await client.fetch_messages()

    while True:
        await asyncio.sleep(1)
        asyncio.create_task(innen())
""",
        ),
    ],
)
def test_geschuetzter_aufruf_geht_durch(bezeichnung: str, quelle: str, tmp_path: Path) -> None:
    """Kein Fehlalarm auf korrektem Code — sonst wird der Waechter abgeschaltet."""
    wurzel = _modul(tmp_path, quelle)

    ergebnis = _lauf(wurzel, _leere_baseline(tmp_path))

    assert ergebnis.returncode == 0, f"{bezeichnung}: {ergebnis.stdout}"


def test_begruendeter_bounded_client_geht_durch(tmp_path: Path) -> None:
    """Der zweite Zweig des Vertrags: nachweislich begrenzter Client statt Timeout.

    Das ist der Fall ``binance_stream`` — ``websockets`` erzwingt die Grenze
    ueber Ping/Pong, eine zusaetzliche ``wait_for`` waere Doppelung.
    """
    wurzel = _modul(
        tmp_path,
        "async def run(connect, url):\n    while True:\n        await verbrauche(url)\n",
        name="stream.py",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "geklaert": {
                    f"{(wurzel / 'stream.py').as_posix()}::run::verbrauche": {
                        "grund": "bounded_client",
                        "begruendung": "websockets ping_interval=20s/ping_timeout=20s",
                        "geprueft_am": "2026-09-07",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ergebnis = _lauf(wurzel, baseline)

    assert ergebnis.returncode == 0, ergebnis.stdout


# ── Der echte Baum ───────────────────────────────────────────────────────────


def test_der_bestand_ist_gruen() -> None:
    """``app/`` gegen die eingecheckte Baseline — das Gate, das CI faehrt."""
    ergebnis = _lauf(_ROOT / "app", _BASELINE)

    assert ergebnis.returncode == 0, ergebnis.stdout


def test_jeder_baseline_eintrag_traegt_eine_begruendung() -> None:
    """Ein Grund-Schluesselwort ohne Text waere ein Haken, keine Entscheidung."""
    daten = json.loads(_BASELINE.read_text(encoding="utf-8"))
    for schluessel, eintrag in daten["geklaert"].items():
        assert eintrag.get("begruendung", "").strip(), f"{schluessel}: keine Begruendung"
        assert eintrag.get("geprueft_am", "").strip(), f"{schluessel}: kein Pruefdatum"
