"""Grenzen für das AI-Budget — eigene Datei, mit Grund.

Nicht in ``app/core/settings.py``: die Datei steht bei 1.883 Zeilen exakt auf
ihrer God-File-Ratchet-Baseline (``scripts/godfile_baseline.json``) und hat
null Zeilen Spielraum. Sie hier hineinzuschreiben hiesse, an anderer Stelle
derselben Datei etwas herauszuschneiden, das mit Kosten nichts zu tun hat —
eine Änderung, die niemand im Review beurteilen kann. Dasselbe Muster wie
``app/core/pay_settings.py`` neben ``app/core/payment_settings.py``: eine
Frage, eine Datei.

**Voreinstellung ist das heutige Verhalten.** Ohne gesetzte Limits sperrt
nichts. Der Zustand wird trotzdem berechnet und ausgewiesen — Sichtbarkeit
braucht keine Erlaubnis, Sperren schon.

**Eine Ausnahme von der Fail-Open-Regel:** unbekannte Kosten. Wer nicht weiss,
was er ausgibt, hat kein gedecktes Budget. Ab
``budget_unknown_max_calls_per_day`` unbelegten Aufrufen an einem Tag gilt das
Routine-Budget als erreicht, obwohl keine Summe es belegt. Das ist bewusst
fail-closed: die Alternative wäre, unbegrenzt weiterzulaufen, solange die
Messung kaputt ist — genau die Bauart, die den ersten Budget-Anlauf wertlos
gemacht hat.
"""

from __future__ import annotations

import os

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.budget import ReservePolicy

#: Präfix der Env-Variablen für Verbrauchsgrenzen je Auftraggeber:
#: ``APP_AI_BUDGET_USECASE_NEWS_INTELLIGENCE_USD=2.50``.
USECASE_LIMIT_PREFIX = "APP_AI_BUDGET_USECASE_"
USECASE_LIMIT_SUFFIX = "_USD"


