"""Contract-Tests fuer das G4-Ratchet „kein neuer Strom ohne Konsument".

Die Tests bauen ein Miniatur-Repo im ``tmp_path`` und pruefen beide Richtungen:
ein neuer Strom ohne Abnehmer faellt durch, ein korrekt deklarierter geht durch.
Dazu die Negativkontrollen, die ein Ratchet erst glaubwuerdig machen — ein
Leser, der den Strom gar nicht nennt, und ein Strom, den nur sein eigener
Schreiber kennt, muessen ebenfalls durchfallen.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RATCHET_PATH = REPO_ROOT / "scripts" / "stream_consumer_ratchet.py"


def _load_ratchet() -> ModuleType:
    spec = importlib.util.spec_from_file_location("stream_consumer_ratchet", RATCHET_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ratchet = _load_ratchet()


VALID_CONTRACT = {
    "reader": "app/alerts/new_reader.py",
    "failure_consequence": "Der Health-Check meldet keinen Ausfall des Entry-Watchers mehr.",
    "freshness_check": "_FRESHNESS_PER_FILE_MIN",
    "failure_would_be_noticed_by": "kai-premium-healthcheck.timer",
    "time_to_notice": "30 Minuten (6 verpasste Zyklen)",
    "decision_that_would_change": "Der Operator wuerde den Entry-Watcher nicht neu starten.",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Miniatur-Repo: ein Bestandsstrom, ein Schreiber, ein Leser, eine Freshness-Zeile."""
    app = tmp_path / "app"
    (app / "alerts").mkdir(parents=True)
    (app / "execution").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config").mkdir()

    (app / "execution" / "writer.py").write_text(
        'PATH = "artifacts/legacy_audit.jsonl"\n', encoding="utf-8"
    )
    (app / "alerts" / "health_check.py").write_text(
        '_FRESHNESS_PER_FILE_MIN: dict[str, int] = {\n    "legacy_audit.jsonl": 480,\n}\n',
        encoding="utf-8",
    )
    _write_baseline(tmp_path, ["legacy_audit.jsonl"])
    _write_contracts(tmp_path, {})
    return tmp_path


def _write_baseline(repo: Path, streams: list[str]) -> None:
    (repo / "scripts" / "stream_baseline.json").write_text(
        json.dumps({"count": len(streams), "streams": streams}), encoding="utf-8"
    )


def _write_contracts(
    repo: Path, streams: dict[str, dict[str, str]], inert: list[str] | None = None
) -> None:
    (repo / "config" / "stream_contracts.json").write_text(
        json.dumps({"streams": streams, "intentionally_inert": inert or []}), encoding="utf-8"
    )


def _add_freshness_entry(repo: Path, name: str, minutes: int = 60) -> None:
    path = repo / "app" / "alerts" / "health_check.py"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("}\n", f'    "{name}": {minutes},\n}}\n'), encoding="utf-8")


def _run(repo: Path) -> ratchet.Verdict:
    streams = ratchet.discover_streams(repo / "app", repo)
    return ratchet.evaluate(
        streams,
        ratchet.load_baseline(repo / "scripts" / "stream_baseline.json"),
        ratchet.load_contracts(repo / "config" / "stream_contracts.json"),
        ratchet.freshness_registry(repo / "app" / "alerts" / "health_check.py"),
        repo,
    )


def _add_new_stream(repo: Path, name: str = "new_audit.jsonl", *, with_reader: bool = True) -> None:
    (repo / "app" / "execution" / "new_writer.py").write_text(
        f'PATH = "artifacts/{name}"\n', encoding="utf-8"
    )
    if with_reader:
        (repo / "app" / "alerts" / "new_reader.py").write_text(
            f'from pathlib import Path\n\nSOURCE = "{name}"\n'
            "\n\ndef read(adir: Path) -> int:\n"
            '    return sum(1 for _ in (adir / SOURCE).open(encoding="utf-8"))\n',
            encoding="utf-8",
        )


