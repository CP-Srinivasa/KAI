"""Externally-recomputable canonical-edge attestation (B5b).

Pins the behaviour that lets a third party recompute a sealed canonical-edge claim
from ONE command:
  * the attested payload pins every input artifact (sha256 + line count), the
    recompute knobs, and the code commit (fail-soft);
  * ``--verify`` reproduces the report from the pinned prefixes and re-hashes it;
  * append-only growth verifies OK; a shrunk/tampered input or a forged hash FAILs;
  * a ``--until`` bound is reproduced exactly;
  * legacy (pre-B5b) entries without ``inputs`` fall back to a plain hash check.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.observability.edge_attestation import (
    build_canonical_edge_payload,
    git_code_state,
    verify_canonical_edge_seq,
)
from app.truth.ledger import append_attestation

_CLOSE = (
    '{{"event_type": "position_closed", "symbol": "{sym}", "position_side": "long",'
    ' "entry_price": 100.0, "exit_price": {exit}, "quantity": 1.0, "reason": "tp",'
    ' "trade_pnl_usd": {pnl}, "fee_usd": 0.1, "timestamp_utc": "{ts}",'
    ' "signal_source": "autonomous_generator"}}'
)


def _write_dataset(tmp_path: Path, *, exec_rows: list[str], loop_rows: list[str] | None = None):
    loop = tmp_path / "loop.jsonl"
    execp = tmp_path / "exec.jsonl"
    loop_rows = loop_rows or ['{"status": "completed", "started_at": "2026-06-27T15:00:00+00:00"}']
    loop.write_text("\n".join(loop_rows) + "\n", encoding="utf-8")
    execp.write_text("\n".join(exec_rows) + "\n", encoding="utf-8")
    return loop, execp


def _attest(tmp_path: Path, loop: Path, execp: Path, ledger: Path, **kwargs) -> dict:
    # repo_dir points at a NON-git dir so ``code`` is deterministically None,
    # decoupling the payload hash from the ambient git state of the test host.
    _report, payload = build_canonical_edge_payload(
        loop_audit_path=loop,
        exec_audit_path=execp,
        root=tmp_path,
        repo_dir=tmp_path,
        **kwargs,
    )
    return append_attestation(
        "canonical_edge_report", None, payload, path=ledger, mirror_audit=False
    )


# --- payload shape -----------------------------------------------------------------


def test_payload_pins_inputs_recompute_and_code(tmp_path: Path) -> None:
    loop, execp = _write_dataset(
        tmp_path,
        exec_rows=[
            _CLOSE.format(sym="BTC/USDT", exit=101.0, pnl=1.0, ts="2026-06-27T15:10:00+00:00")
        ],
    )
    _report, payload = build_canonical_edge_payload(
        loop_audit_path=loop, exec_audit_path=execp, root=tmp_path, repo_dir=tmp_path
    )
    inputs = payload["inputs"]
    assert [p["role"] for p in inputs] == ["exec_audit", "loop_audit"]  # deterministically sorted
    for pin in inputs:
        assert set(pin) == {"role", "path", "sha256", "lines"}
        assert len(pin["sha256"]) == 64
    assert payload["recompute"] == {"min_sample": 8, "p_threshold_bps": 0.0, "until": None}
    assert payload["code"] is None  # fail-soft: tmp_path is not a git repo
    # the report sections still ride along unchanged
    assert "edge" in payload and "window" in payload


def test_git_code_state_fail_soft_on_non_repo(tmp_path: Path) -> None:
    assert git_code_state(repo_dir=tmp_path) is None


def test_git_code_state_reports_commit_and_dirty(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_CONFIG_GLOBAL": str(tmp_path / "noglobal"), "GIT_CONFIG_SYSTEM": ""}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "x",
        ],
        cwd=repo,
        check=True,
        env={**env, "PATH": __import__("os").environ.get("PATH", "")},
    )
    state = git_code_state(repo_dir=repo)
    assert state is not None
    assert len(state["commit"]) == 40
    assert state["dirty"] is False
    (repo / "untracked.txt").write_text("x", encoding="utf-8")
    dirty = git_code_state(repo_dir=repo)
    assert dirty is not None and dirty["dirty"] is True


# --- verify OK / append-only growth -------------------------------------------------


def test_verify_ok_on_fresh_attest(tmp_path: Path) -> None:
    loop, execp = _write_dataset(
        tmp_path,
        exec_rows=[
            _CLOSE.format(sym="BTC/USDT", exit=101.0, pnl=1.0, ts="2026-06-27T15:10:00+00:00")
        ],
    )
    ledger = tmp_path / "truth.jsonl"
    rec = _attest(tmp_path, loop, execp, ledger)
    result = verify_canonical_edge_seq(rec["seq"], ledger_path=ledger, root=tmp_path)
    assert result.ok
    assert result.message == f"VERIFY OK seq={rec['seq']}"


def test_verify_ok_after_append_only_growth(tmp_path: Path) -> None:
    loop, execp = _write_dataset(
        tmp_path,
        exec_rows=[
            _CLOSE.format(sym="BTC/USDT", exit=101.0, pnl=1.0, ts="2026-06-27T15:10:00+00:00")
        ],
    )
    ledger = tmp_path / "truth.jsonl"
    rec = _attest(tmp_path, loop, execp, ledger)
    with execp.open("a", encoding="utf-8") as fh:
        fh.write(
            _CLOSE.format(sym="ETH/USDT", exit=110.0, pnl=10.0, ts="2026-06-27T18:00:00+00:00")
            + "\n"
        )
    result = verify_canonical_edge_seq(rec["seq"], ledger_path=ledger, root=tmp_path)
    assert result.ok, result.message  # growth does not break the pinned prefix


# --- verify FAIL modes --------------------------------------------------------------


def test_verify_fail_on_prefix_tamper(tmp_path: Path) -> None:
    loop, execp = _write_dataset(
        tmp_path,
        exec_rows=[
            _CLOSE.format(sym="BTC/USDT", exit=101.0, pnl=1.0, ts="2026-06-27T15:10:00+00:00")
        ],
    )
    ledger = tmp_path / "truth.jsonl"
    rec = _attest(tmp_path, loop, execp, ledger)
    execp.write_text(
        _CLOSE.format(sym="BTC/USDT", exit=200.0, pnl=100.0, ts="2026-06-27T15:10:00+00:00") + "\n",
        encoding="utf-8",
    )
    result = verify_canonical_edge_seq(rec["seq"], ledger_path=ledger, root=tmp_path)
    assert not result.ok
    assert result.reason == "input_pin_mismatch"


def test_verify_fail_on_shrink(tmp_path: Path) -> None:
    loop, execp = _write_dataset(
        tmp_path,
        exec_rows=[
            _CLOSE.format(sym="BTC/USDT", exit=101.0, pnl=1.0, ts="2026-06-27T15:10:00+00:00"),
            _CLOSE.format(sym="BTC/USDT", exit=99.0, pnl=-1.0, ts="2026-06-27T15:20:00+00:00"),
        ],
    )
    ledger = tmp_path / "truth.jsonl"
    rec = _attest(tmp_path, loop, execp, ledger)
    execp.write_text("", encoding="utf-8")  # shrank
    result = verify_canonical_edge_seq(rec["seq"], ledger_path=ledger, root=tmp_path)
    assert not result.ok
    assert result.reason == "input_pin_mismatch"


def test_verify_fail_on_forged_payload_hash(tmp_path: Path) -> None:
    loop, execp = _write_dataset(
        tmp_path,
        exec_rows=[
            _CLOSE.format(sym="BTC/USDT", exit=101.0, pnl=1.0, ts="2026-06-27T15:10:00+00:00")
        ],
    )
    ledger = tmp_path / "truth.jsonl"
    rec = _attest(tmp_path, loop, execp, ledger)
    doc = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    doc["payload_hash"] = "f" * 64  # forge the sealed hash
    ledger.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    result = verify_canonical_edge_seq(rec["seq"], ledger_path=ledger, root=tmp_path)
    assert not result.ok
    assert result.reason == "hash_mismatch"


def test_verify_fail_on_missing_seq(tmp_path: Path) -> None:
    ledger = tmp_path / "truth.jsonl"
    ledger.write_text("", encoding="utf-8")
    result = verify_canonical_edge_seq(7, ledger_path=ledger, root=tmp_path)
    assert not result.ok
    assert result.reason == "seq_not_found"


# --- --until reproduction -----------------------------------------------------------


def test_until_bounds_window_and_verifies(tmp_path: Path) -> None:
    loop, execp = _write_dataset(
        tmp_path,
        exec_rows=[
            _CLOSE.format(sym="BTC/USDT", exit=101.0, pnl=1.0, ts="2026-06-27T15:10:00+00:00"),
            _CLOSE.format(sym="BTC/USDT", exit=102.0, pnl=2.0, ts="2026-06-27T15:20:00+00:00"),
            _CLOSE.format(sym="BTC/USDT", exit=103.0, pnl=3.0, ts="2026-06-27T18:00:00+00:00"),
        ],
    )
    ledger = tmp_path / "truth.jsonl"
    until = datetime(2026, 6, 27, 15, 30, tzinfo=UTC)
    report, payload = build_canonical_edge_payload(
        loop_audit_path=loop,
        exec_audit_path=execp,
        until=until,
        root=tmp_path,
        repo_dir=tmp_path,
    )
    # the 18:00 close is beyond the bound -> only 2 trades sealed
    assert report.edge.trade_count == 2
    assert payload["recompute"]["until"] == until.isoformat()
    rec = append_attestation(
        "canonical_edge_report", None, payload, path=ledger, mirror_audit=False
    )
    # even after appending MORE rows, the bounded window reproduces exactly
    with execp.open("a", encoding="utf-8") as fh:
        fh.write(
            _CLOSE.format(sym="Z/USDT", exit=999.0, pnl=1.0, ts="2026-06-27T15:25:00+00:00") + "\n"
        )
    result = verify_canonical_edge_seq(rec["seq"], ledger_path=ledger, root=tmp_path)
    assert result.ok, result.message


def test_unbounded_control_sees_all_three(tmp_path: Path) -> None:
    loop, execp = _write_dataset(
        tmp_path,
        exec_rows=[
            _CLOSE.format(sym="BTC/USDT", exit=101.0, pnl=1.0, ts="2026-06-27T15:10:00+00:00"),
            _CLOSE.format(sym="BTC/USDT", exit=102.0, pnl=2.0, ts="2026-06-27T15:20:00+00:00"),
            _CLOSE.format(sym="BTC/USDT", exit=103.0, pnl=3.0, ts="2026-06-27T18:00:00+00:00"),
        ],
    )
    report, _payload = build_canonical_edge_payload(
        loop_audit_path=loop, exec_audit_path=execp, root=tmp_path, repo_dir=tmp_path
    )
    assert report.edge.trade_count == 3  # proves --until actually bounded above


# --- legacy (pre-B5b) ---------------------------------------------------------------


def test_verify_legacy_entry_without_inputs(tmp_path: Path) -> None:
    loop, execp = _write_dataset(
        tmp_path,
        exec_rows=[
            _CLOSE.format(sym="BTC/USDT", exit=101.0, pnl=1.0, ts="2026-06-27T15:10:00+00:00")
        ],
    )
    ledger = tmp_path / "truth.jsonl"
    report, _payload = build_canonical_edge_payload(
        loop_audit_path=loop, exec_audit_path=execp, root=tmp_path, repo_dir=tmp_path
    )
    # a pre-B5b record stored only the bare report dict (no inputs/recompute/code)
    rec = append_attestation(
        "canonical_edge_report", None, report.to_dict(), path=ledger, mirror_audit=False
    )
    result = verify_canonical_edge_seq(rec["seq"], ledger_path=ledger, root=tmp_path)
    assert result.ok
    assert result.reason == "legacy_no_inputs"
    assert "pre-B5b" in result.message


def test_verify_legacy_flags_tampered_payload(tmp_path: Path) -> None:
    ledger = tmp_path / "truth.jsonl"
    rec = append_attestation(
        "canonical_edge_report",
        None,
        {"window": {"ended_at": "x"}},
        path=ledger,
        mirror_audit=False,
    )
    doc = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    doc["payload"]["window"]["ended_at"] = "TAMPERED"
    ledger.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    result = verify_canonical_edge_seq(rec["seq"], ledger_path=ledger, root=tmp_path)
    assert not result.ok
    assert result.reason == "legacy_hash_mismatch"
