"""Unit tests for attested verdict reports (build/render/write round-trip)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.research.verdict_report import (
    build_verdict_report,
    render_verdict_md,
    write_verdict_report,
)
from app.truth.attestation import verify_attestation

_GENERATED = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _report() -> dict:
    return build_verdict_report(
        {"overall": {"n": 68, "actionable": False}},
        hypothesis="directional_news_forward_return",
        prereg_id="5872f817a2d1632d",
        verdict="FAILED at pre-registered 24h horizon",
        params={"lookback_days": 120, "construction": "spot"},
        code_version="c980e70",
        generated_at=_GENERATED,
    )


def test_report_attestation_verifies_and_is_deterministic() -> None:
    r1, r2 = _report(), _report()
    assert verify_attestation(r1["payload"], r1["attestation"])
    assert r1["attestation"]["hash"] == r2["attestation"]["hash"]  # same claim, same hash
    # any tamper breaks verification
    tampered = dict(r1["payload"], verdict="PASSED")
    assert not verify_attestation(tampered, r1["attestation"])


def test_render_contains_hash_prereg_and_verdict() -> None:
    r = _report()
    md = render_verdict_md(r)
    assert r["attestation"]["hash"] in md
    assert "5872f817a2d1632d" in md
    assert "FAILED at pre-registered 24h horizon" in md


def test_render_marks_missing_prereg_as_exploratory() -> None:
    r = build_verdict_report(
        {},
        hypothesis="h",
        prereg_id=None,
        verdict="v",
        params={},
        code_version="x",
        generated_at=_GENERATED,
    )
    assert "NOT PRE-REGISTERED" in render_verdict_md(r)


def test_write_verdict_report_round_trips_from_disk(tmp_path: Path) -> None:
    json_path, md_path = write_verdict_report(_report(), tmp_path / "verdicts")
    assert json_path.is_file() and md_path.is_file()
    assert "directional_news_forward_return" in json_path.name
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert verify_attestation(loaded["payload"], loaded["attestation"])


def test_list_verdict_reports_reads_written_reports(tmp_path: Path) -> None:
    from app.research.verdict_report import list_verdict_reports

    out = tmp_path / "verdicts"
    write_verdict_report(_report(), out)
    (out / "junk.json").write_text("{not json", encoding="utf-8")  # skipped, no crash
    rows = list_verdict_reports(out)
    assert len(rows) == 1
    r = rows[0]
    assert r["hypothesis"] == "directional_news_forward_return"
    assert r["prereg_id"] == "5872f817a2d1632d"
    assert len(r["attestation_hash"]) == 64
    assert list_verdict_reports(tmp_path / "missing") == []
