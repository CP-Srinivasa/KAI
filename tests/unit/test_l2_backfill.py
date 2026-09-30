"""L2-Rueckschau (vorregistriert, Operator 2026-10-01).

Haelt fest: das Merkmal ist exakt die Live-Rechnung und sieht nie in die Zukunft;
die Population schliesst Canary-/Probe-Zeilen aus; Kandidaten derselben
L1-Beobachtung zaehlen als EINE Einheit; das Urteil ist nur „bestaetigt“, wenn
Kandidaten-Ebene, Cluster-Ebene und beide Zeithaelften uebereinstimmen.
Nur synthetische Daten — die echte Auswertung laeuft erst nach dem Merge.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.evidence_settings import L2OnChainEvidenceSettings
from app.research import l2_backfill as bf
from app.signals.l2_features import compute_l2_features

_T0 = datetime(2026, 7, 1, tzinfo=UTC)


def _l1(n: int, *, start: datetime = _T0, step_min: int = 15) -> list[dict]:
    return [
        {
            "ts": (start + timedelta(minutes=step_min * i)).isoformat(),
            "fee_sat_vb": 1.0 + (i * 7 % 13),
            "mempool_tx": 1000 + (i * 11 % 17) * 100,
        }
        for i in range(n)
    ]


def _pair(i: int, feature: float, net: float, *, side: str = "long", l1_ts: str | None = None):
    m = {
        "candidate_id": f"tech-X-{i}",
        "symbol": "BTC/USDT",
        "direction": side,
        "fee_percentile": feature,
        "mempool_percentile": feature,
        "l1_observed_ts": l1_ts or f"l1-{i}",
    }
    o = {
        "candidate_id": f"tech-X-{i}",
        "symbol": "BTC/USDT",
        "side": side,
        "entry_ts": (_T0 + timedelta(minutes=15 * i)).isoformat(),
        "net_bps": net,
    }
    return m, o


# ---------------------------------------------------------------- Vorregistrierung


def test_preregistered_constants_match_the_live_provider() -> None:
    live = L2OnChainEvidenceSettings.model_fields
    assert (bf.WINDOW, bf.MIN_WINDOW, bf.TTL_S) == (
        live["window"].default,
        live["min_window"].default,
        live["ttl_seconds"].default,
    )
    assert bf.HORIZON_S == 3600 and bf.SEED == 1337
    assert bf.FEATURES == ("fee_percentile", "mempool_percentile")


# ---------------------------------------------------------------- Merkmal


def test_feature_is_the_live_computation_on_data_before_the_entry() -> None:
    rows = _l1(300)
    at = _T0 + timedelta(minutes=15 * 250, seconds=30)
    got = bf.L1Index(rows).features_at(at)
    usable = rows[:251][-(bf.WINDOW + 1) :]  # Saetze 0..250 liegen vor ``at``
    live = compute_l2_features(
        usable[:-1],
        fee_sat_vb=usable[-1]["fee_sat_vb"],
        mempool_tx=usable[-1]["mempool_tx"],
    )
    assert got is not None
    assert got["fee_percentile"] == live.fee_percentile
    assert got["mempool_percentile"] == live.mempool_percentile
    assert got["l1_observed_ts"] == rows[250]["ts"]


def test_a_later_l1_row_never_changes_the_feature() -> None:
    rows = _l1(100)
    at = _T0 + timedelta(minutes=15 * 60, seconds=1)
    before = bf.L1Index(rows).features_at(at)
    future = {
        "ts": (at + timedelta(seconds=1)).isoformat(),
        "fee_sat_vb": 999.0,
        "mempool_tx": 10**7,
    }
    assert bf.L1Index([*rows, future]).features_at(at) == before


def test_no_feature_when_l1_is_stale_or_history_too_short() -> None:
    rows = _l1(100)
    last = datetime.fromisoformat(rows[-1]["ts"])
    index = bf.L1Index(rows)
    assert index.features_at(last + timedelta(seconds=bf.TTL_S + 1)) is None
    assert index.features_at(last + timedelta(seconds=bf.TTL_S)) is not None
    short = bf.L1Index(_l1(bf.MIN_WINDOW))  # eine Historie zu wenig
    assert short.features_at(_T0 + timedelta(hours=24)) is None
    assert bf.L1Index(rows).features_at(_T0 - timedelta(seconds=1)) is None


# ---------------------------------------------------------------- Population


def test_population_excludes_canary_and_probe_rows() -> None:
    real = {"source": "technical_screener", "candidate_kind": "technical", "is_canary": False}
    assert bf.in_population(real)
    assert not bf.in_population({**real, "is_canary": True})
    assert not bf.in_population({**real, "source": "canary_probe"})
    assert not bf.in_population({**real, "candidate_kind": "raw_scan"})


# ---------------------------------------------------------------- Einheit


def test_candidates_of_one_l1_observation_and_side_are_one_unit() -> None:
    pairs = [
        _pair(0, 0.9, 10.0, l1_ts="a"),
        _pair(1, 0.9, 30.0, l1_ts="a"),
        _pair(2, 0.9, -5.0, side="short", l1_ts="a"),
        _pair(3, 0.2, 4.0, l1_ts="b"),
    ]
    clusters = bf.cluster_pairs(pairs)
    got = {(m["l1_observed_ts"], o["side"]): (o["net_bps"], o["population"]) for m, o in clusters}
    assert got == {("a", "long"): (20.0, 2), ("a", "short"): (-5.0, 1), ("b", "long"): (4.0, 1)}
    assert [o["entry_ts"] for _m, o in clusters] == sorted(o["entry_ts"] for _m, o in clusters)


# ---------------------------------------------------------------- Urteil


def _alternating(n: int, high: float, low: float, start: int = 0) -> list:
    return [
        _pair(start + i, 0.9 if i % 2 else 0.1, (high if i % 2 else low) + (i % 5 - 2))
        for i in range(n)
    ]


def test_consistent_effect_is_confirmed() -> None:
    pairs = _alternating(400, 50.0, -50.0)
    v = bf.verdict(pairs, bf.cluster_pairs(pairs), "fee_percentile")
    assert v["verdict"] == "bestaetigt: pro_trend"
    assert all(s is not None and s > 0 for s in v["by_half_spread_bps"])


def test_effect_that_flips_in_the_second_half_is_not_confirmed() -> None:
    pairs = _alternating(200, 100.0, -100.0) + _alternating(200, -5.0, 5.0, start=200)
    v = bf.verdict(pairs, bf.cluster_pairs(pairs), "fee_percentile")
    assert v["verdict"] == "nicht bestaetigt (zeitliche Haelften uneinig)"


def test_many_candidates_in_few_observations_are_not_enough() -> None:
    # 5 L1-Beobachtungen mit je 80 Kandidaten: auf Kandidaten-Ebene „klar“, als
    # Einheiten aber nur 5 — das darf kein Urteil tragen.
    pairs = [
        _pair(
            i, 0.9 if (i // 80) % 2 else 0.1, 50.0 if (i // 80) % 2 else -50.0, l1_ts=f"o{i // 80}"
        )
        for i in range(400)
    ]
    v = bf.verdict(pairs, bf.cluster_pairs(pairs), "fee_percentile")
    assert v["candidate_assessment"]["direction"] == "pro_trend"
    assert v["verdict"].startswith("nicht bestaetigt (Cluster-Ebene insufficient")


def test_base_rate_carries_its_decomposition() -> None:
    br = bf.base_rate([_pair(0, 0.9, 5.0), _pair(1, 0.1, -1.0), _pair(2, 0.1, 2.0)])
    assert br == {
        "population": 3,
        "mean_bps": 2.0,
        "numerator": 2,
        "denominator": 3,
        "share_positive": 2 / 3,
    }


# ---------------------------------------------------------------- Durchlauf


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_run_and_script_end_to_end(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    l1 = _write(tmp_path / "l1.jsonl", _l1(400))
    resolved = []
    for i in range(60):
        at = _T0 + timedelta(hours=10, minutes=15 * i, seconds=5)
        resolved.append(
            {
                "candidate_id": f"tech-BTCUSDT-{at.isoformat()}",
                "symbol": "BTC/USDT",
                "side": "long" if i % 3 else "short",
                "source": "canary_probe" if i == 0 else "technical_screener",
                "candidate_kind": "technical",
                "is_canary": i == 1,
                "fwd_3600s_bps": float(i % 7 - 3),
            }
        )
    res = _write(tmp_path / "resolved.jsonl", resolved)
    led = _write(tmp_path / "ledger.jsonl", [])
    report = bf.run(l1_path=l1, resolved_path=res, ledger_path=led)
    assert report["population_with_outcome"] == 58  # Canary-Quelle und is_canary raus
    assert report["pairs"] == 58 and report["clusters"] == 58
    assert [v["feature"] for v in report["verdicts"]] == list(bf.FEATURES)

    from scripts.l2_backfill_eval import main

    out = tmp_path / "r.json"
    assert (
        main(
            ["--l1", str(l1), "--resolved", str(res), "--ledger", str(led), "--json-out", str(out)]
        )
        == 0
    )
    assert "Paare 58" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["pairs"] == 58