class AICostSettings(BaseSettings):
    """``APP_AI_*`` — Kostengrenzen des AI-Gateways, keine zweite Control-Plane."""

    model_config = SettingsConfigDict(
        env_prefix="APP_AI_",
        env_file=".env",
        extra="ignore",
    )

    #: ``None`` heisst: dieses Fenster begrenzt nichts (heutiges Verhalten).
    budget_daily_usd: float | None = Field(default=None, ge=0.0)
    budget_monthly_usd: float | None = Field(default=None, ge=0.0)

    #: Ab wie viel Prozent des Limits gewarnt wird. Eine Warnung SPERRT NICHTS
    #: — sie ist der einzige Zustand, in dem ein Operator noch handeln kann,
    #: bevor die Pipeline stehen bleibt.
    budget_warn_pct: float = Field(default=80.0, ge=0.0, le=100.0)

    #: Obergrenze für Aufrufe OHNE belegbare Kosten pro Tag. Siehe Modul-Docstring.
    budget_unknown_max_calls_per_day: int = Field(default=50, ge=0)

    #: Schattenanalyse überhaupt bauen? **Voreinstellung seit 2026-09-09:
    #: AUS** (Operator-Entscheidung, D-CORE-007 Nachtrag). Die Zweitmeinung lief
    #: als Dauerbetrieb auf JEDEM analysierten Dokument und verdoppelte damit
    #: die Analysekosten für einen Vergleich, den niemand auswertete.
    #:
    #: Sie ist nicht abgeschafft, sondern soll EREIGNISGESTEUERT laufen. Die
    #: vier Anlässe, benannt und heute NICHT gebaut (bewusst: ein halber
    #: Auslöser wäre wieder ein Dauerbetrieb mit anderem Namen):
    #:
    #: 1. das Primärmodell ist unsicher (niedrige ``confidence_score``) oder
    #:    liefert fehlerhaft/leer,
    #: 2. die Quellen widersprechen sich,
    #: 3. High-Impact-Dokument (grosse Marktwirkung),
    #: 4. der Operator fordert eine Zweitmeinung an.
    #:
    #: ``APP_ANALYSIS_SHADOW_ENABLED=true`` schaltet die Kette unverändert
    #: wieder ein. Der Name ist bewusst ``APP_ANALYSIS_*`` und nicht
    #: ``APP_AI_*``: der Schalter gehört zur Analyse-Kette, nicht zum Budget.
    #: Er wohnt hier, weil ``describe_shadow_chain`` UND ``app/ai/health.py``
    #: ihn aus DERSELBEN Quelle lesen müssen — zwei Leser mit zwei
    #: Env-Zugriffen wären zwei Meinungen darüber, ob der Schatten läuft.
    shadow_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("APP_ANALYSIS_SHADOW_ENABLED", "shadow_enabled"),
    )

    # ── Reserven (Budget-Policy v2, 2026-09-10) ────────────────────────────
    #
    # ``None`` heisst: diese Reserve gibt es nicht, und das Budget verhält sich
    # wie vor v2 — ein Topf über alles. Voreinstellung, damit das Einspielen
    # allein noch nichts ändert.
    #
    # Die Zahlenordnung stammt aus zwei echten Fällen vom 2026-09-10
    # (0,00536 und 0,00515 USD je vollständiger Analyse mit rund 770
    # Denk-Token). Ein typischer Fall kostet also etwa einen halben Cent.

    #: Geschützte Restkapazität für alert-fähige Dokumente. Der Grund steht in
    #: ``app/ai/budget.py`` beim Abschnitt "Töpfe": ohne sie fällt das Alerting
    #: bei erreichtem Tageslimit nicht ab, sondern auf null — und zwar lautlos,
    #: weil ``is_analyzed`` durch den Regelpfad bei ~100 % bleibt.
    budget_alert_reserve_usd: float | None = Field(default=None, ge=0.0)

    #: Die Grenze, die auch ohne belegte Kosten trägt. Ohne sie wäre die
    #: Reserve im Zustand ``COST_UNKNOWN`` unbegrenzt — siehe
    #: ``app.ai.budget.ReservePolicy``.
    budget_alert_reserve_max_calls: int | None = Field(default=None, ge=0)

    #: Ausschliesslich SHADOW/kontrollierte Validierung. Kein Übertrag in beide
    #: Richtungen: gewöhnliche Analyse kommt hier nicht heran, und Validierung
    #: greift nicht auf Produktionskapazität zu.
    budget_validation_reserve_usd: float | None = Field(default=None, ge=0.0)
    budget_validation_reserve_max_calls: int | None = Field(default=None, ge=0)

    #: Ab welcher REGELBASIERTEN Vorabpriorität ein Dokument die Alert-Reserve
    #: anzapfen darf.
    #:
    #: Die Vorabpriorität entsteht aus Treffern, die die Pipeline ohnehin schon
    #: berechnet hat (``keyword_hits``, ``entity_mentions``), kostet also nichts.
    #: Sie sagt NICHT voraus, dass ein Dokument einen Alert erzeugt — sie sagt,
    #: dass es nicht von vornherein ausgeschlossen ist.
    #:
    #: **Gemessen am 2026-09-10** über fünf Tage (2026-09-06 bis -10, 2.344
    #: Dokumente, davon 514 mit echter LLM-Bewertung und darunter 217 mit
    #: Priorität >= 7). Für jedes alert-fähige Dokument wurde nachgerechnet,
    #: welche Vorabpriorität der Regelpfad ihm gegeben hätte:
    #:
    #:   k   Deckung der Alerts   Anteil alert-fähiger ab k (LLM-bewertet)
    #:   3      217/217  100 %       217/514  42,2 %
    #:   4      217/217  100 %       217/514  42,2 %
    #:   5      196/217   90,3 %     196/459  42,7 %
    #:   6      138/217   63,6 %     138/318  43,4 %
    #:
    #: Zwei Dinge stehen darin, und das zweite war eine Überraschung:
    #:
    #: 1. Die ursprüngliche Setzung 5 hätte **21 von 217 Alerts verloren**.
    #:    Die niedrigste Vorabpriorität eines tatsächlich alert-fähigen
    #:    Dokuments ist 4.
    #: 2. Die Vorabpriorität **trennt nicht** — und zwar nicht schwach,
    #:    sondern gar nicht. Die Basisrate über alle 514 Aufrufe beträgt
    #:    42,2 %; die Werte bei k=4/5/6 lauten 42,2 / 42,7 / 43,4. Das sind
    #:    1,2 Punkte Streuung über den gesamten belegten Wertebereich, gegen
    #:    einen Deckungsverlust von 36 Punkten. Eine höhere Schwelle
    #:    konzentriert die Reserve also nicht, sie verkleinert nur die
    #:    gedeckte Menge.
    #:
    #:    Der zweite Grund, warum sie nichts steuert: sie BINDET nie. Selbst
    #:    bei k=6 sind rund 64 Aufrufe je Tag zulässig, eine Reserve trägt
    #:    etwa 20. Innerhalb einer flach verteilten Menge entscheidet dann die
    #:    Ankunftsreihenfolge — gegenüber Alerts faktisch eine Zufallsziehung.
    #:    Ein Tor, das nie bindet, ist kein Zielmechanismus.
    #:
    #: Der Grund für (2): wer überhaupt bis zum bezahlten Aufruf kommt, ist vom
    #: Relevanz-Gate bereits gefiltert (514 von 2.344 Dokumenten). Diese Schwelle
    #: ist deshalb ein RÜCKFALLNETZ und kein Zielmechanismus — sie hält die
    #: offensichtlich belanglosen Dokumente von der Reserve fern, falls das
    #: Gate davor je lockerer wird, und sonst tut sie nichts.
    #:
    #: **Korrektur am selben Tag, breiteres Fenster.** Die erste Auswertung lief
    #: ueber fuenf Tage (217 alert-faehige Dokumente) und ergab "der gemessene
    #: Boden ist 4". Das galt fuer dieses Fenster und wurde allgemein
    #: formuliert. Ein Minimum ist die instabilste Statistik ueberhaupt: es
    #: wird mit jedem zusaetzlichen Tag nur kleiner, nie groesser.
    #:
    #: Ueber 01.07.-05.09. (39.354 Dokumente, 10.059 mit LLM-Bewertung, davon
    #: **4.774 mit Prioritaet >= 7**) sieht es so aus:
    #:
    #:   k=3   4774/4774   100,00 %
    #:   k=4   4773/4774    99,98 %
    #:   k=5   4591/4774    96,2 %
    #:   k=6   3655/4774    76,6 %
    #:
    #: Es gibt also **ein** alert-faehiges Dokument mit Vorabprioritaet 3 --
    #: `51546d0f` "Bitcoin Above $70k - Live from Honduras Airport!",
    #: Endprioritaet 7. Bei k=4 waere es verloren. Ein Dokument reicht: die Wahl
    #: ist asymmetrisch, nicht knapp. Die Reserve ist ohnehin in USD UND in
    #: Aufrufzahl gedeckelt; eine zu weite Vorauswahl kostet gedeckeltes Geld,
    #: eine zu enge kostet einen Alert, und der ist nicht nachholbar. Zusammen
    #: mit (2) -- die Schwelle trennt ohnehin nicht -- gibt es keinen Grund,
    #: ueber den Boden zu gehen.
    #:
    #: Die Zahlen stammen aus zwei unabhaengigen Auswertungen (verschiedene
    #: Populationen und Aggregationen), die auf jeder Stelle uebereinstimmen.
    #: Gerechnet wurde mit `monitor/` aus dem aktiven Release -- die Angabe
    #: gehoert dazu und ist keine Formalie: die Vorabprioritaet haengt an
    #: `keywords.txt`, `watchlists.yml` und `entity_aliases.yml`.
    #:
    #: **METHODENREGEL, an der eine fruehere Fassung gescheitert ist.** Sie
    #: nannte 61 Dokumente mit Vorabprioritaet <= 3 und drei namentliche
    #: Belege. Der Verdacht lag zunaechst auf einer abweichenden Keyword-Basis;
    #: am Geraet nachgeprueft ist das WIDERLEGT -- die Treffer sind da
    #: (`hits=1`, `relevance=0,29` in allen benannten Faellen, gerechnet mit dem
    #: `monitor/` des aktiven Release).
    #:
    #: Die Ursache ist die ZEITREFERENZ. `_fallback_novelty` misst das Alter
    #: gegen `now()`: ein Dokument juenger als 24 h bekommt 0,60, aelter als
    #: sieben Tage nur 0,25. Wer historische Dokumente HEUTE nachbewertet,
    #: erhaelt deshalb systematisch zu niedrige Werte -- der Versatz ist
    #: konstant `(0,60 - 0,25) * _W_NOVELTY = 0,07` im `raw`, und genau daran
    #: kippten die drei Belege von 4 auf 3:
    #:
    #:   4307d0d2   heute nachgerechnet 0,274 -> 3   als frisch 0,344 -> 4
    #:   56e2a5bc   heute nachgerechnet 0,265 -> 3   als frisch 0,335 -> 4
    #:   b907ef8e   heute nachgerechnet 0,244 -> 3   als frisch 0,314 -> 4
    #:
    #: `_vorab_alert_faehig` laeuft beim INGEST, wenn das Dokument frisch ist.
    #: Jede Rekalibrierung muss die Neuigkeit deshalb zur urspruenglichen
    #: Bewertungszeit rekonstruieren. Wer gegen `now()` rechnet, misst nicht die
    #: Schwelle, sondern das Alter der Stichprobe.
    #:
    #: 4 bleibt Optimierungskandidat, nicht Voreinstellung: erst wenn eine
    #: Messung ueber ein vergleichbar breites Fenster zeigt, dass 4 nichts
    #: kostet. Nachzumessen ausserdem, sobald sich Keyword-Liste oder Watchlist
    #: wesentlich aendern -- die Vorabprioritaet haengt an beiden.
    budget_alert_min_rule_priority: int = Field(default=3, ge=1, le=10)

    #: Auftraggeber → Tageslimit in USD, aus ``APP_AI_BUDGET_USECASE_<NAME>_USD``.
    #: Einmal beim Bau gelesen, nicht pro Aufruf: ``os.environ`` je LLM-Aufruf
    #: abzufragen wäre dieselbe Sorte versteckter Kosten, die dieses Modul misst.
    budget_usecase_usd: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _collect_usecase_limits(self) -> AICostSettings:
        if self.budget_usecase_usd:
            return self
        gefunden: dict[str, float] = {}
        for key, value in os.environ.items():
            if not key.startswith(USECASE_LIMIT_PREFIX) or not key.endswith(USECASE_LIMIT_SUFFIX):
                continue
            name = key[len(USECASE_LIMIT_PREFIX) : -len(USECASE_LIMIT_SUFFIX)].lower()
            if not name:
                continue
            try:
                betrag = float(value)
            except (TypeError, ValueError):
                # Ein unlesbares Limit wird NICHT als 0 gelesen. Null waere die
                # haerteste denkbare Sperre aus einem Tippfehler heraus.
                continue
            if betrag >= 0.0:
                gefunden[name] = betrag
        object.__setattr__(self, "budget_usecase_usd", gefunden)
        return self

    @property
    def any_limit_set(self) -> bool:
        """Begrenzt überhaupt etwas? Ohne das gibt es keine Warnschwelle."""
        return (
            self.budget_daily_usd is not None
            or self.budget_monthly_usd is not None
            or bool(self.budget_usecase_usd)
            or self.reserve_policy.any_reserve_set
        )

    @property
    def reserve_policy(self) -> ReservePolicy:
        """Die Reserven als reine Policy — die einzige Uebersetzung Env -> Topf.

        Als Eigenschaft und nicht als Feld: ``ReservePolicy`` gehoert in
        ``app.ai.budget``, wo entschieden wird. Sie hier ein zweites Mal zu
        modellieren waere die Sorte Doppelung, aus der in drei Monaten zwei
        Meinungen ueber dieselbe Zahl werden.
        """
        return ReservePolicy(
            alert_reserve_usd=self.budget_alert_reserve_usd,
            alert_reserve_max_calls=self.budget_alert_reserve_max_calls,
            validation_reserve_usd=self.budget_validation_reserve_usd,
            validation_reserve_max_calls=self.budget_validation_reserve_max_calls,
        )


