"""G6 Task 4: die Konfiguration gehoert ins versiegelte Payload (R2-15).

Attestierungen pinnen Eingaben und Code — die Konfiguration nirgends. Dieselben
Zeilen, derselbe Commit, andere Schwellen in ``config/`` ergeben eine andere
Zahl, und nichts im Payload sagt es.

Der gefaehrlichste Teil dieser Aenderung ist nicht das Hinzufuegen, sondern die
Rueckwaertsvertraeglichkeit: ``_assemble_payload`` bedient Attestieren UND
Verifizieren. Entstuende beim Nachrechnen einer Alt-Zeile ein ``config``-Feld,
das sie nie hatte, fiele die gesamte Historie mit „hash mismatch" durch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.observability.edge_attestation import (
    _CONFIG_ABSENT,
    _assemble_payload,
    config_state,
)
from app.observability.evidence_window import EvidenceWindowReport
from app.truth.attestation import compute_attestation


def _report() -> EvidenceWindowReport:
    from app.observability.evidence_window import build_window_from_lines

    return build_window_from_lines(loop_lines=[], exec_lines=[])


def _payload(**kwargs):
    return _assemble_payload(
        _report(),
        [],
        {"commit": "a" * 40, "dirty": False},
        min_sample=30,
        p_threshold_bps=0.0,
        until=None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Rueckwaertsvertraeglichkeit — der eigentliche Einsatz
# ---------------------------------------------------------------------------


def test_legacy_payload_stays_byte_identical() -> None:
    """Ohne Angabe entsteht KEIN config-Schluessel — Alt-Zeilen bleiben pruefbar."""
    payload = _payload()
    assert "config" not in payload


def test_legacy_hash_is_unchanged() -> None:
    """Der Hash einer Alt-Zeile darf sich durch diese Aenderung nicht bewegen."""
    without = compute_attestation(_payload())["hash"]
    passthrough = compute_attestation(_payload(config=_CONFIG_ABSENT))["hash"]
    assert without == passthrough


def test_sealed_config_is_carried_through_not_recomputed() -> None:
    """Beim Verifizieren zaehlt die GESIEGELTE Konfiguration, nicht die heutige."""
    sealed = {"files": {"config/x.yaml": "b" * 64}, "config_sha256": "c" * 64}
    payload = _payload(config=sealed)
    assert payload["config"] == sealed


def test_a_null_config_is_still_a_statement() -> None:
    """``None`` heisst 'gemessen, nichts gefunden' — und ist NICHT dasselbe wie
    'nicht gestempelt'. Beide Faelle muessen unterscheidbar bleiben."""
    stamped_none = _payload(config=None)
    assert "config" in stamped_none and stamped_none["config"] is None
    assert compute_attestation(stamped_none)["hash"] != compute_attestation(_payload())["hash"]


# ---------------------------------------------------------------------------
# Der Stempel selbst
# ---------------------------------------------------------------------------


def test_config_state_hashes_every_tracked_file(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.yaml").write_text("x: 1\n", encoding="utf-8")
    (tmp_path / "config" / "b.json").write_text('{"y": 2}\n', encoding="utf-8")
    state = config_state(tmp_path)
    assert set(state["files"]) == {"config/a.yaml", "config/b.json"}
    expected = hashlib.sha256(
        json.dumps(state["files"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert state["config_sha256"] == expected


def test_a_changed_config_changes_the_rollup(tmp_path: Path) -> None:
    """Die Zusage des Stempels: eine andere Konfiguration ist ein anderer Hash."""
    (tmp_path / "config").mkdir()
    target = tmp_path / "config" / "a.yaml"
    target.write_text("threshold: 5\n", encoding="utf-8")
    before = config_state(tmp_path)["config_sha256"]
    target.write_text("threshold: 6\n", encoding="utf-8")
    assert config_state(tmp_path)["config_sha256"] != before


def test_env_is_never_pinned(tmp_path: Path) -> None:
    """Negativkontrolle: Geheimnisse gehoeren in kein Payload, auch nicht als Hash."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.yaml").write_text("x: 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("APP_API_KEY=supersecret\n", encoding="utf-8")
    (tmp_path / "config" / "secrets.env").write_text("TOKEN=abc\n", encoding="utf-8")
    files = config_state(tmp_path)["files"]
    assert files == {"config/a.yaml": files["config/a.yaml"]}


def test_missing_config_dir_is_fail_soft(tmp_path: Path) -> None:
    """Eine Attestierung darf an ihrem eigenen Stempel nicht scheitern."""
    assert config_state(tmp_path) is None


def test_real_repo_config_is_stamped() -> None:
    """Bindung ans echte Repo: der Stempel ist nicht leer."""
    root = Path(__file__).resolve().parents[2]
    state = config_state(root)
    assert state is not None
    assert len(state["files"]) >= 5
    assert len(state["config_sha256"]) == 64


# ---------------------------------------------------------------------------
# „Siegel gebrochen" und „nicht nachrechenbar" sind zwei verschiedene Befunde
# ---------------------------------------------------------------------------


def test_code_version_drift_is_named_and_still_fails(tmp_path: Path, monkeypatch) -> None:
    """Live gemessen: 47 von 64 Zeilen scheiterten als blosser ``hash_mismatch``,
    obwohl die Eingaben-Pins stimmten — die Trennlinie war exakt der gesiegelte
    Commit. Der Grund muss das sagen; ``ok`` bleibt trotzdem False."""
    import app.observability.edge_attestation as ea

    ledger = tmp_path / "ledger.jsonl"
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    loop.write_text("", encoding="utf-8")
    exec_.write_text("", encoding="utf-8")

    from app.truth.input_pin import pin_inputs

    inputs = pin_inputs([(ea.LOOP_ROLE, loop, []), (ea.EXEC_ROLE, exec_, [])], root=tmp_path)
    payload = _assemble_payload(
        _report(),
        inputs,
        {"commit": "d" * 40, "dirty": False},
        min_sample=30,
        p_threshold_bps=0.0,
        until=None,
    )
    record = {
        "seq": 1,
        "kind": ea.CANONICAL_EDGE_KIND,
        "payload": payload,
        "payload_hash": "0" * 64,
    }
    ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        ea, "git_code_state", lambda repo_dir=None: {"commit": "e" * 40, "dirty": False}
    )
    result = ea.verify_canonical_edge_seq(1, ledger_path=ledger, root=tmp_path)
    assert result.ok is False
    assert result.reason == "code_version_drift"
    assert "nicht nachrechenbar" in result.message


