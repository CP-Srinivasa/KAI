"""Betriebsblock des Operator-Digests (MindBlow 2.0, E2).

Der Block bestaetigt taeglich, was Health nicht auf Frische prueft (Pi-Tages-
backup, Restore-Drill) und was es nur als Fehler kennt (Vault-Alter, KI-Kosten).
Geprueft wird: welcher Beleg zaehlt, wann ein Teil ⚠️ bekommt, dass ein kaputter
Teil den Rest nicht mitnimmt — und dass der Block im Digest oben steht, wo die
4096-Kuerzung ihn nie erreicht.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import digest_ops_block as ob  # noqa: E402
import operator_digest as od  # noqa: E402

from app.observability.offpi_receipts import append_receipt  # noqa: E402

NOW = datetime(2026, 9, 26, 7, 30, tzinfo=UTC)


def _audit(artifacts: Path, *rows: dict[str, Any]) -> None:
    artifacts.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r) + "\n" for r in rows)
    (artifacts / "backup_audit.jsonl").write_text(text, encoding="utf-8")


def _drill(artifacts: Path, name: str, status: str) -> None:
    folder = artifacts / "ops" / "backup_drill"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps({"status": status}), encoding="utf-8")


def _receipt(artifacts: Path, generation_ts: str, probe: str = "PASS") -> None:
    append_receipt(
        artifacts,
        {
            "schema": "offpi_receipt/v1",
            "ts_utc": generation_ts,
            "vault_id": "v",
            "generation": generation_ts.replace(":", "-"),
            "generation_ts_utc": generation_ts,
            "probe": probe,
        },
    )


def test_backup_takes_newest_run_row_not_ledger_corrections(tmp_path: Path) -> None:
    _audit(
        tmp_path,
        {"ts": "2026-09-25T01:48:00Z", "status": "ok"},
        {"ts": "2026-09-26T01:48:43Z", "status": "ok", "remote_status": "local_only"},
        {"ts": "2026-09-26T02:00:00Z", "event": "backup_ledger_correction"},
        {"kaputt": True},
    )
    got = ob.collect_backup_daily(tmp_path, NOW)
    assert got["status"] == "ok"
    assert got["ts"] == datetime(2026, 9, 26, 1, 48, 43, tzinfo=UTC)
    assert ob._backup_part(got) == "Pi 26.09. 01:48Z ok"


@pytest.mark.parametrize(
    ("row", "expect"),
    [
        ({"ts": "2026-09-24T01:48:00Z", "status": "ok"}, "⚠️ Pi 24.09. 01:48Z ok, 54 h alt"),
        ({"ts": "2026-09-26T01:48:00Z", "status": "fail_no_passphrase"}, "⚠️ Pi 26.09. 01:48Z"),
    ],
)
def test_backup_warns_when_stale_or_failed(
    tmp_path: Path, row: dict[str, Any], expect: str
) -> None:
    _audit(tmp_path, row)
    assert ob._backup_part(ob.collect_backup_daily(tmp_path, NOW)).startswith(expect)


def test_missing_backup_ledger_is_a_warning_not_silence(tmp_path: Path) -> None:
    assert ob._backup_part(ob.collect_backup_daily(tmp_path, NOW)) == "⚠️ Pi kein Lauf belegt"


def test_drill_takes_newest_timestamped_proof_and_ignores_other_probes(tmp_path: Path) -> None:
    _drill(tmp_path, "2026-09-01T08-02-08.259582577Z.json", "PASS")
    _drill(tmp_path, "2026-09-23T11-56-18.957391864Z.json", "PASS")
    _drill(tmp_path, "standby_probe_20260901T080526Z.json", "FAIL")
    got = ob.collect_drill(tmp_path, NOW)
    assert got["ts"] == datetime(2026, 9, 23, 11, 56, 18, tzinfo=UTC)
    assert ob._drill_part(got) == "Drill 23.09. PASS"


def test_drill_fail_or_overdue_is_flagged(tmp_path: Path) -> None:
    _drill(tmp_path, "2026-09-23T11-56-18Z.json", "FAIL")
    assert ob._drill_part(ob.collect_drill(tmp_path, NOW)) == "⚠️ Drill 23.09. FAIL"
    later = NOW + timedelta(days=40)
    _drill(tmp_path, "2026-09-24T04-10-00Z.json", "PASS")
    assert ob._drill_part(ob.collect_drill(tmp_path, later)).startswith("⚠️ Drill 24.09. PASS")


def test_vault_uses_newest_pass_receipt(tmp_path: Path) -> None:
    _receipt(tmp_path, "2026-09-25T08:01:43Z")
    _receipt(tmp_path, "2026-09-26T08:59:02Z", probe="FAIL")
    got = ob.collect_vault(tmp_path, NOW)
    assert ob._vault_part(got) == "Vault 25.09. PASS (1 T)"
    old = NOW + timedelta(days=9)
    assert ob._vault_part(ob.collect_vault(tmp_path, old)).startswith("⚠️ Vault 25.09. PASS")


def test_vault_without_verified_copy_is_flagged(tmp_path: Path) -> None:
    assert ob._vault_part(ob.collect_vault(tmp_path, NOW)) == "⚠️ Vault keine verifizierte Kopie"


def test_cost_line_marks_lower_bound_and_non_ok_state() -> None:
    line = ob._cost_line(
        {
            "available": True,
            "state": "WARNING",
            "month_usd": 24.5,
            "month_limit": 31.0,
            "projected": 29.2,
            "lower_bound": True,
            "today_usd": 0.9,
            "today_limit": 1.0,
        }
    )
    assert line == (
        "💶 *KI-Kosten:* Monat ≥24.50/31.00 USD (Hochrechnung 29.20) · heute 0.90/1.00 · ⚠️ WARNING"
    )


def test_cost_line_without_limits_or_projection() -> None:
    line = ob._cost_line(
        {
            "available": True,
            "state": "OK",
            "month_usd": 0.0,
            "month_limit": None,
            "projected": None,
            "lower_bound": False,
            "today_usd": 0.0,
            "today_limit": None,
        }
    )
    assert line == "💶 *KI-Kosten:* Monat 0.00/– USD · heute 0.00/– · OK"


def test_a_broken_part_does_not_take_the_others_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _audit(tmp_path, {"ts": "2026-09-26T01:48:00Z", "status": "ok"})

    def boom(now: datetime) -> dict[str, Any]:
        raise RuntimeError("kein Budget")

    monkeypatch.setattr(ob, "collect_ai_cost", boom)
    status = ob.collect_ops_status(tmp_path, NOW)
    assert status["ai_cost"] == {"error": "RuntimeError"}
    backup_line, cost_line, *_ = ob.format_ops_lines(status)
    assert backup_line.startswith("🛟 *Backup:* Pi 26.09. 01:48Z ok · ⚠️ Drill kein Beweis")
    assert cost_line == "💶 *KI-Kosten:* nicht lesbar (RuntimeError)"


def test_ai_cost_reads_the_same_source_as_the_budget_gate(tmp_path: Path) -> None:
    """Ohne Strom: Nullzustand statt Ausnahme (fail-soft wie current_budget_status)."""
    from app.ai import spend

    spend.reset_spend_cache()
    got = ob.collect_ai_cost(NOW)
    assert got["available"] is True
    assert got["state"] in {"OK", "WARNING", "LIMIT_REACHED", "COST_UNKNOWN"}


def test_digest_places_ops_block_directly_under_mode_line() -> None:
    ops = ["🛟 *Backup:* Pi 26.09. 01:48Z ok", "💶 *KI-Kosten:* Monat 1.00/31.00 USD"]
    msg = od.compose_digest_message(
        today=NOW.date(),
        runtime={"entry_mode": "disabled", "open_routes": [], "contradictions": []},
        fills_by_source={},
        bridge_stages={},
        shadow_funnel=None,
        shadow_report={},
        generator_edge={},
        d227={"error": "x"},
        v5_freshness={},
        v5_activated_on=NOW.date(),
        ops_lines=ops,
    )
    lines = msg.splitlines()
    assert lines[1].startswith("⚙️ *Modus:*")
    assert lines[2:4] == ops


# --------------------------------------------------------------------------- #
# Lightning (Lueckenregister 26.09.) — nur lokale Zustaende, nie der Node
# --------------------------------------------------------------------------- #


def _reconcile(artifacts: Path, **fields: Any) -> None:
    folder = artifacts / "payments"
    folder.mkdir(parents=True, exist_ok=True)
    base = {"last_run_utc": (NOW - timedelta(minutes=5)).isoformat(), "last_status": "ok"}
    base.update(fields)
    (folder / "reconcile_state.json").write_text(json.dumps(base), encoding="utf-8")


def _scb(artifacts: Path) -> None:
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "scb_baseline.json").write_text(
        json.dumps({"sha256": "cb96165271c1800629cf", "recorded_at": "2026-09-25T05:27:40Z"}),
        encoding="utf-8",
    )


def test_ln_line_confirms_a_fresh_complete_reconcile(tmp_path: Path) -> None:
    _reconcile(tmp_path, last_orphans=0, last_complete=True)
    _scb(tmp_path)
    line = ob.format_ops_lines(ob.collect_ops_status(tmp_path, NOW))[2]
    assert line == "⚡ *Lightning:* Reconcile 07:25Z ok · 0 Orphans · SCB cb961652 (seit 25.09.)"


@pytest.mark.parametrize(
    "fields",
    [
        {"last_status": "attention"},
        {"last_orphans": 2},
        {"last_complete": False},
        {"last_run_utc": (NOW - timedelta(hours=2)).isoformat()},
    ],
)
def test_ln_line_warns_on_attention_orphans_blind_or_stale(
    tmp_path: Path, fields: dict[str, Any]
) -> None:
    _reconcile(tmp_path, **fields)
    line = ob.format_ops_lines(ob.collect_ops_status(tmp_path, NOW))[2]
    assert "⚠️" in line


def test_ln_line_without_reconcile_state_is_a_warning(tmp_path: Path) -> None:
    line = ob.format_ops_lines(ob.collect_ops_status(tmp_path, NOW))[2]
    assert line.startswith("⚡ *Lightning:* ⚠️ Reconcile kein Zustand")


def test_ln_line_reports_a_foreign_spend_of_the_last_day(tmp_path: Path) -> None:
    """D-289: eine Node-Ausgabe ohne KAI-Intent steht 24 h mit ⚠️ im Digest."""
    _reconcile(tmp_path, last_unattributed_at=(NOW - timedelta(hours=3)).isoformat())
    line = ob.format_ops_lines(ob.collect_ops_status(tmp_path, NOW))[2]
    assert "⚠️" in line
    assert "fremde Ausgabe 26.09. 04:30Z (nicht zugeordnet)" in line


def test_ln_line_forgets_a_foreign_spend_after_a_day(tmp_path: Path) -> None:
    _reconcile(tmp_path, last_unattributed_at=(NOW - timedelta(hours=30)).isoformat())
    line = ob.format_ops_lines(ob.collect_ops_status(tmp_path, NOW))[2]
    assert "fremde Ausgabe" not in line and "⚠️" not in line
