"""Wer die Alert-Reserve anfassen darf, entscheidet sich VOR dem Aufruf.

Die Frage, die die Vorabbewertung nicht beantwortet: ob ein Dokument einen
Alert erzeugt. Das weiss erst die Analyse — der Regelpfad erreicht über 44.018
Dokumente maximal Priorität 6,0, die Alert-Schwelle liegt bei 7. Beantwortet
wird die schwächere und darum überhaupt entscheidbare Frage: ist es nach den
Signalen, die schon kostenlos vorliegen, nicht von vornherein ausgeschlossen?

Der Unterschied ist kein Wortspiel. Ein Schätzer, der eine Vorhersage
behauptet, müsste an seiner Trefferquote gemessen werden; ein
Ausschlusskriterium muss nur eines zeigen — dass es die offensichtlich
belanglosen Dokumente aussortiert und die plausiblen durchlässt. Genau das
prüfen die zwei Fälle hier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline
from app.core.ai_cost_settings import get_ai_cost_settings, reset_ai_cost_settings
from app.core.domain.document import CanonicalDocument
from app.normalization.entities import hits_to_entity_mentions

REPO = Path(__file__).resolve().parents[2]


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


def _faehig(pipeline: AnalysisPipeline, doc: CanonicalDocument) -> bool:
    text = doc.raw_text or ""
    treffer = pipeline._keyword_engine.match(f"{doc.title} {text}".strip())
    return pipeline._vorab_alert_faehig(doc, text, treffer, hits_to_entity_mentions(treffer))


@pytest.fixture
def pipeline() -> AnalysisPipeline:
    return AnalysisPipeline(keyword_engine=_engine(), provider=None, run_llm=False)


@pytest.fixture(autouse=True)
def _frische_einstellungen() -> Any:
    reset_ai_cost_settings()
    yield
    reset_ai_cost_settings()


def test_ein_dokument_mit_treffern_darf_an_die_reserve(
    pipeline: AnalysisPipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_MIN_RULE_PRIORITY", "3")
    reset_ai_cost_settings()

    doc = CanonicalDocument(
        url="https://example.com/btc",
        title="Bitcoin halving approaches as ETF inflows rise",
        raw_text="Bitcoin halving and ETF demand keep BTC in focus. " * 12,
    )

    assert _faehig(pipeline, doc) is True


def test_ein_belangloses_dokument_darf_nicht(
    pipeline: AnalysisPipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne diesen Fall wäre die Reserve nur eine Erhöhung des Tagesbudgets.

    Sie hat nur dann eine Wirkung, wenn die Masse sie NICHT erreicht — 373
    Dokumente nach dem Limit am 2026-09-10, von denen die allermeisten nichts
    mit Marktbewegung zu tun hatten.
    """
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_MIN_RULE_PRIORITY", "5")
    reset_ai_cost_settings()

    doc = CanonicalDocument(
        url="https://example.com/bakery",
        title="Local bakery opens second branch downtown",
        raw_text="A bakery opened a new branch. " * 20,
    )

    assert _faehig(pipeline, doc) is False


