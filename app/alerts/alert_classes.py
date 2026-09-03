"""Alarmklassen P0–P3 — Dringlichkeit wird zugeteilt, nicht gefuehlt.

**Der Befund (G6 Task 1, KMA-20260827 / A4-005, A4-024, A4-026).** KAI kennt
heute *fuenf* untereinander unvertraegliche Severity-Skalen — ``warning`` /
``critical``, ``warn`` / ``crit``, ``info``, ``ok``, ``blocker`` — und **keine
davon routet**. Alle Befunde des Health-Checks reisen in EINER Telegram-
Nachricht: ein kritischer ``privilege_broker`` steht mit derselben Dringlichkeit
darin wie ein ``annotations``-Rueckstand. Schlimmer ist die Kollision am
Cooldown: das 1440-min-Gate unterdrueckt **alles** gleich — der Muedigkeits-
schutz erstickt damit genau die Klasse, fuer die er nie gedacht war, das stille
Versagen (A4-024/026, gemessen am TV-Eingang, der sechs Tage tot war).

**Die Regel hier.** Jede Komponente traegt genau eine Klasse:

* ``P0`` — Kapital, Truth-Korruption, Backup-Ausfall. Etwas ist unwahr oder
  Geld/Beweis steht auf dem Spiel. Bricht jeden Cooldown.
* ``P1`` — stilles Versagen, toter Eingang, Drift, Auth-Anomalie. Das System
  laeuft weiter und meldet nichts mehr. Bricht jeden Cooldown.
* ``P2`` — Sammelmeldung. Richtig, aber nicht dringend; darf warten und wird
  gebuendelt.
* ``P3`` — Hinweis (Dashboard, Ledger, Standort der Sonde). Kein Alarm.

Der Cooldown gilt **nur fuer P2/P3**. Das ist die eine Zeile, die den
Widerspruch aufloest: Fatigue-Schutz fuer das Laute, Durchbruch fuer das Stille.

**Vollstaendigkeit ist erzwungen.** ``classify`` kennt keinen stillen Default:
eine unbekannte Komponente liefert ``UNCLASSIFIED`` und der Contract-Test
``test_every_health_component_has_a_class`` faellt. Eine neue Sonde ohne Klasse
ist damit ein CI-Fehler, keine stille P2.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AlertClass(StrEnum):
    """Dringlichkeitsklasse eines Befunds. Reihenfolge = Dringlichkeit."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    #: Kein Default, sondern ein Befund ueber uns selbst.
    UNCLASSIFIED = "UNCLASSIFIED"

    @property
    def breaks_cooldown(self) -> bool:
        """P0/P1 duerfen das Reassert-Gate durchbrechen — und nur die.

        Auch ``UNCLASSIFIED`` bricht durch: eine Komponente, deren Dringlichkeit
        niemand festgelegt hat, im Zweifel zu unterdruecken waere genau die
        Stille, die dieses Modul beendet.
        """
        return self in (AlertClass.P0, AlertClass.P1, AlertClass.UNCLASSIFIED)

    @property
    def rank(self) -> int:
        order = {
            AlertClass.P0: 0,
            AlertClass.P1: 1,
            AlertClass.UNCLASSIFIED: 2,
            AlertClass.P2: 3,
            AlertClass.P3: 4,
        }
        return order[self]


@dataclass(frozen=True)
class ClassifiedIssue:
    """Ein Befund mit zugeteilter Klasse (die Quelle bleibt unveraendert)."""

    alert_class: AlertClass
    severity: str
    component: str
    message: str


