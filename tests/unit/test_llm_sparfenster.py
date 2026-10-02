"""LLM-Sparfenster: Stunden und Quellen ohne 4-h-Signal bekommen nur die Regelanalyse.

Gemessen 02.10.2026 an 2830 gpt-4o-Analysen (07.-30.09.): Ertrag je Analyse (Prio >= 7 UND
Kursbewegung ueber der ueblichen Schwankung in 4 h) nachts 02-04 UTC 0-4,6 %, YouTube
4,8 %, in der Spitze 12-14 UTC 12-16 %. Das feste Tagesbudget endet taeglich 12:46-14:51 UTC
-- mitten in der Spitze.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.analysis.llm_sparfenster import Sparfenster, SparfensterSettings, resolve_sparfenster
from app.core.enums import AnalysisSource

NACHT = datetime(2026, 10, 2, 3, 15, tzinfo=UTC)
SPITZE = datetime(2026, 10, 2, 12, 30, tzinfo=UTC)
AKTIV = Sparfenster(mode="enforce", hours_utc=frozenset({2, 3, 4}), sources=frozenset({"youtube"}))


def test_aus_entscheidet_nie() -> None:
    aus = Sparfenster(mode="off", hours_utc=frozenset({3}), sources=frozenset({"youtube"}))
    assert aus.verdict(source="YouTube", at=NACHT) is None


def test_quelle_und_stunde_greifen() -> None:
    assert AKTIV.verdict(source="YouTube", at=SPITZE) == "quelle=YouTube"
    assert AKTIV.verdict(source="cryptobriefing", at=NACHT) == "stunde_utc=03"
    assert AKTIV.verdict(source="cryptobriefing", at=SPITZE) is None


def test_zeit_zaehlt_in_utc() -> None:
    # 05:15 MESZ ist 03:15 UTC; eine naive Zeit gilt als UTC (so speichert KAI fetched_at).
    mesz = timezone(timedelta(hours=2))
    assert (
        AKTIV.verdict(source="x", at=datetime(2026, 10, 2, 5, 15, tzinfo=mesz)) == "stunde_utc=03"
    )
    assert AKTIV.verdict(source="x", at=datetime(2026, 10, 2, 3, 15)) == "stunde_utc=03"


def test_ohne_quelle_greift_nur_die_stunde() -> None:
    assert AKTIV.verdict(source=None, at=SPITZE) is None
    assert AKTIV.verdict(source=None, at=NACHT) == "stunde_utc=03"


def _env(monkeypatch: pytest.MonkeyPatch, **werte: str) -> None:
    for name, wert in werte.items():
        monkeypatch.setenv(f"SOURCE_LLM_SPARFENSTER_{name}", wert)


def test_aus_den_settings_aufgeloest_und_bereinigt(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, MODE="enforce", HOURS_UTC="[2, 3, 4, 99, -1]", SOURCES='[" YouTube ", ""]')
    fenster = resolve_sparfenster()
    assert fenster.mode == "enforce"
    assert fenster.hours_utc == frozenset({2, 3, 4})
    assert fenster.sources == frozenset({"youtube"})


def test_unbekannter_modus_und_kaputte_settings_heissen_aus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch, MODE="ja")
    assert resolve_sparfenster().mode == "off"
    _env(monkeypatch, MODE="enforce", HOURS_UTC="zwei bis vier")
    assert resolve_sparfenster().mode == "off"


def test_der_standard_aendert_nichts(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MODE", "HOURS_UTC", "SOURCES"):
        monkeypatch.delenv(f"SOURCE_LLM_SPARFENSTER_{name}", raising=False)
    s = SparfensterSettings(_env_file=None)
    assert s.mode == "off"
    assert s.hours_utc == [2, 3, 4]
    assert s.sources == ["YouTube"]


def _provider() -> AsyncMock:
    from app.analysis.base.interfaces import LLMAnalysisOutput
    from app.core.enums import MarketScope, SentimentLabel

    p = AsyncMock()
    p.provider_name = "openai"
    p.model = "gpt-4o"
    p.analyze = AsyncMock(
        return_value=LLMAnalysisOutput(
            sentiment_label=SentimentLabel.BULLISH,
            sentiment_score=0.6,
            relevance_score=0.9,
            impact_score=0.7,
            confidence_score=0.8,
            novelty_score=0.5,
            spam_probability=0.0,
            market_scope=MarketScope.CRYPTO,
        )
    )
    return p


def _doc(source: str, at: datetime):  # noqa: ANN202
    from tests.unit.test_analysis_pipeline import _make_doc

    doc = _make_doc()
    return doc.model_copy(update={"source_name": source, "fetched_at": at})


async def test_enforce_spart_den_modellaufruf(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.analysis.pipeline import AnalysisPipeline
    from tests.unit.test_analysis_pipeline import _btc_engine

    monkeypatch.setattr(
        "app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", tmp_path / "t.jsonl"
    )
    provider = _provider()
    pipeline = AnalysisPipeline(keyword_engine=_btc_engine(), provider=provider, sparfenster=AKTIV)
    ergebnis = await pipeline.run(_doc("YouTube", SPITZE))
    provider.analyze.assert_not_called()
    assert ergebnis.analysis_result is not None
    assert ergebnis.analysis_result.analysis_source == AnalysisSource.RULE


async def test_schatten_misst_nur(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.analysis.pipeline import AnalysisPipeline
    from tests.unit.test_analysis_pipeline import _btc_engine

    monkeypatch.setattr(
        "app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", tmp_path / "t.jsonl"
    )
    provider = _provider()
    schatten = Sparfenster(mode="shadow", hours_utc=AKTIV.hours_utc, sources=AKTIV.sources)
    pipeline = AnalysisPipeline(
        keyword_engine=_btc_engine(), provider=provider, sparfenster=schatten
    )
    await pipeline.run(_doc("cryptobriefing", NACHT))
    provider.analyze.assert_called_once()


async def test_ausserhalb_des_fensters_laeuft_das_modell(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.analysis.pipeline import AnalysisPipeline
    from tests.unit.test_analysis_pipeline import _btc_engine

    monkeypatch.setattr(
        "app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", tmp_path / "t.jsonl"
    )
    provider = _provider()
    pipeline = AnalysisPipeline(keyword_engine=_btc_engine(), provider=provider, sparfenster=AKTIV)
    await pipeline.run(_doc("cryptobriefing", SPITZE))
    provider.analyze.assert_called_once()
