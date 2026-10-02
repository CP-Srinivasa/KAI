"""Löschfristen der Oracle-Beta (Datenschutzseite v0.5): was dort steht, muss hier gelten.

IP-Adressen und IP-Kennwerte sind nach sechs Tagen weg (bei täglichem Lauf höchstens
sieben), Meldungen 90 Tage nach Abschluss, Einladungen 30 Tage nach Ende; die übrigen
Belegfelder bleiben unangetastet.
"""

from __future__ import annotations

import json
import sys
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import oracle_legal as legal
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
    """Fälle so anlegen, wie der echte Schreiber sie schreibt (``status``, nicht ``state``).

    Der frühere Test schrieb die Zeilen von Hand mit ``state`` und war damit grün, obwohl
    ``mark_case`` ``status`` schreibt: erledigte Fälle wären nie gelöscht worden.
    """
    path = tmp_path / "oracle" / "oracle_cases.jsonl"

    def case(received_days: float, closed_days: float | None) -> str:
        cid = legal.record_case(
            "meldung", {"email": "x@x.de"}, now=_NOW - timedelta(days=received_days), path=path
        )["case_id"]
        if closed_days is not None:
            legal.mark_case(
                cid, "erledigt", "ok", now=_NOW - timedelta(days=closed_days), path=path
            )
        return cid

    old_closed = case(200, 91)
    recent_closed = case(100, 30)
    still_open = case(300, None)
    assert retention.prune_cases(path, _NOW) == 1
    assert {r["case_id"] for r in _rows(path)} == {recent_closed, still_open}
    assert old_closed not in path.read_text(encoding="utf-8")
    assert retention.prune_cases(path, _NOW) == 0


def _archive_rows(n: int, pad: int = 120) -> list[dict]:
    return [
        {
            "timestamp_utc": f"2026-09-2{i % 9}T10:00:00+00:00",
            "request_id": f"r{i}",
            "path": "/oracle/verdicts/" + "x" * pad,
            "client_ip": f"10.0.{i % 250}.{i % 200}",
        }
        for i in range(n)
    ]


def test_archive_strip_streams_instead_of_loading_the_whole_file(tmp_path: Path) -> None:
    """02.10.: 133/191-MB-Archive in einer Liste -> OOM-Kill unter MemoryMax=256M.

    Gemessen wird der Python-Speicher-Peak: er muss unabhängig von der Archivgröße klein
    bleiben (hier ~8 MB Archiv, Peak-Grenze 1 MB).
    """
    archive = tmp_path / "archive"
    src = _jsonl(archive / "api_request_audit.20261001T044000Z.jsonl", _archive_rows(40_000))
    assert src.stat().st_size > 7_000_000
    tracemalloc.start()
    try:
        assert retention.strip_audit_archive_ips(archive) == 1
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000, f"Peak {peak} B: Archiv wird nicht gestreamt"
    [done] = archive.glob("api_request_audit.*.noip.jsonl")
    with done.open(encoding="utf-8") as fh:
        assert sum(1 for line in fh if "client_ip" not in json.loads(line)) == 40_000


