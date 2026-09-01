"""Zustell-Wache fuer Alarme — ein Alarm, der nicht ankommt, ist kein Alarm.

**Der gemessene Befund (G6, Sprint aus KMA-20260827; A4-017).** In 30 Tagen
lieferte der Premium-Healthcheck **19 FAIL-Alarme**, davon erreichten **15 den
Operator nie** (78,9 %). Der Grund ist enger als „der Kanal faellt still aus":
*alle 15* Fehlschlaege tragen `Temporary failure in name resolution` und liegen
im naechtlichen Fenster **01:04–01:25** (12.08. 4x, 13.08. 10x, 28.08. 1x) —
eine DNS-Luecke der naechtlichen Neueinwahl. Kein Token fehlte (0 Faelle), kein
HTTP-Fehler. Und es gab keinen Wiederversuch: der Alarm war nach einem
`urlopen`-Fehler endgueltig verloren, weil die Zustellung nirgends
protokolliert wurde.

**Was dieses Modul macht.** Es fuehrt einen Zustell-Strom
(``alert_delivery_audit.jsonl``): jeder Sendeversuch wird mit Ergebnis und
Grund angehaengt, ein misslungener bleibt als **ausstehend** stehen, bis ein
spaeterer Lauf denselben Alarm (gleicher `digest`) zustellt. Daraus folgen zwei
Dinge, die es vorher nicht gab: der naechste Lauf kann den Alarm **nachliefern**
(``pending_payloads``), und der Health-Check bekommt einen **eigenen Befund mit
Grund**, wenn etwas ausstehend bleibt (``classify_delivery``).

Der stuendliche ``heartbeat``-Satz ist Absicht: ohne ihn waere der Strom in
ruhigen Wochen leer, und ein toter Zustellpfad saehe aus wie ein ruhiger. Er
macht die Freshness-Zeile im Health-Check zu einer echten Aussage.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: Der Zustell-Strom. Konsument: ``classify_delivery`` (hier) ueber die Sonde
#: ``_check_alert_delivery`` in ``app/alerts/health_check.py``.
DELIVERY_STREAM = "alert_delivery_audit.jsonl"

#: Abstand zweier Lebenszeichen. Der Healthcheck-Timer laeuft jede Minute; ein
#: Satz pro Stunde haelt den Strom lebendig, ohne ihn zuzumuellen (24/Tag).
HEARTBEAT_INTERVAL_S = 3600

#: Ab wann ein unzugestellter Alarm ein Befund ist. Der Timer laeuft jede
#: Minute, ein Nachliefer-Versuch kostet Sekunden — 5 Minuten sind drei
#: gescheiterte Nachlieferungen, nicht ein Netzwerk-Schluckauf.
UNDELIVERED_WARN_MIN = 5

#: Ab wann er kritisch ist. Die gemessene DNS-Luecke dauerte am 13.08. genau
#: 10 Minuten (01:04–01:13); 30 Minuten sind das Dreifache und damit ausserhalb
#: dessen, was die Neueinwahl erklaert.
UNDELIVERED_CRITICAL_MIN = 30


@dataclass(frozen=True)
class DeliveryVerdict:
    """Urteil ueber den Zustellpfad."""

    status: str  # "ok" | "warning" | "critical"
    undelivered: int
    oldest_age_min: float | None
    reasons: tuple[str, ...]
    attempts_seen: int


def payload_digest(text: str) -> str:
    """Stabile Kennung eines Alarmtexts — bindet Versuch und Nachlieferung."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def record_attempt(
    path: Path,
    *,
    now: datetime,
    channel: str,
    alert_kind: str,
    delivered: bool,
    reason: str,
    text: str,
    attempt: int = 1,
) -> dict[str, Any]:
    """Haenge einen Sendeversuch an den Zustell-Strom (append-only)."""
    record = {
        "ts": now.astimezone(UTC).isoformat(),
        "record_type": "attempt",
        "channel": channel,
        "alert_kind": alert_kind,
        "delivered": bool(delivered),
        "reason": reason,
        "digest": payload_digest(text),
        "attempt": int(attempt),
        # Der Text bleibt im Satz, sonst kann ein spaeterer Lauf nicht
        # nachliefern. Er enthaelt den Health-Report, keine Geheimnisse.
        "text": text,
    }
    _append(path, record)
    return record


