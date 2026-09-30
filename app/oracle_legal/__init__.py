"""Öffentliche Oracle-Seiten „Leistungen & Bedingungen“ und „Hilfe & Beschwerde“ (D-291 E1/E6/E9).

Operator-Entscheid 2026-09-30: zwei öffentlich erreichbare Seiten, vor der Zahlung
verlinkt, ohne Kundenkonto. Online gehen sie erst nach Freigabe durch den Anwalt:

- ``APP_LN_ORACLE_LEGAL_PUBLISHED`` ist der Schalter (Default aus).
- Solange eine Vorlage noch ``[[OFFEN: …]]`` enthält, bleibt alles unveröffentlicht,
  egal wie der Schalter steht. Kein Rechtstext mit Lücke geht an Kunden.
- Die Vorschau für Operator und Anwalt liegt hinter dem Dashboard-Schutz.

Meldungen und Widerrufe landen append-only in ``artifacts/oracle/oracle_cases.jsonl``.
Der Betreiber bekommt eine Benachrichtigung mit Vorgangsnummer, aber ohne
personenbezogene Angaben. Bearbeitung und Abschluss dokumentiert er mit
``scripts/oracle_case.py`` (``beantwortet`` / ``erledigt``). Der Health-Check warnt,
wenn ein Vorgang die Serviceziele (2 bzw. 7 Werktage) überschreitet.
"""

from __future__ import annotations

import html
import json
import logging
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.file_lock import append_lock

logger = logging.getLogger(__name__)

VERSION = "0.1-Entwurf"
STAND = "30.09.2026"
PAGES = ("bedingungen", "hilfe")
CASES_PATH = Path("artifacts/oracle/oracle_cases.jsonl")
# Freiwillige Serviceziele von der Hilfe-Seite, in Werktagen (Mo–Fr).
FIRST_ANSWER_WORKDAYS = 2
RESOLUTION_WORKDAYS = 7
CASE_STATES = ("beantwortet", "erledigt")

_DIR = Path(__file__).resolve().parent
_OPEN = re.compile(r"\[\[OFFEN:\s*(.*?)\]\]", re.S)
_BANNER = (
    '<p class="draft">ENTWURF – nicht freigegeben und nicht verbindlich. '
    "Diese Vorschau ist nur für Betreiber und Anwalt bestimmt.</p>"
)


# ---------------------------------------------------------------- Seiten


def _template(page: str) -> str:
    if page not in PAGES:
        raise KeyError(page)
    return (_DIR / f"{page}.html").read_text(encoding="utf-8")


def open_items() -> list[str]:
    """Alle noch offenen Punkte aus allen Vorlagen, ohne Doppelte (leer = freigabefähig)."""
    items: list[str] = []
    for page in PAGES:
        for match in _OPEN.findall(_template(page)):
            item = " ".join(match.split())
            if item not in items:
                items.append(item)
    return items


def is_published(flag: bool) -> bool:
    """Öffentlich nur mit Schalter UND ohne offene Punkte."""
    return bool(flag) and not open_items()


def render(page: str, *, price_sat: int, access_min: int, invoice_min: int, preview: bool) -> str:
    values = {
        "VERSION": VERSION,
        "STAND": STAND,
        "PREIS_SAT": f"{int(price_sat):,}".replace(",", "."),
        "ZUGANG_MIN": str(int(access_min)),
        "RECHNUNG_MIN": str(int(invoice_min)),
    }
    out = _template(page)
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", html.escape(value))
    out = out.replace("{{ROBOTS}}", "noindex, nofollow" if preview else "index, follow")
    out = out.replace("{{ENTWURF_BANNER}}", _BANNER if preview else "")
    return _OPEN.sub(lambda m: f'<mark class="offen">OFFEN: {m.group(1)}</mark>', out)


def pre_payment_notice(access_min: int) -> str:
    """Kurzhinweis unmittelbar vor der Zahlung (Entwurf v0.2, Teil D)."""
    return (
        "Ihre Zahlung schaltet diesen Oracle-Bereich bis zum angezeigten Ablaufzeitpunkt (UTC) "
        f"frei, mindestens {access_min} Minuten nach Ihrer Zahlung. Bis dahin können Sie beliebig "
        "oft abrufen und bei einem technischen Ausfall mit demselben Zugangsschlüssel ohne erneute "
        "Zahlung wiederholen; jeder Abruf liefert den aktuellen Datenstand. Danach endet der "
        "Zugang. Ihre gesetzlichen Rechte bei Nichtlieferung oder mangelhafter Leistung bleiben "
        "bestehen. Bedingungen: /oracle/bedingungen · Hilfe: /oracle/hilfe"
    )


LINK_HEADER = '</oracle/bedingungen>; rel="terms-of-service", </oracle/hilfe>; rel="help"'


# ---------------------------------------------------------------- Formulare

