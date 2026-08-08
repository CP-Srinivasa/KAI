"""H2-Nachfolger ``signal_hit_to_win_conversion_v2`` (``26d3e0eb29f553f3``).

Der Vorgänger scheiterte nicht an der Frage, sondern an der Messbarkeit: nur
~26 % der geschlossenen Trades konnten die Population je erreichen, und ohne
Frist konnte der Claim weder PASS noch FAIL werden. Diese Tests halten beide
Reparaturen fest — plus die **Positivkontrolle**: ohne sie ist ein FAIL nicht
von einem kaputten Evaluator zu unterscheiden
(Lehre aus ``feedback_prereg_evaluator_must_be_committed``).

Gatend ist ausschließlich die hit-Konversion. Die Diagnostik (miss-Seite,
Trennschärfe, Konkordanz) wird mitgemessen, darf aber das Urteil NIE
beeinflussen — das ist hier explizit getestet, weil genau dort die
Versuchung läge, ein FAIL weichzuzeichnen.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.research.prereg_gate import check_gate
from app.research.quote_evals import evaluate_hit_to_win_conversion

REG = "2026-08-08T10:41:26+00:00"
AFTER = "2026-08-09T12:00:00+00:00"
BEFORE = "2026-08-07T12:00:00+00:00"
GATE = {"level": "overall", "horizon_s": 86400, "n_min": 30, "p_min": 0.9}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _close(doc: str, pnl: float | None, *, ts: str = AFTER, src: str = "technical_paper") -> dict:
    row: dict = {
        "event_type": "position_closed",
        "document_id": doc,
        "timestamp_utc": ts,
        "signal_source": src,
    }
    if pnl is not None:
        row["trade_pnl_usd"] = pnl
    return row


def _outcome(doc: str, outcome: str) -> dict:
    return {"document_id": doc, "outcome": outcome, "annotated_at": AFTER}


def _run(tmp_path: Path, outcomes: list[dict], closes: list[dict]) -> dict:
    op, ep = tmp_path / "outcomes.jsonl", tmp_path / "exec.jsonl"
    _write_jsonl(op, outcomes)
    _write_jsonl(ep, closes)
    return evaluate_hit_to_win_conversion(
        outcomes_path=op, exec_audit_path=ep, registered_at_utc=REG
    )


# ── Positivkontrolle: bekannter Input → bekannte Antwort ─────────────────────


def test_positive_control_all_hits_win_passes_gate(tmp_path: Path) -> None:
    """40 hit-Trades, alle profitabel ⇒ Konversion 1.0, Gate PASS.

    Die Kontrolle beweist, dass ein PASS überhaupt erreichbar ist. Ohne sie
    wäre ein späteres FAIL nicht interpretierbar.
    """
    outcomes = [_outcome(f"d{i}", "hit") for i in range(40)]
    closes = [_close(f"d{i}", 10.0) for i in range(40)]
    res = _run(tmp_path, outcomes, closes)

    row = res["overall"]["horizons"]["86400"]
    assert row["n"] == 40
    assert row["positive_rate"] == 1.0
    assert row["p_positive"] == 1.0
    assert res["diagnostics"]["win_rate_hit"] == 1.0
    assert check_gate(GATE, res)["passed"] is True


def test_negative_control_all_hits_lose_fails_gate(tmp_path: Path) -> None:
    """Spiegelbild: 40 hit-Trades, alle im Minus ⇒ Gate FAIL, nicht Crash."""
    outcomes = [_outcome(f"d{i}", "hit") for i in range(40)]
    closes = [_close(f"d{i}", -10.0) for i in range(40)]
    res = _run(tmp_path, outcomes, closes)

    row = res["overall"]["horizons"]["86400"]
    assert row["n"] == 40
    assert row["positive_rate"] == 0.0
    assert row["p_positive"] == 0.0
    assert check_gate(GATE, res)["passed"] is False


# ── Die miss-Seite darf das Urteil NICHT bewegen ─────────────────────────────


def test_miss_side_is_diagnostic_only_and_never_gates(tmp_path: Path) -> None:
    """Beliebig viele profitable miss-Trades ändern den gatenden Block nicht.

    Das ist der Kern der Konstruktion: die miss-Zellen erklären ein Ergebnis
    (Signal- vs. Execution-Problem), sie erzeugen keines.
    """
    outcomes = [_outcome(f"h{i}", "hit") for i in range(4)]
    outcomes += [_outcome(f"m{i}", "miss") for i in range(50)]
    closes = [_close(f"h{i}", -5.0) for i in range(4)]
    closes += [_close(f"m{i}", 99.0) for i in range(50)]  # miss + Gewinn
    res = _run(tmp_path, outcomes, closes)

    row = res["overall"]["horizons"]["86400"]
    assert row["n"] == 4, "nur hit-Dokumente sind gatend"
    assert row["positive_rate"] == 0.0
    diag = res["diagnostics"]
    assert diag["n_miss"] == 50
    assert diag["cells"] == {"hit_win": 0, "hit_loss": 4, "miss_win": 50, "miss_loss": 0}
    assert diag["win_rate_miss"] == 1.0
    # Trennschärfe negativ = Signal zeigt in die falsche Richtung.
    assert diag["discrimination_pp"] == -1.0
    assert check_gate(GATE, res)["passed"] is False


def test_cells_and_discrimination_reproduce_the_2026_08_08_in_sample_shape(
    tmp_path: Path,
) -> None:
    """Die offengelegte in-sample-Form (8/10/1/14) muss exakt herauskommen.

    Hält die Zahl fest, die in den versiegelten ``success_criteria`` steht —
    eine spätere Änderung der Zell-Logik fiele hier sofort auf.
    """
    outcomes, closes = [], []
    for i in range(8):
        outcomes.append(_outcome(f"hw{i}", "hit"))
        closes.append(_close(f"hw{i}", 5.0))
    for i in range(10):
        outcomes.append(_outcome(f"hl{i}", "hit"))
        closes.append(_close(f"hl{i}", -5.0))
    outcomes.append(_outcome("mw0", "miss"))
    closes.append(_close("mw0", 5.0))
    for i in range(14):
        outcomes.append(_outcome(f"ml{i}", "miss"))
        closes.append(_close(f"ml{i}", -5.0))
    res = _run(tmp_path, outcomes, closes)

    diag = res["diagnostics"]
    assert diag["cells"] == {"hit_win": 8, "hit_loss": 10, "miss_win": 1, "miss_loss": 14}
    assert diag["n_hit"] == 18
    assert diag["win_rate_hit"] == round(8 / 18, 4)
    assert diag["win_rate_miss"] == round(1 / 15, 4)
    assert diag["concordance_n"] == 33
    assert diag["concordance_rate"] == round(22 / 33, 4)
    # Konversion 44,4 % < 50 % ⇒ das Gate darf NICHT passieren.
    assert res["overall"]["horizons"]["86400"]["positive_rate"] == round(8 / 18, 4)


# ── Populationsschnitte, fail-closed ─────────────────────────────────────────


def test_population_gap_is_counted_by_signal_source(tmp_path: Path) -> None:
    """Die Blindstelle, an der H2 verhungerte, wird gezählt statt verschwiegen."""
    outcomes = [_outcome("known", "hit")]
    closes = [
        _close("known", 5.0),
        _close("uuid-a", 5.0, src="real_analysis"),
        _close("uuid-b", -5.0, src="real_analysis"),
        _close("uuid-c", 5.0, src="tradingview_webhook"),
    ]
    res = _run(tmp_path, outcomes, closes)

    pop = res["population"]
    assert pop["absent_from_outcome_ledger"] == 3
    assert pop["absent_by_signal_source"] == {"real_analysis": 2, "tradingview_webhook": 1}
    assert res["overall"]["horizons"]["86400"]["n"] == 1


def test_missing_trade_pnl_is_excluded_and_counted_not_estimated(tmp_path: Path) -> None:
    """TL-003: kumulatives ``realized_pnl_usd`` ist KEIN Ersatz."""
    outcomes = [_outcome("a", "hit"), _outcome("b", "hit")]
    closes = [
        _close("a", 5.0),
        {
            "event_type": "position_closed",
            "document_id": "b",
            "timestamp_utc": AFTER,
            "signal_source": "technical_paper",
            "realized_pnl_usd": 9999.0,  # Falle: kumulativ, darf nicht zählen
        },
    ]
    res = _run(tmp_path, outcomes, closes)

    assert res["population"]["docs_excluded_missing_trade_pnl"] == 1
    assert res["overall"]["horizons"]["86400"]["n"] == 1


def test_closes_before_registration_are_out_of_population(tmp_path: Path) -> None:
    """FORWARD-Population: alles vor t0 zählt nicht (Prä-Reg-Disziplin)."""
    outcomes = [_outcome("old", "hit"), _outcome("new", "hit")]
    closes = [_close("old", 5.0, ts=BEFORE), _close("new", 5.0)]
    res = _run(tmp_path, outcomes, closes)

    assert res["overall"]["horizons"]["86400"]["n"] == 1
    assert res["population"]["closed_docs_since_reg"] == 1


def test_partial_closes_sum_per_document(tmp_path: Path) -> None:
    """Mehrere Teilschließungen ergeben EIN Dokument-Ergebnis (Summe)."""
    outcomes = [_outcome("d", "hit")]
    closes = [
        {
            "event_type": "position_partial_closed",
            "document_id": "d",
            "timestamp_utc": AFTER,
            "signal_source": "technical_paper",
            "trade_pnl_usd": -8.0,
        },
        _close("d", 3.0),
    ]
    res = _run(tmp_path, outcomes, closes)

    row = res["overall"]["horizons"]["86400"]
    assert row["n"] == 1
    assert row["positive_rate"] == 0.0  # -8 + 3 < 0


def test_last_outcome_row_per_document_wins(tmp_path: Path) -> None:
    """Dokument-Dedup: eine spätere Korrektur überschreibt die frühere Zeile."""
    outcomes = [_outcome("d", "miss"), _outcome("d", "hit")]
    closes = [_close("d", 5.0)]
    res = _run(tmp_path, outcomes, closes)

    assert res["diagnostics"]["cells"]["hit_win"] == 1
    assert res["diagnostics"]["cells"]["miss_win"] == 0


def test_inconclusive_outcomes_are_excluded_and_visible(tmp_path: Path) -> None:
    outcomes = [_outcome("a", "hit"), _outcome("b", "inconclusive")]
    closes = [_close("a", 5.0), _close("b", 5.0)]
    res = _run(tmp_path, outcomes, closes)

    assert res["population"]["excluded_by_outcome"] == {"inconclusive": 1}
    assert res["overall"]["horizons"]["86400"]["n"] == 1


def test_empty_inputs_are_fail_closed_not_a_crash(tmp_path: Path) -> None:
    res = _run(tmp_path, [], [])
    row = res["overall"]["horizons"]["86400"]
    assert row["n"] == 0
    assert row["p_positive"] is None
    assert check_gate(GATE, res)["passed"] is False
