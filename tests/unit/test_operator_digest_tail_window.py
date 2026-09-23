"""``_read_jsonl_tail`` liest ein FENSTER, nicht die ganze Datei (23.09.2026).

Der Digest lief am 22./23.09. in einen cgroup-OOM am 512-M-Limit; der Peak war
zuvor von 494,7 M (16.09.) linear gewachsen. Ursache war kein Leck, sondern
``fh.readlines()[-max_lines:]``: erst die GANZE Datei als Zeilenliste in den
Speicher, dann schneiden. Damit hing der Bedarf an der Historie der nicht
rotierten Stroeme (``paper_execution_audit`` ist HARD EXCLUSION) statt am
Fenster. Diese Tests pinnen das Fenster-Verhalten und schliessen den alten
Schnitt aus, ohne die Ausgabe zu veraendern.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import operator_digest as od  # noqa: E402


def _write(path: Path, count: int, *, pad: int = 0) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for i in range(count):
            row: dict[str, object] = {"i": i}
            if pad:
                row["pad"] = "x" * pad
            fh.write(json.dumps(row) + "\n")


def test_liefert_nur_die_letzten_saetze_in_reihenfolge(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    _write(path, 100)

    rows = od._read_jsonl_tail(path, max_lines=10)

    assert [r["i"] for r in rows] == list(range(90, 100))


def test_kleinere_datei_als_fenster_kommt_vollstaendig(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    _write(path, 5)

    rows = od._read_jsonl_tail(path, max_lines=10)

    assert [r["i"] for r in rows] == [0, 1, 2, 3, 4]


def test_leere_und_kaputte_zeilen_werden_uebersprungen(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text(
        '{"i": 1}\n\nkein json\n   \n["liste statt objekt"]\n{"i": 2}\n',
        encoding="utf-8",
    )

    rows = od._read_jsonl_tail(path, max_lines=10)

    assert [r["i"] for r in rows] == [1, 2]


def test_fehlende_datei_ist_leer(tmp_path: Path) -> None:
    assert od._read_jsonl_tail(tmp_path / "gibt_es_nicht.jsonl") == []


def test_liest_die_datei_nicht_als_ganzes_ein(tmp_path: Path, monkeypatch) -> None:
    """Der eigentliche Regressionsschutz: ``readlines()`` haelt die ganze Datei.

    Ein Aufruf davon wuerde den Speicher wieder an die Historie binden — genau
    der Fehler, der zum OOM fuehrte. Die Ausgabe allein kann das nicht zeigen,
    beide Varianten liefern dasselbe. Deshalb wird hier ein Datei-Objekt
    untergeschoben, das sich iterieren, aber nicht auf einmal lesen laesst.
    """
    path = tmp_path / "stream.jsonl"
    _write(path, 50)
    real_open = Path.open

    class _NurIterierbar:
        def __init__(self, fh: object) -> None:
            self._fh = fh

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(self._fh)

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *exc: object) -> None:
            self._fh.close()  # type: ignore[attr-defined]

        def readlines(self, *args: object, **kwargs: object) -> list[str]:
            raise AssertionError("readlines() liest die ganze Datei — Fenster nutzen")

        def read(self, *args: object, **kwargs: object) -> str:
            raise AssertionError("read() liest die ganze Datei — Fenster nutzen")

    def _open(self: Path, *args: object, **kwargs: object) -> _NurIterierbar:
        return _NurIterierbar(real_open(self, *args, **kwargs))  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _open)

    rows = od._read_jsonl_tail(path, max_lines=5)

    assert [r["i"] for r in rows] == [45, 46, 47, 48, 49]


def test_grosse_datei_bleibt_beim_fenster(tmp_path: Path) -> None:
    # 5 000 Zeilen à ~1 kB; ein Voll-Read materialisierte hier ~5 MB, das
    # Fenster haelt 20.
    path = tmp_path / "gross.jsonl"
    _write(path, 5_000, pad=1_000)

    rows = od._read_jsonl_tail(path, max_lines=20)

    assert len(rows) == 20
    assert [r["i"] for r in rows] == list(range(4_980, 5_000))