PROBLEMS = {
    "nicht_erhalten": "Bezahlt, nichts erhalten",
    "fehlerhaft": "Daten fehlerhaft",
    "doppelt": "Doppelt bezahlt",
    "erneute_pruefung": "Erneute Prüfung",
    "anderes": "Anderes",
}
AREAS = {"", "onchain-facts", "fee-series", "verdicts", "timestamp"}
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9-]{1,80}$")
# Ein L402-Token (Macaroon, base64) oder ein Preimage/Schlüssel ist ein langer
# Block ohne Leerzeichen. Die Seite bittet ausdrücklich, so etwas nicht zu senden.
_SECRET_BLOB = re.compile(r"L402\s|[A-Za-z0-9+/=_-]{90,}")


class FormError(ValueError):
    """Ungültige Eingabe; die Nachricht darf dem Absender gezeigt werden."""


def _text(form: dict[str, str], key: str, limit: int) -> str:
    value = (form.get(key) or "").strip()
    if len(value) > limit:
        raise FormError(f"Das Feld „{key}“ ist zu lang (höchstens {limit} Zeichen).")
    return value


def _no_secrets(*values: str) -> None:
    for value in values:
        if _SECRET_BLOB.search(value):
            raise FormError(
                "Bitte keinen Zugangsschlüssel, keine Schlüssel und keine Seeds senden. "
                "Der Payment-Hash (64 Zeichen) als Referenz genügt."
            )


def _email(form: dict[str, str]) -> str:
    email = _text(form, "email", 254)
    if not _EMAIL.match(email):
        raise FormError("Bitte eine gültige E-Mail-Adresse für die Antwort angeben.")
    return email


def _reference(form: dict[str, str], *, required: bool) -> str:
    ref = _text(form, "referenz", 80)
    if not ref:
        if required:
            raise FormError("Bitte die Auftragsreferenz oder den Payment-Hash angeben.")
        return ""
    if not _REFERENCE.match(ref):
        raise FormError("Die Referenz darf nur Buchstaben, Ziffern und Bindestriche enthalten.")
    return ref


def parse_report(form: dict[str, str]) -> dict[str, str]:
    """Meldung prüfen und auf das Nötige reduzieren."""
    if form.get("website"):
        raise FormError("Die Meldung konnte nicht angenommen werden.")
    problem = (form.get("problem") or "").strip()
    if problem not in PROBLEMS:
        raise FormError("Bitte die Art des Problems auswählen.")
    area = (form.get("bereich") or "").strip()
    if area not in AREAS:
        raise FormError("Unbekannter Bereich.")
    description = _text(form, "beschreibung", 2000)
    if len(description) < 10:
        raise FormError("Bitte das Problem kurz beschreiben (mindestens 10 Zeichen).")
    reference = _reference(form, required=False)
    when = _text(form, "zeitpunkt", 40)
    _no_secrets(description, when)
    return {
        "problem": problem,
        "referenz": reference,
        "bereich": area,
        "zeitpunkt": when,
        "beschreibung": description,
        "email": _email(form),
    }


def parse_withdrawal(form: dict[str, str]) -> dict[str, str]:
    """Widerruf prüfen: welcher Vertrag, Kontakt für die Bestätigung."""
    if form.get("website"):
        raise FormError("Der Widerruf konnte nicht angenommen werden.")
    name = _text(form, "name", 120)
    _no_secrets(name)
    return {"referenz": _reference(form, required=True), "name": name, "email": _email(form)}


# ---------------------------------------------------------------- Vorgänge


def new_case_id(now: datetime) -> str:
    return f"KAI-O-{now:%y%m%d}-{secrets.token_hex(3).upper()}"


def record_case(
    kind: str, fields: dict[str, str], *, now: datetime, path: Path | None = None
) -> dict[str, Any]:
    """Vorgang append-only speichern. Fehler steigen auf: ohne Beleg keine Bestätigung."""
    case = {
        "case_id": new_case_id(now),
        "kind": kind,
        "received_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "terms_version": VERSION,
        "status": "eingegangen",
        **fields,
    }
    target = path or CASES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with append_lock(target), target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    return case


def mark_case(
    case_id: str, state: str, note: str, *, now: datetime, path: Path | None = None
) -> None:
    """Bearbeitungsschritt anhängen (``beantwortet`` / ``erledigt``), nie überschreiben."""
    if state not in CASE_STATES:
        raise ValueError(f"unbekannter Zustand {state!r}")
    target = path or CASES_PATH
    if case_id not in {c["case_id"] for c in read_cases(target)}:
        raise KeyError(case_id)
    record = {
        "case_id": case_id,
        "status": state,
        "at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "note": note.strip(),
    }
    with append_lock(target), target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_cases(path: Path | None = None) -> list[dict[str, Any]]:
    """Vorgänge mit ihrem letzten Bearbeitungsstand. Eine kaputte Zeile ist ein Fehler."""
    target = path or CASES_PATH
    if not target.exists():
        return []
    cases: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Zeile {number} nicht lesbar") from exc
        if "kind" in rec:
            cases[rec["case_id"]] = {**rec, "answered_at": None, "resolved_at": None}
        elif rec.get("case_id") in cases:
            key = "answered_at" if rec.get("status") == "beantwortet" else "resolved_at"
            cases[rec["case_id"]][key] = rec.get("at")
    return list(cases.values())


