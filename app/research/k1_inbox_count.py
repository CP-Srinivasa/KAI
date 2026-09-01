"""K1 ``00c75a76a2b0e78b`` — mechanische Zählung des Posteingangs.

Existiert, damit die letzte offene Prä-Registrierung nicht dadurch entschieden
wird, dass jemand eine Tabelle liest. Der Evaluator ist committet, **bevor** die
Daten gesehen sind; was er nicht kennt, wirft er, statt es als „zählt nicht" zu
verbuchen.

Versiegelter Text (``artifacts/research/prereg_ledger.jsonl``, unverändert):

    PASS-check for the K1 single-channel signal-TÜV offering. >=5 qualified
    inbound inquiries (written, each naming a concrete signal provider to audit
    OR a clear payment-willingness signal) within 30 days of publishing the
    anonymized K1 pilot report. Fewer than 5 means KILL this offering (no
    further unsolicited channel audits; truth-infra focus unchanged). NOT
    counted: spam, signal-provider self-promotion without an audit request,
    internal inquiries.

Vier Dinge, die dort NICHT stehen — und die dieser Zähler deshalb NICHT tut:

1. **Kein Kanalfilter.** Der Seal sagt „inbound inquiries", nicht „E-Mail an
   Adresse X". Reichweitenzahlen der Angebotsseite sind Kontext, keine Schranke.
2. **``unsolicited`` ist keine Einschlussregel.** Das Wort kommt genau einmal
   vor, in der FOLGEklausel nach FAIL („no further *unsolicited* channel
   audits"). Antworten auf eigene Ansprache nachträglich auszuschließen wäre
   eine Kriteriumsänderung nach Sicht der Daten; sie werden gezählt und separat
   ausgewiesen.
3. **Keine Deduplizierung auf Personen.** Gezählt werden ``inquiries``. Die
   Thread-Zahl steht daneben, treibt aber nie das Verdikt.
4. **Kein INCONCLUSIVE-Zweig.** Der Seal kennt ``>=5`` und ``<5``. Eine kleine
   Population ergibt FAIL; dass sie strukturell zu klein war, ist ein Vermerk
   im Verdikt und nicht sein Ersatz.

Das Fenster ist die einzige Stelle mit echter Unschärfe: der Seal ankert auf der
**Veröffentlichung des Pilotreports**, nicht auf der Registrierung, und nennt
kein Datum. Zwei Lesarten sind belegbar — der Datenstand-Seal der öffentlichen
Seite (2026-07-02) und die auf ebendieser Seite **vor Fensterschluss**
veröffentlichte Operationalisierung („registered 2026-07-04 with a 30-day
horizon, so its window closed on 2026-08-03"). Beide werden getrennt gerechnet;
nur wenn die Verdikte auseinanderfallen, ist überhaupt etwas zu entscheiden.
Eine Vereinigung der beiden Fenster (33 Tage) wäre KEIN Seal-Fenster.

Read-only Forschungswerkzeug: kein Gate, keine Order, kein Kapital.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: Aus dem Seal: ``sample_size_target = 5``, ``>=5`` bestanden.
SEAL_THRESHOLD = 5

#: Aus dem Seal: ``horizon = 30d``.
_WINDOW_DAYS = 30

#: Die zwei belegbaren Anker der Fenstermitte. Schlüssel ist das Startdatum.
WINDOW_CANDIDATES: dict[str, str] = {
    # Datenstand-Seal der öffentlichen /paper-Seite (append-only snapshot).
    "2026-07-02": "2026-07-02T00:00:00+00:00",
    # Öffentlich VOR Fensterschluss deklariert; identisch zum Reifeblick-Spec.
    "2026-07-04": "2026-07-04T12:51:11.469459+00:00",
}

#: Geschlossene Menge. Eine unbekannte Klasse ist ein Fehler, keine Null —
#: sonst entscheidet ein Tippfehler das Verdikt in die milde Richtung.
SENDER_CLASSES = frozenset(
    {
        "extern_menschlich",
        "signalanbieter_selbstpr",
        "intern_operator",
        "operator",
        "newsletter",
        "system_notification",
        "provider_api",
        "rechnung_receipt",
        "spam",
    }
)

#: Die drei im Seal AUSDRÜCKLICH nicht gezählten Kategorien. Sie stechen ein
#: ``qualifiziert=ja`` — eine Markierung kann den versiegelten Text nicht
#: aufheben. Der Widerspruch wird gemeldet, nicht verschluckt.
SEAL_EXCLUDED_CLASSES = frozenset({"spam", "signalanbieter_selbstpr", "intern_operator"})

_YES_NO = {"ja": True, "nein": False}

_COLUMNS = (
    "datum_utc",
    "richtung",
    "absenderklasse",
    "betreff",
    "antwort_auf_eigene_ansprache",
    "thread_id",
    "qualifiziert",
)


class K1CountError(ValueError):
    """Eine Zeile, die der Zähler nicht sicher versteht — nie stillschweigend."""


@dataclass(frozen=True)
class InboxRow:
    """Eine anonymisierte Posteingangszeile. Keine Texte, keine Adressen."""

    at: datetime
    inbound: bool
    sender_class: str
    subject: str
    reply_to_own_outreach: bool
    thread_id: str
    qualified: bool
    payment_intent: bool | None


def _flag(raw: str, field: str, line_no: int) -> bool:
    value = raw.strip().lower()
    if value not in _YES_NO:
        raise K1CountError(f"Zeile {line_no}: {field} muss ja|nein sein, war {raw.strip()!r}")
    return _YES_NO[value]


def parse_rows(text: str) -> list[InboxRow]:
    """Pipe-getrennte Zeilen einlesen — fail-closed bei allem Unklaren.

    Sieben Pflichtspalten, eine optionale achte (``zahlungsabsicht``). Zeilen,
    die mit ``#`` beginnen, und Leerzeilen werden übersprungen.
    """
    rows: list[InboxRow] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) not in (len(_COLUMNS), len(_COLUMNS) + 1):
            raise K1CountError(
                f"Zeile {line_no}: {len(_COLUMNS)} oder {len(_COLUMNS) + 1} Spalten erwartet, "
                f"{len(parts)} gefunden"
            )
        stamp = parts[0].replace("Z", "+00:00")
        try:
            at = datetime.fromisoformat(stamp)
        except ValueError as exc:
            raise K1CountError(f"Zeile {line_no}: datum_utc unlesbar ({parts[0]!r})") from exc
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)

        direction = parts[1].strip().lower()
        if direction not in ("in", "out"):
            raise K1CountError(f"Zeile {line_no}: richtung muss in|out sein, war {parts[1]!r}")

        sender_class = parts[2].strip().lower()
        if sender_class not in SENDER_CLASSES:
            raise K1CountError(
                f"Zeile {line_no}: absenderklasse {parts[2]!r} ist unbekannt. "
                f"Erlaubt: {', '.join(sorted(SENDER_CLASSES))}"
            )

        payment_intent: bool | None = None
        if len(parts) == len(_COLUMNS) + 1:
            payment_intent = _flag(parts[7], "zahlungsabsicht", line_no)

        rows.append(
            InboxRow(
                at=at.astimezone(UTC),
                inbound=direction == "in",
                sender_class=sender_class,
                subject=parts[3],
                reply_to_own_outreach=_flag(parts[4], "antwort_auf_eigene_ansprache", line_no),
                thread_id=parts[5],
                qualified=_flag(parts[6], "qualifiziert", line_no),
                payment_intent=payment_intent,
            )
        )
    return rows


def _window_bounds(key: str) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(WINDOW_CANDIDATES[key])
    return start, start + timedelta(days=_WINDOW_DAYS)


def _count_one_window(rows: list[InboxRow], key: str) -> dict[str, Any]:
    start, end = _window_bounds(key)
    inside = [r for r in rows if start <= r.at <= end]
    inbound = [r for r in inside if r.inbound]

    # Die versiegelte Einheit: qualifizierte EINGEHENDE Anfragen, abzüglich der
    # drei ausdrücklich nicht gezählten Kategorien.
    qualified = [r for r in inbound if r.qualified and r.sender_class not in SEAL_EXCLUDED_CLASSES]
    sealed_count = len(qualified)

    intents: int | str
    if any(r.payment_intent is None for r in inbound):
        # Keine erfundene Null: fehlt die Spalte auch nur bei einer Zeile, ist
        # die Zahl nicht gemessen.
        intents = "NOT_MEASURED"
    else:
        intents = sum(1 for r in inbound if r.payment_intent)

    return {
        "WINDOW_START": start.isoformat(),
        "WINDOW_END": end.isoformat(),
        "INBOUND_MESSAGES_TOTAL": len(inbound),
        "INBOUND_HUMAN_INQUIRIES": sum(1 for r in inbound if r.sender_class == "extern_menschlich"),
        "QUALIFIED_INBOUND_INQUIRIES": sealed_count,
        # Näherung, kein Personen-Identifikator — siehe ``distinct_contacts_note``.
        "DISTINCT_QUALIFIED_THREADS": len({r.thread_id for r in qualified}),
        "QUALIFIED_RESPONSES_TO_OWN_OUTREACH": sum(1 for r in qualified if r.reply_to_own_outreach),
        "QUALIFIED_UNSOLICITED_INQUIRIES": sum(1 for r in qualified if not r.reply_to_own_outreach),
        "PAYMENT_INTENTS": intents,
        "EXCLUDED_BY_SEAL_CATEGORY": sum(
            1 for r in inbound if r.sender_class in SEAL_EXCLUDED_CLASSES
        ),
        "OUTBOUND_IGNORED": len(inside) - len(inbound),
        # Nur die versiegelte Einheit treibt das Verdikt.
        "SEALED_COUNT": sealed_count,
        "THRESHOLD": SEAL_THRESHOLD,
        "VERDICT": "MET" if sealed_count >= SEAL_THRESHOLD else "NOT_MET",
    }


def count_rows(rows: list[InboxRow]) -> dict[str, Any]:
    """Beide Seal-Fenster getrennt auszählen, plus die Diagnostik daneben."""
    windows = {key: _count_one_window(rows, key) for key in WINDOW_CANDIDATES}
    verdicts = {w["VERDICT"] for w in windows.values()}

    # Ein ``qualifiziert=ja`` in einer vom Seal ausgeschlossenen Kategorie ist
    # ein Widerspruch zwischen Erhebung und Vertrag. Der Vertrag gewinnt — aber
    # der Widerspruch wird benannt, sonst verschwindet er im Aggregat.
    conflicts = [
        {
            "at": r.at.isoformat(),
            "thread_id": r.thread_id,
            "sender_class": r.sender_class,
            "reason": "vom Seal ausdrücklich nicht gezählt, aber als qualifiziert markiert",
        }
        for r in rows
        if r.inbound and r.qualified and r.sender_class in SEAL_EXCLUDED_CLASSES
    ]

    starts = [datetime.fromisoformat(v) for v in WINDOW_CANDIDATES.values()]
    outer_start, outer_end = min(starts), max(starts) + timedelta(days=_WINDOW_DAYS)

    return {
        "prereg_id": "00c75a76a2b0e78b",
        "claim_name": "k1_channel_audit_resonance",
        "rows_parsed": len(rows),
        "windows": windows,
        "windows_agree_on_verdict": len(verdicts) == 1,
        "out_of_both_windows": sum(1 for r in rows if not (outer_start <= r.at <= outer_end)),
        "conflicts": conflicts,
        "distinct_contacts_note": (
            "DISTINCT_QUALIFIED_THREADS zaehlt thread_id, nicht Personen — der Export "
            "traegt keinen Personen-Identifikator. Die Zahl ist Diagnostik; der Seal "
            "zaehlt 'inquiries', eine Dedup-Regel wurde nicht versiegelt."
        ),
        "seal_notes": [
            "Kein Kanalfilter: der Seal sagt 'inbound inquiries', nicht 'E-Mail'.",
            "'unsolicited' steht nur in der Folgeklausel nach FAIL und ist keine "
            "Einschlussregel — Antworten auf eigene Ansprache zaehlen mit.",
            "Kein INCONCLUSIVE-Zweig: eine kleine Population ergibt NOT_MET.",
            "Reichweiten- und L402-Zahlen sind Diagnostik und ersetzen die Zaehlung nicht.",
        ],
    }


__all__ = [
    "SEAL_EXCLUDED_CLASSES",
    "SEAL_THRESHOLD",
    "SENDER_CLASSES",
    "WINDOW_CANDIDATES",
    "InboxRow",
    "K1CountError",
    "count_rows",
    "parse_rows",
]
