"""H1/H2-Quoten-Evaluatoren: Population, ±1-Statistik, Gate-Kompatibilität.

Die Konstruktion ist in den versiegelten ``success_criteria`` festgelegt
(Prä-Regs ``fd6f5f7842f49244`` / ``0c7ead764621dd17`` vom 2026-07-29) —
diese Tests halten die Populations-Schnitte (erste Füllung NACH Registrierung,
letzte Zeile je Dokument, inconclusive exkludiert, trade_pnl_usd-Pflicht)
und die Normal-Approximation fest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.research.prereg_gate import check_gate
from app.research.quote_evals import (
    evaluate_execution_translation,
    evaluate_technical_paper_precision,
    normal_p_positive,
    reconstruct_tv_signal_id,
)

H1_REG = "2026-07-29T09:14:47+00:00"
H2_REG = "2026-07-29T09:15:10+00:00"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _fill(doc: str, ts: str) -> dict:
    return {
        "event_type": "order_filled",
        "document_id": doc,
        "filled_at": ts,
        "timestamp_utc": ts,
    }


def _close(doc: str, ts: str, pnl: float | None) -> dict:
    row = {
        "event_type": "position_closed",
        "document_id": doc,
        "timestamp_utc": ts,
    }
    if pnl is not None:
        row["trade_pnl_usd"] = pnl
    return row


def _outcome(doc: str, outcome: str, *, asset: str | None = None, src: str | None = None) -> dict:
    row: dict = {
        "document_id": doc,
        "outcome": outcome,
        "annotated_at": "2026-07-30T00:00:00+00:00",
    }
    if asset is not None:
        row["asset"] = asset
    if src is not None:
        row["price_source"] = src
    return row


# ── Normal-Approximation ─────────────────────────────────────────────────────


def test_normal_p_positive_matches_hand_computed_z() -> None:
    # 9 hit / 1 miss: mean 0.8, sd² = 10/9·0.36 = 0.4, se = 0.2, z = 4.
    p = normal_p_positive([1.0] * 9 + [-1.0])
    assert p is not None and 0.99996 < p < 1.0


def test_normal_p_positive_degenerate_and_small_samples() -> None:
    assert normal_p_positive([]) is None
    assert normal_p_positive([1.0]) is None  # n<2: keine Varianzschätzung
    assert normal_p_positive([1.0, 1.0, 1.0]) == 1.0
    assert normal_p_positive([-1.0, -1.0]) == 0.0
    assert normal_p_positive([1.0, -1.0]) == 0.5


# ── H1 technical_paper_precision_fwd_v1 ──────────────────────────────────────


@pytest.fixture
def h1_paths(tmp_path: Path) -> tuple[Path, Path]:
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _fill("technical_paper_A_pre", "2026-07-28T00:00:00+00:00"),  # vor Reg.
            _fill("technical_paper_B", "2026-07-29T12:00:00+00:00"),
            _fill("technical_paper_B", "2026-07-30T12:00:00+00:00"),  # Zweitfüllung egal
            _fill("technical_paper_C", "2026-07-29T13:00:00+00:00"),
            _fill("technical_paper_D", "2026-07-29T14:00:00+00:00"),
            _fill("technical_paper_E", "2026-07-29T15:00:00+00:00"),
            _fill("SIG-TVP-ETHUSDT-deadbeef", "2026-07-29T16:00:00+00:00"),  # fremder Präfix
        ],
    )
    _write_jsonl(
        outcomes,
        [
            _outcome("technical_paper_A_pre", "hit"),  # nicht in Population
            _outcome("technical_paper_B", "hit"),
            _outcome("technical_paper_B", "miss"),  # letzte Zeile gewinnt
            _outcome("technical_paper_C", "hit", src="coingecko"),
            _outcome("technical_paper_D", "inconclusive"),
            # E: kein Outcome -> pending
        ],
    )
    return outcomes, audit


def test_h1_population_and_stats(h1_paths: tuple[Path, Path]) -> None:
    outcomes, audit = h1_paths
    res = evaluate_technical_paper_precision(
        outcomes_path=outcomes, exec_audit_path=audit, registered_at_utc=H1_REG
    )
    pop = res["population"]
    assert pop["docs_first_fill_after_reg"] == 4  # B C D E
    assert pop["docs_resolved"] == 2  # B miss, C hit
    assert pop["docs_inconclusive"] == 1
    assert pop["inconclusive_share"] == pytest.approx(1 / 3, abs=1e-4)
    assert pop["docs_pending_no_outcome"] == 1
    # price_source: nur C trägt das Feld -> Coverage 1/2, Fallback 1/1.
    assert pop["price_source_coverage"] == 0.5
    assert pop["price_source_fallback_share"] == 1.0
    row = res["overall"]["horizons"]["604800"]
    assert row["n"] == 2 and row["mean_x"] == 0.0 and row["p_positive"] == 0.5


def test_h1_block_is_judgeable_by_sealed_gate(h1_paths: tuple[Path, Path]) -> None:
    outcomes, audit = h1_paths
    res = evaluate_technical_paper_precision(
        outcomes_path=outcomes, exec_audit_path=audit, registered_at_utc=H1_REG
    )
    gate = {"level": "overall", "horizon_s": 604800, "n_min": 200, "p_min": 0.95}
    out = check_gate(gate, res)
    checks = {c["name"]: c for c in out["checks"]}
    assert checks["overall@604800s present"]["ok"] is True
    assert checks["n_min"]["actual"] == 2 and out["passed"] is False


def test_h1_missing_files_mean_empty_population(tmp_path: Path) -> None:
    res = evaluate_technical_paper_precision(
        outcomes_path=tmp_path / "no_outcomes.jsonl",
        exec_audit_path=tmp_path / "no_audit.jsonl",
        registered_at_utc=H1_REG,
    )
    assert res["population"]["docs_first_fill_after_reg"] == 0
    assert res["overall"]["horizons"]["604800"]["p_positive"] is None


# ── H2 execution_translation_hit_to_win_v1 ───────────────────────────────────


def test_reconstruct_tv_signal_id_matches_feeder_formula() -> None:
    digest16 = hashlib.sha256(b"evt123").hexdigest()[:16]
    assert reconstruct_tv_signal_id("evt123", "ETHUSDT") == f"SIG-TVP-ETHUSDT-{digest16[-8:]}"


def test_h2_join_pnl_sign_and_exclusions(tmp_path: Path) -> None:
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "paper_execution_audit.jsonl"
    sig_eth = reconstruct_tv_signal_id("evt123", "ETHUSDT")
    _write_jsonl(
        outcomes,
        [
            _outcome("technical_paper_X", "hit"),
            _outcome("technical_paper_Y", "hit"),
            _outcome("tv:evt123", "hit", asset="ETH/USDT"),
            _outcome("technical_paper_Z", "miss"),
            _outcome("technical_paper_V", "hit"),
            _outcome("technical_paper_U", "hit"),
        ],
    )
    _write_jsonl(
        audit,
        [
            _close("technical_paper_X", "2026-07-29T12:00:00+00:00", 10.0),
            {
                **_close("technical_paper_X", "2026-07-29T13:00:00+00:00", 5.0),
                "event_type": "position_partial_closed",
            },
            _close("technical_paper_Y", "2026-07-29T12:30:00+00:00", -5.0),
            _close("technical_paper_Y", "2026-07-29T14:00:00+00:00", 2.0),
            _close(sig_eth, "2026-07-29T15:00:00+00:00", 7.0),  # Join via Rekonstruktion
            _close("technical_paper_Z", "2026-07-29T16:00:00+00:00", 10.0),  # Outcome=miss
            _close("technical_paper_W", "2026-07-29T17:00:00+00:00", 3.0),  # kein Outcome
            _close("technical_paper_V", "2026-07-29T00:00:00+00:00", 9.0),  # vor Reg.
            _close("technical_paper_U", "2026-07-29T18:00:00+00:00", None),  # pnl fehlt
        ],
    )
    res = evaluate_execution_translation(
        outcomes_path=outcomes, exec_audit_path=audit, registered_at_utc=H2_REG
    )
    pop = res["population"]
    assert pop["docs_joined_to_hit"] == 3  # X (+15), Y (−3), ETH (+7)
    assert pop["joined_direct"] == 2 and pop["joined_reconstructed"] == 1
    assert pop["docs_excluded_missing_trade_pnl"] == 1  # U fail-closed
    assert pop["closed_docs_since_reg"] == 6  # X Y ETH Z W U (V vor Reg.)
    row = res["overall"]["horizons"]["86400"]
    assert row["n"] == 3
    assert row["positive_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert row["mean_x"] == pytest.approx(1 / 3, abs=1e-4)


def test_h2_block_is_judgeable_by_sealed_gate(tmp_path: Path) -> None:
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(outcomes, [_outcome("technical_paper_X", "hit")])
    _write_jsonl(audit, [_close("technical_paper_X", "2026-07-29T12:00:00+00:00", 1.0)])
    res = evaluate_execution_translation(
        outcomes_path=outcomes, exec_audit_path=audit, registered_at_utc=H2_REG
    )
    gate = {"level": "overall", "horizon_s": 86400, "n_min": 50, "p_min": 0.9}
    out = check_gate(gate, res)
    checks = {c["name"]: c for c in out["checks"]}
    assert checks["overall@86400s present"]["ok"] is True
    assert checks["n_min"]["actual"] == 1 and out["passed"] is False
