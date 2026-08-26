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


def test_registry_keeps_operator_eleven_in_order_plus_additions() -> None:
    ids = [i.invariant_id for i in REGISTRY]
    # Die Operator-Liste 07-11 (TL-001..TL-011) bleibt vollständig und in
    # Original-Reihenfolge; Nachregistrierungen sind NUR am Ende erlaubt.
    assert ids[:11] == [f"TL-{n:03d}" for n in range(1, 12)]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    assert "TL-012" in ids  # Quoten-Sprint 07-30: Resolutions-Batch-Konzentration


def test_registry_planned_invariants_stay_visible(tmp_path: Path) -> None:
    # Nicht implementierte Invarianten verschwinden nicht — sie erscheinen im
    # Ergebnis als status=planned (Abdeckungslücke bleibt sichtbar).
    result = run_lint(_artifacts(tmp_path))
    planned = [i for i in result["invariants"] if i["status"] == "planned"]
    assert len(planned) == result["registry_planned"] > 0
    assert result["registry_active"] + result["registry_planned"] == len(REGISTRY)


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
                "symbol": "BNB/USDT",
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


# ── TL-002 Preise aus der Mock-Kurve ─────────────────────────────────────────


def test_tl002_warns_on_exact_mock_curve_hit(tmp_path: Path) -> None:
    """V3 (2026-08-26): geprueft wird die Kurve, nicht mehr das Band [95,105].

    Frueher hiess der Test ``..._on_fill_in_mock_band`` und nutzte ABC/USDT bei
    100,76. Beides traegt nicht mehr: das Band ist weg, und ABC hat keinen
    eigenen Mock-Basispreis — bei Basis 100 ist die Kurve lueckenlos, ein Treffer
    dort ist ein Muenzwurf. SOL (Basis 150) hat eine duenne Kurve, dort traegt er.
    """
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "symbol": "BNB/USDT",
                "fill_price": 403.66,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            },
            {  # realer BTC-Preis, weit weg von der Mock-Kurve um 65000 — sauber
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
    assert v[0]["evidence"]["per_symbol"] == {"BNB/USDT": 1}


def test_tl002_excludes_band_fill_with_real_source(tmp_path: Path) -> None:
    """V1 (Daily 07-12): ein Fill mit belegter realer market_data_source ist kein
    Mock-Verdacht; mock-Quelle und fehlender Join bleiben WARNING (fail-closed).

    V3 (2026-08-26): die Preise liegen jetzt auf der TATSAECHLICHEN Mock-Kurve.
    Die frueheren Werte (98,77 / 99,50) lagen unter dem Basispreis und sind vom
    Mock gar nicht erzeugbar — der Test pruefte damit die Entlastungslogik an
    Faellen, die nie ausgeloest haetten. Die Entlastung greift ausserdem nur noch
    bei ROHEN Kurventreffern; ein Slippage-Treffer oder ``price_source: mock``
    laesst sich davon nicht mehr aufweichen.
    """
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "paper_execution_audit.jsonl",
        [
            {  # reale Quelle -> ausgenommen, nur Evidence-Zaehler
                "event_type": "order_filled",
                "order_id": "ord_real",
                "symbol": "BNB/USDT",
                "fill_price": 403.66,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            },
            {  # mock-Quelle -> Verletzung
                "event_type": "order_filled",
                "order_id": "ord_mock",
                "symbol": "MSFT",
                "fill_price": 423.88,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            },
            {  # kein Join -> Verletzung (fail-closed)
                "event_type": "order_filled",
                "order_id": "ord_unknown",
                "symbol": "SPY",
                "fill_price": 524.88,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            },
        ],
    )
    _write_jsonl(
        art / "trading_loop_audit.jsonl",
        [
            {"order_id": "ord_real", "notes": ["market_data_source:bybit"]},
            {"order_id": "ord_mock", "notes": ["market_data_source:mock"]},
        ],
    )
    result = run_lint(art)
    v = [x for x in result["violations"] if x["invariant_id"] == "TL-002"]
    assert len(v) == 1
    assert v[0]["evidence"]["count"] == 2
    assert v[0]["evidence"]["real_source_excluded"] == 1
    assert "BNB/USDT" not in v[0]["evidence"]["per_symbol"]