def test_die_schwelle_wirkt_und_ist_nicht_fest_verdrahtet(
    pipeline: AnalysisPipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Wert, den der Operator setzen kann, muss auch etwas tun.

    Der Test ist bewusst so gebaut, dass er nicht von einer bestimmten Zahl
    abhaengt: bei Schwelle 1 kommt jedes Dokument durch, bei Schwelle 10
    keines. Damit bleibt er richtig, wenn sich die Gewichte in
    ``compute_priority`` aendern -- und er faellt trotzdem, wenn die Schwelle
    gar nicht mehr gelesen wird.
    """
    doc = CanonicalDocument(
        url="https://example.com/bakery-2",
        title="Local bakery opens second branch downtown",
        raw_text="A bakery opened a new branch. " * 20,
    )

    monkeypatch.setenv("APP_AI_BUDGET_ALERT_MIN_RULE_PRIORITY", "1")
    reset_ai_cost_settings()
    assert _faehig(pipeline, doc) is True

    monkeypatch.setenv("APP_AI_BUDGET_ALERT_MIN_RULE_PRIORITY", "10")
    reset_ai_cost_settings()
    assert _faehig(pipeline, doc) is False


def test_eine_kaputte_vorabbewertung_laesst_durch_statt_zu_sperren(
    pipeline: AnalysisPipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open, und zwar mit Begruendung.

    Ein falsch durchgelassenes Dokument kostet einen halben Cent und trifft
    ausserdem auf eine doppelt begrenzte Reserve. Ein falsch abgewiesenes
    kostet einen Alert. Die beiden Fehler sind nicht gleich teuer, also darf
    die Behandlung nicht symmetrisch sein.
    """

    def kaputt(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Regelpfad defekt")

    monkeypatch.setattr(pipeline, "_build_fallback_analysis", kaputt)

    doc = CanonicalDocument(
        url="https://example.com/bakery-3",
        title="Local bakery opens second branch downtown",
        raw_text="A bakery opened a new branch. " * 20,
    )

    assert _faehig(pipeline, doc) is True


def test_die_voreinstellung_ist_der_gemessene_boden() -> None:
    """3 ist gemessen, nicht gesetzt — und das Literal hält die Messung fest.

    Die erste Messung lief über fünf Tage (2026-09-06 bis -10, 217 Alerts) und
    fand als niedrigste Vorabpriorität eines alert-fähigen Dokuments die 4. Über
    ein breiteres Fenster — 4.000 alert-fähige Dokumente statt 217 — liegen
    **61 davon bei Vorabpriorität <= 3**, darunter Endpriorität 9 und 10:
    der Cronos/Tectonic-Angriff über ~75 Mio USD (30.08., vorab 3, final 10),
    der Injective-Ausfall über ~4,88 Mio USD (01.09., vorab 3, final 10) und
    der Markteinbruch nach den US-Angriffen auf Iran (01.09., vorab 3, final 9).

    Recall bei k=4: 98,8 % im neuesten, 93,3 % im ältesten 4.000er-Fenster;
    k=3 hält in beiden 100 %. Dass das Fünf-Tage-Fenster keinen solchen Fall
    enthielt, macht ihn nicht seltener — es macht das Fenster zu schmal.

    Der Test pinnt die Zahl bewusst wörtlich. Wer sie anhebt, verschiebt keine
    Voreinstellung, sondern schliesst gemessene Alerts aus — das soll hier
    ankommen und nicht in einem Diff untergehen.
    """
    reset_ai_cost_settings()

    assert get_ai_cost_settings().budget_alert_min_rule_priority == 3


def test_vier_haette_gemessene_alerts_ausgeschlossen() -> None:
    """Die Gegenprobe zur verworfenen Voreinstellung 4.

    Kein Datenzugriff, sondern die Konsequenz als Zusicherung: bei k=4 fallen
    genau die Dokumente heraus, deren Regelpfad auf 3 kommt — und von denen
    waren im breiten Fenster 61 tatsächlich alert-fähig. Steigt die
    Voreinstellung je über 3, muss dieser Test mitwandern und die dann gültige
    Messung nennen.
    """
    reset_ai_cost_settings()
    schwelle = get_ai_cost_settings().budget_alert_min_rule_priority

    # Ein Dokument mit Vorabpriorität 3 muss die Reserve erreichen dürfen.
    assert schwelle <= 3


def test_die_schwelle_trennt_nicht_und_das_steht_auch_so_da() -> None:
    """Der zweite, unbequemere Teil derselben Messung.

    Der Anteil alert-fähiger Dokumente bleibt über den ganzen Schwellenbereich
    bei rund 42 % (k=3: 42,2 %, k=6: 43,4 %), während die Deckung von 100 % auf
    63,6 % fällt. Die Vorabpriorität konzentriert die Reserve also nicht — sie
    ist ein Rückfallnetz gegen offensichtlich Belangloses, kein Zielmechanismus.

    Das gehört in den Code und nicht nur in einen Bericht: eine spätere Lesart,
    die aus dieser Schwelle einen Treffer-Optimierer macht, wäre durch die
    Daten widerlegt, und niemand hätte die Daten zur Hand.
    """
    quelle = (REPO / "app" / "core" / "ai_cost_settings.py").read_text(encoding="utf-8")

    assert "RÜCKFALLNETZ" in quelle, "die Einordnung darf nicht verlorengehen"
    assert "trennt nicht" in quelle
    assert "217" in quelle, "die Messgrundlage steht im Code"