def workdays_between(start: datetime, end: datetime) -> int:
    """Volle Werktage (Mo–Fr) nach dem Eingangstag bis einschließlich ``end``."""
    days, day = 0, start.date()
    while day < end.date():
        day = day.fromordinal(day.toordinal() + 1)
        days += day.weekday() < 5
    return days


def overdue_cases(now: datetime, path: Path | None = None) -> list[str]:
    """Vorgänge, die ein freiwilliges Serviceziel überschritten haben (für den Health-Check)."""
    late = []
    for case in read_cases(path):
        if case["resolved_at"]:
            continue
        received = datetime.fromisoformat(case["received_at"].replace("Z", "+00:00"))
        age = workdays_between(received, now)
        if not case["answered_at"] and age > FIRST_ANSWER_WORKDAYS:
            late.append(f"{case['case_id']} ohne Antwort seit {age} Werktagen")
        elif age > RESOLUTION_WORKDAYS:
            late.append(f"{case['case_id']} nicht erledigt seit {age} Werktagen")
    return late


def operator_text(case: dict[str, Any]) -> str:
    """Benachrichtigung ohne personenbezogene Angaben."""
    what = (
        "Widerruf"
        if case["kind"] == "widerruf"
        else PROBLEMS.get(case.get("problem", ""), "Meldung")
    )
    return (
        f"KAI Oracle: neuer Vorgang {case['case_id']} ({what}). Details im Fall-Log (cases.jsonl)."
    )


_PLAIN_CSS = (
    "body{font:16px/1.6 system-ui,sans-serif;max-width:40rem;margin:0 auto;padding:1.5rem 1rem}"
)


def receipt_html(case: dict[str, Any]) -> str:
    """Eingangsbestätigung zum Speichern: Nummer, Zeit, Inhalt der Erklärung."""
    labels = {
        "problem": "Problem",
        "referenz": "Referenz",
        "bereich": "Bereich",
        "zeitpunkt": "Zeitpunkt",
        "beschreibung": "Beschreibung",
        "name": "Name",
        "email": "E-Mail",
    }
    rows = []
    for key, label in labels.items():
        value = case.get(key)
        if not value:
            continue
        shown = PROBLEMS.get(value, value) if key == "problem" else value
        rows.append(f"<tr><th>{label}</th><td>{html.escape(str(shown))}</td></tr>")
    title = "Widerruf eingegangen" if case["kind"] == "widerruf" else "Meldung eingegangen"
    lead = (
        "Wir haben Ihren Widerruf mit folgendem Inhalt erhalten."
        if case["kind"] == "widerruf"
        else "Wir haben Ihre Meldung erhalten. Ein Mensch prüft sie; die erste persönliche "
        "Antwort erhalten Sie in der Regel binnen zwei Werktagen."
    )
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f"<title>{title} – {html.escape(case['case_id'])}</title>"
        f"<style>{_PLAIN_CSS}"
        "th{text-align:left;padding:.3rem .6rem .3rem 0;vertical-align:top;color:#666}"
        "td{padding:.3rem 0;white-space:pre-wrap;word-break:break-word}</style></head><body>"
        f"<h1>{title}</h1><p>{lead}</p>"
        f"<p><strong>Vorgangsnummer: {html.escape(case['case_id'])}</strong><br>"
        f"Eingang: {html.escape(case['received_at'])} (UTC)</p>"
        f"<table>{''.join(rows)}</table>"
        "<p>Bitte speichern oder drucken Sie diese Bestätigung (Strg+S / Strg+P) und geben Sie "
        "bei Rückfragen die Vorgangsnummer an.</p>"
        '<p><a href="/oracle/hilfe">Zurück zu Hilfe &amp; Beschwerde</a></p></body></html>'
    )


def error_html(message: str) -> str:
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="robots" content="noindex, nofollow"><title>Eingabe prüfen</title>'
        f"<style>{_PLAIN_CSS}</style></head><body>"
        f"<h1>Bitte Eingabe prüfen</h1><p>{html.escape(message)}</p>"
        '<p><a href="/oracle/hilfe">Zurück zum Formular</a></p></body></html>'
    )


# ---------------------------------------------------------------- Missbrauchsschutz


@dataclass
class RateLimiter:
    """Einfache Grenze je Absender-Kennwert und insgesamt (im Speicher, pro Prozess)."""

    per_key: int = 5
    total: int = 60
    window_s: float = 3600.0

    def __post_init__(self) -> None:
        self._keys: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def allow(self, key: str, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        cutoff = t - self.window_s
        while self._all and self._all[0] < cutoff:
            self._all.popleft()
        hits = self._keys.setdefault(key, deque())
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self.per_key or len(self._all) >= self.total:
            return False
        hits.append(t)
        self._all.append(t)
        return True


__all__ = [
    "AREAS",
    "CASES_PATH",
    "LINK_HEADER",
    "PAGES",
    "PROBLEMS",
    "STAND",
    "VERSION",
    "FormError",
    "RateLimiter",
    "error_html",
    "is_published",
    "open_items",
    "operator_text",
    "parse_report",
    "parse_withdrawal",
    "pre_payment_notice",
    "receipt_html",
    "record_case",
    "render",
]
