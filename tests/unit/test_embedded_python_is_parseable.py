"""Eingebetteter Python-Code in Shell-Skripten muss überhaupt Python sein.

Der Anlass ist ein Fehlschlag beim ersten Bau eines Release mit Extras. Im
Builder stand

    print("
".join(specs))

also eine Zeichenkette mit einem **echten** Zeilenumbruch. Zwei Zeilen darüber
steht dieselbe Schreibweise in ``printf '%s\\n'`` und funktioniert einwandfrei —
in der Shell ist sie gültig, in Python ein ``SyntaxError``. Die Schreibweise
wandert beim Einfügen über eine Sprachgrenze und bedeutet diesseits und jenseits
etwas anderes.

Neun CI-Checks waren grün. Die Tests dazu prüften den Skript-**Text**: ob
``--extra`` in der Argumentliste vorkommt, ob ``extras_sha256`` geschrieben
wird, ob der Pfad den Diskriminator trägt. Alles wahre Aussagen über die Datei —
und keine einzige über das Verhalten. Das Schnipsel wurde nie ausgeführt.

Diese Datei schließt die Klasse statt des Einzelfalls: sie schneidet **jeden**
eingebetteten ``python -c``-Aufruf aus **jedem** Shell-Skript und lässt ihn vom
Parser beurteilen. Kein Prozess, keine Seiteneffekte, keine Abhängigkeiten —
nur die Frage, ob das, was da als Python steht, eines ist.

EIN HINWEIS ZUM PRÜFER SELBST

Die erste Fassung dieses Werkzeugs meldete einen zweiten Treffer, den es nicht
gab: sie schnitt ``python -c "… \\"SELECT …\\" …"`` am ersten escapten
Anführungszeichen ab und hielt den Rest für unvollständig. Ein Prüfer, der
seinen Prüfgegenstand vorher beschädigt, findet zuverlässig das Falsche —
dieselbe Klasse wie der Fehler, den er finden soll. Deshalb löst
:func:`_shell_entquoten` die Shell-Escapes auf, bevor der Parser urteilt.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Verzeichnisse, in denen fremder oder generierter Code liegt.
IGNORIERT = frozenset({".git", "node_modules", ".venv", "__pycache__", "dist"})

#: ``python -c '<einfach>'`` oder ``python -c "<doppelt>"``. In doppelten
#: Anführungszeichen beendet nur ein UNescaptes ``"`` die Zeichenkette — genau
#: daran ist die erste Fassung dieses Musters gescheitert.
_AUFRUF = re.compile(
    r"""(?:python3?|"\$PY"|\$PYTHON|\$\{PY\})\s+-c\s+"""
    r"""(?:'(?P<einfach>(?:[^']|\\')*?)'"""
    r"""|"(?P<doppelt>(?:[^"\\]|\\.)*?)")""",
    re.DOTALL,
)

#: Was die Shell in doppelten Anführungszeichen auflöst. In einfachen löst sie
#: nichts auf — dort kommt der Text unverändert bei Python an.
_ESCAPE = re.compile(r"\\([\"$`\\])")


def _shell_entquoten(treffer: re.Match[str]) -> str:
    if treffer.group("einfach") is not None:
        return treffer.group("einfach")
    return _ESCAPE.sub(r"\1", treffer.group("doppelt"))


def _shell_skripte() -> list[Path]:
    return sorted(
        pfad for pfad in REPO.rglob("*.sh") if not IGNORIERT & set(pfad.relative_to(REPO).parts)
    )


#: ``python - <<'DELIM'`` … ``DELIM``. Nur mit GEQUOTETEM Delimiter: dort reicht
#: die Shell den Text unverändert durch, und was dasteht, ist genau das, was
#: Python sieht. Ohne Quotes expandiert sie ``$…`` und Backticks — der Text im
#: Skript ist dann nicht der Text, der ausgeführt wird, und ein Parser urteilte
#: über etwas, das es so nie gibt.
_HEREDOC = re.compile(
    r"""(?:python3?|"\$PY"|\$PYTHON)\s+-[^\n<]*<<-?'(?P<delim>[A-Za-z_][A-Za-z0-9_]*)'"""
    r"""[^\n]*\n(?P<quelle>.*?)^\s*(?P=delim)\s*$""",
    re.DOTALL | re.MULTILINE,
)


def _schnipsel() -> list[tuple[Path, int, str]]:
    gefunden: list[tuple[Path, int, str]] = []
    for pfad in _shell_skripte():
        text = pfad.read_text(encoding="utf-8", errors="replace")
        for treffer in _AUFRUF.finditer(text):
            zeile = text[: treffer.start()].count("\n") + 1
            gefunden.append((pfad, zeile, _shell_entquoten(treffer)))
        for treffer in _HEREDOC.finditer(text):
            zeile = text[: treffer.start()].count("\n") + 1
            gefunden.append((pfad, zeile, treffer.group("quelle")))
    return gefunden


