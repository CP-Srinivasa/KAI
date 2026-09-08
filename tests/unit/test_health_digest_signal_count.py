"""Das eine Python-Schnipsel im Repo, das kein Parser beurteilen konnte.

``scripts/pi_health_digest.sh`` zaehlte die Telegram-Signale der letzten Tage in
einem Heredoc mit UNgequotetem Delimiter (``<<PYEOF``). Die Shell setzte dort
``${WINDOW_DAYS}`` und ``${RAW_LOG}`` in den Python-QUELLTEXT ein.

Zwei Folgen, und die erste ist die stillere:

1. Der Text im Skript war nicht der Text, der laeuft. Ein Pruefer wie
   ``test_embedded_python_is_parseable.py`` MUSS so ein Schnipsel ueberspringen
   -- er urteilte sonst ueber etwas, das es so nie gibt. Repo-weit gemessen war
   das genau eine Stelle: die Ausnahme kostete den Waechter sein einziges Loch.

2. Ein Pfad, der in Quelltext eingesetzt wird, ist eine Zeichenkette an der
   falschen Stelle. Enthaelt er ein Zeichen, das Python in einer Zeichenkette
   deutet, liest das Schnipsel etwas anderes -- oder gar nichts.

Beides verschwindet mit gequotetem Delimiter und Werten ueber ``argv``.

Der Test schneidet das Schnipsel aus dem ECHTEN Skript und fuehrt es aus. Eine
Nachbildung wuerde die Nachbildung pruefen; der Fehler sass gerade in der
Wanderung ueber die Sprachgrenze, also muss die Grenze im Test vorkommen.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKRIPT = REPO / "scripts" / "pi_health_digest.sh"

_HEREDOC = re.compile(
    r"python3? - [^\n]*<<'(?P<delim>[A-Za-z_][A-Za-z0-9_]*)'[^\n]*\n(?P<quelle>.*?)^(?P=delim)$",
    re.DOTALL | re.MULTILINE,
)


def _schnipsel() -> str:
    treffer = _HEREDOC.search(SKRIPT.read_text(encoding="utf-8"))
    assert treffer is not None, (
        "kein gequotetes Python-Heredoc in pi_health_digest.sh gefunden -- "
        "entweder wurde es umgebaut oder der Delimiter ist wieder ungequotet"
    )
    return treffer.group("quelle")


def _zeile(alter_tage: float) -> str:
    ts = datetime.now(tz=UTC) - timedelta(days=alter_tage)
    return json.dumps({"timestamp_utc": ts.isoformat()})


def _zaehle(log: Path, tage: int) -> str:
    fertig = subprocess.run(
        [sys.executable, "-", str(tage), str(log)],
        input=_schnipsel(),
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert fertig.returncode == 0, fertig.stderr
    return fertig.stdout.strip()


def test_der_delimiter_ist_gequotet_und_das_schnipsel_ist_python() -> None:
    """Die Voraussetzung fuer alles andere hier -- und fuer den Waechter in
    ``test_embedded_python_is_parseable.py``, der das Schnipsel jetzt sieht."""
    import ast

    ast.parse(_schnipsel())
    assert "${" not in _schnipsel(), "es wird wieder in den Quelltext interpoliert"
    assert "sys.argv" in _schnipsel(), "die Werte kommen nicht als Argument an"


def test_zaehlt_nur_was_im_fenster_liegt(tmp_path: Path) -> None:
    log = tmp_path / "raw.jsonl"
    log.write_text(
        "\n".join([_zeile(1), _zeile(3), _zeile(6.9), _zeile(8), _zeile(30)]) + "\n",
        encoding="utf-8",
    )
    assert _zaehle(log, 7) == "3"


def test_kaputte_zeilen_halten_die_zaehlung_nicht_an(tmp_path: Path) -> None:
    """Ein einziger unvollstaendiger Schreibvorgang darf die Kennzahl nicht auf
    null setzen -- dann meldete der Digest "Listener tot", waehrend er laeuft."""
    log = tmp_path / "raw.jsonl"
    log.write_text(
        "\n".join(["{kein json", "", json.dumps({"ohne": "timestamp"}), _zeile(1)]) + "\n",
        encoding="utf-8",
    )
    assert _zaehle(log, 7) == "1"


@pytest.mark.skipif(
    os.name != "posix",
    reason="Windows verbietet Backslash im Dateinamen -- die Eigenschaft IST das Zeichen",
)
def test_ein_pfad_mit_sonderzeichen_wird_gelesen_statt_gedeutet(tmp_path: Path) -> None:
    """Die Gegenprobe: hier haette die alte Fassung falsch gelesen.

    Ein Backslash im Pfad wandert bei Quelltext-Interpolation als
    Escape-Sequenz in eine Python-Zeichenkette. Ueber ``argv`` ist er ein
    Zeichen wie jedes andere. Der zweite Teil des Tests baut die ALTE Form nach
    und verlangt, dass sie scheitert -- ohne das waere "die neue Form
    funktioniert" nur eine Aussage ueber einen harmlosen Pfad.
    """
    verzeichnis = tmp_path / "a\nb"
    verzeichnis.mkdir()
    log = verzeichnis / "raw.jsonl"
    log.write_text(_zeile(1) + "\n", encoding="utf-8")

    assert _zaehle(log, 7) == "1"

    alte_form = _schnipsel().replace("sys.argv[2]", f'"{log}"')
    fertig = subprocess.run(
        [sys.executable, "-", "7", "unbenutzt"],
        input=alte_form,
        capture_output=True,
        text=True,
    )
    assert fertig.returncode != 0, (
        "die nachgebaute alte Form laeuft durch -- dann beweist der Test darueber "
        "nichts ueber den Unterschied"
    )
