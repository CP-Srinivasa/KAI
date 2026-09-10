"""Budget-Policy v2 — der Regelpfad-Deckel und die Alert-Faehigkeit.

Befund Spur A (09./10.09.2026, kai-pi5): nach Erreichen des Tagesbudgets laeuft
der Dokumentzufluss unvermindert weiter und `is_analyzed` bleibt bei ~100 %,
aber `external_llm` faellt von rund 25 % auf 0. Ueber 44.018 regelanalysierte
Dokumente lag die hoechste Prioritaet bei 6; alle 11.863 Dokumente ab
Prioritaet 7 kamen ausnahmslos aus `external_llm`.

Diese Datei prueft, dass die Rechnung den Befund ERKLAERT statt ihn zu
wiederholen — und dass die Zahl mitwandert, wenn jemand ein Gewicht anfasst.
"""

from __future__ import annotations

import pytest

from app.analysis.scoring import (
    ALERT_GATE_RAW,
    RULE_IMPACT_CEILING,
    RULE_NOVELTY_CEILING,
    RULE_RELEVANCE_CEILING,
    AnalysisResult,
    compute_priority,
    rule_path_can_reach_alert_gate,
    rule_path_priority_ceiling,
    rule_path_raw_ceiling,
)


def test_deckel_stimmt_mit_der_unabhaengig_gerechneten_zahl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gegenrechnung von Hand — nicht die Funktion gegen sich selbst.

    0.30 (relevance 1.00 x 0.30) + 0.105 (impact 0.35 x 0.30)
    + 0.12 (novelty 0.60 x 0.20) + 0.00 (actionable, I-13) + 0.05 (quality)
    = 0.575  ->  round(0.575 x 9) + 1 = 6
    """
    assert rule_path_raw_ceiling() == pytest.approx(0.575)
    assert rule_path_priority_ceiling() == 6


def test_deckel_wandert_mit_den_gewichten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eine notierte Zahl waere schon beim naechsten Gewichtswechsel falsch."""
    from app.analysis import scoring

    monkeypatch.setattr(scoring, "RULE_IMPACT_CEILING", 1.0)
    # impact 1.00 x 0.30 statt 0.35 x 0.30 => +0.195
    assert scoring.rule_path_raw_ceiling() == pytest.approx(0.575 + 0.195)


def test_regelpfad_kommt_nicht_ueber_das_alert_gate() -> None:
    """Die Kernaussage, auf der der Health-Zustand steht."""
    assert rule_path_raw_ceiling() < ALERT_GATE_RAW
    assert rule_path_can_reach_alert_gate() is False
    # Und mit einem tiefer gelegten Gate schon.
    assert rule_path_can_reach_alert_gate(gate_raw=0.5) is True


def test_deckel_ist_mit_compute_priority_konsistent() -> None:
    """Ein Ergebnis AN der Obergrenze darf compute_priority nicht ueberschreiten.

    Verhindert, dass Deckel und Prioritaetsformel auseinanderlaufen: hier laeuft
    ein echtes ``AnalysisResult`` mit den Grenzwerten durch dieselbe Funktion,
    die auch die Pipeline benutzt.
    """
    from app.core.enums import AnalysisSource, MarketScope, SentimentLabel

    grenzfall = AnalysisResult(
        document_id="d1",
        analysis_source=AnalysisSource.INTERNAL,
        sentiment_label=SentimentLabel.NEUTRAL,
        sentiment_score=0.0,
        relevance_score=RULE_RELEVANCE_CEILING,
        impact_score=RULE_IMPACT_CEILING,
        novelty_score=RULE_NOVELTY_CEILING,
        confidence_score=0.75,
        spam_probability=0.0,
        recommended_priority=1,
        market_scope=MarketScope.UNKNOWN,
        explanation_short="grenzfall",
        explanation_long="grenzfall",
        actionable=False,  # I-13: im Regelpfad dauerhaft
    )
    ergebnis = compute_priority(grenzfall, spam_probability=0.0)

    assert ergebnis.raw_score == pytest.approx(rule_path_raw_ceiling())
    assert ergebnis.priority == rule_path_priority_ceiling()
    assert ergebnis.priority < 7


