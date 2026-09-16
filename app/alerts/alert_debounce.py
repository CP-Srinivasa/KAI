"""Entprellung wiederholter Alerts (V10, Daily Review 2026-09-16).

Befund, der diesen Baustein ausgeloest hat: 293 von 355 dispatchten Alerts der
Woche 09.-16.09. kamen aus ``tradingview_webhook``, alle bullish, auf genau zwei
Basiswerten. Der Abstand zwischen zwei Alerts gleicher ``(asset, sentiment)``
hatte den Median 1,08 min -- 75,6 % aller Wiederholungen lagen unter zwei
Minuten. Das ist kein Ereignisstrom, sondern ein Zustand, der jede Minute erneut
gemeldet wird.

Entscheidend war nicht die Menge, sondern was in ihr steckt. Nach Position in
der Serie (neue Serie ab einer Luecke > 60 min, n = 291 aufgeloest):

    Position 1   n= 35   14 hit / 13 miss   51,9 %
    Position 2-5 n= 52   15 hit / 36 miss   29,4 %
    Position 6-20 n=109  27 hit / 82 miss   24,8 %
    Position >20 n= 95   13 hit / 82 miss   13,7 %

Eine monotone Dosis-Wirkung: je oefter sich ein Signal wiederholt, desto
schlechter trifft es. Bei 60 Minuten bleiben 41 der 293 Alerts uebrig; sie
treffen zu 51,6 %, die 252 weggefallenen zu 21,1 %. Die Entprellung wirft also
ueberwiegend Fehlschlaege weg, nicht Signal.

Zwei Entwurfsentscheidungen, die NICHT aus der Messung folgen:

* **Der Richtungswechsel geht immer sofort durch.** Ein Alert ist nur dann eine
  Wiederholung, wenn er dieselbe Richtung fuer denselben Wert meldet. An den
  Livedaten ist das nicht pruefbar -- seit dem 16.04. sind 3300 von 3301
  TV-Alerts bullish, genau einer bearish. Die Variante ohne diese Regel liefert
  auf den vorhandenen Daten identische Zahlen; sie kostet nichts und schliesst
  den einzigen Fall, in dem eine Entprellung echten Schaden anrichten wuerde.
* **Fail-open.** Was sich nicht sauber verschluesseln laesst, geht durch. Ein
  unterdrueckter echter Alert ist teurer als ein doppelter, und der Zustand
  liegt bewusst nur im Prozess: nach einem Neustart laesst der Entpreller einen
  Alert zu viel durch statt einen zu verschlucken.

Der Rohstrom bleibt vollstaendig erhalten -- entprellt wird der Versand, nicht
die Aufzeichnung. Unterdrueckte Alerts werden mit ``DEBOUNCE_BLOCK_REASON``
protokolliert, sonst verschwindet die Messgrundlage fuer die naechste Runde.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: Grund, unter dem eine unterdrueckte Wiederholung protokolliert wird.
DEBOUNCE_BLOCK_REASON = "repeat_within_debounce_window"

#: Fenster in Minuten, wenn die Umgebung nichts sagt (Messung vom 2026-09-16).
DEFAULT_WINDOW_MINUTES = 60

#: Umgebungsvariable. ``0`` schaltet die Entprellung ab.
WINDOW_ENV_VAR = "KAI_ALERT_DEBOUNCE_WINDOW_MIN"

# Ein Schluessel, der laenger als das Vielfache des Fensters nicht mehr gesehen
# wurde, kann nichts mehr unterdruecken und wird geraeumt. Der Faktor haelt den
# Puffer klein, ohne an der Fenstergrenze zu raeumen.
_EVICT_FACTOR = 2


def debounce_window_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    """Entprell-Fenster aus der Umgebung, fail-safe auf den Default.

    Ein unlesbarer Wert darf die Alert-Kette nicht anhalten; er faellt auf den
    gemessenen Default zurueck. Ein negativer Wert ist als "aus" zu lesen und
    nicht als Fenster in die Vergangenheit.
    """
    raw = (env if env is not None else os.environ).get(WINDOW_ENV_VAR, "")
    try:
        minutes = int(str(raw).strip())
    except (TypeError, ValueError):
        minutes = DEFAULT_WINDOW_MINUTES
    return timedelta(minutes=max(0, minutes))


def _norm(value: str | None) -> str:
    return str(value).strip().casefold() if value is not None else ""


@dataclass(frozen=True)
class DebounceDecision:
    """Was mit genau einem Alert geschehen soll.

    ``emit`` ist die Antwort; ``reason`` traegt den Protokollgrund und ist leer,
    wenn der Alert durchgeht. ``repeat_index`` zaehlt, der wievielte
    unterdrueckte Alert dieser Serie das war -- 0 heisst "erster der Serie".
    """

    emit: bool
    reason: str = ""
    repeat_index: int = 0
    last_emitted_at: datetime | None = None


class AlertDebouncer:
    """Haelt je ``(quelle, wert)`` fest, welche Richtung zuletzt gemeldet wurde.

    Bewusst in-process und ohne Persistenz: der Entpreller ist ein Puffer, kein
    Gedaechtnis. Er ist nicht fuer nebenlaeufigen Zugriff aus mehreren Threads
    ausgelegt -- der Dispatch laeuft seriell.
    """

    def __init__(self, window: timedelta | None = None) -> None:
        self._window = window if window is not None else debounce_window_from_env()
        # (quelle, wert) -> (richtung, zuletzt_gesendet, unterdrueckt_seitdem)
        self._state: dict[tuple[str, str], tuple[str, datetime, int]] = {}

    @property
    def window(self) -> timedelta:
        return self._window

    def tracked_keys(self) -> int:
        """Anzahl gehaltener Schluessel -- fuer Tests und Diagnose."""
        return len(self._state)

    def decide(
        self,
        source: str | None,
        asset: str | None,
        sentiment: str | None,
        *,
        now: datetime | None = None,
    ) -> DebounceDecision:
        """Entscheidet ueber genau einen Alert und schreibt den Zustand fort."""
        if self._window <= timedelta(0):
            return DebounceDecision(emit=True)

        src, sym, direction = _norm(source), _norm(asset), _norm(sentiment)
        if not src or not sym or not direction:
            # Ohne vollstaendigen Schluessel gibt es keine Wiederholung, die man
            # erkennen koennte. Durchlassen und nicht mitschreiben.
            return DebounceDecision(emit=True)

        moment = now if now is not None else datetime.now(UTC)
        key = (src, sym)
        previous = self._state.get(key)

        if previous is None:
            self._state[key] = (direction, moment, 0)
            self._evict(moment)
            return DebounceDecision(emit=True)

        last_direction, last_emitted, repeats = previous

        if last_direction != direction:
            # Richtungswechsel: geht immer durch und setzt die Serie zurueck.
            self._state[key] = (direction, moment, 0)
            return DebounceDecision(emit=True)

        elapsed = _elapsed(moment, last_emitted)
        if elapsed is None or elapsed > self._window:
            # ``None`` heisst: nicht vergleichbar (Zeitsprung, gemischte Zonen).
            # Dann gilt fail-open, und der Zustand wird auf das Jetzt gesetzt.
            self._state[key] = (direction, moment, 0)
            return DebounceDecision(emit=True)

        repeats += 1
        self._state[key] = (last_direction, last_emitted, repeats)
        return DebounceDecision(
            emit=False,
            reason=DEBOUNCE_BLOCK_REASON,
            repeat_index=repeats,
            last_emitted_at=last_emitted,
        )

    def seed(
        self,
        source: str | None,
        asset: str | None,
        sentiment: str | None,
        *,
        emitted_at: datetime,
    ) -> bool:
        """Setzt einen bereits gesendeten Alert in den Zustand, ohne zu werten.

        Gebraucht, weil der Entpreller je Durchlauf neu entsteht: der
        Bridge-Takt ruft seine Funktion alle paar Minuten erneut auf. Ohne
        diesen Wiederanlauf waere das Fenster faktisch so kurz wie der Takt.
        """
        src, sym, direction = _norm(source), _norm(asset), _norm(sentiment)
        if not src or not sym or not direction:
            return False
        self._state[(src, sym)] = (direction, emitted_at, 0)
        return True

    def _evict(self, now: datetime) -> None:
        """Abgelaufene Schluessel raeumen, damit der Puffer nicht waechst."""
        horizon = self._window * _EVICT_FACTOR
        stale = [
            key
            for key, (_, last, _) in self._state.items()
            if (gap := _elapsed(now, last)) is not None and gap > horizon
        ]
        for key in stale:
            del self._state[key]


def seed_debouncer_from_audit_rows(
    debouncer: AlertDebouncer,
    rows: Iterable[object],
    *,
    channel: str,
    now: datetime | None = None,
) -> int:
    """Stellt den Zustand aus dem Audit-Trail wieder her. Gibt die Anzahl zurueck.

    Der Audit-Trail IST der Beleg darueber, was zuletzt hinausging -- es braucht
    dafuer keinen zweiten Zustandsspeicher, der mit ihm auseinanderlaufen
    koennte. Gelesen werden nur Zeilen des angegebenen Kanals, die im Fenster
    liegen; je Schluessel gewinnt die juengste.

    Kaputte Zeilen werden uebergangen, nicht geworfen: ein unlesbarer
    Audit-Eintrag darf die Bruecke nicht anhalten.
    """
    window = debouncer.window
    if window <= timedelta(0):
        return 0

    wanted = _norm(channel)
    latest: dict[tuple[str, str], tuple[str, datetime]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _norm(row.get("channel")) != wanted:
            continue
        sentiment = row.get("sentiment_label")
        asset = row.get("canonical_asset")
        if not asset:
            affected = row.get("affected_assets")
            if isinstance(affected, list) and affected:
                asset = affected[0]
        moment = _parse_ts(row.get("dispatched_at"))
        if moment is None or not _norm(asset) or not _norm(sentiment):
            continue
        if now is not None:
            # Der Altersfilter ist reine Sparsamkeit. Ob ein geseedeter Stand
            # noch unterdrueckt, entscheidet allein ``decide`` -- die Regel
            # steht an genau einer Stelle. Aufrufer ohne sinnvolles "jetzt"
            # (der Bruecken-Takt arbeitet in Ereigniszeit) lassen ihn weg.
            gap = _elapsed(now, moment)
            if gap is None or gap > window:
                continue
        key = (wanted, _norm(asset))
        known = latest.get(key)
        if known is None or moment > known[1]:
            latest[key] = (str(sentiment), moment)

    seeded = 0
    for (_, sym), (sentiment, moment) in latest.items():
        if debouncer.seed(wanted, sym, sentiment, emitted_at=moment):
            seeded += 1
    return seeded


def _parse_ts(raw: object) -> datetime | None:
    """ISO-Zeitstempel aus einer Audit-Zeile, oder ``None``."""
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed(now: datetime, before: datetime) -> timedelta | None:
    """Vergangene Zeit, oder ``None``, wenn die beiden nicht vergleichbar sind.

    Nicht vergleichbar heisst: gemischte Zeitzonen-Bewusstheit oder eine Uhr,
    die zurueckgesprungen ist. Beides darf keinen Alert verschlucken.
    """
    try:
        gap = now - before
    except TypeError:
        return None
    return None if gap < timedelta(0) else gap