_CACHE: dict[str, AICostSettings] = {}


def get_ai_cost_settings() -> AICostSettings:
    """Die Kostengrenzen — EINMAL gelesen, nicht pro Aufruf.

    ``BaseSettings()`` liest ``.env`` von der Platte. Das je LLM-Aufruf zu tun
    wäre blockierendes Datei-I/O im Event-Loop; dieselbe Begründung wie
    ``app.ai.runtime.environment_settings``.
    """
    zwischenspeicher = _CACHE.get("current")
    if zwischenspeicher is not None:
        return zwischenspeicher
    try:
        gelesen = AICostSettings()
    except Exception:  # noqa: BLE001 — eine kaputte Env darf nicht sperren
        gelesen = AICostSettings.model_construct(
            budget_daily_usd=None,
            budget_monthly_usd=None,
            budget_warn_pct=80.0,
            budget_unknown_max_calls_per_day=50,
            budget_alert_reserve_usd=None,
            budget_alert_reserve_max_calls=None,
            budget_validation_reserve_usd=None,
            budget_validation_reserve_max_calls=None,
            budget_alert_min_rule_priority=3,
            shadow_enabled=False,
            budget_usecase_usd={},
        )
    _CACHE["current"] = gelesen
    return gelesen


def reset_ai_cost_settings() -> None:
    """Zwischenspeicher leeren (Tests, Neustart nach Env-Wechsel)."""
    _CACHE.clear()


__all__ = [
    "USECASE_LIMIT_PREFIX",
    "USECASE_LIMIT_SUFFIX",
    "AICostSettings",
    "get_ai_cost_settings",
    "reset_ai_cost_settings",
]
