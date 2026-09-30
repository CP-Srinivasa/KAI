"""Unit tests for L2 on-chain flow features (Sprint 2, shadow-only, B-003).

DIRECTION-AGNOSTIC by design: we compute RAW percentile features (fee, mempool)
of the current on-chain state within a recent window of KAI's OWN L1 fee-shadow
stream, and log them with the candidate signal context. We do NOT pre-choose a
contrarian/pro-trend direction — that is learned later by evaluate_l2_evidence.py.
"""

from __future__ import annotations

import json

from app.core.l2_candidate_context import bind_candidate
from app.signals.l2_features import (
    OnchainFlowFeatures,
    append_l2_shadow_log,
    compute_l2_features,
    percentile_rank,
    read_onchain_fee_shadow,
)

# --- percentile_rank -------------------------------------------------------------


def test_percentile_rank_empty_window_is_none() -> None:
    assert percentile_rank(5.0, []) is None


def test_percentile_rank_none_value_is_none() -> None:
    assert percentile_rank(None, [1.0, 2.0]) is None


def test_percentile_rank_fraction_leq() -> None:
    window = [1.0, 2.0, 3.0, 4.0]
    assert percentile_rank(2.0, window) == 0.5  # 2 of 4 are <= 2.0
    assert percentile_rank(0.0, window) == 0.0  # below all
    assert percentile_rank(4.0, window) == 1.0  # at/above all


# --- compute_l2_features ---------------------------------------------------------


def test_compute_features_percentiles_within_history() -> None:
    history = [
        {"fee_sat_vb": 1.0, "mempool_tx": 1000},
        {"fee_sat_vb": 2.0, "mempool_tx": 2000},
        {"fee_sat_vb": 3.0, "mempool_tx": 3000},
        {"fee_sat_vb": 4.0, "mempool_tx": 4000},
    ]
    feats = compute_l2_features(history, fee_sat_vb=3.0, mempool_tx=2000)
    assert isinstance(feats, OnchainFlowFeatures)
    assert feats.fee_sat_vb == 3.0
    assert feats.mempool_tx == 2000
    assert feats.fee_percentile == 0.75  # 3 of 4 <= 3.0
    assert feats.mempool_percentile == 0.5  # 2 of 4 <= 2000
    assert feats.window_n == 4


def test_compute_features_empty_history_percentiles_none() -> None:
    feats = compute_l2_features([], fee_sat_vb=2.0, mempool_tx=1500)
    assert feats.fee_percentile is None
    assert feats.mempool_percentile is None
    assert feats.window_n == 0


def test_compute_features_skips_none_fee_in_history() -> None:
    history = [
        {"fee_sat_vb": None, "mempool_tx": 1000},
        {"fee_sat_vb": 2.0, "mempool_tx": 2000},
    ]
    feats = compute_l2_features(history, fee_sat_vb=2.0, mempool_tx=2000)
    # fee window only has the 2.0 sample (None skipped) → 1.0; mempool has both
    assert feats.fee_percentile == 1.0
    assert feats.mempool_percentile == 1.0


# --- read_onchain_fee_shadow -----------------------------------------------------


def test_read_stream_missing_file_returns_empty(tmp_path) -> None:
    assert read_onchain_fee_shadow(tmp_path / "nope.jsonl") == []


def test_read_stream_tolerant_tail(tmp_path) -> None:
    p = tmp_path / "onchain_fee_shadow.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"fee_sat_vb": 1.0, "mempool_tx": 100}) + "\n")
        fh.write("not json\n")  # corrupt line skipped
        fh.write("\n")  # blank skipped
        fh.write(json.dumps({"fee_sat_vb": 2.0, "mempool_tx": 200}) + "\n")
    recs = read_onchain_fee_shadow(p)
    assert [r["fee_sat_vb"] for r in recs] == [1.0, 2.0]


def test_read_stream_honours_limit_last_n(tmp_path) -> None:
    p = tmp_path / "s.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for i in range(5):
            fh.write(json.dumps({"fee_sat_vb": float(i), "mempool_tx": i}) + "\n")
    recs = read_onchain_fee_shadow(p, limit=2)
    assert [r["fee_sat_vb"] for r in recs] == [3.0, 4.0]


# --- shadow log ------------------------------------------------------------------


def test_append_shadow_log_writes_raw_features_and_context(tmp_path) -> None:
    out = tmp_path / "l2_shadow.jsonl"
    feats = OnchainFlowFeatures(
        fee_sat_vb=2.5, mempool_tx=2000, fee_percentile=0.75, mempool_percentile=0.5, window_n=4
    )
    append_l2_shadow_log(out, symbol="BTC/USDT", direction="LONG", features=feats, source_trust=0.5)
    line = json.loads(out.read_text(encoding="utf-8").strip())
    assert line["symbol"] == "BTC/USDT"
    assert line["direction"] == "LONG"
    assert line["fee_sat_vb"] == 2.5
    assert line["mempool_tx"] == 2000
    assert line["fee_percentile"] == 0.75
    assert line["mempool_percentile"] == 0.5
    assert line["window_n"] == 4
    assert line["source_trust"] == 0.5
    assert "ts" in line
    # B-003: NO pre-chosen direction-aligned strength is recorded — raw only.
    assert "evidence_direction_aligned" not in line
    assert "evidence_value" not in line