# --------------------------------------------------------------------------
# Inventar
# --------------------------------------------------------------------------


def test_inventory_normalises_paths_to_basenames(repo: Path) -> None:
    streams = ratchet.discover_streams(repo / "app", repo)
    assert "legacy_audit.jsonl" in streams
    # Die Freshness-Registry nennt den Strom ebenfalls — sie wird spaeter als
    # Konsument ausgeschlossen, taucht aber im Inventar auf.
    assert streams["legacy_audit.jsonl"].modules == (
        "app/alerts/health_check.py",
        "app/execution/writer.py",
    )


def test_suffix_fragments_are_not_streams(repo: Path) -> None:
    """`.jsonl` und `_regime.jsonl` sind Suffix-Konstanten, keine Stroeme."""
    (repo / "app" / "execution" / "frag.py").write_text(
        'SUFFIX = ".jsonl"\nOTHER = "_regime.jsonl"\n', encoding="utf-8"
    )
    streams = ratchet.discover_streams(repo / "app", repo)
    assert ".jsonl" not in streams
    assert "_regime.jsonl" not in streams


def test_dynamic_stream_names_are_tracked_as_family(repo: Path) -> None:
    (repo / "app" / "execution" / "dyn.py").write_text(
        "def p(sym: str) -> str:\n    return f'{sym}_regime.jsonl'\n", encoding="utf-8"
    )
    streams = ratchet.discover_streams(repo / "app", repo)
    assert "app/execution/dyn.py::*_regime.jsonl" in streams
    assert streams["app/execution/dyn.py::*_regime.jsonl"].dynamic is True


# --------------------------------------------------------------------------
# Das Gate — beide Richtungen (RUNTIME_PROOF des Sprints)
# --------------------------------------------------------------------------


def test_new_stream_without_contract_is_rejected(repo: Path) -> None:
    _add_new_stream(repo)
    verdict = _run(repo)
    assert verdict.ok is False
    assert any("NEEDS_CONSUMER_FIRST" in v for v in verdict.violations)


def test_correctly_declared_new_stream_passes(repo: Path) -> None:
    _add_new_stream(repo)
    _add_freshness_entry(repo, "new_audit.jsonl")
    _write_contracts(repo, {"new_audit.jsonl": dict(VALID_CONTRACT)})
    verdict = _run(repo)
    assert verdict.violations == []
    assert verdict.accepted == ["new_audit.jsonl"]


def test_baseline_streams_are_not_examined(repo: Path) -> None:
    """Der Bestand wird ausdruecklich NICHT rueckwirkend erzwungen."""
    verdict = _run(repo)
    assert verdict.ok is True
    assert verdict.new_streams == []


def test_disappeared_stream_is_reported_but_not_a_violation(repo: Path) -> None:
    (repo / "app" / "execution" / "writer.py").unlink()
    (repo / "app" / "alerts" / "health_check.py").write_text(
        "_FRESHNESS_PER_FILE_MIN: dict[str, int] = {}\n", encoding="utf-8"
    )
    verdict = _run(repo)
    assert verdict.disappeared == ["legacy_audit.jsonl"]
    assert verdict.ok is True


# --------------------------------------------------------------------------
# Negativkontrollen — woran ein Papier-Vertrag scheitert
# --------------------------------------------------------------------------


def test_reader_that_does_not_reference_the_stream_is_rejected(repo: Path) -> None:
    _add_new_stream(repo)
    _add_freshness_entry(repo, "new_audit.jsonl")
    (repo / "app" / "alerts" / "new_reader.py").write_text(
        "def read() -> None:\n    return None\n", encoding="utf-8"
    )
    _write_contracts(repo, {"new_audit.jsonl": dict(VALID_CONTRACT)})
    verdict = _run(repo)
    assert any("nennt 'new_audit.jsonl' nicht" in v for v in verdict.violations)


