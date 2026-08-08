"""Zerlegungs-Pflicht ausgeweitet: Paper-Qualität und News-Evaluator.

Welle 1 erfasste die Quoten-Evaluatoren. Hier kommen die beiden übrigen
urteilstragenden Aggregate dazu:

* ``paper_quality_snapshot.win_rate`` — steht im Daily, im Dashboard und in
  jedem Qualitätsbericht.
* ``news_signal_eval`` ``mean_bps`` — über diesen Pfad lief das TERMINALE
  ND-v2-Verdikt. Hier ist Verdeckung am teuersten.

Beide hatten ihre Gruppentabellen längst (``by_symbol``/``by_reason``,
``top_symbol_share``) — bewertet hat sie niemand. Genau diese Lücke schließen
die Tests hier fest.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.paper_quality_snapshot import build_paper_quality_snapshot
from app.research.news_signal_eval import evaluate_cohort


def _audit(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _close(symbol: str, pnl: float, reason: str = "take") -> dict:
    return {
        "event_type": "position_closed",
        "document_id": f"d-{symbol}-{pnl}-{reason}",
        "timestamp_utc": "2026-08-08T10:00:00+00:00",
        "symbol": symbol,
        "reason": reason,
        "trade_pnl_usd": pnl,
    }


# ── Paper-Qualität ───────────────────────────────────────────────────────────


def test_paper_quality_exposes_who_carries_the_win_rate(tmp_path: Path) -> None:
    """Ein Symbol traegt die Quote — das MUSS im Snapshot stehen, nicht im Kopf.

    BTC gewinnt 8/8, alle uebrigen verlieren. Die Gesamtquote liegt ueber dem
    Muenzwurf, ohne BTC darunter: genau der Fall, den ein blosser ``win_rate``
    verschweigt.
    """
    p = tmp_path / "audit.jsonl"
    rows = [_close("BTC/USDT", 10.0) for _ in range(8)]
    rows += [_close("ETH/USDT", -5.0) for _ in range(3)]
    rows += [_close("SOL/USDT", -5.0) for _ in range(3)]
    _audit(p, rows)

    snap = build_paper_quality_snapshot(audit_path=p)
    assess = snap.win_rate_by_symbol_assessment

    assert snap.win_rate > 0.5
    assert assess["by_group"]["BTC/USDT"]["rate"] == 1.0
    assert assess["leave_one_group_out_worst"]["group"] == "BTC/USDT"
    assert assess["leave_one_group_out_worst"]["rate"] == 0.0
    assert any("getragen" in f for f in assess["flags"])


def test_paper_quality_assessment_uses_decided_trades_only(tmp_path: Path) -> None:
    """Basis muss ``wins+losses`` sein — dieselbe wie ``win_rate``.

    Stuende dort ``count``, waeren unentschiedene Zeilen stille Verlierer und
    die Zerlegung widerspraeche der Quote, die sie erklaeren soll.
    """
    p = tmp_path / "audit.jsonl"
    rows = [_close("BTC/USDT", 10.0), _close("BTC/USDT", -10.0), _close("ETH/USDT", 0.0)]
    _audit(p, rows)

    snap = build_paper_quality_snapshot(audit_path=p)
    assess = snap.win_rate_by_symbol_assessment
    total_decided = sum(c["n"] for c in assess["by_group"].values())

    # 0.0 ist weder win noch loss -> zaehlt in keiner der beiden Gruppen mit.
    assert total_decided == 2
    assert "ETH/USDT" not in assess["by_group"]


def test_paper_quality_homogeneous_book_stays_quiet(tmp_path: Path) -> None:
    """Kein Fehlalarm bei gleichmaessig verteilten Ergebnissen."""
    p = tmp_path / "audit.jsonl"
    rows = []
    for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        rows += [_close(sym, 10.0), _close(sym, 10.0), _close(sym, -5.0)]
    _audit(p, rows)

    snap = build_paper_quality_snapshot(audit_path=p)
    assert snap.win_rate_by_symbol_assessment["flags"] == []


def test_paper_quality_empty_book_is_fail_closed(tmp_path: Path) -> None:
    p = tmp_path / "audit.jsonl"
    _audit(p, [])
    snap = build_paper_quality_snapshot(audit_path=p)

    assert snap.win_rate_by_symbol_assessment["n"] == 0


def test_reason_axis_is_deliberately_not_assessed(tmp_path: Path) -> None:
    """``reason`` ist mit dem Ergebnis definitorisch gekoppelt.

    ``take`` = Gewinn-Exit, ``stop`` = Verlust-Exit — eine Win-Rate-Zerlegung
    darueber ergibt zwangslaeufig 100 % vs 0 % und traegt null Information.
    Am echten Buch feuerte dort ein Dauer-Flag. Die Verteilung bleibt als
    ``by_reason`` sichtbar, wird aber nicht als Erklaerung der Quote verkauft.
    """
    p = tmp_path / "audit.jsonl"
    rows = [_close("BTC/USDT", 10.0, "take") for _ in range(3)]
    rows += [_close("ETH/USDT", -5.0, "stop") for _ in range(7)]
    _audit(p, rows)

    snap = build_paper_quality_snapshot(audit_path=p)

    assert not hasattr(snap, "win_rate_by_reason_assessment")
    assert snap.by_reason["stop"]["count"] == 7.0
    assert snap.by_reason["take"]["count"] == 3.0


# ── News-Evaluator (der Pfad des terminalen ND-v2-Verdikts) ──────────────────


def _outcome(symbol: str, fwd_bps: float, horizon: int) -> dict:
    return {"symbol": symbol, "fwd": {horizon: fwd_bps}}


def test_news_eval_flags_a_mean_carried_by_one_outlier() -> None:
    """Das canonical-edge-Muster im News-Pfad: ein Ausreisser traegt den Mittelwert.

    ``top_symbol_share`` allein faengt das NICHT — die Symbole sind hier
    verschieden, nur EIN Wert ist gross.
    """
    h = 3600
    outcomes = [_outcome(f"S{i}/USDT", -4.0, h) for i in range(9)]
    outcomes.append(_outcome("MOON/USDT", 400.0, h))

    res = evaluate_cohort(outcomes, horizons=(h,))
    row = res["horizons"][h]

    assert row["mean_bps"] > 0
    vd = row["value_decomposition"]
    assert vd["without_top"]["mean"] < 0
    assert any("getragen" in f for f in vd["flags"])
    assert vd["top_contributor"]["label"] == "MOON/USDT"


def test_news_eval_decomposition_never_moves_the_sealed_gate() -> None:
    """``actionable`` darf sich durch die Zerlegung NICHT aendern.

    Die Gates sind versiegelt; die Zerlegung berichtet, sie urteilt nicht mit.
    """
    h = 3600
    outcomes = [_outcome(f"S{i}/USDT", -4.0, h) for i in range(9)]
    outcomes.append(_outcome("MOON/USDT", 400.0, h))

    res = evaluate_cohort(outcomes, horizons=(h,))
    row = res["horizons"][h]

    # Trotz positivem Mittelwert bleibt das Gate zu (p_positive/Konzentration).
    assert row["actionable"] is False
    assert res["actionable"] is False
    # Und die Zerlegung ist trotzdem vorhanden und aussagekraeftig.
    assert row["value_decomposition"]["flags"]


def test_news_eval_symbol_decomposition_partitions_the_cohort() -> None:
    h = 3600
    outcomes = [_outcome("BTC/USDT", 5.0, h), _outcome("BTC/USDT", -5.0, h)]
    outcomes += [_outcome("ETH/USDT", 5.0, h)]

    res = evaluate_cohort(outcomes, horizons=(h,))
    dec = res["horizons"][h]["hit_decomposition_by_symbol"]

    assert dec["n"] == 3
    assert sum(c["n"] for c in dec["by_group"].values()) == 3
    assert dec["by_group"]["BTC/USDT"]["rate"] == 0.5
    assert dec["by_group"]["ETH/USDT"]["rate"] == 1.0


def test_news_eval_empty_cohort_is_fail_closed() -> None:
    h = 3600
    res = evaluate_cohort([], horizons=(h,))
    row = res["horizons"][h]

    assert row["n"] == 0
    assert row["value_decomposition"]["n"] == 0
    assert row["hit_decomposition_by_symbol"]["n"] == 0
    assert row["actionable"] is False


# ── Dashboard: rechnet win_rate/expectancy eigenstaendig, braucht eigene Belege ──


def test_quality_endpoint_ships_the_decomposition(tmp_path: Path) -> None:
    """``/dashboard/api/quality`` liefert die Belastungsprobe zu seinen Kennzahlen.

    Der Endpoint rechnet ``win_rate``/``expectancy`` NICHT ueber
    ``paper_quality_snapshot``, sondern selbst — ohne eigene Bewertung bliebe
    ausgerechnet die meistgelesene Zahl unbelegt.
    """
    from app.api.routers import dashboard as dash

    closes = [
        {"event_type": "position_closed", "symbol": "BTC/USDT", "trade_pnl_usd": 10.0},
        {"event_type": "position_closed", "symbol": "ETH/USDT", "trade_pnl_usd": -4.0},
    ]
    counts: dict[str, dict[str, int]] = {}
    for r in closes:
        p = float(r["trade_pnl_usd"])  # type: ignore[arg-type]
        cell = counts.setdefault(str(r["symbol"]), {"n": 0, "positives": 0})
        cell["n"] += 1
        if p > 0:
            cell["positives"] += 1

    assessment = dash.assess_group_table(counts)
    assert assessment["n"] == 2
    assert set(assessment["by_group"]) == {"BTC/USDT", "ETH/USDT"}

    dec = dash.decompose_mean([10.0, -4.0], labels=["BTC/USDT", "ETH/USDT"])
    assert dec["without_top"]["mean"] == -4.0
    assert dec["top_contributor"]["label"] == "BTC/USDT"