# --- shadow log: Kandidatenkontext ------------------------------------------------
#
# Der Writer laeuft INNERHALB von ``SignalGenerator.generate``. Steht dort ein
# Kandidatenkontext, traegt die Messzeile ihn mit — additiv, damit ein Leser
# beide Formen akzeptieren muss und Altzeilen gueltig bleiben.


def _feats() -> OnchainFlowFeatures:
    return OnchainFlowFeatures(
        fee_sat_vb=2.5, mempool_tx=2000, fee_percentile=0.75, mempool_percentile=0.5, window_n=4
    )


def _write(out, **kw) -> dict:
    append_l2_shadow_log(
        out, symbol="BTC/USDT", direction="LONG", features=_feats(), source_trust=0.5, **kw
    )
    return json.loads(out.read_text(encoding="utf-8").strip().splitlines()[-1])


def test_ohne_zyklus_bleibt_die_messzeile_genau_wie_bisher(tmp_path) -> None:
    out = tmp_path / "l2_shadow.jsonl"
    line = _write(out)
    for feld in (
        "candidate_id",
        "cycle_started_at",
        "input_cutoff_ts",
        "decision_ts",
        "reference_price_ts",
        "causality_ok",
        "l1_observed_ts",
    ):
        assert feld not in line


def _bind(**kw):  # noqa: ANN202
    return bind_candidate(
        candidate_id="cycle-42",
        cycle_started_at=kw.get("start", "2026-09-23T10:00:00+00:00"),
        input_cutoff_ts=kw.get("cut", "2026-09-23T10:00:00.400000+00:00"),
        reference_price_ts=kw.get("price"),
    )


def test_im_zyklus_traegt_die_messzeile_id_zeiten_und_l1(tmp_path) -> None:
    out = tmp_path / "l2_shadow.jsonl"
    with _bind(price="2026-09-23T10:00:00.170000+00:00"):
        line = _write(out, l1_observed_ts="2026-09-23T09:45:00+00:00")
    assert line["candidate_id"] == "cycle-42"
    assert line["cycle_started_at"] == "2026-09-23T10:00:00+00:00"
    assert line["input_cutoff_ts"] == "2026-09-23T10:00:00.400000+00:00"
    assert line["reference_price_ts"] == "2026-09-23T10:00:00.170000+00:00"
    assert line["l1_observed_ts"] == "2026-09-23T09:45:00+00:00"
    assert line["causality_ok"] is True
    assert "decision_ts" not in line


def test_der_beobachtungszeitpunkt_wird_nicht_zurueckdatiert(tmp_path) -> None:
    """``ts`` bleibt die Uhr der Messung — NICHT der Zyklusbeginn oder der Schnitt."""
    out = tmp_path / "l2_shadow.jsonl"
    vergangen = "2020-01-01T00:00:00+00:00"
    with _bind(start=vergangen, cut=vergangen):
        line = _write(out)
    assert line["ts"] != vergangen
    assert line["ts"] > "2026-"
    assert line["cycle_started_at"] == vergangen


def test_look_ahead_wird_markiert_statt_verschwiegen(tmp_path) -> None:
    out = tmp_path / "l2_shadow.jsonl"
    with _bind(price="2026-09-23T10:00:05+00:00"):  # Preis JUENGER als der Schnitt
        line = _write(out, l1_observed_ts="2026-09-23T09:45:00+00:00")
    assert line["causality_ok"] is False
    # Die Zeile wird trotzdem geschrieben — das Urteil gehoert dem Evaluator.
    assert line["candidate_id"] == "cycle-42"


def test_l1_zeit_steht_auch_ausserhalb_eines_zyklus(tmp_path) -> None:
    line = _write(tmp_path / "l2_shadow.jsonl", l1_observed_ts="2026-09-23T09:45:00+00:00")
    assert line["l1_observed_ts"] == "2026-09-23T09:45:00+00:00"
    assert "candidate_id" not in line


def test_die_rohmerkmale_bleiben_unangetastet(tmp_path) -> None:
    out = tmp_path / "l2_shadow.jsonl"
    ohne = _write(out)
    with _bind():
        mit = _write(out)
    roh = (
        "symbol",
        "direction",
        "fee_sat_vb",
        "mempool_tx",
        "fee_percentile",
        "mempool_percentile",
        "window_n",
        "source_trust",
    )
    assert {k: ohne[k] for k in roh} == {k: mit[k] for k in roh}
    # B-003 gilt weiter: keine vorgewaehlte Richtungsstaerke.
    assert "evidence_direction_aligned" not in mit
