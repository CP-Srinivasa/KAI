"""Truth-Observability in der Health-Probe (Voll-Audit 2026-08-06, WP7).

Blindstellen #2/#5/#6: Die Truth-Kette (attestation_ledger), die Outcome-/
Shadow-Streams und das Prä-Reg-Ledger tauchten in KEINER Freshness-Liste auf —
ein toter Writer sah für den Truth-Lint wie ein sauberes System aus, und ein
still scheiternder Anchor-Lauf fiel niemandem auf.

Design-Entscheidungen, hier gepinnt:
* attestation_ledger/alert_outcomes/shadow_candidate: required=False —
  Staleness greift erst, wenn die Datei existiert (fresh checkout stolpert
  nicht); Schwellen ≥ 2× legitime Stille (Health-Probe-Lehre).
* prereg_ledger: NUR Existenz, KEINE mtime-Schwelle (Prä-Regs dürfen Wochen
  ruhen — Stille ist kein Defekt, ein VERSCHWUNDENES Ledger schon). Der Check
  armiert nur, wenn artifacts/research/ existiert (Pi-Realität).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

from app.alerts.health_check import _check_data_freshness

NOW = datetime.now(UTC)


def _base(adir: Path) -> None:
    """Pflichtdateien der bestehenden Checks frisch anlegen."""
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "alert_audit.jsonl").write_text("{}\n", encoding="utf-8")
    (adir / "trading_loop_audit.jsonl").write_text("{}\n", encoding="utf-8")


def _age(path: Path, minutes: int) -> None:
    ts = time.time() - minutes * 60
    os.utime(path, (ts, ts))


def test_missing_truth_files_do_not_trip_fresh_checkout(tmp_path: Path) -> None:
    _base(tmp_path)
    issues, stale = _check_data_freshness(tmp_path, NOW)
    assert issues == []
    assert stale is False


def test_stale_attestation_ledger_is_flagged(tmp_path: Path) -> None:
    _base(tmp_path)
    ledger = tmp_path / "truth" / "attestation_ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{}\n", encoding="utf-8")
    _age(ledger, 3000)  # > 2880 min (2 Tage)
    issues, stale = _check_data_freshness(tmp_path, NOW)
    assert any(i.component == "truth_anchor_freshness" for i in issues)
    assert stale is True


def test_fresh_attestation_ledger_is_clean(tmp_path: Path) -> None:
    _base(tmp_path)
    ledger = tmp_path / "truth" / "attestation_ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{}\n", encoding="utf-8")
    issues, _ = _check_data_freshness(tmp_path, NOW)
    assert not any(i.component == "truth_anchor_freshness" for i in issues)


def test_stale_outcome_and_shadow_streams_are_flagged(tmp_path: Path) -> None:
    _base(tmp_path)
    outcomes = tmp_path / "alert_outcomes.jsonl"
    shadow = tmp_path / "shadow_candidate_ledger.jsonl"
    outcomes.write_text("{}\n", encoding="utf-8")
    shadow.write_text("{}\n", encoding="utf-8")
    _age(outcomes, 4400)  # > 4320 min (3 Tage)
    _age(shadow, 4400)
    issues, _ = _check_data_freshness(tmp_path, NOW)
    assert any(i.component == "outcome_writer_freshness" for i in issues)
    assert any(i.component == "shadow_writer_freshness" for i in issues)


def test_prereg_ledger_presence_only(tmp_path: Path) -> None:
    _base(tmp_path)
    research = tmp_path / "research"
    research.mkdir()
    # research/ existiert, Ledger fehlt => Wahrheitsverlust, critical.
    issues, stale = _check_data_freshness(tmp_path, NOW)
    assert any(i.component == "prereg_ledger_presence" and i.severity == "critical" for i in issues)
    assert stale is True

    # Ledger vorhanden => sauber, auch wenn es seit Wochen unverändert ist.
    ledger = research / "prereg_ledger.jsonl"
    ledger.write_text("{}\n", encoding="utf-8")
    _age(ledger, 60 * 24 * 30)  # 30 Tage alt — legitim
    issues, _ = _check_data_freshness(tmp_path, NOW)
    assert not any(i.component == "prereg_ledger_presence" for i in issues)


def test_no_research_dir_means_no_prereg_check(tmp_path: Path) -> None:
    """Fresh checkout ohne artifacts/research/ darf nicht alarmieren."""
    _base(tmp_path)
    issues, _ = _check_data_freshness(tmp_path, NOW)
    assert not any(i.component == "prereg_ledger_presence" for i in issues)


def test_stale_reconciliation_report_is_flagged(tmp_path: Path) -> None:
    """Ein still gestorbener kai-ln-reconcile.timer muss sichtbar werden.

    Der Timer lief ab 2026-08-08 alle 15 min, aber sein Ausgang stand in
    KEINER Wache — exakt das Muster, das den TV-Ingest 6 Tage unbemerkt tot
    liegen liess.
    """
    _base(tmp_path)
    report = tmp_path / "lightning" / "ln_reconciliation.jsonl"
    report.parent.mkdir(parents=True)
    report.write_text("{}\n", encoding="utf-8")
    _age(report, 60)  # > 45 min (3 verpasste 15-min-Laeufe)
    issues, stale = _check_data_freshness(tmp_path, NOW)
    assert any(i.component == "ln_reconcile_freshness" for i in issues)
    assert stale is True


def test_fresh_reconciliation_report_is_clean(tmp_path: Path) -> None:
    _base(tmp_path)
    report = tmp_path / "lightning" / "ln_reconciliation.jsonl"
    report.parent.mkdir(parents=True)
    report.write_text("{}\n", encoding="utf-8")
    _age(report, 20)  # ein verpasster Lauf ist noch kein Alarm
    issues, _ = _check_data_freshness(tmp_path, NOW)
    assert not any(i.component == "ln_reconcile_freshness" for i in issues)
