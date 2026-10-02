"""14-Tage-Verlauf: Kosten je Anbieter, Aufrufe, Token, Budgetende je Tag (UTC)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from app.ai.spend import row_ts, row_usage

#: Gruende, mit denen das Budget einen Aufruf ablehnt, weil der Topf leer ist.
_ERSCHOEPFT: Final = ("normal_budget_exhausted", "alert_reserve_exhausted", "daily_limit_reached")


@dataclass
class DayRow:
    day: str
    cost_by_provider: dict[str, float] = field(default_factory=dict)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    budget_exhausted_at: str | None = None


def _ist_sperre(row: dict[str, Any]) -> bool:
    wert = row.get("budget_decision")
    return (
        isinstance(wert, str)
        and wert.startswith("reject:")
        and wert.split(":", 1)[1] in _ERSCHOEPFT
    )


def budget_exhausted_at(rows: Iterable[dict[str, Any]], *, day: str) -> datetime | None:
    zeiten = [
        ts
        for row in rows
        if _ist_sperre(row) and (ts := row_ts(row)) is not None and ts.date().isoformat() == day
    ]
    return min(zeiten) if zeiten else None


def daily(rows: Iterable[dict[str, Any]], *, now: datetime, days: int = 14) -> list[DayRow]:
    tage: dict[str, DayRow] = {}
    for i in range(days):
        tag = (now.date() - timedelta(days=i)).isoformat()
        tage[tag] = DayRow(day=tag)
    for row in rows:
        ts = row_ts(row)
        if ts is None:
            continue
        eintrag = tage.get(ts.date().isoformat())
        if eintrag is None:
            continue
        if _ist_sperre(row) and (
            eintrag.budget_exhausted_at is None
            or ts < datetime.fromisoformat(eintrag.budget_exhausted_at)
        ):
            eintrag.budget_exhausted_at = ts.isoformat()
        eintrag.calls += 1
        ein, aus = row_usage(row)
        eintrag.input_tokens += ein
        eintrag.output_tokens += aus
        kosten = row.get("cost_usd")
        if isinstance(kosten, (int, float)) and not isinstance(kosten, bool):
            name = str(row.get("actual_provider") or row.get("provider") or "unbekannt")
            eintrag.cost_by_provider[name] = round(
                eintrag.cost_by_provider.get(name, 0.0) + float(kosten), 6
            )
    return sorted(tage.values(), key=lambda t: t.day)


__all__ = ["DayRow", "budget_exhausted_at", "daily"]
