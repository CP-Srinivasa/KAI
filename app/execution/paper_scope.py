"""Epochen-Scoping der Paper-Closes — eine Regel, an einer Stelle (G3).

Der Quality-Endpunkt filterte an zwei Stellen mit zwei Reihenfolgen: die
Quarantäne-Liste entstand **vor** dem Epochenschnitt, alles daneben danach. Im
Payload standen dadurch 24 Closes / 82.404,06 USD Lifetime-Quarantäne neben
287 Closes / −241,89 USD Epochen-PnL — im selben Objekt, ohne Kennzeichnung. Wer
beides nebeneinander las, verglich Ungleiches, und genau daraus entstand die
Vermutung, die Quarantäne erkläre die gemessene Differenz. Sie erklärt sie nicht.

Die Regel steht deshalb hier, nicht im Router: eine Funktion, die eine Liste von
Close-Zeilen in genau vier disjunkte Töpfe teilt, und ein Payload-Block, der
jedem Topf sein Etikett gibt.

**Warum die Reihenfolge egal ist** — und warum niemand sie je wieder prüfen muss:
Epochenzugehörigkeit und Korruptionssignatur sind unabhängige Prädikate. Ihre
Konjunktion ist kommutativ; an 693 echten Closes gemessen liefern beide Wege
identisch 287 Zeilen und −241,89 USD. Der Unterschied lag nie in der Reihenfolge,
sondern darin, dass eine der beiden Zahlen den Epochenfilter gar nicht durchlief.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Die Schlüssel, in denen eine Close-Zeile ihren Zeitpunkt tragen kann, in der
#: Reihenfolge ihrer Verlässlichkeit. ``closed_at`` ist der Domänenzeitpunkt;
#: ``updated_at`` steht am Ende, weil es auch eine spätere Berührung sein kann.
CLOSE_TS_KEYS = (
    "closed_at",
    "timestamp_utc",
    "filled_at",
    "executed_at",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True)
class ScopedCloses:
    """Vier disjunkte Töpfe plus die Zählungen, die der Payload ausweisen muss."""

    clean_in_epoch: list[dict[str, Any]]
    quarantined_in_epoch: list[dict[str, Any]]
    quarantined_lifetime: list[dict[str, Any]]
    pre_epoch_excluded: int
    without_timestamp: int

    @property
    def has_epoch(self) -> bool:
        return self.pre_epoch_excluded > 0 or self.without_timestamp > 0


def split_closes(
    rows: list[dict[str, Any]],
    *,
    is_corrupt: Callable[[dict[str, Any]], bool],
    first_ts: Callable[[dict[str, Any], tuple[str, ...]], datetime | None],
    epoch_start: datetime | None,
) -> ScopedCloses:
    """Teile Close-Zeilen in Epoche × Quarantäne — beide Filter, eine Stelle.

    ``epoch_start=None`` heißt: kein Epochen-Reset bekannt. Dann ist die
    Epochen-Sicht die Lifetime-Sicht, und das ist keine Notlösung, sondern der
    ehrliche Zustand — es gibt schlicht keine Grenze zu ziehen.

    Eine Zeile ohne datierbaren Zeitstempel wird unter Epochen-Regime
    **ausgeschlossen**, nicht geraten (fail-closed Richtung Ausschluss), aber
    sichtbar gezählt: ein Performance-Anspruch braucht einen Zeitpunkt.
    """
    corrupt_all = [r for r in rows if is_corrupt(r)]
    clean_all = [r for r in rows if not is_corrupt(r)]

    if epoch_start is None:
        return ScopedCloses(
            clean_in_epoch=clean_all,
            quarantined_in_epoch=corrupt_all,
            quarantined_lifetime=corrupt_all,
            pre_epoch_excluded=0,
            without_timestamp=0,
        )

    clean_in: list[dict[str, Any]] = []
    pre = 0
    undated = 0
    for row in clean_all:
        ts = first_ts(row, CLOSE_TS_KEYS)
        if ts is None:
            undated += 1
            continue
        if ts < epoch_start:
            pre += 1
            continue
        clean_in.append(row)

    corrupt_in = [
        r
        for r in corrupt_all
        if (ts := first_ts(r, CLOSE_TS_KEYS)) is not None and ts >= epoch_start
    ]
    return ScopedCloses(
        clean_in_epoch=clean_in,
        quarantined_in_epoch=corrupt_in,
        quarantined_lifetime=corrupt_all,
        pre_epoch_excluded=pre,
        without_timestamp=undated,
    )


def quarantine_payload(
    scoped: ScopedCloses,
    *,
    pnl: Callable[[dict[str, Any]], float],
    epoch_id: str | None,
) -> dict[str, Any]:
    """Der Payload-Block der Quarantäne — beide Sichten, beide benannt.

    Die unsuffigierten Felder teilen die Population der PnL, die neben ihnen
    steht. Die Lifetime-Sicht verschwindet nicht, sie bekommt nur einen Namen:
    nichts wird gelöscht, es wird nur aufgehört, Ungleiches gleich zu benennen.
    """
    from app.research.decomposition import decompose_mean

    epoch_values = [pnl(r) for r in scoped.quarantined_in_epoch]
    lifetime_values = [pnl(r) for r in scoped.quarantined_lifetime]
    return {
        "paper_quarantined_pnl_usd": round(sum(epoch_values), 2),
        "paper_quarantined_closes": len(scoped.quarantined_in_epoch),
        "paper_quarantined_pnl_usd_lifetime": round(sum(lifetime_values), 2),
        "paper_quarantined_closes_lifetime": len(scoped.quarantined_lifetime),
        "paper_quarantine_scope": f"epoch:{epoch_id}" if epoch_id else "lifetime",
        # Direktive 2026-08-08: kein Aggregat ohne Zerlegung. Hier ist sie keine
        # Formsache -- die Lifetime-Quarantaene summiert 82.404 USD aus 24 Zeilen,
        # von denen 17 gewinnen und 4 verlieren. Ohne leave-one-out sagt die Summe
        # nicht, ob ein einziges Phantom sie traegt.
        "paper_quarantined_decomposition": {
            "epoch": decompose_mean(
                epoch_values,
                labels=[str(r.get("symbol") or "unknown") for r in scoped.quarantined_in_epoch],
            ),
            "lifetime": decompose_mean(
                lifetime_values,
                labels=[str(r.get("symbol") or "unknown") for r in scoped.quarantined_lifetime],
            ),
        },
    }


def fills_scope_label(epoch_id: str | None, *, has_cutoff: bool) -> str:
    """Das Etikett für ``paper_fills_with_pnl`` — es folgt der eigenen Rechnung.

    Der Wert entsteht über die epochengefilterte Liste; das Etikett behauptete
    ``cutoff_since``. Ein Etikett, das der eigenen Rechnung widerspricht, ist
    schlimmer als gar keines: es macht die Zahl unzitierbar, ohne dass es
    auffällt.
    """
    if epoch_id:
        return f"epoch:{epoch_id}"
    return "cutoff_since" if has_cutoff else "lifetime"


__all__ = [
    "CLOSE_TS_KEYS",
    "ScopedCloses",
    "fills_scope_label",
    "quarantine_payload",
    "split_closes",
]
