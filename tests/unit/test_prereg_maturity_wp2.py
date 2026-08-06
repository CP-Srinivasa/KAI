"""Prä-Reg-Integritäts-Härtung (Voll-Audit 2026-08-06, WP2).

Vier gepinnte Invarianten:

1. ``record_exact_observation`` persistiert den vollen ``meta``-Block des
   Evaluator-Laufs (P0-2): Der Guard #648 prüft nur die Hedge-Achse; alle
   anderen Konstruktions-Parameter (max_symbols, tiered costs, timeframe, …)
   verändern die Kohorte ebenso. Ohne persistiertes meta wäre ein mit
   abweichenden Caps gefahrener Lauf nachträglich NICHT erkennbar — bei
   FAIL=terminal-Claims unheilbar.
2. Deadline-Specs (P0-3): fensterbasierte Prä-Regs (Analyst-Probe
   ``f0e1a3a8073fd4c0``, Fenster-Ende 2026-08-10) erscheinen in der
   Reife-Überwachung — NOT_DUE vor Fensterende, EVAL_CHECK_DUE danach,
   NIEMALS JUDGEABLE (das Verdikt fällt die versiegelte Regel, manuell).
3. Die DUE-Zeile trägt den Confounder-Vermerk (``note``) — er darf nicht am
   Operator-Gedächtnis hängen.
4. H1/H2-Reifezähler übergeben den Gate-Horizont EXPLIZIT aus dem Spec (P1-2)
   statt sich auf Modul-Defaults zu verlassen — exakt die Divergenz-Bauart,
   die #648 ausgelöst hat.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.research.prereg_maturity import (
    EXACT_OBSERVATIONS_RELPATH,
    MATURITY_SPECS,
    STATE_EVAL_CHECK_DUE,
    STATE_NOT_DUE,
    _maturity_exec_translation,
    _maturity_tech_precision,
    compute_maturity,
    record_exact_observation,
)

_GATE = {"level": "stories", "horizon_s": 86400, "n_min": 300, "p_min": 0.95}


def _eval_result(meta: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "stories": {
            "horizons": {
                "86400": {
                    "n": 302,
                    "mean_bps": 1.0,
                    "p_positive": 0.5,
                    "cost_ref_bps": 33.7,
                    "top_symbol_share": 0.2,
                }
            }
        }
    }
    if meta is not None:
        result["meta"] = meta
    return result


# --------------------------------------------------------------------------- #
# 1. meta-Persistenz
# --------------------------------------------------------------------------- #


def test_record_exact_observation_persists_full_meta(tmp_path: Path) -> None:
    meta = {
        "construction": "hedged_vs_BTC/USDT",
        "n_symbols": 40,
        "cost_tiering": True,
        "timeframe": "1h",
        "published_after_anchor": "2026-07-02T05:43:32.211092+00:00",
    }
    record = record_exact_observation(
        prereg_id="b20ef1487ccba99d",
        gate=_GATE,
        n_target=300,
        eval_result=_eval_result(meta),
        artifacts_dir=tmp_path,
        observed_at=datetime(2026, 8, 6, 3, 0, tzinfo=UTC),
        source_json="/tmp/x.json",
    )
    assert record["meta"] == meta

    line = (tmp_path / EXACT_OBSERVATIONS_RELPATH).read_text(encoding="utf-8").strip()
    persisted = json.loads(line)
    assert persisted["meta"]["n_symbols"] == 40
    assert persisted["meta"]["construction"] == "hedged_vs_BTC/USDT"


def test_record_exact_observation_without_meta_persists_null(tmp_path: Path) -> None:
    """Alt-Läufe ohne meta bleiben aufzeichenbar — das Feld ist dann ehrlich null."""
    record = record_exact_observation(
        prereg_id="b20ef1487ccba99d",
        gate=_GATE,
        n_target=300,
        eval_result=_eval_result(None),
        artifacts_dir=tmp_path,
        observed_at=datetime(2026, 8, 6, 3, 0, tzinfo=UTC),
    )
    assert record["meta"] is None


# --------------------------------------------------------------------------- #
# 2./3. Deadline-Spec (Analyst-Probe)
# --------------------------------------------------------------------------- #

_DEADLINE_SPEC: dict[str, Any] = {
    "name": "analyst_prediction_ledger_demand_v1",
    "prereg_id": "f0e1a3a8073fd4c0",
    "kind": "deadline",
    "since_utc": "2026-07-11T00:13:00+00:00",
    "window_end_utc": "2026-08-10T00:13:00+00:00",
    "n_target": 0,
    "note": "Verdikt NUR mit Confounder-Vermerk AP-DEF-2",
}


async def test_deadline_spec_not_due_before_window_end(tmp_path: Path) -> None:
    rows = await compute_maturity(
        None,  # type: ignore[arg-type] — deadline-Spec berührt die Session nie
        specs=(_DEADLINE_SPEC,),
        artifacts_dir=tmp_path,
        now=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["state"] == STATE_NOT_DUE
    assert row["due"] is False
    assert row["kind"] == "deadline"
    assert row["per_source"]["days_remaining"] >= 3
    assert row["per_source"]["window_end_utc"] == "2026-08-10T00:13:00+00:00"


async def test_deadline_spec_due_after_window_end_never_judgeable(tmp_path: Path) -> None:
    rows = await compute_maturity(
        None,  # type: ignore[arg-type]
        specs=(_DEADLINE_SPEC,),
        artifacts_dir=tmp_path,
        now=datetime(2026, 8, 10, 0, 14, tzinfo=UTC),
    )
    row = rows[0]
    assert row["state"] == STATE_EVAL_CHECK_DUE
    assert row["due"] is True
    assert row["per_source"]["days_remaining"] == 0
    # Der Vermerk hängt an der Zeile, nicht am Operator-Gedächtnis.
    assert "Confounder" in str(row.get("note"))


def test_analyst_probe_is_registered_in_maturity_specs() -> None:
    """P0-3: Die Probe läuft am 10.08. aus und MUSS in der Reife-Überwachung stehen."""
    by_id = {spec.get("prereg_id"): spec for spec in MATURITY_SPECS}
    spec = by_id.get("f0e1a3a8073fd4c0")
    assert spec is not None, "Analyst-Probe fehlt in MATURITY_SPECS"
    assert spec["kind"] == "deadline"
    assert spec["window_end_utc"] == "2026-08-10T00:13:00+00:00"
    assert "Confounder" in str(spec.get("note"))


# --------------------------------------------------------------------------- #
# 4. H1/H2: Gate-Horizont explizit aus dem Spec
# --------------------------------------------------------------------------- #


def _fake_eval_capture(captured: dict[str, Any]) -> Any:
    def fake(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "population": {
                "docs_resolved": 1,
                "docs_inconclusive": 0,
                "docs_pending_no_outcome": 0,
                "docs_joined_to_hit": 1,
                "closed_docs_since_reg": 1,
            }
        }

    return fake


def test_tech_precision_passes_gate_horizon_from_spec(tmp_path: Path, monkeypatch: Any) -> None:
    import app.research.quote_evals as quote_evals

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        quote_evals, "evaluate_technical_paper_precision", _fake_eval_capture(captured)
    )
    spec = {
        "name": "technical_paper_precision_fwd_v1",
        "prereg_id": "fd6f5f7842f49244",
        "kind": "tech_precision",
        "since_utc": "2026-07-29T09:14:47.210068+00:00",
        "n_target": 200,
        "gate_horizon_s": 604800,
    }
    _maturity_tech_precision(spec, tmp_path)
    assert captured["horizon_s"] == 604800


def test_exec_translation_passes_gate_horizon_from_spec(tmp_path: Path, monkeypatch: Any) -> None:
    import app.research.quote_evals as quote_evals

    captured: dict[str, Any] = {}
    monkeypatch.setattr(quote_evals, "evaluate_execution_translation", _fake_eval_capture(captured))
    spec = {
        "name": "execution_translation_hit_to_win_v1",
        "prereg_id": "0c7ead764621dd17",
        "kind": "exec_translation",
        "since_utc": "2026-07-29T09:15:10.626958+00:00",
        "n_target": 50,
        "gate_horizon_s": 86400,
    }
    _maturity_exec_translation(spec, tmp_path)
    assert captured["horizon_s"] == 86400


def test_h1_h2_specs_carry_sealed_gate_horizons() -> None:
    """Die Konstanten sind gegen das Ledger verifiziert (gate.horizon_s, 2026-08-06)."""
    by_id = {spec.get("prereg_id"): spec for spec in MATURITY_SPECS}
    assert by_id["fd6f5f7842f49244"]["gate_horizon_s"] == 604800
    assert by_id["0c7ead764621dd17"]["gate_horizon_s"] == 86400
