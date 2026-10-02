"""LLM-Sparfenster: Stunden und Quellen, deren Analysen kaum 4-h-Kurssignal tragen.

Das feste Tagesbudget (``APP_AI_BUDGET_DAILY_USD``) endet taeglich zwischen 12:46 und
14:51 UTC -- mitten in der Spitze. Gemessen 02.10.2026 an 2830 gpt-4o-Analysen vom
07.-30.09.: Ertrag je Analyse (Prio >= 7 UND Kursbewegung ueber der ueblichen Schwankung
binnen 4 h) nachts 02-04 UTC 0-4,6 %, YouTube 4,8 %, in der Spitze 12-14 UTC 12-16 %.
Was hier gespart wird, reicht das Budget an die Spitze weiter; Dokumente im Fenster
bekommen die Regelanalyse wie nach Budgetende.

Wie das Relevanz-Gate: ``off`` (Standard) aendert nichts, ``shadow`` protokolliert nur,
``enforce`` spart den Aufruf. Fail-safe: jede Unklarheit heisst ``off``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

MODES: Final = frozenset({"off", "shadow", "enforce"})


@dataclass(frozen=True)
class Sparfenster:
    mode: str = "off"
    hours_utc: frozenset[int] = field(default_factory=frozenset)
    #: casefolded Quellennamen (``source_name``)
    sources: frozenset[str] = field(default_factory=frozenset)

    def verdict(self, *, source: str | None, at: datetime) -> str | None:
        """Grund, das Dokument ohne Modell zu analysieren -- oder ``None``."""
        if self.mode == "off":
            return None
        if source and source.strip().casefold() in self.sources:
            return f"quelle={source.strip()}"
        stunde = (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC).hour
        if stunde in self.hours_utc:
            return f"stunde_utc={stunde:02d}"
        return None


def resolve_sparfenster(explicit: Sparfenster | None = None) -> Sparfenster:
    """Das konfigurierte Fenster (``SOURCE_LLM_SPARFENSTER_*``), sonst ``off``."""
    if explicit is not None:
        return explicit
    try:
        from app.core.settings import get_settings

        s = get_settings().sources
        mode = str(s.llm_sparfenster_mode).strip().lower()
        if mode not in MODES:
            return Sparfenster()
        return Sparfenster(
            mode=mode,
            hours_utc=frozenset(h for h in s.llm_sparfenster_hours_utc if 0 <= int(h) <= 23),
            sources=frozenset(q.strip().casefold() for q in s.llm_sparfenster_sources if q.strip()),
        )
    except Exception:  # noqa: BLE001 -- ein Konfigurationsfehler darf nie Aufrufe sparen
        return Sparfenster()


__all__ = ["MODES", "Sparfenster", "resolve_sparfenster"]
