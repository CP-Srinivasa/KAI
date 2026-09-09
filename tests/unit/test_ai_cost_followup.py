"""Nachtrag zu KAI COST CONTROL v0.1: Modellname, Auftraggeber, Sichtbarkeit.

Drei Befunde vom 2026-09-09, am Geraet gemessen:

* der Serverpfad schrieb den ANBIETERNAMEN ins Modellfeld, wodurch echte
  gpt-4o-Aufrufe als ``unknown_model`` unbepreisbar wurden,
* dieselben Zeilen trugen ``use_case="unknown"``, obwohl der Eintrittspunkt
  den Auftraggeber laengst bindet - die direkte Emission las den Kontext nicht,
* ``analyze pending`` meldete 47 Erfolge zu 10 Telemetriezeilen, weil
  "analysiert" und "bezahlt" dieselbe Zahl teilten.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline, llm_usage_summary
from app.core.domain.document import CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel


def _engine() -> KeywordEngine:
    return KeywordEngine(
        keywords=frozenset({"halving", "etf"}),
        watchlist_entries=[
            WatchlistEntry(
                symbol="BTC",
                name="Bitcoin",
                aliases=frozenset({"bitcoin"}),
                tags=(),
                category="crypto",
            )
        ],
        entity_aliases=[],
    )


def _ausgabe() -> LLMAnalysisOutput:
    return LLMAnalysisOutput(
        sentiment_label=SentimentLabel.NEUTRAL,
        sentiment_score=0.0,
        relevance_score=0.6,
        impact_score=0.6,
        confidence_score=0.6,
        novelty_score=0.5,
        spam_probability=0.01,
        market_scope=MarketScope.CRYPTO,
        short_reasoning="ok",
        prompt_tokens=1889,
        completion_tokens=211,
    )


class _DirektProvider:
    """Ein Direktprovider: ``model`` ist ein MODELL."""

    def __init__(self, name: str = "openai", model: str = "gpt-4o") -> None:
        self.provider_name = name
        self.model = model
        self.aufrufe = 0

    async def analyze(
        self, title: str, text: str, context: dict[str, Any] | None = None
    ) -> LLMAnalysisOutput:
        self.aufrufe += 1
        return _ausgabe()


class _EnsembleDoppel:
    """Wie ``EnsembleProvider``: ``model`` ist der aktive ANBIETER, kein Modell."""

    def __init__(self, mitglieder: list[_DirektProvider]) -> None:
        self._mitglieder = mitglieder
        self.active_provider_name = mitglieder[0].provider_name

    @property
    def provider_name(self) -> str:
        return f"ensemble({','.join(m.provider_name for m in self._mitglieder)})"

    @property
    def model(self) -> str | None:
        return self.active_provider_name

    @property
    def providers(self) -> tuple[_DirektProvider, ...]:
        return tuple(self._mitglieder)

    @property
    def provider_chain(self) -> list[str]:
        return [m.provider_name for m in self._mitglieder]

    async def analyze(
        self, title: str, text: str, context: dict[str, Any] | None = None
    ) -> LLMAnalysisOutput:
        ausgabe = await self._mitglieder[0].analyze(title, text, context)
        ausgabe.provider_used = self._mitglieder[0].provider_name
        return ausgabe


def _btc_doc(nummer: int = 1) -> CanonicalDocument:
    return CanonicalDocument(
        url=f"https://example.com/btc-{nummer}",
        title="Bitcoin halving approaches as ETF inflows rise",
        raw_text="Bitcoin halving and ETF demand keep BTC in focus. " * 12,
    )


def _irrelevantes_doc(nummer: int) -> CanonicalDocument:
    return CanonicalDocument(
        url=f"https://example.com/bakery-{nummer}",
        title="Local bakery opens second branch downtown",
        raw_text="A bakery opened a new branch. " * 20,
    )


def _zeilen(sink: Path) -> list[dict[str, Any]]:
    if not sink.exists():
        return []
    return [json.loads(z) for z in sink.read_text(encoding="utf-8").splitlines() if z.strip()]


@pytest.fixture
def telemetrie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sink = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    return sink


# -- Befund 2: der Modellname ist ein Modellname ----------------------------


async def test_ensemble_emission_carries_a_priced_model(telemetrie: Path) -> None:
    """Der gemessene Defekt: ``model="openai"`` statt ``gpt-4o`` -> COST_UNKNOWN."""
    pipeline = AnalysisPipeline(
        keyword_engine=_engine(),
        provider=_EnsembleDoppel([_DirektProvider()]),
        run_llm=True,
    )
    ergebnis = await pipeline.run(_btc_doc())
    assert ergebnis.llm_output is not None

    zeilen = _zeilen(telemetrie)
    assert len(zeilen) == 1
    zeile = zeilen[0]
    assert zeile["provider"] == "openai"
    assert zeile["model"] == "gpt-4o"
    assert zeile["actual_model"] == "gpt-4o"
    # Der eigentliche Beleg: die Zeile ist bepreisbar.
    assert zeile["cost_status"] == "OK"
    assert zeile["cost_usd"] > 0.0


async def test_shadow_emission_carries_a_priced_model(telemetrie: Path) -> None:
    """Dieselbe Pruefung fuer die Zweitmeinung (anthropic/gemini/xai-Pfad)."""
    pipeline = AnalysisPipeline(
        keyword_engine=_engine(),
        provider=None,
        shadow_provider=_DirektProvider(name="anthropic", model="claude-sonnet-4-6"),
        run_llm=False,
    )
    await pipeline.run(_btc_doc(2))

    zeilen = _zeilen(telemetrie)
    assert [z["model"] for z in zeilen] == ["claude-sonnet-4-6"]
    assert zeilen[0]["cost_status"] == "OK"
    assert zeilen[0]["cost_usd"] > 0.0


# -- Befund 3: use_case an jedem Analyse-Eintrittspunkt ---------------------


async def test_analysis_rows_carry_the_news_intelligence_use_case(telemetrie: Path) -> None:
    """Die gemeinsame Ebene aller drei Eintrittspunkte: ``AnalysisPipeline.run``."""
    pipeline = AnalysisPipeline(
        keyword_engine=_engine(),
        provider=_EnsembleDoppel([_DirektProvider()]),
        run_llm=True,
    )
    await pipeline.run(_btc_doc(3))
    assert [z["use_case"] for z in _zeilen(telemetrie)] == ["news_intelligence"]


def test_cli_analyze_pending_enters_through_run_batch() -> None:
    """Eintrittspunkt 1: ``analyze pending`` - der use_case sitzt eine Ebene tiefer.

    Bewusst KEINE zweite Setzstelle in ``app/cli/main.py``: die Datei steht auf
    ihrer God-File-Baseline, und ein zweiter Setzort waere ohnehin eine zweite
    Wahrheit darueber, wer den Aufruf bezahlt hat.
    """
    from app.cli.main import analyze_pending

    assert "run_batch(" in inspect.getsource(analyze_pending)


def test_pipeline_run_and_run_all_enter_through_run_batch() -> None:
    """Eintrittspunkt 2: ``pipeline run`` / ``run-all`` ueber den Service."""
    from app.pipeline.service import run_rss_pipeline

    assert "run_batch(" in inspect.getsource(run_rss_pipeline)


def test_in_process_rss_scheduler_enters_through_the_same_service() -> None:
    """Eintrittspunkt 3: der In-Process-Scheduler des Servers."""
    from app.ingestion.schedulers.rss_scheduler import RSSScheduler

    assert "run_rss_pipeline(" in inspect.getsource(RSSScheduler._run_pipeline)


def test_use_case_is_bound_once_at_the_pipeline_entry() -> None:
    """Genau EINE Setzstelle - sonst gibt es zwei Meinungen ueber den Zahler."""
    quelle = Path("app/analysis/pipeline.py").read_text(encoding="utf-8")
    assert quelle.count('use_case_scope("news_intelligence")') == 1


# -- Befund 4: success ist nicht llm_call ----------------------------------


async def test_gate_skips_are_visible_next_to_success(telemetrie: Path) -> None:
    """3 Dokumente, 1 echter Aufruf, 2 Gate-Skips - und die Ausgabe sagt es."""
    direkt = _DirektProvider()
    pipeline = AnalysisPipeline(
        keyword_engine=_engine(),
        provider=_EnsembleDoppel([direkt]),
        run_llm=True,
    )
    ergebnisse = await pipeline.run_batch([_btc_doc(4), _irrelevantes_doc(1), _irrelevantes_doc(2)])

    assert all(r.success for r in ergebnisse)
    assert direkt.aufrufe == 1
    assert len(_zeilen(telemetrie)) == 1
    assert llm_usage_summary(ergebnisse) == "3 success / 1 llm_call / 2 skipped"
    # Der Grund steht am Ergebnis, nicht nur im Log.
    assert [r.skip_reason for r in ergebnisse if r.skip_reason] != []


def test_cli_prints_the_llm_call_count() -> None:
    """Die CLI-Ausgabe traegt die Aufschluesselung - sonst bleibt sie unsichtbar."""
    quelle = Path("app/cli/main.py").read_text(encoding="utf-8")
    assert "llm_usage_summary(results, success=success_count)" in quelle
