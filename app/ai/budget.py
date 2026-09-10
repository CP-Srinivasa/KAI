"""Budget auf verbuchten Kosten — und auf der ehrlichen Antwort „unbekannt".

Der Defekt des ersten Anlaufs hiess „wirkungsloser Per-Call-Budget-Gate ohne
belastbare Kostenschätzung": jeder Aufruf wurde gegen eine Schätzung geprüft,
die es gar nicht gab, unbekannte Kosten wurden als 0 verbucht, und das
Tagesbudget bestand aus lauter Nullen — während real Geld abfloss.

Zwei Regeln, die das ausschliessen:

1. **Verbucht wird, was gemessen wurde.** Unbekannte Kosten erhöhen den
   Verbrauch nicht, aber sie werden GEZÄHLT. Ein Budget, dessen Deckung man
   nicht kennt, ist kein gedecktes Budget — :attr:`BudgetState.unknown_calls`
   macht die Lücke sichtbar, statt sie mit Nullen zu füllen.
2. **Hart abgelehnt wird nur mit Beleg.** Ohne belastbare Schätzung gibt es
   keine Vorab-Ablehnung. Wer ohne Zahlen ablehnt, lehnt nach Gefühl ab —
   und wer ohne Zahlen durchlässt, obwohl das Limit bereits überschritten IST,
   ebenso. Beide Fälle sind hier getrennt.

Rein: keine Uhr, kein I/O. Der Aufrufer bringt Fenster und Einträge mit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, get_args

from app.ai.models import AttemptTrace
from app.ai.routes import Route

#: ``allow``            im Rahmen, Kosten belegt oder Limit unberührt
#: ``allow_unbudgeted`` erlaubt, aber ohne Deckungsnachweis — sichtbar machen
#: ``reject``           belegbar über dem Limit
BudgetDecision = Literal["allow", "allow_unbudgeted", "reject"]


@dataclass(frozen=True)
class BudgetPolicy:
    """Limits in USD. ``None`` heisst: dieses Fenster begrenzt nichts."""

    daily_limit_usd: float | None = None
    monthly_limit_usd: float | None = None


@dataclass(frozen=True)
class BudgetEntry:
    """Eine verbuchte Position. ``cost_usd=None`` heisst UNBEKANNT, nicht 0."""

    route: Route
    cost_usd: float | None = None

    @classmethod
    def from_attempt(cls, route: Route, attempt: AttemptTrace) -> BudgetEntry:
        return cls(route=route, cost_usd=attempt.cost_usd)


@dataclass(frozen=True)
class BudgetState:
    """Was in einem Fenster tatsächlich bekannt ist."""

    booked_usd: float
    known_calls: int
    unknown_calls: int

    @property
    def total_calls(self) -> int:
        return self.known_calls + self.unknown_calls

    @property
    def cost_known_rate(self) -> float | None:
        """``None`` ohne Aufrufe — nicht 1.0, denn nichts ist auch nichts belegt."""
        if self.total_calls == 0:
            return None
        return self.known_calls / self.total_calls

    @property
    def fully_accounted(self) -> bool:
        """Ist jeder Aufruf des Fensters mit Kosten belegt?

        Nur dann trägt ``booked_usd`` die ganze Wahrheit. Sonst ist es eine
        Untergrenze — und eine Untergrenze rechtfertigt keine harte Ablehnung
        und erst recht keine Entwarnung.
        """
        return self.total_calls > 0 and self.unknown_calls == 0


#: Der leere Zustand — ein Topf, in dem heute noch nichts gebucht wurde.
_LEER: Final[BudgetState] = BudgetState(booked_usd=0.0, known_calls=0, unknown_calls=0)


def accumulate(entries: Sequence[BudgetEntry]) -> BudgetState:
    """Einträge zu einem Fensterzustand — unbekannte Kosten werden gezählt, nicht genullt."""
    booked = 0.0
    known = 0
    unknown = 0
    for entry in entries:
        if entry.cost_usd is None:
            unknown += 1
            continue
        booked += entry.cost_usd
        known += 1
    return BudgetState(booked_usd=booked, known_calls=known, unknown_calls=unknown)


def _limit_breached(state: BudgetState, limit: float | None, estimate: float | None) -> bool:
    if limit is None:
        return False
    if state.booked_usd >= limit:
        return True
    return estimate is not None and state.booked_usd + estimate > limit


def decide(
    *,
    daily: BudgetState,
    monthly: BudgetState,
    policy: BudgetPolicy,
    estimated_request_cost_usd: float | None = None,
) -> BudgetDecision:
    """Darf dieser Aufruf laufen?

    ``reject`` nur, wenn es belegbar ist: entweder ist das Limit mit bereits
    VERBUCHTEN Kosten schon erreicht, oder eine vorhandene Schätzung führt
    darüber hinaus. Ohne Schätzung und unterhalb des Limits wird nicht
    abgelehnt — aber die Antwort heisst dann ``allow_unbudgeted`` und nicht
    ``allow``, damit „wir wissen es nicht" nicht als „alles in Ordnung"
    protokolliert wird.
    """
    if _limit_breached(daily, policy.daily_limit_usd, estimated_request_cost_usd):
        return "reject"
    if _limit_breached(monthly, policy.monthly_limit_usd, estimated_request_cost_usd):
        return "reject"
    limited = policy.daily_limit_usd is not None or policy.monthly_limit_usd is not None
    if limited and estimated_request_cost_usd is None:
        return "allow_unbudgeted"
    if not daily.fully_accounted and daily.total_calls > 0 and limited:
        return "allow_unbudgeted"
    return "allow"


def headroom_usd(state: BudgetState, limit: float | None) -> float | None:
    """Verbleibender Spielraum — ``None``, wenn unbegrenzt oder unbelegbar.

    Bewusst ``None`` statt einer Zahl, sobald das Fenster unbelegte Aufrufe
    enthält: der wahre Verbrauch liegt dann irgendwo über ``booked_usd``, und
    eine Restgrösse auszuweisen wäre eine Genauigkeit, die es nicht gibt.
    """
    if limit is None:
        return None
    if not state.fully_accounted and state.total_calls > 0:
        return None
    return max(0.0, limit - state.booked_usd)


# ── Zustand und Durchsetzung (KAI COST CONTROL v0.1, D-CORE-007) ───────────

#: ``OK``            im Rahmen
#: ``WARNING``       über der Warnschwelle, sperrt NICHTS
#: ``LIMIT_REACHED`` belegbar am oder über dem Limit
#: ``COST_UNKNOWN``  zu viele unbelegte Aufrufe — Routine wird wie
#:                   ``LIMIT_REACHED`` behandelt, ohne dass eine Summe es belegt
BudgetStatusState = Literal["OK", "WARNING", "LIMIT_REACHED", "COST_UNKNOWN"]

#: Routen, die ein Limit NICHT stoppt. ``critical`` ist die Absichtsübersetzung
#: des Operators (``intent``, ``app/ai/routes.py:86-96``): wer sie sperrt,
#: nimmt dem Menschen die Fernbedienung für genau den Zustand, den er gerade
#: beheben muss. Diese Ausnahme ist bewusst NICHT konfigurierbar.
BUDGET_EXEMPT_ROUTES: frozenset[str] = frozenset({"critical"})


# N818: kein `...Error`-Suffix. Der Name benennt einen ZUSTAND (das Budget
# ist ueberschritten), keinen Defekt -- und Aufrufer sollen ihn genau so
# behandeln: verschieben statt reparieren. Ein `Error` im Namen haette die
# Meldung an derselben Stelle einsortiert wie einen Anbieterausfall.
class BudgetExceeded(RuntimeError):  # noqa: N818
    """Dieser Aufruf wird nicht bezahlt — typisiert, damit Aufrufer ihn erkennen.

    Bewusst eine Ausnahme und kein stiller ``None``-Rückgabewert: ein
    übersprungener Aufruf, der wie ein leeres Ergebnis aussieht, wäre von
    einem Anbieterausfall nicht zu unterscheiden. Der Aufrufer soll
    VERSCHIEBEN oder ÜBERSPRINGEN und das sichtbar vermerken — nicht abstürzen.
    """

    def __init__(self, *, route: str, state: BudgetStatusState, reason: str) -> None:
        super().__init__(f"ai_budget_exceeded: route={route} state={state} reason={reason}")
        self.route = route
        self.state = state
        self.reason = reason


#: ``error_type``-Werte, die eine LOKALE Abweisung benennen: der Aufruf wurde
#: abgelehnt, BEVOR ein Provider kontaktiert wurde. Solche Zeilen sind kein
#: Beleg ueber die Erreichbarkeit eines Anbieters -- weder dafuer noch dagegen.
#:
#: Der Satz steht hier und nicht in der Gesundheitsschicht, damit Ausnahme und
#: Marker nicht auseinanderlaufen: ``__name__`` folgt einer Umbenennung der
#: Klasse automatisch, ein Stringliteral an anderer Stelle nicht.
LOCAL_REFUSAL_ERROR_TYPES: frozenset[str] = frozenset({BudgetExceeded.__name__})


@dataclass(frozen=True)
class BudgetStatus:
    """Wo das Budget steht — und ob Routinearbeit noch laufen darf."""

    state: BudgetStatusState
    daily: BudgetState
    monthly: BudgetState
    policy: BudgetPolicy
    warn_pct: float = 80.0
    unknown_max_calls_per_day: int = 50
    reason: str = ""
    #: Ist ueberhaupt ein Limit gesetzt? Ohne Limit gibt es nichts, worauf
    #: fail-closed geschlossen werden koennte -- siehe :attr:`blocks_routine`.
    limits_configured: bool = False

    @property
    def blocks_routine(self) -> bool:
        """Wird Routinearbeit gesperrt? ``WARNING`` sperrt ausdrücklich nicht.

        ``COST_UNKNOWN`` sperrt **nur, wenn ein Limit gesetzt ist**. Das ist
        keine Aufweichung, sondern die Bedingung, unter der fail-closed
        ueberhaupt Sinn ergibt: ohne Budget gibt es keine Deckung, die fehlen
        koennte. Ohne diese Einschraenkung wuerde ein Betrieb, der NIE ein
        Limit gesetzt hat, ab dem 51. unbelegten Aufruf eines Tages stehen
        bleiben -- eine Stilllegung aus der Voreinstellung heraus, ausgeloest
        von einem Messproblem statt von Kosten. Genau das hat die Testsuite
        hier gefangen (``test_voice_transcriber_records_stt_call``).

        Der Zustand wird trotzdem BERECHNET und in ``/health/ai`` ausgewiesen:
        Sichtbarkeit braucht keine Erlaubnis, Sperren schon.
        """
        if self.state == "LIMIT_REACHED":
            return True
        return self.state == "COST_UNKNOWN" and self.limits_configured

    def allows(self, route: str) -> bool:
        """Darf ein Aufruf dieser Route laufen?"""
        return not self.blocks_routine or route in BUDGET_EXEMPT_ROUTES


def _over_warn(state: BudgetState, limit: float | None, warn_pct: float) -> bool:
    return limit is not None and limit > 0 and state.booked_usd >= limit * (warn_pct / 100.0)


def evaluate_status(
    *,
    daily: BudgetState,
    monthly: BudgetState,
    policy: BudgetPolicy,
    warn_pct: float = 80.0,
    unknown_max_calls_per_day: int = 50,
) -> BudgetStatus:
    """Der Zustand des Budgets — auch ohne gesetzte Limits berechnet.

    Reihenfolge mit Absicht:

    1. **Belegtes Limit erreicht** schlägt alles. Eine Zahl, die über dem
       Limit liegt, ist der stärkste Beleg, den es gibt.
    2. **Zu viele unbelegte Aufrufe** ergeben ``COST_UNKNOWN`` — und
       :attr:`BudgetStatus.blocks_routine` behandelt das wie ein erreichtes
       Limit. Nicht weil die Kosten hoch WÄREN, sondern weil niemand weiss, ob
       sie es sind. Das ist der einzige fail-closed Zweig hier.
    3. **Warnschwelle** nur, wenn überhaupt ein Limit gesetzt ist. Ohne Limit
       gibt es keinen Prozentsatz, vor dem gewarnt werden könnte.
    """
    begrenzt = policy.daily_limit_usd is not None or policy.monthly_limit_usd is not None
    if _at_or_over(daily, policy.daily_limit_usd):
        return BudgetStatus(
            state="LIMIT_REACHED",
            daily=daily,
            monthly=monthly,
            policy=policy,
            warn_pct=warn_pct,
            unknown_max_calls_per_day=unknown_max_calls_per_day,
            limits_configured=begrenzt,
            reason="daily_limit_reached",
        )
    if _at_or_over(monthly, policy.monthly_limit_usd):
        return BudgetStatus(
            state="LIMIT_REACHED",
            daily=daily,
            monthly=monthly,
            policy=policy,
            warn_pct=warn_pct,
            unknown_max_calls_per_day=unknown_max_calls_per_day,
            limits_configured=begrenzt,
            reason="monthly_limit_reached",
        )
    if daily.unknown_calls > unknown_max_calls_per_day:
        return BudgetStatus(
            state="COST_UNKNOWN",
            daily=daily,
            monthly=monthly,
            policy=policy,
            warn_pct=warn_pct,
            unknown_max_calls_per_day=unknown_max_calls_per_day,
            limits_configured=begrenzt,
            reason=(f"unknown_cost_calls_today={daily.unknown_calls}>{unknown_max_calls_per_day}"),
        )
    if _over_warn(daily, policy.daily_limit_usd, warn_pct):
        return BudgetStatus(
            state="WARNING",
            daily=daily,
            monthly=monthly,
            policy=policy,
            warn_pct=warn_pct,
            unknown_max_calls_per_day=unknown_max_calls_per_day,
            limits_configured=begrenzt,
            reason="daily_warn_threshold",
        )
    if _over_warn(monthly, policy.monthly_limit_usd, warn_pct):
        return BudgetStatus(
            state="WARNING",
            daily=daily,
            monthly=monthly,
            policy=policy,
            warn_pct=warn_pct,
            unknown_max_calls_per_day=unknown_max_calls_per_day,
            limits_configured=begrenzt,
            reason="monthly_warn_threshold",
        )
    return BudgetStatus(
        state="OK",
        daily=daily,
        monthly=monthly,
        policy=policy,
        warn_pct=warn_pct,
        unknown_max_calls_per_day=unknown_max_calls_per_day,
        limits_configured=begrenzt,
    )


# ── Töpfe (Budget-Policy v2, 2026-09-10) ───────────────────────────────────
#
# Der Befund, der diesen Abschnitt erzwingt (2026-09-10): nach Erreichen des
# Tageslimits fiel der Anteil echter LLM-Analyse von 24,8 % auf 0,3 %, der
# Dokumentzufluss lief unvermindert weiter, und ``is_analyzed`` blieb bei rund
# 100 % — das Dashboard blieb grün. Alerts jedoch: 23 von 202 Dokumenten VOR
# dem Limit, **0 von 373 danach**. Der Grund ist strukturell und nicht knapp:
# der Regelpfad erreicht über 44.018 Dokumente maximal Priorität 6,0, die
# Alert-Schwelle liegt bei 7. Ein erschöpftes Budget schaltet das Alerting also
# nicht schlechter, sondern vollständig ab — lautlos.
#
# Ein einziges Limit über alle Aufrufe kann das nicht verhindern: die Masse
# gewöhnlicher Nachrichten verbraucht es, lange bevor das eine Dokument kommt,
# auf das es ankommt. Deshalb Töpfe statt einer Summe.

#: Aus welchem Topf ein Aufruf bezahlt wird.
#:
#: ``normal``         gewöhnliche Analyse-, Chat- und Ingestionsarbeit
#: ``alert_reserve``  geschützte Restkapazität für alert-fähige Dokumente
#: ``validation``     ausschliesslich SHADOW/kontrollierte Validierung
#: ``exempt``         ``critical`` — war noch nie gesperrt, bleibt es nicht
BudgetPot = Literal["normal", "alert_reserve", "validation", "exempt"]

POTS: Final[tuple[BudgetPot, ...]] = get_args(BudgetPot)

#: Der Topf, dem eine Zeile ohne ``budget_pot`` zugeschlagen wird.
#:
#: Bewusst ``normal`` und nicht ``exempt``: Altzeilen aus der Zeit vor v2
#: dürfen die Reserven nicht belasten, sollen aber den normalen Topf füllen.
#: Andersherum — Altverbrauch als reservefrei zu behandeln — hiesse, am ersten
#: Tag nach dem Rollout mit einem geschenkten Guthaben zu starten, das nie
#: ausgegeben wurde.
LEGACY_POT: Final[BudgetPot] = "normal"


@dataclass(frozen=True)
class ReservePolicy:
    """Wie viel Tagesbudget den Reserven vorbehalten bleibt.

    ``None`` heisst überall: diese Reserve existiert nicht — dann verhält sich
    v2 wie v1, ein Topf über alles. Das ist die Voreinstellung, damit ein
    Rollout ohne gesetzte Werte nichts ändert.

    Zwei Grenzen je Reserve, und beide sind nötig:

    * **USD** ist die eigentliche Aussage, solange Kosten belegt sind.
    * **Aufrufzahl** ist die Grenze, die auch dann noch trägt, wenn sie es
      nicht sind. Genau dafür gibt es sie: ``COST_UNKNOWN`` sperrt in v1 die
      gesamte Routine, weil niemand mehr weiss, was ausgegeben wird. Würde das
      auch die Alert-Reserve sperren, wäre ein MESSPROBLEM wieder ein
      vollständiger Alert-Ausfall — dieselbe lautlose Bauart wie der Befund
      oben, nur mit anderem Auslöser. Mit einer Aufrufgrenze bleibt die Reserve
      begrenzt, ohne bezifferbar zu sein.
    """

    alert_reserve_usd: float | None = None
    alert_reserve_max_calls: int | None = None
    validation_reserve_usd: float | None = None
    validation_reserve_max_calls: int | None = None

    @property
    def reserved_usd(self) -> float:
        """Wie viel des Tagesbudgets gewöhnlicher Arbeit nicht zur Verfügung steht."""
        return (self.alert_reserve_usd or 0.0) + (self.validation_reserve_usd or 0.0)

    @property
    def any_reserve_set(self) -> bool:
        return any(
            wert is not None
            for wert in (
                self.alert_reserve_usd,
                self.alert_reserve_max_calls,
                self.validation_reserve_usd,
                self.validation_reserve_max_calls,
            )
        )

    def normal_ceiling_usd(self, daily_limit_usd: float | None) -> float | None:
        """Die Decke des normalen Topfes — ``None`` heisst unbegrenzt.

        Nicht negativ: sind die Reserven grösser als das Tagesbudget, bleibt
        für gewöhnliche Arbeit eben nichts übrig. Das ist eine extreme, aber
        sinnvolle Konfiguration — und allemal besser als eine negative Decke,
        gegen die jeder Vergleich unvorhersehbar ausfiele.
        """
        if daily_limit_usd is None:
            return None
        return max(0.0, daily_limit_usd - self.reserved_usd)


@dataclass(frozen=True)
class PotVerdict:
    """Aus welchem Topf dieser Aufruf bezahlt wird — und ob überhaupt.

    Der Topf steht auch bei ``allowed=False``: die Telemetriezeile einer
    Ablehnung soll sagen, WORAN sie gescheitert ist. Eine abgelehnte Zeile
    ohne Topf wäre wieder die Sorte Buchführung, die man hinterher rät.
    """

    pot: BudgetPot
    allowed: bool
    reason: str = ""


def _at_or_over(state: BudgetState, limit: float | None) -> bool:
    return limit is not None and state.booked_usd >= limit


def _reserve_erschoepft(state: BudgetState, limit_usd: float | None, max_calls: int | None) -> bool:
    """Ist eine Reserve aufgebraucht? Beide Grenzen zählen, die erste gewinnt.

    ``total_calls`` und nicht ``known_calls``: ein Aufruf, dessen Kosten
    unbekannt sind, hat die Reserve trotzdem benutzt. Ihn nicht mitzuzählen
    hiesse, die Aufrufgrenze genau in dem Zustand wirkungslos zu machen, für
    den sie gebaut ist.
    """
    if limit_usd is not None and state.booked_usd >= limit_usd:
        return True
    return max_calls is not None and state.total_calls >= max_calls


def decide_pot(
    *,
    route: str,
    pots: Mapping[BudgetPot, BudgetState],
    policy: BudgetPolicy,
    reserves: ReservePolicy,
    monthly: BudgetState | None = None,
    alert_eligible: bool = False,
    validation: bool = False,
    cost_unknown: bool = False,
) -> PotVerdict:
    """Welcher Topf zahlt — und darf er?

    Die Reihenfolge ist die Aussage:

    1. ``critical`` bleibt ausgenommen. Diese Ausnahme stand vor v2 und wird
       hier nicht angetastet; sie ist die Fernbedienung des Operators für
       genau den Zustand, den er gerade beheben muss.
    2. **Validierung zahlt aus ihrem eigenen Topf, immer.** Kein Rückfall auf
       ``normal`` und kein Zugriff auf die Alert-Reserve — in beide Richtungen
       gesperrt. Ein Testtopf, der sich bei Bedarf aus der Produktionskapazität
       bedient, ist keine Reserve, sondern eine Umgehung.
    3. **Die Alert-Reserve ist der LETZTE Ausweg, nicht eine zweite Spur.**
       Alert-fähige Arbeit zahlt zuerst aus ``normal`` und rutscht erst in die
       Reserve, wenn ``normal`` erschöpft ist. Andernfalls wäre die Reserve am
       Vormittag von gewöhnlichen Dokumenten aufgebraucht, die zufällig gut
       gepunktet haben, und abends leer — also genau dann, wenn sie zählt.
    4. Alles andere endet bei erschöpftem ``normal``, wie bisher.

    ``cost_unknown`` behandelt den normalen Topf als erschöpft, ohne dass eine
    Summe es belegt (v1-Verhalten von ``COST_UNKNOWN``). Die Alert-Reserve
    bleibt erreichbar, dann aber allein über ihre Aufrufgrenze — siehe
    :class:`ReservePolicy`.
    """
    if route in BUDGET_EXEMPT_ROUTES:
        return PotVerdict(pot="exempt", allowed=True)

    # Das Monatslimit steht ueber den Toepfen und wird von keinem aufgeweicht.
    #
    # Die Reserven teilen den TAG, nicht den Monat: sie sind dafuer gebaut,
    # dass die Masse eines Tages nicht die Kapazitaet fuer das eine wichtige
    # Dokument desselben Tages auffrisst. Ein erreichtes Monatslimit ist eine
    # andere Lage — dort ist kein Vorrang mehr zu verteilen, sondern es ist
    # nichts mehr da. Ohne diesen Zweig haette das Setzen einer Reserve die
    # Monatsgrenze stillschweigend abgeschaltet, weil unten nur noch
    # Tagestoepfe geprueft werden.
    if monthly is not None and _at_or_over(monthly, policy.monthly_limit_usd):
        return PotVerdict(
            pot="validation" if validation else "normal",
            allowed=False,
            reason="monthly_limit_reached",
        )

    if validation:
        zustand = pots.get("validation", _LEER)
        if _reserve_erschoepft(
            zustand, reserves.validation_reserve_usd, reserves.validation_reserve_max_calls
        ):
            return PotVerdict(
                pot="validation", allowed=False, reason="validation_reserve_exhausted"
            )
        return PotVerdict(pot="validation", allowed=True)

    normal = pots.get("normal", _LEER)
    decke = reserves.normal_ceiling_usd(policy.daily_limit_usd)
    normal_erschoepft = cost_unknown or (decke is not None and normal.booked_usd >= decke)

    if not normal_erschoepft:
        return PotVerdict(pot="normal", allowed=True)

    if not alert_eligible:
        return PotVerdict(
            pot="normal",
            allowed=False,
            reason="cost_unknown" if cost_unknown else "normal_budget_exhausted",
        )

    reserve = pots.get("alert_reserve", _LEER)
    if _reserve_erschoepft(reserve, reserves.alert_reserve_usd, reserves.alert_reserve_max_calls):
        return PotVerdict(pot="alert_reserve", allowed=False, reason="alert_reserve_exhausted")
    return PotVerdict(pot="alert_reserve", allowed=True)


__all__ = [
    "BUDGET_EXEMPT_ROUTES",
    "BudgetDecision",
    "BudgetEntry",
    "BudgetExceeded",
    "BudgetPolicy",
    "BudgetPot",
    "BudgetState",
    "BudgetStatus",
    "BudgetStatusState",
    "LEGACY_POT",
    "LOCAL_REFUSAL_ERROR_TYPES",
    "POTS",
    "PotVerdict",
    "ReservePolicy",
    "accumulate",
    "decide",
    "decide_pot",
    "evaluate_status",
    "headroom_usd",
]