def test_tl002_excludes_screener_fill_via_document_id_join(tmp_path: Path) -> None:
    """V2 (2026-08-05): der technical_paper-Pfad laeuft ohne Loop-Zyklus, hat
    also nie eine ``market_data_source``-Note — er trug den Beleg aber immer
    schon, nur unter einem anderen Schluessel: ``document_id`` enthaelt den
    ``candidate_id``, und der Shadow-Candidate haelt ``entry_price_basis``.

    Derselbe Fehlertyp wie die Close-Attribution (#621): der Join wurde ueber
    ``order_id`` versucht, wo ``document_id`` der tragende Schluessel ist.
    """
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "paper_execution_audit.jsonl",
        [
            {  # Screener-Fill mit Binance-Beleg -> ausgenommen
                "event_type": "order_filled",
                "order_id": "ord_tech",
                "symbol": "BNB/USDT",
                "fill_price": 403.66,
                "timestamp_utc": "2026-07-27T15:26:00+00:00",
                "document_id": "technical_paper_BNBUSDT_tech-BNBUSDT-2026-07-27T15:20:00+00:00",
            },
            {  # Screener-Fill, aber nur Fallback-Basis -> bleibt Verletzung
                "event_type": "order_filled",
                "order_id": "ord_fallback",
                "symbol": "MSFT",
                "fill_price": 423.88,
                "timestamp_utc": "2026-07-27T15:26:00+00:00",
                "document_id": "technical_paper_MSFT_tech-MSFT-2026-07-27T15:20:00+00:00",
            },
        ],
    )
    _write_jsonl(
        art / "shadow_candidate_ledger.jsonl",
        [
            {
                "candidate_id": "tech-BNBUSDT-2026-07-27T15:20:00+00:00",
                "entry_price_basis": "binance_1m_decision",
            },
            {
                "candidate_id": "tech-MSFT-2026-07-27T15:20:00+00:00",
                "entry_price_basis": "fallback_1h_last",
            },
        ],
    )
    result = run_lint(art)
    v = [x for x in result["violations"] if x["invariant_id"] == "TL-002"]
    assert len(v) == 1
    assert v[0]["evidence"]["count"] == 1
    assert v[0]["evidence"]["per_symbol"] == {"MSFT": 1}
    assert v[0]["evidence"]["real_source_excluded"] == 1


def test_tl002_document_id_without_matching_candidate_stays_violation(tmp_path: Path) -> None:
    """Fail-closed: ein document_id ohne auffindbaren Candidate ist KEIN Beleg."""
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_orphan",
                "symbol": "BNB/USDT",
                "fill_price": 403.66,
                "timestamp_utc": "2026-07-27T15:26:00+00:00",
                "document_id": "technical_paper_BNBUSDT_tech-BNBUSDT-2026-07-27T15:20:00+00:00",
            }
        ],
    )
    _write_jsonl(
        art / "shadow_candidate_ledger.jsonl",
        [
            {
                "candidate_id": "tech-OTHER-2026-01-01T00:00:00+00:00",
                "entry_price_basis": "binance_1m_decision",
            }
        ],
    )
    result = run_lint(art)
    v = [x for x in result["violations"] if x["invariant_id"] == "TL-002"]
    assert len(v) == 1
    assert v[0]["evidence"]["count"] == 1


def test_tl002_explicit_mock_source_wins_over_screener_basis(tmp_path: Path) -> None:
    """Fail-closed bei Widerspruch: sagt der Loop-Audit ausdruecklich ``mock``,
    darf ein positiver Screener-Beleg das NICHT ueberstimmen. Der neue Join ist
    ein ZUSAETZLICHER Weg fuer Fills ohne Zyklus, kein Freibrief."""
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_conflict",
                "symbol": "BNB/USDT",
                "fill_price": 403.66,
                "timestamp_utc": "2026-07-27T15:26:00+00:00",
                "document_id": "technical_paper_BNBUSDT_tech-BNBUSDT-2026-07-27T15:20:00+00:00",
            }
        ],
    )
    _write_jsonl(
        art / "trading_loop_audit.jsonl",
        [{"order_id": "ord_conflict", "notes": ["market_data_source:mock"]}],
    )
    _write_jsonl(
        art / "shadow_candidate_ledger.jsonl",
        [
            {
                "candidate_id": "tech-BNBUSDT-2026-07-27T15:20:00+00:00",
                "entry_price_basis": "binance_1m_decision",
            }
        ],
    )
    result = run_lint(art)
    v = [x for x in result["violations"] if x["invariant_id"] == "TL-002"]
    assert len(v) == 1
    assert v[0]["evidence"]["per_symbol"] == {"BNB/USDT": 1}


def test_tl002_silent_when_all_band_fills_have_real_source(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _write_jsonl(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_real",
                "symbol": "BNB/USDT",
                "fill_price": 403.66,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            }
        ],
    )
    _write_jsonl(
        art / "trading_loop_audit.jsonl",
        [{"order_id": "ord_real", "notes": ["market_data_source:coingecko"]}],
    )
    result = run_lint(art)
    assert not [x for x in result["violations"] if x["invariant_id"] == "TL-002"]


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


# ── TL-004 Klassifikations-Metriken (Operator-Nachtrag 07-11) ────────────────