def record_heartbeat(path: Path, *, now: datetime, channel: str) -> dict[str, Any]:
    """Lebenszeichen des Zustellpfads (macht die Freshness-Zeile aussagekraeftig)."""
    record = {
        "ts": now.astimezone(UTC).isoformat(),
        "record_type": "heartbeat",
        "channel": channel,
    }
    _append(path, record)
    return record


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_records(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Lies den Zustell-Strom; defekte Zeilen werden uebersprungen, nie geraten."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                out.append(data)
    return out[-limit:] if limit else out


def heartbeat_due(records: list[dict[str, Any]], *, now: datetime, interval_s: int) -> bool:
    """True, wenn seit dem letzten Lebenszeichen ``interval_s`` vergangen sind."""
    last: datetime | None = None
    for rec in records:
        if rec.get("record_type") != "heartbeat":
            continue
        ts = _parse_ts(rec.get("ts"))
        if ts and (last is None or ts > last):
            last = ts
    if last is None:
        return True
    return (now - last).total_seconds() >= interval_s


def pending_payloads(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Alarme, deren letzter Versuch misslang — Kandidaten fuer die Nachlieferung.

    Gruppiert ueber ``digest``: ein spaeterer geglueckter Versuch loescht den
    frueheren misslungenen aus der Liste. Reihenfolge = Reihenfolge im Strom.
    """
    latest: dict[str, dict[str, Any]] = {}
    for rec in records:
        if rec.get("record_type") != "attempt":
            continue
        digest = str(rec.get("digest", ""))
        if not digest:
            continue
        previous = latest.get(digest)
        if previous is None:
            latest[digest] = rec
            continue
        prev_ts = _parse_ts(previous.get("ts"))
        cur_ts = _parse_ts(rec.get("ts"))
        if cur_ts is None or (prev_ts is not None and cur_ts >= prev_ts):
            latest[digest] = rec
    return [rec for rec in latest.values() if not rec.get("delivered")]


def classify_delivery(
    records: list[dict[str, Any]],
    *,
    now: datetime,
    warn_min: int = UNDELIVERED_WARN_MIN,
    critical_min: int = UNDELIVERED_CRITICAL_MIN,
) -> DeliveryVerdict:
    """Urteile ueber den Zustellpfad — ohne je zu werfen."""
    attempts = [r for r in records if r.get("record_type") == "attempt"]
    pending = pending_payloads(records)
    if not pending:
        return DeliveryVerdict(
            status="ok",
            undelivered=0,
            oldest_age_min=None,
            reasons=(),
            attempts_seen=len(attempts),
        )

    ages: list[float] = []
    reasons: list[str] = []
    for rec in pending:
        ts = _parse_ts(rec.get("ts"))
        if ts is not None:
            ages.append((now - ts).total_seconds() / 60.0)
        reason = str(rec.get("reason") or "unknown")
        if reason not in reasons:
            reasons.append(reason)

    oldest = max(ages) if ages else None
    if oldest is None:
        # Kein lesbarer Zeitstempel — das ist selbst ein Befund, kein "ok".
        status = "warning"
    elif oldest >= critical_min:
        status = "critical"
    elif oldest >= warn_min:
        status = "warning"
    else:
        status = "ok"

    return DeliveryVerdict(
        status=status,
        undelivered=len(pending),
        oldest_age_min=oldest,
        reasons=tuple(reasons),
        attempts_seen=len(attempts),
    )


def prune_delivered(records: list[dict[str, Any]], *, now: datetime, keep_days: int = 30) -> int:
    """Wie viele Saetze aelter als ``keep_days`` sind (nur Auskunft, kein Loeschen).

    Der Strom wird hier bewusst NICHT beschnitten — Rotation ist Sache des
    bestehenden Rotationspfads, und ein Zustellbeleg zu loeschen waere genau
    die Art stiller Korrektur, die dieses Modul verhindern soll.
    """
    cutoff = now - timedelta(days=keep_days)
    count = 0
    for rec in records:
        ts = _parse_ts(rec.get("ts"))
        if ts is not None and ts < cutoff:
            count += 1
    return count
