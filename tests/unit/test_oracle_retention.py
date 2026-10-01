"""Löschfristen der Oracle-Beta (Datenschutzseite v0.5): was dort steht, muss hier gelten.

IP-Adressen und IP-Kennwerte sind nach sechs Tagen weg (bei täglichem Lauf höchstens
sieben), Meldungen 90 Tage nach Abschluss, Einladungen 30 Tage nach Ende; die übrigen
Belegfelder bleiben unangetastet.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.oracle_legal import invites, retention

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import audit_rotate  # noqa: E402

_NOW = datetime(2026, 10, 10, 4, 40, tzinfo=UTC)


def _jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_demand_fingerprints_older_than_six_days_are_cleared(tmp_path: Path) -> None:
    old = (_NOW - timedelta(days=6, minutes=1)).isoformat()
    new = (_NOW - timedelta(days=5)).isoformat()
    path = _jsonl(
        tmp_path / "ln_demand_ledger.jsonl",
        [
            {"ts": old, "event": "l402_challenge_minted", "requester_fp": "abcd", "price_sat": 10},
            {"ts": new, "event": "l402_challenge_minted", "requester_fp": "ef01", "price_sat": 10},
        ],
    )
    assert retention.strip_demand_fingerprints(path, _NOW) == 1
    rows = _rows(path)
    assert rows[0]["requester_fp"] == "" and rows[0]["price_sat"] == 10
    assert rows[1]["requester_fp"] == "ef01"
    assert retention.strip_demand_fingerprints(path, _NOW) == 0  # idempotent


def test_audit_archives_lose_the_ip_and_are_not_reread(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    _jsonl(
        archive / "api_request_audit.20261001T044000Z.jsonl",
        [
            {
                "timestamp_utc": "2026-09-28T10:00:00+00:00",
                "path": "/oracle/x",
                "client_ip": "1.2.3.4",
            }
        ],
    )
    _jsonl(archive / "other_stream.20261001T044000Z.jsonl", [{"client_ip": "5.6.7.8"}])
    assert retention.strip_audit_archive_ips(archive) == 1
    [done] = list(archive.glob("api_request_audit.*.noip.jsonl"))
    assert _rows(done) == [{"timestamp_utc": "2026-09-28T10:00:00+00:00", "path": "/oracle/x"}]
    assert not (archive / "api_request_audit.20261001T044000Z.jsonl").exists()
    assert "5.6.7.8" in (archive / "other_stream.20261001T044000Z.jsonl").read_text()
    assert retention.strip_audit_archive_ips(archive) == 0


def test_cases_go_90_days_after_closure_and_open_cases_stay(tmp_path: Path) -> None:
    def at(days: float) -> str:
        return (_NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")

    path = _jsonl(
        tmp_path / "oracle" / "oracle_cases.jsonl",
        [
            {"case_id": "A", "kind": "meldung", "received_at": at(200), "email": "a@x.de"},
            {"case_id": "A", "state": "erledigt", "at": at(91), "note": "ok"},
            {"case_id": "B", "kind": "meldung", "received_at": at(100), "email": "b@x.de"},
            {"case_id": "B", "state": "erledigt", "at": at(30), "note": "ok"},
            {"case_id": "C", "kind": "meldung", "received_at": at(300), "email": "c@x.de"},
        ],
    )
    assert retention.prune_cases(path, _NOW) == 1
    assert {r["case_id"] for r in _rows(path)} == {"B", "C"}


def test_apply_runs_every_rule_and_survives_a_failing_one(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    invites.create(
        "Alt", days=1, now=_NOW - timedelta(days=40), path=tmp_path / "oracle" / "invites.jsonl"
    )

    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise OSError("platte voll")

    monkeypatch.setattr(retention, "strip_audit_archive_ips", boom)
    counts = retention.apply(tmp_path, now=_NOW)
    assert counts["audit_archives_stripped"] == -1
    assert counts["invites_removed"] == 1
    assert counts["demand_fingerprints_cleared"] == 0 and counts["cases_removed"] == 0


def test_api_audit_rotates_by_age_even_when_small(tmp_path: Path) -> None:
    rule = next(r for r in audit_rotate.ROTATION_RULES if r.filename == "api_request_audit.jsonl")
    assert rule.keep_hours == 144 and rule.max_age_hours == 144
    live = _jsonl(
        tmp_path / "api_request_audit.jsonl",
        [
            {"timestamp_utc": (_NOW - timedelta(days=8)).isoformat(), "client_ip": "1.1.1.1"},
            {"timestamp_utc": (_NOW - timedelta(days=1)).isoformat(), "client_ip": "2.2.2.2"},
        ],
    )
    result = audit_rotate.rotate_stream(
        live,
        max_bytes=rule.max_bytes,
        keep_lines=rule.keep_lines,
        archive_dir=tmp_path / "archive",
        apply=True,
        now=_NOW,
        keep_hours=rule.keep_hours,
        timestamp_key=rule.timestamp_key,
        max_age_hours=rule.max_age_hours,
    )
    assert result.rotated
    assert [r["client_ip"] for r in _rows(live)] == ["2.2.2.2"]


def test_a_young_small_file_is_left_alone(tmp_path: Path) -> None:
    live = _jsonl(
        tmp_path / "api_request_audit.jsonl",
        [{"timestamp_utc": (_NOW - timedelta(days=2)).isoformat(), "client_ip": "2.2.2.2"}],
    )
    result = audit_rotate.rotate_stream(
        live,
        max_bytes=10_000_000,
        keep_lines=100,
        archive_dir=tmp_path / "archive",
        apply=True,
        now=_NOW,
        keep_hours=144,
        timestamp_key="timestamp_utc",
        max_age_hours=144,
    )
    assert not result.rotated and result.reason == "under_threshold"
