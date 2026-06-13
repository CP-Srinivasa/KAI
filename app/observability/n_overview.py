"""SSOT für die fünf „resolved/n"-Kennzahlen des Systems (Dali 2026-06-13).

Operator-Befund: Es existieren **fünf verschiedene „n"**, die unterschiedliche
Pipelines zählen und alle ähnlich „resolved" heißen — genau die UX-Falle, über
die der Edge-Re-Run selbst gestolpert ist. Diese Funktion bündelt sie an EINER
Stelle mit Klartext-Label, Quelle und „misst …", und hebt das einzige n hervor,
das fürs **#167-Edge-Gate** zählt: ``resolved_real`` (Re-Run-Schwelle n≥100).

Zuordnung (Stand 2026-06-13):

  * ``resolved_real``               — shadow_candidate_resolved.jsonl, nur
    source=autonomous_generator, Canary hart raus → IC/Brier des Generators.
    DAS ist das n fürs #167-Gate.
  * ``total_resolved``              — paper_execution_audit, geschlossene Trades
    NACH Implausibilitäts-Filter → realisierte EV/PnL aller Quellen.
  * ``resolved_ledger_lines``       — komplettes Shadow-resolved-Ledger inkl. der
    Nicht-Real-Candidates, die fürs Gate geskippt werden.
  * ``resolved_directional_alerts`` — News-/Alert-Outcome-Pipeline (D-227):
    News-Treffer, nichts mit Trading.
  * ``paper_trades_all_time``       — geschlossene Paper-Trades ungefiltert
    (reiner Trade-Zähler über alle Zeit).

Rein (keine I/O): der Endpoint liest die Artefakte und reicht die Rohwerte
hinein, sodass Zuordnung + Labels an EINER testbaren Stelle gepflegt sind und
nie wieder zwischen Seiten auseinanderdriften.
"""

from __future__ import annotations

from typing import Any

# Operator-Entscheidung (Memory kai_167_watchdog_first_edge_measurement_20260611
# + kai_edge_cohort_key_fix_20260613): der Edge-Report wird erst bei n≳100 als
# aussagekräftig re-gefahren. Das ist die OPERATIVE Re-Run-Schwelle, nicht die
# interne Report-Mindeststichprobe (generator_edge.gate_config.min_resolved=30).
RE_RUN_THRESHOLD = 100


def _ratio_pct(value: int | None, threshold: int) -> float | None:
    if value is None or threshold <= 0:
        return None
    return round(100.0 * value / threshold, 1)


def build_n_overview(
    *,
    resolved_real: int | None,
    resolved_ledger_lines: int | None,
    total_resolved: int | None,
    paper_trades_all_time: int | None,
    resolved_directional_alerts: int | None,
    gate_threshold: int = RE_RUN_THRESHOLD,
) -> dict[str, Any]:
    """Bündelt die fünf n in EINE Operator-lesbare Struktur (pure).

    Jeder Wert darf ``None`` sein (unlesbares/leeres Artefakt) — das Frontend
    zeigt dann ehrlich „—" statt einer erfundenen Zahl. Das Gate-n trägt
    zusätzlich Schwelle, Fortschritt und Suffizienz, weil es das einzige ist,
    auf das der Operator hin handelt.
    """
    ratio = _ratio_pct(resolved_real, gate_threshold)

    # Wie viele Ledger-Zeilen sind Nicht-Real (werden fürs Gate geskippt)?
    non_real_note: str | None = None
    if resolved_ledger_lines is not None and resolved_real is not None:
        skipped = max(0, resolved_ledger_lines - resolved_real)
        non_real_note = f"{skipped} Nicht-Real (geskippt)"

    gate = {
        "key": "resolved_real",
        "label": "resolved_real",
        "value": resolved_real,
        "threshold": gate_threshold,
        "ratio_pct": ratio,
        "sufficient": resolved_real is not None and resolved_real >= gate_threshold,
        "source": "shadow_candidate_resolved.jsonl",
        "filter": "source=autonomous_generator · Canary hart raus",
        "measures": "IC / Brier des Generators — #167 Edge-Gate",
        "watch_hint": "Das ist das n, das du beobachtest.",
    }

    others = [
        {
            "key": "total_resolved",
            "label": "realisierte Trades (alle Quellen)",
            "value": total_resolved,
            "source": "trading generator-edge · total_resolved (impl.≤0.40)",
            "measures": "realisierte EV/PnL aller Quellen · nach Implausibilitäts-Filter",
            "note": None,
        },
        {
            "key": "resolved_ledger_lines",
            "label": "Shadow-Ledger gesamt",
            "value": resolved_ledger_lines,
            "source": "shadow-report · raw_count (shadow_candidate_resolved.jsonl)",
            "measures": "komplettes resolved-Ledger inkl. Nicht-Real-Candidates",
            "note": non_real_note,
        },
        {
            "key": "resolved_directional_alerts",
            "label": "News-Outcomes (D-227)",
            "value": resolved_directional_alerts,
            "source": "alert_outcomes.jsonl · hit+miss (D-227)",
            "measures": "entschiedene directional News-Outcomes — nichts mit Trading",
            "note": None,
        },
        {
            "key": "paper_trades_all_time",
            "label": "Paper-Trades all-time",
            "value": paper_trades_all_time,
            "source": "paper_execution_audit.jsonl · position_closed+partial",
            "measures": "reiner Trade-Zähler über alle Zeit (ungefiltert)",
            "note": None,
        },
    ]

    return {
        "gate": gate,
        "others": others,
        "trap_note": (
            "Alle heißen „resolved“ und sehen ähnlich aus — aber nur "
            "resolved_real zählt fürs #167-Edge-Gate. Die anderen messen andere "
            "Pipelines (nicht falsch, nur nicht das Gate)."
        ),
    }


__all__ = ["RE_RUN_THRESHOLD", "build_n_overview"]