def test_missing_reader_file_is_rejected(repo: Path) -> None:
    _add_new_stream(repo, with_reader=False)
    _add_freshness_entry(repo, "new_audit.jsonl")
    _write_contracts(repo, {"new_audit.jsonl": dict(VALID_CONTRACT)})
    verdict = _run(repo)
    assert any("existiert nicht" in v for v in verdict.violations)


def test_stream_known_only_to_its_own_writer_is_rejected(repo: Path) -> None:
    """Die Klasse ``telegram_webhook_rejections.jsonl``: 85 Schreibstellen, 0 Leser."""
    _add_new_stream(repo, with_reader=False)
    _add_freshness_entry(repo, "new_audit.jsonl")
    contract = dict(VALID_CONTRACT, reader="app/execution/new_writer.py")
    _write_contracts(repo, {"new_audit.jsonl": contract})
    verdict = _run(repo)
    assert any("Schreiber und Leser koennen nicht" in v for v in verdict.violations)


def test_missing_freshness_entry_is_rejected(repo: Path) -> None:
    _add_new_stream(repo)
    _write_contracts(repo, {"new_audit.jsonl": dict(VALID_CONTRACT)})
    verdict = _run(repo)
    assert any("_FRESHNESS_PER_FILE_MIN" in v for v in verdict.violations)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("failure_would_be_noticed_by", "niemand"),
        ("time_to_notice", "nie"),
        ("decision_that_would_change", "keine"),
        ("failure_consequence", "  "),
        ("decision_that_would_change", "None."),
    ],
)
def test_empty_answers_are_needs_consumer_first(repo: Path, field_name: str, value: str) -> None:
    _add_new_stream(repo)
    _add_freshness_entry(repo, "new_audit.jsonl")
    _write_contracts(repo, {"new_audit.jsonl": dict(VALID_CONTRACT, **{field_name: value})})
    verdict = _run(repo)
    assert any(field_name in v and "NEEDS_CONSUMER_FIRST" in v for v in verdict.violations)


def test_missing_required_fields_are_rejected(repo: Path) -> None:
    _add_new_stream(repo)
    _add_freshness_entry(repo, "new_audit.jsonl")
    contract = {k: v for k, v in VALID_CONTRACT.items() if k != "decision_that_would_change"}
    _write_contracts(repo, {"new_audit.jsonl": contract})
    verdict = _run(repo)
    assert any("decision_that_would_change" in v for v in verdict.violations)


def test_freshness_registry_alone_is_not_a_consumer(repo: Path) -> None:
    """Die Ueberwachungszeile darf die Konsumenten-Regel nicht selbst erfuellen."""
    _add_new_stream(repo, with_reader=False)
    _add_freshness_entry(repo, "new_audit.jsonl")
    contract = dict(VALID_CONTRACT, reader="app/alerts/health_check.py")
    _write_contracts(repo, {"new_audit.jsonl": contract})
    verdict = _run(repo)
    assert any("Ueberwachungszeile ist kein Konsument" in v for v in verdict.violations)
    assert any("Schreiber und Leser koennen" in v for v in verdict.violations)


def test_intentionally_inert_streams_are_exempt_and_named(repo: Path) -> None:
    _add_new_stream(repo, with_reader=False)
    _write_contracts(repo, {}, inert=["new_audit.jsonl"])
    verdict = _run(repo)
    assert verdict.ok is True
    assert verdict.inert == ["new_audit.jsonl"]
    assert verdict.accepted == []


# --------------------------------------------------------------------------
# Bindung an das echte Repo
# --------------------------------------------------------------------------


def test_freshness_registry_reads_the_real_health_check() -> None:
    keys = ratchet.freshness_registry(REPO_ROOT / "app" / "alerts" / "health_check.py")
    assert "alert_audit.jsonl" in keys
    assert "trading_loop_audit.jsonl" in keys


