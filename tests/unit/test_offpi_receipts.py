"""Schreiber der Offsite-Quittungen (MindBlow 2.0, E3).

Die Quittung kommt per ssh vom Laptop (kai_vault.ps1). Geprueft wird, dass nur
gueltige Quittungen ins Journal kommen, dass Ablehnungen laut sind und dass
Schreiber und Leser (health_check_host) denselben Strom meinen.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.alerts import health_check_host as reader
from app.observability import offpi_receipts as w


def _valid(**over: object) -> dict[str, object]:
    rec: dict[str, object] = {
        "schema": "offpi_receipt/v1",
        "ts_utc": "2026-09-26T07:48:27Z",
        "vault_id": "8b5f0ce4-b42b-41b9-978b-df35c6cb1444",
        "generation": "2026-09-25T08-01-43Z",
        "generation_ts_utc": "2026-09-25T08:01:43Z",
        "probe": "PASS",
        "probe_checks": 27,
    }
    rec.update(over)
    return rec


def test_valid_receipt_is_appended_as_one_line(tmp_path: Path) -> None:
    path = w.append_receipt(tmp_path, _valid())
    w.append_receipt(tmp_path, _valid(generation="2026-09-26T08-59-02Z"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert path == tmp_path / "backup" / "offpi_receipts.jsonl"
    assert [json.loads(line)["generation"] for line in lines] == [
        "2026-09-25T08-01-43Z",
        "2026-09-26T08-59-02Z",
    ]


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"schema": "anderes/v1"}, "fremdes Schema"),
        ({"probe": "OK"}, "PASS oder FAIL"),
        ({"generation_ts_utc": "2026-09-25 08:01"}, "kein UTC-Zeitstempel"),
        ({"vault_id": ""}, "Feld fehlt: vault_id"),
    ],
)
def test_invalid_receipt_is_rejected_and_not_written(
    tmp_path: Path, override: dict[str, object], fragment: str
) -> None:
    with pytest.raises(ValueError, match=fragment):
        w.append_receipt(tmp_path, _valid(**override))
    assert not (tmp_path / w.OFFPI_RECEIPTS_RELPATH).exists()


def test_cli_appends_from_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_valid())))
    assert w.main(["append", "--artifacts-dir", str(tmp_path)]) == 0
    assert "OFFPI_RECEIPT_OK" in capsys.readouterr().out
    assert (tmp_path / w.OFFPI_RECEIPTS_RELPATH).exists()


@pytest.mark.parametrize("payload", ["kein json", json.dumps({"schema": "offpi_receipt/v1"})])
def test_cli_rejects_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: str,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert w.main(["append", "--artifacts-dir", str(tmp_path)]) == 2
    assert "OFFPI_RECEIPT_REJECTED" in capsys.readouterr().err
    assert not (tmp_path / w.OFFPI_RECEIPTS_RELPATH).exists()


def test_cli_rejects_oversized_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("x" * (w.MAX_RECEIPT_BYTES + 10)))
    assert w.main(["append", "--artifacts-dir", str(tmp_path)]) == 2
    assert not (tmp_path / w.OFFPI_RECEIPTS_RELPATH).exists()


def test_writer_and_reader_mean_the_same_stream(tmp_path: Path) -> None:
    assert reader.OFFPI_RECEIPTS_RELPATH == w.OFFPI_RECEIPTS_RELPATH
    assert reader.OFFPI_RECEIPT_SCHEMA == w.SCHEMA
    # Ende-zu-Ende: was der Schreiber annimmt, erkennt der Leser als Beleg.
    w.append_receipt(tmp_path, _valid(generation_ts_utc="2099-01-01T00:00:00Z"))
    assert reader.offpi_backup_finding(tmp_path) is None