def test_tl004_evidence_carries_classification_metrics(tmp_path, monkeypatch) -> None:
    """Rohe Zeilenzahl NIE als Episodenanzahl: die Verletzung trägt die
    Dimensionen, die Transparenz von echter Inflation unterscheiden."""
    from types import SimpleNamespace

    import app.observability.outcome_dedupe_report as odr
    from app.truth.lint import LintContext, _check_cross_path_episode_inflation

    art = _artifacts(tmp_path)
    rows = []
    # 4 resolved Rows: 2 teilen dieselbe document_id (Duplikat), 1 ohne Pfad-ID.
    for i, (doc, pid) in enumerate(
        [("d1", "tvpath_a"), ("d1", "tvpath_b"), ("d2", "rsspath_news_v1"), ("d3", None)]
    ):
        rows.append(
            {
                "outcome": "hit",
                "asset": "BTC/USDT",
                "document_id": doc,
                "annotated_at": f"2026-07-1{i}T08:00:00+00:00",
                "provenance": {"signal_path_id": pid} if pid else {},
            }
        )
    _write_jsonl(art / "alert_outcomes.jsonl", rows)
    # Vorheriger Lint-Lauf mit TL-004 largest=58 → growth = 70-58 = 12.
    _write_jsonl(
        art / "truth_lint_report.jsonl",
        [{"violations": [{"invariant_id": "TL-004", "evidence": {"largest_episode_size": 58}}]}],
    )
    monkeypatch.setattr(
        odr,
        "build_episode_dedupe_report",
        lambda **_kw: SimpleNamespace(largest_episode_size=70, episode_total=9, resolved_rows=120),
    )
    violations = _check_cross_path_episode_inflation(LintContext(artifacts_dir=art))
    assert len(violations) == 1
    ev = violations[0].evidence
    assert ev["largest_episode_size"] == 70
    assert ev["canonical_episode_count"] == 9
    assert ev["growth_since_last_run"] == 12
    assert ev["raw_rows"] == 4
    assert ev["distinct_document_ids"] == 3
    assert ev["duplicate_ratio"] == 0.25
    assert ev["distinct_signal_path_ids"] == 3
    assert ev["null_path_rows"] == 1
    assert "NIE als" in violations[0].message


def test_tl004_growth_none_without_previous_run(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    import app.observability.outcome_dedupe_report as odr
    from app.truth.lint import LintContext, _check_cross_path_episode_inflation

    art = _artifacts(tmp_path)
    _write_jsonl(art / "alert_outcomes.jsonl", [{"outcome": "hit", "document_id": "d1"}])
    monkeypatch.setattr(
        odr,
        "build_episode_dedupe_report",
        lambda **_kw: SimpleNamespace(largest_episode_size=41, episode_total=1, resolved_rows=41),
    )
    violations = _check_cross_path_episode_inflation(LintContext(artifacts_dir=art))
    assert violations[0].evidence["growth_since_last_run"] is None


# ── TL-012 Resolutions-Batch-Konzentration ───────────────────────────────────


def _resolved_row(doc: str, ts: str, *, outcome: str = "miss", asset: str = "ETH/USDT") -> dict:
    return {
        "document_id": doc,
        "outcome": outcome,
        "annotated_at": ts,
        "resolved_at": ts,
        "asset": asset,
        "provenance": {"signal_path_id": "tvpath_webhook_v1"},
    }


def _tl012(result: dict) -> list[dict]:
    return [v for v in result["violations"] if v["invariant_id"] == "TL-012"]


def test_tl012_flags_dominant_resolution_batch(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _write_jsonl(art / "alert_audit.jsonl", [])
    rows = [
        # 25 Resolutionen im selben Lauf (Sekunden-Abstand) — der Praxisfall
        # vom 2026-07-30 (33 ETH-misses in einem Annotate-Batch).
        _resolved_row(f"batch{i}", f"2026-07-27T07:07:{i:02d}+00:00")
        for i in range(25)
    ] + [
        # 5 verstreute Einzel-Resolutionen an anderen Tagen.
        _resolved_row(f"solo{i}", f"2026-07-2{2 + i}T01:00:00+00:00", outcome="hit")
        for i in range(5)
    ]
    _write_jsonl(art / "alert_outcomes.jsonl", rows)
    found = _tl012(run_lint(art))
    assert len(found) == 1
    ev = found[0]["evidence"]
    assert ev["top_batch_size"] == 25 and ev["n_window"] == 30
    assert ev["top_batch_share"] > 0.5
    assert ev["top_batch_assets"] == {"ETH/USDT": 25}
    assert found[0]["severity"] == "WARNING"


def test_tl012_silent_when_batches_are_spread(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _write_jsonl(art / "alert_audit.jsonl", [])
    rows = [
        _resolved_row(f"d{day}_{i}", f"2026-07-{20 + day:02d}T0{i}:00:00+00:00")
        for day in range(6)
        for i in range(4)  # 24 Resolutionen, kein Batch > 4/24
    ]
    _write_jsonl(art / "alert_outcomes.jsonl", rows)
    assert _tl012(run_lint(art)) == []


def test_tl012_silent_below_min_n_and_dedups_documents(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _write_jsonl(art / "alert_audit.jsonl", [])
    # 30 Zeilen, aber nur 10 Dokumente (Re-Evals) — dokument-dedupliziert
    # bleiben 10 < MIN_N, die Konzentration darf NICHT feuern.
    rows = [_resolved_row(f"doc{i % 10}", f"2026-07-27T07:07:{i:02d}+00:00") for i in range(30)]
    _write_jsonl(art / "alert_outcomes.jsonl", rows)
    assert _tl012(run_lint(art)) == []