#: Statische Komponenten des Health-Checks, jede genau einer Klasse zugeteilt.
#: Quelle: ``component=`` in ``app/alerts/health_check.py`` (Stand 7ea4637e).
COMPONENT_CLASSES: dict[str, AlertClass] = {
    # --- P0: Kapital, Truth-Korruption, Backup ---------------------------
    # Der Broker ist der einzige passwortfreie privilegierte Pfad; faellt er
    # aus oder ist er manipuliert, ist die Rechtekette selbst offen (#734).
    "privilege_broker": AlertClass.P0,
    # Ein Close-Preis, der die Sanity-Pruefung reisst, vergiftet das Paper-Buch
    # — genau die Klasse, die das Buch am 18.08. unbrauchbar gemacht hat.
    "close_price_sanity": AlertClass.P0,
    # Das Geld-Journal ist die einzige Wahrheit ueber jede Wertbewegung
    # (ADR 0017 §5). Eine gebrochene Kette ist schlimmer als eine fehlende
    # Datei: sie sieht weiterhin aus wie ein Beweis, und Idempotenz, Tages-Cap
    # und Reconciliation verlieren gleichzeitig ihre Grundlage.
    "payment_journal": AlertClass.P0,
    # Ein Waisen-Settlement ist Geld, das der Node bewegt hat, ohne dass ein
    # Intent es beauftragt haette; ein ungeklaerter Send ist Geld, ueber dessen
    # Verbleib niemand etwas sagen kann. Beide sind offene Fragen ueber Kapital
    # und gehoeren nicht in die Sammelmeldung (ADR 0017 §8).
    "payment_reconciliation": AlertClass.P0,
    # --- P1: stilles Versagen, toter Eingang, Drift, Auth ---------------
    # Der Alarmkanal selbst: 15 von 19 Alarmen kamen nie an (A4-017).
    "alert_delivery": AlertClass.P1,
    "sudo_policy": AlertClass.P1,
    "runtime_identity": AlertClass.P1,
    "timer_scheduleability": AlertClass.P1,
    "document_ingest": AlertClass.P1,
    "youtube_transcript_coverage": AlertClass.P1,
    # G5-Eingangsvertraege: der Reject-Strom traegt den GRUND einer Ablehnung.
    # Faellt er aus, bleibt der Geldpfad fail-closed (deshalb nicht P0), aber
    # der Operator kann Caller-Fehler nicht mehr von Vertragsverletzung
    # unterscheiden — eine Ablehnung ohne lesbaren Grund ist ein stilles
    # Versagen der Beweisfuehrung.
    "input_contract_rejection_stream": AlertClass.P1,
    "alerts": AlertClass.P1,
    "alerts_actionable": AlertClass.P1,
    "trading_loop": AlertClass.P1,
    "trading_loop_signal_health": AlertClass.P1,
    "trading_loop_open_deadlock": AlertClass.P1,
    # --- P2: Sammelmeldung ----------------------------------------------
    "precision": AlertClass.P2,
    "annotations": AlertClass.P2,
    "prereg_reconciliation": AlertClass.P2,
    # P0: ein Dienst auf altem Code macht die Aussage "deployt" unwahr. Weder
    # `active` noch `/health=200` sehen das — der kai-tg-listener lief am
    # 2026-09-01 sechs Tage mit Bibliotheken im Speicher, die auf der Platte
    # laengst ersetzt waren, und meldete durchgehend gruen.
    "runtime_provenance": AlertClass.P0,
    "prereg_ledger_presence": AlertClass.P2,
    # --- P3: Hinweis -----------------------------------------------------
    # Sagt, WO die Sonde lief — eine Eigenschaft der Messung, kein Systemzustand.
    "probe_location": AlertClass.P3,
}

#: Dynamische Komponentenfamilien: ``<name>_freshness`` und ``<stream>_schema``
#: entstehen erst zur Laufzeit aus den Tabellen in ``health_check``.
#:
#: ``*_freshness`` ist die Signatur des stillen Versagens schlechthin (ein
#: Schreiber ist tot, das System laeuft weiter) ⇒ P1. ``*_schema`` heisst, dass
#: ein Audit-Strom nicht mehr seinem Vertrag entspricht — eine Aussage ueber die
#: Wahrheit der Aufzeichnung selbst ⇒ P0.
SUFFIX_CLASSES: tuple[tuple[str, AlertClass], ...] = (
    ("_freshness", AlertClass.P1),
    ("_schema", AlertClass.P0),
)


def classify(component: str) -> AlertClass:
    """Klasse einer Komponente. Kein stiller Default (siehe Modul-Doku)."""
    known = COMPONENT_CLASSES.get(component)
    if known is not None:
        return known
    for suffix, alert_class in SUFFIX_CLASSES:
        if component.endswith(suffix):
            return alert_class
    return AlertClass.UNCLASSIFIED


def classify_issues(issues: object) -> list[ClassifiedIssue]:
    """Teile eine Liste von ``HealthIssue`` in Klassen ein, dringlichste zuerst.

    Nimmt bewusst ``object`` statt des Health-Typs: dieses Modul soll von
    ``health_check`` unabhaengig bleiben (und umgekehrt), damit die Zuteilung
    auch fuer Befunde anderer Quellen gilt.
    """
    out: list[ClassifiedIssue] = []
    for issue in issues:  # type: ignore[attr-defined]
        component = str(getattr(issue, "component", ""))
        out.append(
            ClassifiedIssue(
                alert_class=classify(component),
                severity=str(getattr(issue, "severity", "")),
                component=component,
                message=str(getattr(issue, "message", "")),
            )
        )
    out.sort(key=lambda c: (c.alert_class.rank, c.component))
    return out


def partition(issues: object) -> dict[AlertClass, list[ClassifiedIssue]]:
    """Gruppiere nach Klasse — die Grundlage dafuer, getrennt zu senden."""
    grouped: dict[AlertClass, list[ClassifiedIssue]] = {}
    for item in classify_issues(issues):
        grouped.setdefault(item.alert_class, []).append(item)
    return grouped


def cooldown_applies(issues: object) -> bool:
    """False, sobald ein Befund dabei ist, der das Reassert-Gate durchbrechen darf.

    Das ist die Aufloesung von A4-024/026: der Muedigkeitsschutz bleibt fuer
    P2/P3 in Kraft, aber er darf kein stilles Versagen mehr verschlucken.
    """
    return not any(item.alert_class.breaks_cooldown for item in classify_issues(issues))


def render_grouped(issues: object) -> str:
    """Nachrichtentext, nach Klassen getrennt statt in EINEM Block gebuendelt."""
    grouped = partition(issues)
    lines: list[str] = []
    for alert_class in sorted(grouped, key=lambda c: c.rank):
        items = grouped[alert_class]
        lines.append(f"[{alert_class.value}] {len(items)}:")
        lines.extend(f"  {item.component}: {item.message}" for item in items)
    return "\n".join(lines)
