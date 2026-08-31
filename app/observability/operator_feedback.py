"""Rueckkante Operator → Truth: hat der Befund je eine Handlung ausgeloest?

**Die nie gestellte Frage (G8, KMA-20260827 / R2-07, R2-12, R2-25).** KAI hat
19 Hypothesen praeregistriert und die eine nie, an der alles haengt: ob es die
Entscheidungen seines einzigen Nutzers messbar verbessert. 141 Stroeme messen
KAI, **null** messen seinen Nutzen. Das ist ein *Instrumentierungs*-Befund, kein
Ergebnisbefund — die Frage ist nicht negativ beantwortet, sie ist nie gestellt.

**Was live gemessen wurde (31.08.2026, ueber alle drei aufbewahrten Fenster des
Request-Audits, 23.03.–31.08.):**

* **643** schreibende Operator-Handlungen ueber acht Operator-Pfade insgesamt
* davon in den letzten **90 Tagen: 44**
* in den letzten **30 Tagen: 0** — an null Tagen

Es war also nie „nie": die Rueckkante ist auf null *abgeklungen*. Dazu schweigt
``operator_commands.jsonl`` seit dem 14.05. (**109** Tage) — nicht weil der
Schreiber kaputt waere: der Telegram-Poller laeuft, ``bot_configured=true``,
``dry_run=false``, acht Aufrufstellen sind verdrahtet. Der Kanal ist lebendig
und **unbenutzt**. Das ist ein anderer Befund als „Instrumentierung fehlt", und
er ist schwerer.

**Was dieses Modul macht.** Es fuehrt genau eine Kette ein, die es nie gab:

    Befund → ``trigger_id`` → Alarmtext → Operator-Klick/Kommando → Request-Audit

Die ID ist der Korrelationsschluessel (Task 1). Ohne sie ist „der Operator hat
nach dem Alarm etwas getan" nicht von „der Operator hat zufaellig etwas getan"
unterscheidbar, und genau daran scheitert jede Nutzenaussage.

**Was es NICHT macht.** Es faellt kein Urteil und behauptet keinen Nutzen. Die
Praeregistrierung (Schwelle S, Datum D, erreichbare Population) ist ein eigener,
spaeterer Schritt — vor der ersten Messung versiegelt, nicht danach.

Bewusst **kein neuer Strom**: geschrieben wird in das bestehende
``artifacts/operator_commands.jsonl``. Einen zweiten Strom neben einen seit 109
Tagen stillen zu stellen, waere genau die Klasse, die das G4-Ratchet verhindern
soll — ein Produzent mehr, ein Konsument nicht.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: Der bestehende Operator-Strom. Kein neuer Strom (siehe Modul-Doku).
OPERATOR_ACTION_STREAM = "operator_commands.jsonl"

#: Praefix der Auslöser-ID. Kurz genug fuer eine Telegram-Zeile, lang genug,
#: dass zwei Befunde desselben Tages nicht kollidieren.
TRIGGER_PREFIX = "trg_"
TRIGGER_HEX_LEN = 12

#: Query-Parameter, ueber den ein Alarm-Link seine Auslöser-ID ins Dashboard
#: traegt. Bewusst ein Parameter und kein Header: ein Operator klickt einen
#: Link, er setzt keinen Header.
TRIGGER_QUERY_PARAM = "t"

#: Fenster, in dem eine Handlung noch als Reaktion auf den Befund gilt.
#: 24 h, weil der Reassert-Takt der lautesten Klasse (P0) 60 min ist und ein
#: Operator nachts schlaeft — kuerzer waere eine Aussage ueber Reaktionszeit,
#: nicht ueber Wirkung.
REACTION_WINDOW_HOURS = 24


@dataclass(frozen=True)
class Reaction:
    """Ein Befund und die Handlung, die ihm folgte (oder eben nicht)."""

    trigger_id: str
    emitted_at: datetime
    acted_at: datetime | None
    channel: str | None
    latency_minutes: float | None

    @property
    def acted(self) -> bool:
        return self.acted_at is not None


@dataclass(frozen=True)
class BackEdgeVerdict:
    """Zerlegung der Rueckkante — nie ein Aggregat allein.

    ``acted`` ohne ``emitted`` waere eine Quote ohne Nenner, und eine Quote
    ohne die Liste der unbeantworteten Befunde waere ein Aggregat ohne
    Zerlegung (Operator-Doktrin 08-08).
    """

    emitted: int
    acted: int
    unanswered: tuple[str, ...]
    median_latency_minutes: float | None
    by_channel: tuple[tuple[str, int], ...]

    @property
    def action_rate(self) -> float | None:
        return None if self.emitted == 0 else self.acted / self.emitted


def new_trigger_id(*, seed: str = "", now: datetime | None = None) -> str:
    """Erzeuge eine Auslöser-ID.

    Mit ``seed`` deterministisch (derselbe Befund zur selben Minute ergibt
    dieselbe ID — damit ein wiederholter Alarm nicht als neuer Ausloeser
    zaehlt), ohne ``seed`` zufaellig.
    """
    if not seed:
        return TRIGGER_PREFIX + secrets.token_hex(TRIGGER_HEX_LEN // 2)
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%d%H%M")
    digest = hashlib.sha256(f"{seed}|{stamp}".encode()).hexdigest()
    return TRIGGER_PREFIX + digest[:TRIGGER_HEX_LEN]


def is_trigger_id(value: object) -> bool:
    """Strenge Form-Pruefung — ein Query-Parameter ist Fremdeingabe.

    Ohne sie landet beliebiger Text aus der URL im Audit-Strom; das ist die
    Klasse „ungeprüfter Eingang vor einem Schreibpfad", die G5 gerade an drei
    anderen Stellen schliesst.
    """
    if not isinstance(value, str) or not value.startswith(TRIGGER_PREFIX):
        return False
    tail = value[len(TRIGGER_PREFIX) :]
    return len(tail) == TRIGGER_HEX_LEN and all(c in "0123456789abcdef" for c in tail)


def record_operator_action(
    path: Path,
    *,
    now: datetime,
    channel: str,
    action: str,
    trigger_id: str | None = None,
    detail: str = "",
) -> dict[str, Any]:
    """Schreibe eine Operator-Handlung in den bestehenden Strom (append-only).

    ``channel`` nennt den Weg (``telegram`` · ``dashboard`` · ``cli``), nicht
    das Ergebnis — welcher Kanal ueberhaupt benutzt wird, ist selbst ein
    Befund: der Telegram-Kanal ist seit 109 Tagen still, obwohl er laeuft.
    """
    record: dict[str, Any] = {
        "timestamp_utc": now.astimezone(UTC).isoformat(),
        "record_type": "operator_action",
        "channel": channel,
        "action": action,
        "detail": detail,
    }
    if trigger_id and is_trigger_id(trigger_id):
        record["trigger_id"] = trigger_id
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def record_trigger_emitted(
    path: Path,
    *,
    now: datetime,
    trigger_id: str,
    channel: str,
    finding_count: int,
    fingerprint: str = "",
) -> dict[str, Any]:
    """Halte fest, DASS ein Befund ausgesendet wurde — die linke Seite der Kette.

    Ohne diesen Satz gaebe es zu einer Handlung keinen Nenner: man saehe, dass
    jemand geklickt hat, aber nicht, wie viele Befunde ungeklickt blieben. Eine
    Quote ohne Nenner ist keine Aussage.
    """
    record = {
        "timestamp_utc": now.astimezone(UTC).isoformat(),
        "record_type": "alert_emitted",
        "trigger_id": trigger_id,
        "channel": channel,
        "finding_count": int(finding_count),
        "fingerprint": fingerprint,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def emitted_triggers(records: list[dict[str, Any]]) -> list[tuple[str, datetime]]:
    """(trigger_id, Sendezeitpunkt) je ausgesendetem Befund, aelteste zuerst."""
    out: list[tuple[str, datetime]] = []
    for rec in records:
        if rec.get("record_type") != "alert_emitted":
            continue
        trigger = rec.get("trigger_id")
        ts = _parse_ts(rec.get("timestamp_utc"))
        if isinstance(trigger, str) and is_trigger_id(trigger) and ts is not None:
            out.append((trigger, ts))
    out.sort(key=lambda pair: pair[1])
    return out


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Lies einen JSONL-Strom; defekte Zeilen werden uebersprungen, nie geraten."""
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
    return out


