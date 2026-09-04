"""Ergebnis und Gedaechtnis des Reconcilers (ADR 0018 §8).

Getrennt von :mod:`app.payments.reconcile`, weil zwei verschiedene Leser diese
Typen brauchen: der Reconciler selbst und — in einem ANDEREN Prozess — der
Health-Check (``/health/payment``, ``app/alerts/health_check.py``). Der
Reconcile-Timer laeuft als eigene systemd-Unit; ohne persistierten Zustand
waere sein Ergebnis mit dem Prozess weg, und ein Alarm haette nichts zu lesen.

**Warum der Zustand eine eigene Datei ist und kein Journal-Record.** Das
Journal ist append-only und traegt Wertbewegungen. Ein Lauf, der nichts
gefunden hat, ist keine Wertbewegung — ihn dort einzutragen hiesse, die Kette
im Fuenf-Minuten-Takt mit Rauschen zu verlaengern.

**Warum eine monotone UND eine Wall-Clock-Marke.** Nur ihr Vergleich zeigt
einen Uhr-Sprung (Red-Team D-06). Die monotone Uhr ist auf Linux
boot-relativ — deshalb steht die Boot-Referenz mit dabei: nach einem Neustart
sind die beiden Marken nicht vergleichbar, und ein Reconciler, der das
uebersieht, haelt jeden Neustart fuer einen Zeitsprung (oder, schlimmer, einen
Zeitsprung fuer einen Neustart).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Ab dieser Abweichung zwischen Wall-Clock- und monotoner Differenz gilt die
#: Uhr als gesprungen. 300 s ist grosszuegig gegen NTP-Feinkorrektur (Sekunden)
#: und eng genug fuer eine Zeitzonen-/Sommerzeit-Verstellung (Stunden).
DEFAULT_CLOCK_SKEW_TOLERANCE_S = 300.0

STATE_FILENAME = "reconcile_state.json"

STATE_SCHEMA = "payment-reconcile-state/v1"


@dataclass(frozen=True)
class ReconcileReport:
    """Was ein Lauf gesehen und was er daraus gemacht hat.

    ``status`` kennt genau zwei Werte. ``attention`` heisst nicht "kaputt",
    sondern "ein Mensch muss hinsehen": ein Waisen-Settlement, ein Intent, den
    der Node nicht bestaetigt, oder eine gesprungene Uhr. Alle drei sind
    Aussagen ueber Geld, deren Klaerung nicht warten kann.
    """

    status: str = "ok"
    counts: dict[str, int] = field(default_factory=dict)
    orphans: tuple[str, ...] = ()
    clock_anomaly: bool = False
    #: ``False`` heisst: in DIESEM Lauf wurde kein Intent zum Verfallen
    #: gebracht — entweder wegen eines Uhr-Sprungs oder weil es keine
    #: vergleichbare Basislinie gab (erster Lauf, Neustart).
    expiry_enabled: bool = True
    checked_intents: int = 0
    #: Vorgaenge, ueber deren Geld nach diesem Lauf immer noch niemand etwas
    #: sagen kann. Sie erzeugen KEINEN neuen Record (der Zustand aendert sich
    #: nicht), muessen aber sichtbar sein — sonst waere ein Journal voller
    #: ungeklaerter Sends von einem sauberen nicht zu unterscheiden.
    unresolved: int = 0
    checked_receivables: int = 0
    #: Zahlungen, die BEIDE Geldjournale fuehren und die der Altpfad nicht
    #: bewiesen abgeschlossen hat (ADR §12). Genau wie eine Waise: einmal
    #: gemeldet, danach still — aber der Lauf bleibt ``attention``, solange
    #: sie in diesem Lauf gefunden wurden.
    dual_conflicts: tuple[str, ...] = ()
    #: Vom Rail durchgereichte Ehrlichkeit seiner Aufzaehlung (ADR §8).
    window_enforced: bool = False
    complete: bool = True
    ran_at: str = ""
    rail: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "counts": dict(sorted(self.counts.items())),
            "orphans": list(self.orphans),
            "clock_anomaly": self.clock_anomaly,
            "expiry_enabled": self.expiry_enabled,
            "checked_intents": self.checked_intents,
            "unresolved": self.unresolved,
            "checked_receivables": self.checked_receivables,
            "dual_conflicts": list(self.dual_conflicts),
            "window_enforced": self.window_enforced,
            "complete": self.complete,
            "ran_at": self.ran_at,
            "rail": self.rail,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ReconcileState:
    """Was ein Lauf dem naechsten hinterlaesst — und dem Health-Check."""

    last_run_utc: str = ""
    last_monotonic: float | None = None
    boot_ref: str = ""
    last_status: str = ""
    last_orphans: int = 0
    last_clock_anomaly: bool = False

    def comparable_with(self, boot_ref: str) -> bool:
        """Darf die monotone Marke dieses Zustands verglichen werden?

        Nur innerhalb desselben Boots. Eine leere Referenz (Plattform ohne
        Boot-ID) ist ausdruecklich NICHT vergleichbar — lieber ein Lauf ohne
        Ablauf-Uebergaenge als ein Uebergang in einen terminalen Zustand auf
        einer Annahme.
        """
        return bool(self.boot_ref) and bool(boot_ref) and self.boot_ref == boot_ref

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": STATE_SCHEMA,
            "last_run_utc": self.last_run_utc,
            "last_monotonic": self.last_monotonic,
            "boot_ref": self.boot_ref,
            "last_status": self.last_status,
            "last_orphans": self.last_orphans,
            "last_clock_anomaly": self.last_clock_anomaly,
        }


def load_state(path: Path) -> ReconcileState:
    """Lies den Zustand. Ein unlesbarer Zustand ist ein LEERER Zustand.

    Fail-closed in die harmlose Richtung: ohne Basislinie laeuft der naechste
    Lauf ohne Ablauf-Uebergaenge, statt auf einer beschaedigten Marke zu
    rechnen.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ReconcileState()
    if not isinstance(raw, dict):
        return ReconcileState()
    monotonic = raw.get("last_monotonic")
    return ReconcileState(
        last_run_utc=str(raw.get("last_run_utc", "")),
        last_monotonic=float(monotonic) if isinstance(monotonic, (int, float)) else None,
        boot_ref=str(raw.get("boot_ref", "")),
        last_status=str(raw.get("last_status", "")),
        last_orphans=int(raw.get("last_orphans", 0) or 0),
        last_clock_anomaly=bool(raw.get("last_clock_anomaly", False)),
    )


def save_state(path: Path, state: ReconcileState) -> None:
    """Schreibe den Zustand atomar — ein halber Zustand ist keiner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.to_dict(), sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def boot_reference() -> str:
    """Kennzeichen des laufenden Boots, oder ``""`` wenn nicht feststellbar.

    Linux liefert es in ``/proc/sys/kernel/random/boot_id``. Auf jeder anderen
    Plattform gibt es keins — und dann sagt diese Funktion das, statt einen
    Ersatzwert zu erfinden, auf den sich der Uhr-Vergleich stuetzen wuerde.
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return ""


__all__ = [
    "DEFAULT_CLOCK_SKEW_TOLERANCE_S",
    "STATE_FILENAME",
    "ReconcileReport",
    "ReconcileState",
    "boot_reference",
    "load_state",
    "save_state",
]
