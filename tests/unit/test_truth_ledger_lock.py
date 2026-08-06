"""Truth-Ledger-Serialisierung (Voll-Audit 2026-08-06, WP3 / Befund P0-1).

Das Truth-Ledger war die EINZIGE append-only-Wahrheitsdatei ohne Write-Lock:
zwei parallele Writer (kai-truth-anchor.timer + canonical-edge-attest nach
einem Persistent=true-Boot-Storm, oder Timer + Operator-CLI) lasen denselben
Tip und schrieben beide seq=N+1 mit identischem prev_hash — die Kette forkt,
ein bereits gesetzter OTS-Anker beweist einen toten Tip, und append-only heißt:
nicht reparierbar.

Gepinnte Invarianten:

1. Konkurrierende Appends serialisieren — die Kette bleibt lückenlos gültig.
2. Lock nicht beschaffbar ⇒ ``TruthLedgerError`` und NICHTS geschrieben
   (fail-closed): ein unserialisierter Append ist schlimmer als ein
   verschobener (Anchor-Timer/CLI wiederholen).
3. ``append_lock(strict=True)`` hebt die best-effort-Semantik auf; die
   bestehenden best-effort-Aufrufer (Trade-Pfad) bleiben unverändert.
4. Auch der Prä-Reg-Ledger-Append läuft unter dem Lock (PIPE_BUF-Interleaving
   zweier Prozesse würde versiegelte Einträge als "malformed" verlieren).
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from app.core.file_lock import FileLockError, append_lock
from app.truth.ledger import TruthLedgerError, append_attestation, verify_ledger


def _break_platform_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("lock refused (test)")

    if sys.platform.startswith("win"):
        import msvcrt

        monkeypatch.setattr(msvcrt, "locking", _refuse)
    else:
        import fcntl

        monkeypatch.setattr(fcntl, "flock", _refuse)


# --------------------------------------------------------------------------- #
# 1. Serialisierung unter Konkurrenz
# --------------------------------------------------------------------------- #


def test_concurrent_appends_never_fork_chain(tmp_path: Path) -> None:
    ledger = tmp_path / "truth.jsonl"
    threads = 8
    per_thread = 3
    barrier = threading.Barrier(threads)
    errors: list[BaseException] = []

    def work(worker: int) -> None:
        try:
            barrier.wait(timeout=30)
            for i in range(per_thread):
                append_attestation(
                    "verdict",
                    f"subject-{worker}-{i}",
                    {"worker": worker, "i": i},
                    path=ledger,
                    mirror_audit=False,
                )
        except BaseException as exc:  # noqa: BLE001 — im Haupt-Thread re-raisen
            errors.append(exc)

    pool = [threading.Thread(target=work, args=(w,)) for w in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(timeout=60)

    assert not errors, errors
    result = verify_ledger(ledger)
    assert result["ok"], result["errors"]
    assert result["records"] == threads * per_thread
    seqs = [
        json.loads(line)["seq"]
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert seqs == list(range(1, threads * per_thread + 1))


# --------------------------------------------------------------------------- #
# 2. Fail-closed bei Lock-Fehler
# --------------------------------------------------------------------------- #


def test_lock_failure_refuses_append_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "truth.jsonl"
    append_attestation("verdict", "s-1", {"a": 1}, path=ledger, mirror_audit=False)
    before = ledger.read_text(encoding="utf-8")

    _break_platform_lock(monkeypatch)
    with pytest.raises(TruthLedgerError, match="unserialized"):
        append_attestation("verdict", "s-2", {"a": 2}, path=ledger, mirror_audit=False)

    assert ledger.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# 3. strict-Semantik des Lock-Helfers (best-effort-Verhalten unverändert)
# --------------------------------------------------------------------------- #


def test_append_lock_strict_raises_but_default_stays_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "x.jsonl"
    _break_platform_lock(monkeypatch)

    with pytest.raises(FileLockError):
        with append_lock(target, strict=True):
            pytest.fail("strict darf den Block bei Lock-Fehler nicht betreten")

    entered = False
    with append_lock(target):  # best-effort: Trade-Pfad darf nicht blocken
        entered = True
    assert entered


def test_append_lock_strict_raises_when_lockfile_unopenable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "x.jsonl"
    real_open = Path.open

    def refuse_lockfile(self: Path, *args: Any, **kwargs: Any) -> Any:
        if str(self).endswith(".lock"):
            raise OSError("no lockfile (test)")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_lockfile)
    with pytest.raises(FileLockError):
        with append_lock(target, strict=True):
            pytest.fail("strict darf ohne Lockfile nicht betreten werden")


# --------------------------------------------------------------------------- #
# 4. Prä-Reg-Ledger-Append serialisiert
# --------------------------------------------------------------------------- #


def test_prereg_ledger_concurrent_records_stay_parseable(tmp_path: Path) -> None:
    from app.research.prereg_ledger import PreRegistrationLedger, register

    path = tmp_path / "prereg.jsonl"
    ledger = PreRegistrationLedger(path)
    threads = 6
    barrier = threading.Barrier(threads)
    errors: list[BaseException] = []

    def work(worker: int) -> None:
        try:
            barrier.wait(timeout=30)
            entry = register(
                name=f"hyp_{worker}",
                direction="long",
                horizon="1d",
                success_criteria=f"criteria {worker} " + "x" * 256,
                sample_size_target=100,
                created_at_utc="2026-08-06T05:00:00+00:00",
            )
            ledger.record(entry)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    pool = [threading.Thread(target=work, args=(w,)) for w in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(timeout=60)

    assert not errors, errors
    entries = ledger.entries()
    assert len(entries) == threads  # keine interleaved/verlorenen Zeilen
    raw_lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(raw_lines) == threads
    for line in raw_lines:
        json.loads(line)  # jede Zeile für sich vollständig
