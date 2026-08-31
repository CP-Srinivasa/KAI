"""Tests fuer das Forensik-Werkzeug ``verify_attestation_with_sealed_code``.

Der wichtigste Test ist der Abbruch: ein Pruefskript, das ``app`` aus einem
ANDEREN Baum laedt als dem geprueften, prueft den falschen Code — genau das
liess am 2026-09-01 drei Gruppen faelschlich als gebrochene Siegel erscheinen.
Lieber gar kein Ergebnis als ein falsches.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_attestation_with_sealed_code.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_with_sealed_code", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load()


def _ledger(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "ledger.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def test_sealed_seqs_selects_only_the_named_commit(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        [
            {
                "seq": 1,
                "kind": "canonical_edge_report",
                "payload": {"code": {"commit": "aabbcc11"}},
            },
            {
                "seq": 2,
                "kind": "canonical_edge_report",
                "payload": {"code": {"commit": "ddeeff22"}},
            },
            {
                "seq": 3,
                "kind": "canonical_edge_report",
                "payload": {"code": {"commit": "aabbcc11"}},
            },
        ],
    )
    assert tool.sealed_seqs(ledger, "aabbcc", "canonical_edge_report") == [1, 3]


def test_other_kinds_are_ignored(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        [
            {"seq": 1, "kind": "verdict", "payload": {"code": {"commit": "aabbcc11"}}},
            {
                "seq": 2,
                "kind": "canonical_edge_report",
                "payload": {"code": {"commit": "aabbcc11"}},
            },
        ],
    )
    assert tool.sealed_seqs(ledger, "aabbcc", "canonical_edge_report") == [2]


def test_broken_lines_are_skipped_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        json.dumps(
            {"seq": 1, "kind": "canonical_edge_report", "payload": {"code": {"commit": "aa"}}}
        )
        + "\n{kaputt\n\n",
        encoding="utf-8",
    )
    assert tool.sealed_seqs(path, "aa", "canonical_edge_report") == [1]


def test_entries_without_code_are_not_matched(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, [{"seq": 1, "kind": "canonical_edge_report", "payload": {}}])
    assert tool.sealed_seqs(ledger, "aa", "canonical_edge_report") == []


def test_aborts_when_app_comes_from_another_tree(tmp_path: Path, capsys, monkeypatch) -> None:
    """Der Kern: lieber kein Ergebnis als ein falsches.

    Python setzt ``sys.path[0]`` auf das Skriptverzeichnis. Liegt dort kein
    ``app/``, gewinnt die editierbare Installation aus einem fremden Checkout —
    und das Skript misst dann eine ganz andere Code-Version als die, die es
    zu pruefen behauptet.
    """
    monkeypatch.chdir(tmp_path)
    ledger = _ledger(tmp_path, [])
    rc = tool.main(["--commit", "abc", "--ledger", str(ledger), "--root", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "ABBRUCH" in err
    assert "sys.path[0]" in err


def test_module_tree_resolves_the_repo_root() -> None:
    """``app/observability/edge_attestation.py`` -> Wurzel drei Ebenen darueber."""
    got = tool._module_tree("/x/repo/app/observability/edge_attestation.py")
    assert got == Path("/x/repo").resolve()
