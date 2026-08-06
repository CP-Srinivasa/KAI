"""Operator-CLI contract for explicit HOTP journal initialization."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hotp_bootstrap.py"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("hotp_bootstrap", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_cli_writes_explicit_next_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "hotp.jsonl"
    module = _load()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hotp_bootstrap.py",
            "--journal-path",
            str(journal),
            "--next-counter",
            "12",
        ],
    )

    assert module.main() == 0

    record = json.loads(journal.read_text(encoding="utf-8"))
    assert record["schema_version"] == "hotp-bootstrap-v1"
    assert record["last_used_counter"] == 11
    assert "next_counter=12" in capsys.readouterr().out


def test_bootstrap_cli_refuses_existing_file_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "hotp.jsonl"
    journal.write_text("preserve-evidence\n", encoding="utf-8")
    module = _load()
    monkeypatch.setattr(
        sys,
        "argv",
        ["hotp_bootstrap.py", "--journal-path", str(journal), "--next-counter", "0"],
    )

    assert module.main() == 2
    assert journal.read_text(encoding="utf-8") == "preserve-evidence\n"
    assert "already exists" in capsys.readouterr().err
