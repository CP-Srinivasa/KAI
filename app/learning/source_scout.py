"""Source-Scout: pure Logik für die Kandidaten→Vorschläge-Aufbereitung (Phase 3).

Der „Vorbau" (intake-gate + graduation + scheduler) ist die Entscheidungs-/Audit-
Seite — aber niemand FAND Kandidaten; ``monitor/source_proposals.jsonl`` blieb leer.
Der Scout schließt diese Lücke shadow-first: er nimmt eine kuratierte/recherchierte
Kandidatenliste (``monitor/source_candidates_seed.json``), dedupliziert gegen die
bestehende Registry, bewertet (flag-gated) die Feed-Gesundheit und schreibt eine
ranked ``source_proposals.jsonl`` im Schema, das der bestehende Scheduler liest.
Aktiviert NICHTS — Proposals sind nur eine prüfbare Datei.

Dieses Modul ist pur (kein I/O, kein Netzwerk): Dedup, Feed-Health-Parsing (auf
übergebenem Text) und Ranking sind deterministisch + offline-testbar. Outbound-
Probe + Datei-/DB-Zugriff leben im Skript ``scripts/source_scout.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as _xml_fromstring

from app.learning.source_intake_gate import normalize_url

# Feed gilt als „frisch", wenn das jüngste Item höchstens so alt ist; älter →
# linear abfallender Frische-Score bis STALE_DAYS, darüber 0.
_FRESH_DAYS = 2.0
_STALE_DAYS = 30.0
# Volumen-Sättigung: ab so vielen Items zählt mehr nicht als „mehr Substanz".
_VOLUME_SAT = 20.0


@dataclass(frozen=True)
class ScoutProposal:
    """Ein Kandidat auf dem Weg zur Proposal-Zeile (Schema des Schedulers + Health)."""

    url: str
    access: str
    source_type: str
    provider: str | None = None
    notes: str | None = None
    item_count: int | None = None  # None = ungeprobt (Flag aus)
    latest_age_days: float | None = None
    score: float | None = None  # None = ungeprobt → ans Ende der Rangliste

    def to_proposal_row(self) -> dict[str, Any]:
        """Exakt das Schema, das ``source_discovery_scheduler.read_proposals`` liest
        (url/access/source_type/provider/notes) plus Health-Felder (Scheduler
        ignoriert unbekannte Keys)."""
        return {
            "url": self.url,
            "access": self.access,
            "source_type": self.source_type,
            "provider": self.provider,
            "notes": self.notes,
            "item_count": self.item_count,
            "latest_age_days": self.latest_age_days,
            "score": self.score,
        }


def feed_health_score(item_count: int | None, latest_age_days: float | None) -> float | None:
    """Frische×Volumen-Score in [0,1]; ``None`` wenn ungeprobt (item_count None).

    Frische dominiert (0.7) — ein totes Archiv mit vielen alten Items ist wertlos;
    Volumen (0.3) belohnt Substanz bis zur Sättigung.
    """
    if item_count is None:
        return None
    if item_count <= 0:
        return 0.0
    if latest_age_days is None:
        fresh = 0.3  # erreichbar, aber kein Datum lesbar → vorsichtig
    elif latest_age_days <= _FRESH_DAYS:
        fresh = 1.0
    elif latest_age_days >= _STALE_DAYS:
        fresh = 0.0
    else:
        fresh = 1.0 - (latest_age_days - _FRESH_DAYS) / (_STALE_DAYS - _FRESH_DAYS)
    volume = min(1.0, item_count / _VOLUME_SAT)
    return round(0.7 * fresh + 0.3 * volume, 4)


def _local(tag: str) -> str:
    """Tag ohne Namespace (``{http://www.w3.org/2005/Atom}entry`` → ``entry``)."""
    return tag.rsplit("}", 1)[-1].lower()


def _parse_date(text: str | None) -> datetime | None:
    if not text or not text.strip():
        return None
    raw = text.strip()
    # Atom: ISO-8601; RSS: RFC-822. Beide tolerant versuchen.
    try:
        iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return datetime.fromisoformat(iso)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def parse_feed_health(feed_text: str, now_utc: datetime) -> tuple[int, float | None]:
    """Zähle Items (RSS ``item`` / Atom ``entry``) + Alter des jüngsten in Tagen.

    Pur auf übergebenem Text — kein Netzwerk. Liefert ``(0, None)`` wenn kein
    valides Feed/keine Items. Tolerant gegen Namespaces + defekte Einzeldaten.
    """
    try:
        root = _xml_fromstring(feed_text)  # defusedxml: blockt XXE / billion-laughs
    except (ParseError, DefusedXmlException, ValueError):
        return (0, None)
    items = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]
    if not items:
        return (0, None)
    newest: datetime | None = None
    for it in items:
        for child in it:
            if _local(child.tag) in ("pubdate", "published", "updated", "date"):
                dt = _parse_date(child.text)
                if dt is not None and (newest is None or dt > newest):
                    newest = dt
    if newest is None:
        return (len(items), None)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=now_utc.tzinfo)
    age_days = max(0.0, (now_utc - newest).total_seconds() / 86400.0)
    return (len(items), round(age_days, 2))


def dedup_against_registry(
    candidates: list[ScoutProposal],
    *,
    existing_normalized_urls: set[str],
    existing_providers: set[str],
) -> tuple[list[ScoutProposal], list[tuple[str, str]]]:
    """Wirf Kandidaten raus, die schon registriert sind (URL ODER provider-slug)
    oder im Batch doppeln. Liefert (kept, dropped[(url, reason)]). Pur."""
    known_urls = {normalize_url(u) for u in existing_normalized_urls}
    known_prov = {p.strip().lower() for p in existing_providers if p and p.strip()}
    kept: list[ScoutProposal] = []
    dropped: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    seen_prov: set[str] = set()
    for c in candidates:
        nu = normalize_url(c.url)
        if not nu:
            dropped.append((c.url, "malformed_url"))
            continue
        if nu in known_urls or nu in seen_urls:
            dropped.append((c.url, "duplicate_url"))
            continue
        prov = (c.provider or "").strip().lower()
        if prov and (prov in known_prov or prov in seen_prov):
            dropped.append((c.url, f"duplicate_provider:{prov}"))
            continue
        kept.append(c)
        seen_urls.add(nu)
        if prov:
            seen_prov.add(prov)
    return kept, dropped


def score_candidate(c: ScoutProposal) -> ScoutProposal:
    """Setze ``score`` aus item_count/latest_age_days (idempotent, pur)."""
    return replace(c, score=feed_health_score(c.item_count, c.latest_age_days))


def rank_proposals(candidates: list[ScoutProposal]) -> list[ScoutProposal]:
    """Best zuerst: höchster Score; ungeprobt (score None) ans Ende, stabil nach
    provider. Pur."""
    return sorted(
        candidates,
        key=lambda c: (-(c.score if c.score is not None else -1.0), (c.provider or c.url)),
    )
