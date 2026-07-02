"""SSOT für die fünf „resolved/n"-Kennzahlen des Systems (Dali 2026-06-13).

Operator-Befund: Es existieren **fünf verschiedene „n"**, die unterschiedliche
Pipelines zählen und alle ähnlich „resolved" heißen — genau die UX-Falle, über
die der Edge-Re-Run selbst gestolpert ist. Diese Funktion bündelt sie an EINER
Stelle mit Klartext-Label, Quelle und „misst …", und hebt die ZWEI offenen
Gates hervor, auf die der Operator hin handelt:

  1. ``resolved_real`` — IC/Brier-Shadow-Sample fürs #167-Gate (Schwelle 100).
  2. ``autonomous_generator``-EV — AUSGEFÜHRTE Generator-Trades fürs EV-Verdict
     (Schwelle = generator-edge ``min_resolved``). DAS ist der bindende Engpass
     (Prioritäts-Doktrin 2026-06-13): nicht das Shadow-n, nicht die all-sources
     ``total_resolved``, sondern wieviele ECHTE Generator-Trades geschlossen sind.

Die übrigen Zähler sind Kontext OHNE offenes Trading-Gate (Diagnose, News-only,
oder bereits erreicht) — sie bekommen ehrliche Status-Tags statt Fake-Balken.

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
    generator_executed: int | None = None,
    generator_threshold: int | None = None,
    generator_verdict: str | None = None,
    generator_ev_bps: float | None = None,
    paper_fills_min: int = 3,
    gate_threshold: int = RE_RUN_THRESHOLD,
) -> dict[str, Any]:
    """Bündelt die fünf n + zwei offene Gates in EINE Struktur (pure).

    Jeder Wert darf ``None`` sein (unlesbares/leeres Artefakt) — das Frontend
    zeigt dann ehrlich „—" statt einer erfundenen Zahl. Die zwei Gates tragen
    Schwelle, Fortschritt und Suffizienz; die Kontext-Zeilen tragen ehrliche
    Status-Tags (erreicht / Diagnose / kein Trading-Gate) — keine Fortschritts-
    balken auf Größen, die gar kein Gate haben.
    """
    # --- Gate 1: IC/Brier-Shadow-Sample (#167) ---
    gate = {
        "key": "resolved_real",
        "label": "resolved_real",
        "value": resolved_real,
        "threshold": gate_threshold,
        "ratio_pct": _ratio_pct(resolved_real, gate_threshold),
        "sufficient": resolved_real is not None and resolved_real >= gate_threshold,
        "source": "shadow-report · real_resolved",
        "filter": "source=autonomous_generator · Canary hart raus",
        "measures": "IC / Brier-Shadow-Sample — #167 Edge-Gate",
        "watch_hint": "Shadow-Sample für die Kalibrierung.",
    }

    # --- Gate 2: EV-Verdict aus AUSGEFÜHRTEN Generator-Trades (der Engpass) ---
    gen_threshold = generator_threshold if generator_threshold and generator_threshold > 0 else 30
    ev_gate = {
        "key": "autonomous_generator_executed",
        "label": "autonomous_generator",
        "value": generator_executed,
        "threshold": gen_threshold,
        "ratio_pct": _ratio_pct(generator_executed, gen_threshold),
        "sufficient": generator_executed is not None and generator_executed >= gen_threshold,
        "verdict": generator_verdict,
        "ev_after_costs_bps": (
            round(generator_ev_bps, 1) if isinstance(generator_ev_bps, (int, float)) else None
        ),
        "source": "trading generator-edge · profile autonomous_generator",
        "measures": "ausgeführte Generator-Trades fürs EV-Verdict",
        "watch_hint": "DAS ist der echte Engpass — nicht die all-sources-Zahl.",
    }

    # --- Kontext-Zeilen: ehrliche Status-Tags, KEINE Fake-Balken ---
    # total_resolved ist all-sources; davon ist nur ``generator_executed`` der
    # echte Generator — der Rest sind unknown/Legacy/Probe-Closes. Das laut zu
    # sagen verhindert die Mini-Falle „115 sieht fertig aus".
    if generator_executed is not None and total_resolved is not None:
        total_tag = f"⚠ davon nur {generator_executed} echter Generator"
        total_tone = "warn"
    else:
        total_tag = None
        total_tone = "muted"

    # Wie viele Ledger-Zeilen sind Nicht-Real (werden fürs Gate geskippt)?
    if resolved_ledger_lines is not None and resolved_real is not None:
        ledger_tag = f"Diagnose · {max(0, resolved_ledger_lines - resolved_real)} Nicht-Real"
    else:
        ledger_tag = "Diagnose · kein Gate"

    if paper_trades_all_time is None:
        paper_tag, paper_tone = None, "muted"
    elif paper_trades_all_time >= paper_fills_min:
        paper_tag, paper_tone = f"Re-Entry ≥{paper_fills_min} ✓", "pos"
    else:
        paper_tag, paper_tone = f"Re-Entry {paper_trades_all_time}/{paper_fills_min}", "warn"

    others = [
        {
            "key": "total_resolved",
            "label": "realisierte Trades (alle Quellen)",
            "value": total_resolved,
            "source": "trading generator-edge · total_resolved (impl.≤0.40)",
            "measures": "realisierte EV/PnL aller Quellen · nach Implausibilitäts-Filter",
            "status_tag": total_tag,
            "status_tone": total_tone,
        },
        {
            "key": "resolved_ledger_lines",
            "label": "Shadow-Ledger gesamt",
            "value": resolved_ledger_lines,
            "source": "shadow-report · raw_count (shadow_candidate_resolved.jsonl)",
            "measures": "komplettes resolved-Ledger inkl. Nicht-Real-Candidates",
            "status_tag": ledger_tag,
            "status_tone": "muted",
        },
        {
            "key": "resolved_directional_alerts",
            "label": "News-Outcomes (D-227)",
            "value": resolved_directional_alerts,
            "source": "alert_outcomes.jsonl · hit+miss (D-227)",
            "measures": "entschiedene directional News-Outcomes — nichts mit Trading",
            "status_tag": "kein Trading-Gate",
            "status_tone": "muted",
        },
        {
            "key": "paper_trades_all_time",
            "label": "Paper-Trades all-time",
            "value": paper_trades_all_time,
            "source": "paper_execution_audit.jsonl · position_closed+partial",
            "measures": "reiner Trade-Zähler über alle Zeit (ungefiltert)",
            "status_tag": paper_tag,
            "status_tone": paper_tone,
        },
    ]

    return {
        "gate": gate,
        "ev_gate": ev_gate,
        "others": others,
        "trap_note": (
            "Zwei offene Gates: resolved_real (IC/Brier-Sample) und die "
            "ausgeführten Generator-Trades (EV-Verdict). Die anderen Zähler "
            "messen andere Pipelines — Diagnose, News-only oder bereits erreicht, "
            "KEIN offenes Trading-Gate."
        ),
    }


__all__ = ["RE_RUN_THRESHOLD", "build_n_overview"]