def test_malformed_archive_lines_never_reach_the_noip_file(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    src = archive / "api_request_audit.20261001T044000Z.jsonl"
    src.parent.mkdir(parents=True)
    src.write_text(
        json.dumps({"timestamp_utc": "2026-09-28T10:00:00+00:00", "client_ip": "1.2.3.4"})
        + "\n"
        + '{"timestamp_utc": "2026-09-28T10:00:01+00:00", "client_ip": "9.9.9.9", "pa\n'
        + '"8.8.8.8"\n'
        + "\n"
        + json.dumps({"timestamp_utc": "2026-09-28T10:00:02+00:00", "client_ip": "7.7.7.7"}),
        encoding="utf-8",
    )
    assert retention.strip_audit_archive_ips(archive) == 1
    [done] = archive.glob("api_request_audit.*.noip.jsonl")
    text = done.read_text(encoding="utf-8")
    for ip in ("1.2.3.4", "9.9.9.9", "8.8.8.8", "7.7.7.7"):
        assert ip not in text
    assert [r["timestamp_utc"][17:19] for r in _rows(done)] == ["00", "02"]


def test_an_interrupted_archive_run_is_finished_on_the_next_run(tmp_path: Path) -> None:
    """Abbruch nach dem Ersetzen, vor dem Löschen der Quelle, plus liegengebliebene .tmp."""
    archive = tmp_path / "archive"
    src = _jsonl(archive / "api_request_audit.20261001T044000Z.jsonl", _archive_rows(5, pad=1))
    _jsonl(archive / "api_request_audit.20261001T044000Z.noip.jsonl", _archive_rows(2, pad=1))
    (archive / "api_request_audit.20261001T044000Z.noip.jsonl.tmp").write_text("{kaputt")
    assert retention.strip_audit_archive_ips(archive) == 1
    assert not src.exists()
    assert not list(archive.glob("*.tmp"))
    rows = _rows(archive / "api_request_audit.20261001T044000Z.noip.jsonl")
    assert len(rows) == 5 and all("client_ip" not in r for r in rows)


def test_one_unreadable_archive_does_not_stop_the_others_but_is_reported(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    (archive / "api_request_audit.20260901T044000Z.jsonl").mkdir(parents=True)  # unlesbar
    _jsonl(archive / "api_request_audit.20261001T044000Z.jsonl", _archive_rows(3, pad=1))
    with pytest.raises(retention.RetentionIncompleteError):
        retention.strip_audit_archive_ips(archive)
    assert (archive / "api_request_audit.20261001T044000Z.noip.jsonl").exists()
    assert not (archive / "api_request_audit.20261001T044000Z.jsonl").exists()


def test_small_rules_run_before_the_archives(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """Ein Abbruch in den großen Archiven darf die kleinen Fristen nicht mitreißen."""
    order: list[str] = []
    for name in (
        "strip_audit_archive_ips",
        "strip_demand_fingerprints",
        "prune_cases",
    ):
        monkeypatch.setattr(retention, name, lambda *_a, _n=name, **_k: order.append(_n) or 0)
    monkeypatch.setattr(invites, "prune", lambda **_k: order.append("invites") or 0)
    retention.apply(tmp_path, now=_NOW)
    assert order[-1] == "strip_audit_archive_ips"
    assert set(order[:-1]) == {"strip_demand_fingerprints", "prune_cases", "invites"}


def test_malformed_demand_lines_lose_their_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "ln_demand_ledger.jsonl"
    path.write_text(
        json.dumps({"ts": _NOW.isoformat(), "event": "x", "requester_fp": "beef"})
        + "\n"
        + '{"ts": "2026-09-01T00:00:00+00:00", "requester_fp": "dead", "eve\n'
        + '{"ts": "2026-09-01T00:00:00+00:00", "requester_fp": "ca\n',
        encoding="utf-8",
    )
    retention.strip_demand_fingerprints(path, _NOW)
    text = path.read_text(encoding="utf-8")
    assert "dead" not in text and '"ca' not in text
    assert json.loads(text.splitlines()[0])["requester_fp"] == "beef"  # jung, gültig: bleibt


def test_overdue_reports_what_the_privacy_page_promises(tmp_path: Path) -> None:
    """Selbstprüfung nach dem Lauf: grün heißt Zusage eingehalten, nicht nur „gelaufen“."""
    old = (_NOW - timedelta(days=8)).isoformat()
    _jsonl(tmp_path / "archive" / "api_request_audit.20261001T044000Z.jsonl", [{"x": 1}])
    _jsonl(tmp_path / "ln_demand_ledger.jsonl", [{"ts": old, "requester_fp": "abcd"}])
    _jsonl(tmp_path / "api_request_audit.jsonl", [{"timestamp_utc": old, "client_ip": "1.1.1.1"}])
    assert retention.overdue(tmp_path, _NOW) == {
        "audit_archives_with_ip": 1,
        "demand_fingerprints_overdue": 1,
        "live_audit_older_than_7d": 1,
        "cases_overdue": 0,
    }
    retention.apply(tmp_path, now=_NOW)
    _jsonl(tmp_path / "api_request_audit.jsonl", [{"timestamp_utc": _NOW.isoformat()}])
    assert set(retention.overdue(tmp_path, _NOW).values()) == {0}


def test_audit_rotate_exits_nonzero_when_a_rule_fails_or_is_overdue(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(retention, "apply", lambda *_a, **_k: {"x": -1})
    monkeypatch.setattr(retention, "overdue", lambda *_a, **_k: {"y": 0})
    assert audit_rotate.main(["--apply", "--artifacts-dir", str(tmp_path)]) == 1
    monkeypatch.setattr(retention, "apply", lambda *_a, **_k: {"x": 3})
    monkeypatch.setattr(retention, "overdue", lambda *_a, **_k: {"y": 2})
    assert audit_rotate.main(["--apply", "--artifacts-dir", str(tmp_path)]) == 1
    monkeypatch.setattr(retention, "overdue", lambda *_a, **_k: {"y": 0})
    assert audit_rotate.main(["--apply", "--artifacts-dir", str(tmp_path)]) == 0


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