def correlate(
    emitted: list[tuple[str, datetime]],
    actions: list[dict[str, Any]],
    *,
    window_hours: int = REACTION_WINDOW_HOURS,
) -> list[Reaction]:
    """Ordne jedem ausgesendeten Befund die erste Handlung mit seiner ID zu.

    Eine Handlung zaehlt nur, wenn sie die ``trigger_id`` **traegt** und
    zeitlich NACH dem Befund liegt. Reihenfolge und Identitaet zusammen — eine
    Handlung davor ist keine Reaktion, und eine Handlung ohne ID ist kein
    Beleg, sondern eine Vermutung.
    """
    by_trigger: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        trigger = action.get("trigger_id")
        if isinstance(trigger, str):
            by_trigger.setdefault(trigger, []).append(action)

    reactions: list[Reaction] = []
    for trigger_id, emitted_at in emitted:
        deadline = emitted_at + timedelta(hours=window_hours)
        best: tuple[datetime, str] | None = None
        for action in by_trigger.get(trigger_id, []):
            ts = _parse_ts(action.get("timestamp_utc"))
            if ts is None or ts < emitted_at or ts > deadline:
                continue
            if best is None or ts < best[0]:
                best = (ts, str(action.get("channel") or "unknown"))
        reactions.append(
            Reaction(
                trigger_id=trigger_id,
                emitted_at=emitted_at,
                acted_at=best[0] if best else None,
                channel=best[1] if best else None,
                latency_minutes=(
                    round((best[0] - emitted_at).total_seconds() / 60.0, 1) if best else None
                ),
            )
        )
    return reactions


def summarise(reactions: list[Reaction]) -> BackEdgeVerdict:
    """Zerlegung statt Quote: Nenner, Zaehler, die Unbeantworteten, die Kanaele."""
    acted = [r for r in reactions if r.acted]
    latencies = sorted(r.latency_minutes for r in acted if r.latency_minutes is not None)
    median: float | None = None
    if latencies:
        mid = len(latencies) // 2
        median = (
            latencies[mid]
            if len(latencies) % 2
            else round((latencies[mid - 1] + latencies[mid]) / 2, 1)
        )
    channels: dict[str, int] = {}
    for reaction in acted:
        channels[reaction.channel or "unknown"] = channels.get(reaction.channel or "unknown", 0) + 1
    return BackEdgeVerdict(
        emitted=len(reactions),
        acted=len(acted),
        unanswered=tuple(r.trigger_id for r in reactions if not r.acted),
        median_latency_minutes=median,
        by_channel=tuple(sorted(channels.items(), key=lambda kv: (-kv[1], kv[0]))),
    )