def test_der_pruefer_findet_ueberhaupt_etwas() -> None:
    """Ein Wächter, der nichts sieht, ist von einem grünen nicht zu unterscheiden.

    Ohne diese Zusicherung würde ein kaputtes Suchmuster als „alles in Ordnung"
    durchgehen — die stillste Art, eine Prüfung abzuschalten.
    """
    schnipsel = _schnipsel()
    assert len(schnipsel) >= 10, f"nur {len(schnipsel)} Schnipsel gefunden"
    assert len({pfad for pfad, _, _ in schnipsel}) >= 3, "nur eine Datei getroffen"


def test_beide_einbettungsarten_werden_erfasst() -> None:
    """`python -c` UND `python - <<'DELIM'` — sonst hält der Kopf nicht, was er sagt.

    Die erste Fassung erfasste nur die gequotete Variante. Drei Skripte im
    Cron-Pfad der Pi betten ihr Python per Heredoc ein, und dort bleibt ein
    Parse-Fehler still: der Lauf endet mit `|| true`, das Log trägt eine Zeile,
    und niemand sieht hin. Ein Test, der „die Klasse" verspricht und eine
    Hälfte auslässt, ist selbst die Zusage ohne Deckung, gegen die er antritt.

    Beide Zahlen einzeln, nicht ihre Summe: sonst könnte die eine Art auf null
    fallen, ohne dass es auffällt — solange die andere genug liefert.
    """
    treffer_c = treffer_heredoc = 0
    for pfad in _shell_skripte():
        text = pfad.read_text(encoding="utf-8", errors="replace")
        treffer_c += len(_AUFRUF.findall(text))
        treffer_heredoc += len(_HEREDOC.findall(text))

    assert treffer_c >= 10, f"nur {treffer_c} `python -c`-Aufrufe"
    assert treffer_heredoc >= 2, f"nur {treffer_heredoc} Heredoc-Einbettungen"


def test_ein_ungequoteter_heredoc_delimiter_wird_uebersprungen() -> None:
    """Ohne Quotes expandiert die Shell `$…` — der Text im Skript ist dann nicht
    der Text, der läuft.

    Ein Parser urteilte dort über etwas, das es so nie gibt, und ein Fehlalarm
    an dieser Stelle wäre schlimmer als die Lücke: er würde den Wächter
    unglaubwürdig machen, der die anderen Fälle richtig meldet.
    """
    mit_quotes = "python3 - <<'PY'\nprint(1)\nPY\n"
    ohne_quotes = "python3 - <<PY\nprint($WERT)\nPY\n"

    assert _HEREDOC.search(mit_quotes) is not None
    assert _HEREDOC.search(ohne_quotes) is None


@pytest.mark.parametrize(
    ("pfad", "zeile", "quelle"),
    [pytest.param(p, z, q, id=f"{p.relative_to(REPO).as_posix()}:{z}") for p, z, q in _schnipsel()],
)
def test_jeder_eingebettete_schnipsel_ist_gueltiges_python(
    pfad: Path, zeile: int, quelle: str
) -> None:
    """Was als Python dasteht, muss auch Python sein.

    Der Parser ist hier das richtige Werkzeug: er braucht keine Umgebung, keine
    Abhängigkeiten und keinen Prozess, und er urteilt über genau die Frage, die
    ein Textvergleich nicht beantwortet.
    """
    try:
        ast.parse(quelle)
    except SyntaxError as exc:
        auszug = "\n".join(f"  {i:3} | {z}" for i, z in enumerate(quelle.splitlines()[:14], 1))
        pytest.fail(
            f"{pfad.relative_to(REPO).as_posix()}:{zeile} — {exc.msg} "
            f"(Schnipsel-Zeile {exc.lineno})\n{auszug}"
        )


def test_der_entquoter_loest_auf_was_die_shell_aufloest() -> None:
    """Sonst schneidet der Prüfer mitten im Ausdruck — genau so entstand ein
    Fehlalarm in der ersten Fassung dieses Werkzeugs."""
    doppelt = _AUFRUF.search('python -c "print(\\"hallo\\")"')
    assert doppelt is not None
    assert _shell_entquoten(doppelt) == 'print("hallo")'

    einfach = _AUFRUF.search("python3 -c 'print(1)'")
    assert einfach is not None
    assert _shell_entquoten(einfach) == "print(1)"


def test_ein_echter_zeilenumbruch_in_einer_zeichenkette_faellt_auf() -> None:
    """Die Gegenprobe: genau der Fehler, der den Release-Bau gestoppt hat.

    In der Shell ist ein umbrochener String gültig, in Python nicht. Ohne
    diesen Test wäre die Zusicherung oben nur so gut wie die Annahme, dass der
    Parser so etwas überhaupt bemängelt.
    """
    kaputt = 'python3 -c "\nimport sys\nprint(\\"\n\\".join([]))\n"'
    treffer = _AUFRUF.search(kaputt)
    assert treffer is not None
    with pytest.raises(SyntaxError):
        ast.parse(_shell_entquoten(treffer))
