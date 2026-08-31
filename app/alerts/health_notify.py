"""Versand des Health-Befunds an den Operator-Kanal, gegated auf ÄNDERUNG.

Aus ``app/cli/main.py`` herausgezogen (God-File-Ratchet, repo_hygiene_policy §5)
— und inhaltlich ohnehin fällig: der Versand lag dort im linearen Ablauf HINTER
dem Stale-Exit, wodurch der Health-Check genau im Ausfall schwieg, für den er
gebaut ist. Als eigene Funktion kann er vor dem Abbruch aufgerufen werden, ohne
den Ablauf zu duplizieren, und ist ohne CLI-Rahmen testbar.

ÄNDERUNGS-GATE 2026-08-18. Das Gate war rein zeitbasiert: ein Zeitstempel in
``.health_check_last_notification``, und nach Ablauf des Cooldowns ging derselbe
Text wieder raus. Gemessen auf dem Pi: **30 gesendete** Health-Alarme an einem
Tag (plus 47 durch Cooldown unterdrückte), und jeder einzelne trug denselben,
seit 16 Tagen bekannten Befund ``tradingview_ingress_freshness``.

Das ist dieselbe Krankheit wie der 5-Minuten-Watchdog-Spam: ein Kanal, der
Bekanntes wiederholt, wird ignoriert — und dann fällt das Neue mit durch.

Was sich ändert, ist NICHT was erkannt wird, sondern wann gesprochen wird:

    Befundmenge geändert      → sofort melden (das ist Neuigkeit)
    Befundmenge unverändert   → erst nach ``reassert_minutes`` wieder (Default 24 h)
    Befundmenge leer geworden → einmal Entwarnung (gab es vorher NICHT)

Der Fingerprint läuft über ``severity:component``, bewusst NICHT über den
Meldetext: der trägt das Alter in Minuten (``mtime is 22521min old``) und
ändert sich bei jedem Lauf. Ein Fingerprint darüber würde nie greifen.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "HEALTH_NOTIFY_STATE_FILE",
    "build_health_alert_text",
    "dispatch_health_notification",
    "issues_fingerprint",
    "reassert_minutes_for",
]

HEALTH_NOTIFY_STATE_FILE = Path("artifacts") / ".health_check_last_notification"

# Ein Daueralarm darf nicht still verschwinden: einmal pro Tag wird auch ein
# unveränderter Befund wiederholt. 24 h statt 30 min senkt die Last um Faktor
# ~48, ohne einen Befund je aus dem Kanal zu verlieren.
_DEFAULT_REASSERT_MINUTES = 1440.0

# KLASSENABHAENGIGE WIEDERHOLUNG (G6 Task 1, A4-024/026). Das 24-h-Gate loeste
# den Wiederholungs-Spam — und schuf denselben Widerspruch noch einmal an
# anderer Stelle: ein STILLES VERSAGEN, das sich per Definition nicht aendert,
# verschwand damit fuer 24 h aus dem Kanal. Genau dieser Fatigue-Schutz erstickt
# die Klasse, gegen die er nie gerichtet war.
#
# Aufloesung: der Fingerprint entscheidet weiter, WAS neu ist; die Klasse
# entscheidet, wie lange Bekanntes schweigen darf. Kein Durchbruch auf 0 —
# das waere der Zustand vor dem 18.08. (gemessen: 30 gesendete Alarme an EINEM
# Tag, alle mit demselben 16 Tage alten Befund).
_REASSERT_MINUTES_BY_CLASS: dict[str, float] = {
    "P0": 60.0,  # Kapital/Truth/Backup: stuendlich, solange es steht
    "P1": 360.0,  # stilles Versagen: 4x taeglich statt 1x
    "UNCLASSIFIED": 360.0,  # unbekannte Dringlichkeit wird wie P1 behandelt
}

_RECOVERY_TEXT = (
    "KAI Health Alert — behoben\nAlle vorher gemeldeten Befunde sind aufgelöst; "
    "der Health-Check läuft ohne Beanstandung."
)


class _Console(Protocol):
    def print(self, *args: Any, **kwargs: Any) -> None: ...


def issues_fingerprint(issues: Any) -> str:
    """Stabiler Fingerprint der Befund-MENGE (severity + component, sortiert).

    Bewusst ohne den Meldetext: er enthält veränderliche Zahlen (Alter in
    Minuten, Zykluszahlen). Zwei Läufe mit demselben Problem müssen denselben
    Fingerprint ergeben, sonst bremst das Gate die Wiederholung nicht.
    """
    keys = sorted(f"{getattr(i, 'severity', '')}:{getattr(i, 'component', '')}" for i in issues)
    if not keys:
        return ""
    return hashlib.sha256("|".join(keys).encode("utf-8")).hexdigest()[:16]


def reassert_minutes_for(issues: Any, *, default_minutes: float) -> float:
    """Wie lange ein UNVERAENDERTER Befund schweigen darf — nach Klasse.

    Die dringlichste anwesende Klasse gewinnt: eine P0 neben zehn P2 macht die
    ganze Meldung stuendlich, nicht taeglich.
    """
    from app.alerts.alert_classes import classify_issues

    windows = [
        _REASSERT_MINUTES_BY_CLASS[item.alert_class.value]
        for item in classify_issues(issues)
        if item.alert_class.value in _REASSERT_MINUTES_BY_CLASS
    ]
    return min(windows) if windows else default_minutes


def build_health_alert_text(report: Any, *, lookback_hours: int) -> str:
    """Alarmtext aus dem Report. Rein, damit der Inhalt prüfbar ist.

    Nach Klassen getrennt statt in EINEM Block: bis hierher reisten bis zu 35
    Komponenten in einer Nachricht, ein kritischer ``privilege_broker`` mit
    derselben Dringlichkeit wie ein ``annotations``-Rueckstand (A4-005).
    """
    from app.alerts.alert_classes import partition

    lines = ["KAI Health Alert"]
    if report.data_sources_stale:
        lines.append("[NOTE] data sources stale — Pi-sync may be lagging")
    lines.append(
        f"Window: {lookback_hours}h · alerts={report.recent_alerts} "
        f"(actionable={report.recent_actionable_alerts}) · cycles={report.recent_cycles}"
    )
    grouped = partition(report.issues)
    for alert_class in sorted(grouped, key=lambda c: c.rank):
        items = grouped[alert_class]
        lines.append("")
        lines.append(f"== {alert_class.value} ({len(items)}) ==")
        for item in items:
            tag = "CRITICAL" if item.severity == "critical" else "WARNING"
            lines.append(f"[{tag}] {item.component}: {item.message}")
    return "\n".join(lines)


def _read_state(state_file: Path) -> tuple[float | None, str | None]:
    """(letzter Zeitstempel, letzter Fingerprint). Unlesbar ⇒ ``(None, None)``.

    Unlesbarer Zustand darf nicht stummschalten — lieber einmal zu viel melden
    als einen echten Befund wegen eines kaputten Zustands verschlucken. Der
    Altzustand war ein nackter Zeitstempel; er wird ohne Fingerprint akzeptiert,
    damit der Umstieg nicht knallt.
    """
    try:
        raw = state_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    if not raw:
        return None, None
    try:
        data = json.loads(raw)
    except ValueError:
        try:
            return float(raw), None  # Legacy: nur Zeitstempel
        except ValueError:
            return None, None
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        # ACHTUNG: ein nackter Zeitstempel ist GUELTIGES JSON. `json.loads`
        # liefert dafuer einen Float, keinen Dict -- ohne diesen Zweig wuerde
        # der Altzustand stillschweigend verworfen und das Zeit-Gate beim
        # Umstieg wirkungslos.
        return float(data), None
    if not isinstance(data, dict):
        return None, None
    ts = data.get("ts")
    fingerprint = data.get("fingerprint")
    return (
        float(ts) if isinstance(ts, (int, float)) else None,
        fingerprint if isinstance(fingerprint, str) else None,
    )


def _write_state(state_file: Path, *, now_ts: float, fingerprint: str) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"ts": now_ts, "fingerprint": fingerprint}), encoding="utf-8"
        )
    except OSError:
        pass  # Nicht schreiben zu können darf das Melden nicht verhindern.


def _send(text: str, *, sender: Any) -> bool:
    if sender is not None:
        return bool(sender(text))
    from app.alerts.notify import send_operator_notification

    return bool(asyncio.run(send_operator_notification(text)))


def dispatch_health_notification(
    report: Any,
    *,
    lookback_hours: int,
    notify_cooldown_minutes: float,
    console: _Console,
    state_file: Path | None = None,
    reassert_minutes: float = _DEFAULT_REASSERT_MINUTES,
    now_ts: float | None = None,
    sender: Any = None,
) -> bool:
    """Health-Befund senden, wenn er NEU ist. Gibt zurück, ob gesendet wurde.

    ``notify_cooldown_minutes`` bleibt in der Signatur, damit der CLI-Aufruf und
    die Struktur-Tests unverändert bleiben; die Entscheidung trägt jetzt der
    Fingerprint. ``now_ts`` und ``sender`` sind injizierbar, damit das
    Zeitverhalten ohne Wanduhr und ohne echten Versand prüfbar ist — Lehre aus
    den Zeitbomben-Tests, die die CI vier Tage lahmgelegt haben.
    """
    path = state_file or HEALTH_NOTIFY_STATE_FILE
    now = time.time() if now_ts is None else now_ts

    fingerprint = issues_fingerprint(report.issues)
    last_ts, last_fingerprint = _read_state(path)

    if not fingerprint:
        # Gesundes System. Nur wenn zuvor etwas gemeldet WAR, gibt es Entwarnung —
        # sonst schweigt der Kanal, wie es sich gehört.
        if not last_fingerprint:
            return False
        ok = _send(_RECOVERY_TEXT, sender=sender)
        if ok:
            _write_state(path, now_ts=now, fingerprint="")
            console.print("[green]Telegram recovery notice sent.[/green]")
        return bool(ok)

    if last_ts is not None:
        elapsed_min = (now - last_ts) / 60.0
        if last_fingerprint is None:
            # Altzustand: nur ein Zeitstempel, kein Fingerprint. Ohne ihn ist
            # NICHT entscheidbar, ob der Befund derselbe ist -- also gilt bis
            # zum ersten Schreiben des neuen Zustands konservativ weiter das
            # alte Zeit-Gate. Ein Uebergang darf keine Meldung erfinden und
            # keine verschlucken.
            if elapsed_min < notify_cooldown_minutes:
                console.print(
                    f"[dim]Notification suppressed (cooldown: "
                    f"{elapsed_min:.1f}/{notify_cooldown_minutes}min).[/dim]"
                )
                return False
        else:
            window = reassert_minutes_for(report.issues, default_minutes=reassert_minutes)
            if fingerprint == last_fingerprint and elapsed_min < window:
                console.print(
                    f"[dim]Notification suppressed (unchanged finding, "
                    f"{elapsed_min:.0f}/{window:.0f}min till re-assert).[/dim]"
                )
                return False

    # Geänderte Befundmenge ist Neuigkeit und wartet auf keinen Cooldown: der
    # frühere Zeit-Cooldown hätte einen NEUEN kritischen Befund bis zu 30 Minuten
    # zurückgehalten. Genau das darf nicht passieren.
    text = build_health_alert_text(report, lookback_hours=lookback_hours)
    ok = _send(text, sender=sender)
    if ok:
        _write_state(path, now_ts=now, fingerprint=fingerprint)
        console.print("[green]Telegram notification sent.[/green]")
    else:
        console.print("[yellow]Telegram not configured or send failed.[/yellow]")
    return bool(ok)
