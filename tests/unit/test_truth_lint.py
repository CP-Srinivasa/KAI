"""Truth-Lint Invariant-Registry (Operator-Direktive 07-11).

Fixture-Tests pro aktiver Invariante (Verletzung + sauber), Registry-Vertrag
(11 Einträge, eindeutige IDs, planned sichtbar), Severity-Gate-Semantik und
append-only Report/Quarantäne-Writer. Lint korrigiert NIE Quelldaten.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.truth.lint import (
    REGISTRY,
    Severity,
    run_lint,
    write_lint_report,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _artifacts(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Registry-Vertrag ─────────────────────────────────────────────────────────


def test_registry_has_all_eleven_operator_invariants() -> None:
    assert len(REGISTRY) == 11
    ids = [i.invariant_id for i in REGISTRY]
    assert len(set(ids)) == 11
    assert ids == sorted(ids)  # TL-001..TL-011 stabil geordnet


def test_registry_planned_invariants_stay_visible(tmp_path: Path) -> None:
    # Nicht implementierte Invarianten verschwinden nicht — sie erscheinen im
    # Ergebnis als status=planned (Abdeckungslücke bleibt sichtbar).
    result = run_lint(_artifacts(tmp_path))
    planned = [i for i in result["invariants"] if i["status"] == "planned"]
    assert len(planned) == result["registry_planned"] > 0
    assert result["registry_active"] + result["registry_planned"] == 11


def test_active_invariants_have_checks_planned_have_none() -> None:
    for inv in REGISTRY:
        if inv.status == "active":
            assert inv.check is not None, inv.invariant_id
        else:
            assert inv.check is None, inv.invariant_id


# ── TL-001 Mock in Fills ─────────────────────────────────────────────────────


def test_tl001_flags_unrefused_mock_after_baseline(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "trading_loop_audit.jsonl",
        [
            {
                "symbol": "SUMR/USDT",
                "completed_at": "2026-07-12T10:00:00+00:00",
                "market_data_fetched": {"market_data_source": "mock"},
                "notes": [],
            }
        ],
    )
    result = run_lint(art)
    v = [x for x in result["violations"] if x["invariant_id"] == "TL-001"]
    assert len(v) == 1
    assert v[0]["severity"] == "CRITICAL"
    assert result["max_severity"] == "CRITICAL"


def test_tl001_ignores_refused_and_pre_baseline(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "trading_loop_audit.jsonl",
        [
            {  # Gate hat gegriffen — gewollt, keine Verletzung
                "symbol": "XYZ/USDT",
                "completed_at": "2026-07-12T10:00:00+00:00",
                "market_data_fetched": {"market_data_source": "mock"},
                "notes": ["synthetic_last_resort_refused:XYZ/USDT"],
            },
            {  # dokumentierter Alt-Vorfall vor Gate #584
                "symbol": "MIM/USDT",
                "completed_at": "2026-06-25T13:00:00+00:00",
                "market_data_fetched": {"market_data_source": "mock"},
                "notes": [],
            },
        ],
    )
    result = run_lint(art)
    assert [x for x in result["violations"] if x["invariant_id"] == "TL-001"] == []


# ── TL-002 Mock-Preisband ────────────────────────────────────────────────────


def test_tl002_warns_on_fill_in_mock_band(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "symbol": "ABC/USDT",
                "fill_price": 100.76,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            },
            {  # außerhalb des Bands — sauber
                "event_type": "order_filled",
                "symbol": "BTC/USDT",
                "fill_price": 108000.0,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            },
        ],
    )
    result = run_lint(art)
    v = [x for x in result["violations"] if x["invariant_id"] == "TL-002"]
    assert len(v) == 1
    assert v[0]["severity"] == "WARNING"
    assert v[0]["evidence"]["per_symbol"] == {"ABC/USDT": 1}


# ── TL-008 fehlende Provenance ───────────────────────────────────────────────


def test_tl008_flags_resolved_rows_without_signal_path(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "alert_outcomes.jsonl",
        [
            {"outcome": "hit", "asset": "BTC", "annotated_at": "2026-07-12T08:00:00+00:00"},
            {  # Provenance vorhanden — sauber
                "outcome": "miss",
                "asset": "ETH",
                "annotated_at": "2026-07-12T08:00:00+00:00",
                "provenance": {"signal_path_id": "p1"},
            },
            {  # vor Baseline — Schema-Historie, keine Verletzung
                "outcome": "hit",
                "asset": "SOL",
                "annotated_at": "2026-05-01T08:00:00+00:00",
            },
            {
                "outcome": "inconclusive",
                "asset": "DOGE",
                "annotated_at": "2026-07-12T08:00:00+00:00",
            },
        ],
    )
    result = run_lint(art)
    v = [x for x in result["violations"] if x["invariant_id"] == "TL-008"]
    assert len(v) == 1
    assert v[0]["evidence"]["count"] == 1


# ── TL-011 Report-Integrität ─────────────────────────────────────────────────


def _write_verdict(dirpath: Path, hypothesis: str, tamper: bool) -> None:
    from app.research.verdict_report import build_verdict_report

    report = build_verdict_report(
        {"n": 1},
        hypothesis=hypothesis,
        prereg_id="x",
        verdict="NOT_MET test",
        params={},
        code_version="deadbeef",
        generated_at=datetime(2026, 7, 10, 18, 50, 15, tzinfo=UTC),
    )
    if tamper:
        report["payload"]["verdict"] = "PASSED test"  # nachträglich umgeschrieben
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"{hypothesis}.json").write_text(json.dumps(report), encoding="utf-8")


def test_tl011_detects_tampered_verdict_and_passes_intact(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    vdir = art / "research" / "verdicts"
    _write_verdict(vdir, "intact_claim", tamper=False)
    _write_verdict(vdir, "tampered_claim", tamper=True)
    result = run_lint(art)
    v = [x for x in result["violations"] if x["invariant_id"] == "TL-011"]
    assert len(v) == 1
    assert v[0]["severity"] == "CRITICAL"
    assert "tampered_claim" in v[0]["dataset"]


# ── Writer + Quarantäne-Semantik ─────────────────────────────────────────────


def test_writer_appends_report_and_quarantines_error_plus(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    result = {
        "schema": "truth_lint/v1",
        "ts_utc": "2026-07-11T12:00:00+00:00",
        "violations": [
            {"invariant_id": "TL-002", "severity": "WARNING", "dataset": "a", "message": "m"},
            {"invariant_id": "TL-011", "severity": "CRITICAL", "dataset": "b", "message": "m"},
        ],
    }
    rp = art / "truth_lint_report.jsonl"
    qp = art / "truth_quarantine.jsonl"
    markers = write_lint_report(result, report_path=rp, quarantine_path=qp)
    markers += write_lint_report(result, report_path=rp, quarantine_path=qp)  # append-only
    assert markers == 2
    assert len(rp.read_text(encoding="utf-8").splitlines()) == 2
    q = [json.loads(x) for x in qp.read_text(encoding="utf-8").splitlines()]
    # NUR ERROR/CRITICAL werden quarantänisiert — WARNING degradiert nur.
    assert {row["invariant_id"] for row in q} == {"TL-011"}


def test_clean_artifacts_produce_no_violations_and_no_quarantine(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    result = run_lint(art)
    assert result["violations"] == []
    assert result["max_severity"] is None
    qp = art / "truth_quarantine.jsonl"
    write_lint_report(result, report_path=art / "r.jsonl", quarantine_path=qp)
    assert not qp.exists()


def test_severity_ordering_matches_gate_semantics() -> None:
    assert Severity.INFO < Severity.WARNING < Severity.ERROR < Severity.CRITICAL