def test_actionable_true_wuerde_den_deckel_brechen() -> None:
    """Warum I-13 die tragende Invariante ist — und nicht Kosmetik."""
    from app.core.enums import AnalysisSource, MarketScope, SentimentLabel

    def _ergebnis(actionable: bool) -> int:
        r = AnalysisResult(
            document_id="d1",
            analysis_source=AnalysisSource.INTERNAL,
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            relevance_score=RULE_RELEVANCE_CEILING,
            impact_score=RULE_IMPACT_CEILING,
            novelty_score=RULE_NOVELTY_CEILING,
            confidence_score=0.75,
            spam_probability=0.0,
            recommended_priority=1,
            market_scope=MarketScope.UNKNOWN,
            explanation_short="x",
            explanation_long="x",
            actionable=actionable,
        )
        return compute_priority(r, spam_probability=0.0).priority

    assert _ergebnis(False) == 6
    # Mit actionable=True traegt sowohl das Gewicht als auch der Bonus:
    # der Regelpfad laege ueber der Alert-Schwelle.
    assert _ergebnis(True) >= 7


# ── Health-Zustand ──────────────────────────────────────────────────────────


def test_alert_capability_ok_solange_das_budget_offen_ist() -> None:
    from app.ai.health import _alert_capability_block

    block = _alert_capability_block(routine_blocked=False)

    assert block["alert_capability_for_new_documents"] == "ok"
    assert block["alert_capability_reason"] == ""
    assert block["rule_path_raw_ceiling"] == pytest.approx(0.575)
    assert block["rule_path_priority_ceiling"] == 6
    assert block["alert_gate_raw"] == pytest.approx(ALERT_GATE_RAW)


def test_alert_capability_unreachable_wenn_das_budget_blockiert() -> None:
    """Der Zustand, den es am 09.09. haette geben muessen."""
    from app.ai.health import _alert_capability_block

    block = _alert_capability_block(routine_blocked=True)

    assert block["alert_capability_for_new_documents"] == "unreachable"
    assert block["alert_capability_reason"] == "budget_blocked_rule_path_below_alert_gate"


def test_alert_capability_nur_degraded_wenn_der_regelpfad_durchkaeme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Gegenprobe: der Zustand ist an die Rechnung gebunden, nicht fest verdrahtet.

    Kaeme der Regelpfad ueber das Gate, waere bei blockiertem Budget nur die
    Analysetiefe verloren — nicht die Alert-Faehigkeit.
    """
    from app.analysis import scoring

    monkeypatch.setattr(scoring, "RULE_IMPACT_CEILING", 1.0)
    from app.ai.health import _alert_capability_block

    block = _alert_capability_block(routine_blocked=True)

    assert block["alert_capability_for_new_documents"] == "degraded"
    assert block["alert_capability_reason"] == "budget_blocked_rule_path_still_passes_gate"


def test_endpunkt_liefert_die_neuen_felder_aus(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nicht deklariert heisst bei Pydantic: still weggeworfen (Lehre aus #939)."""
    import json
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers.health import router as health_router
    from app.core.settings import get_settings

    sink = tmp_path / "llm_telemetry.jsonl"
    sink.write_text(
        json.dumps(
            {
                "schema_version": "v2",
                "ts": datetime.now(UTC).isoformat(),
                "provider": "openai",
                "model": "gpt-4o",
                "ok": True,
                "latency_ms": 100.0,
                "chain_position": 0,
                "correlation_id": "c1",
                "purpose": "analysis",
                "input_tokens": 10,
                "output_tokens": 10,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.health.DEFAULT_TELEMETRY_PATH", sink)

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        providers=SimpleNamespace(
            openai_api_key="sk-x",
            gemini_api_key="gk-x",
            anthropic_api_key="ak-x",
            xai_api_key="",
            xai_fallback_enabled=False,
        )
    )
    budget = TestClient(app).get("/health/ai").json()["budget"]

    assert set(budget) >= {
        "alert_capability_for_new_documents",
        "alert_capability_reason",
        "alert_gate_raw",
        "rule_path_raw_ceiling",
        "rule_path_priority_ceiling",
    }
    assert budget["rule_path_raw_ceiling"] == pytest.approx(0.575)
    assert budget["rule_path_priority_ceiling"] == 6
    assert budget["alert_gate_raw"] == pytest.approx(ALERT_GATE_RAW)