def test_same_commit_mismatch_stays_a_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    """Negativkontrolle: bei GLEICHEM Commit bleibt es der harte Befund —
    der neue Grund darf kein Sammelbecken werden."""
    import app.observability.edge_attestation as ea

    ledger = tmp_path / "ledger.jsonl"
    loop = tmp_path / "loop.jsonl"
    exec_ = tmp_path / "exec.jsonl"
    loop.write_text("", encoding="utf-8")
    exec_.write_text("", encoding="utf-8")

    from app.truth.input_pin import pin_inputs

    inputs = pin_inputs([(ea.LOOP_ROLE, loop, []), (ea.EXEC_ROLE, exec_, [])], root=tmp_path)
    payload = _assemble_payload(
        _report(),
        inputs,
        {"commit": "f" * 40, "dirty": False},
        min_sample=30,
        p_threshold_bps=0.0,
        until=None,
    )
    record = {
        "seq": 1,
        "kind": ea.CANONICAL_EDGE_KIND,
        "payload": payload,
        "payload_hash": "0" * 64,
    }
    ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        ea, "git_code_state", lambda repo_dir=None: {"commit": "f" * 40, "dirty": False}
    )
    result = ea.verify_canonical_edge_seq(1, ledger_path=ledger, root=tmp_path)
    assert result.ok is False
    assert result.reason == "hash_mismatch"