def test_repo_gate_is_green_and_baseline_is_current() -> None:
    """Positivkontrolle am echten Repo: die Baseline darf nicht driften."""
    streams = ratchet.discover_streams(ratchet.SCAN_ROOT, ratchet.REPO_ROOT)
    baseline = ratchet.load_baseline(ratchet.BASELINE_PATH)
    assert baseline, "Baseline fehlt — scripts/stream_baseline.json einchecken"
    verdict = ratchet.evaluate(
        streams,
        baseline,
        ratchet.load_contracts(ratchet.CONTRACTS_PATH),
        ratchet.freshness_registry(ratchet.HEALTH_CHECK_PATH),
        ratchet.REPO_ROOT,
    )
    assert verdict.violations == []


def test_cli_main_returns_zero_on_clean_repo(capsys: pytest.CaptureFixture[str]) -> None:
    assert ratchet.main([]) == 0
    assert "kein unvertraglicher Zuwachs" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Konstanten statt kopierter Literale (G6-Haertung)
# --------------------------------------------------------------------------


def test_reader_referencing_the_stream_via_imported_constant_counts(repo: Path) -> None:
    """Haus-Muster `from app.x import STREAM`: ein Gate, das nur Literale sieht,
    bestraft genau das und belohnt kopierte Strings."""
    (repo / "app" / "execution" / "new_writer.py").write_text(
        'NEW_STREAM = "new_audit.jsonl"\n', encoding="utf-8"
    )
    (repo / "app" / "alerts" / "new_reader.py").write_text(
        "from app.execution.new_writer import NEW_STREAM\n\n\n"
        "def read(adir):\n    return (adir / NEW_STREAM).read_text()\n",
        encoding="utf-8",
    )
    _add_freshness_entry(repo, "new_audit.jsonl")
    _write_contracts(repo, {"new_audit.jsonl": dict(VALID_CONTRACT)})
    verdict = _run(repo)
    assert verdict.violations == []
    assert verdict.accepted == ["new_audit.jsonl"]


def test_unrelated_name_does_not_count_as_a_reference(repo: Path) -> None:
    """Negativkontrolle zur Haertung: ein fremder Name oeffnet kein Schlupfloch."""
    (repo / "app" / "execution" / "new_writer.py").write_text(
        'NEW_STREAM = "new_audit.jsonl"\n', encoding="utf-8"
    )
    (repo / "app" / "alerts" / "new_reader.py").write_text(
        "SOMETHING_ELSE = 1\n\n\ndef read():\n    return SOMETHING_ELSE\n",
        encoding="utf-8",
    )
    _add_freshness_entry(repo, "new_audit.jsonl")
    _write_contracts(repo, {"new_audit.jsonl": dict(VALID_CONTRACT)})
    verdict = _run(repo)
    assert any("nennt 'new_audit.jsonl' nicht" in v for v in verdict.violations)


def test_health_check_with_a_real_probe_is_a_consumer(repo: Path) -> None:
    """Die Schwellen-Zeile zaehlt nicht — eine echte Sonde in derselben Datei schon."""
    (repo / "app" / "execution" / "new_writer.py").write_text(
        'NEW_STREAM = "new_audit.jsonl"\n', encoding="utf-8"
    )
    _add_freshness_entry(repo, "new_audit.jsonl")
    path = repo / "app" / "alerts" / "health_check.py"
    path.write_text(
        "from app.execution.new_writer import NEW_STREAM\n\n"
        + path.read_text(encoding="utf-8")
        + "\n\ndef _check_new(adir):\n    return (adir / NEW_STREAM).exists()\n",
        encoding="utf-8",
    )
    contract = dict(VALID_CONTRACT, reader="app/alerts/health_check.py")
    _write_contracts(repo, {"new_audit.jsonl": contract})
    verdict = _run(repo)
    assert verdict.violations == []
