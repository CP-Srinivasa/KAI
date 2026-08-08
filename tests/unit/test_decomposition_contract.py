"""VERTRAG: kein Evaluator-Aggregat ohne Zerlegung (Direktive 2026-08-08).

Dieser Test ist die Durchsetzung, nicht die Dokumentation. Er läuft über
**alle** registrierten Evaluatoren; wer einen neuen hinzufügt und den
``decomposition``-Block vergisst, wird hier rot — nicht erst, wenn Monate
später jemand ein verdecktes Aggregat versiegelt hat.

Hintergrund: die Regel existierte im Projekt bereits, aber nur für EINE
Kennzahl (``edge_validation_gate`` ``outlier_robust``). Die Quoten-Evaluatoren
hatten sie nicht — deshalb konnte am 2026-08-08 eine Konkordanz von 66,7 %
solide aussehen, obwohl sie fast vollständig von der miss-Seite getragen war.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.quote_evals import (
    evaluate_execution_translation,
    evaluate_hit_to_win_conversion,
    evaluate_technical_paper_precision,
)

REG = "2026-07-29T09:00:00+00:00"
AFTER = "2026-07-30T12:00:00+00:00"

# Jeder Evaluator, der ein Aggregat produziert, MUSS hier stehen. Ein neuer
# Evaluator ohne Eintrag fällt im Vollständigkeits-Test unten auf.
EVALUATORS = {
    "quote_eval/tech_precision/v1": evaluate_technical_paper_precision,
    "quote_eval/exec_translation/v1": evaluate_execution_translation,
    "quote_eval/hit_to_win_conversion/v2": evaluate_hit_to_win_conversion,
}


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def artifacts(tmp_path: Path) -> tuple[Path, Path]:
    """Minimale, aber für ALLE Evaluatoren auswertbare Population."""
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "paper_execution_audit.jsonl"
    docs = [f"technical_paper_{i}" for i in range(6)]
    _write(
        outcomes,
        [
            {
                "document_id": d,
                "outcome": "hit" if i % 2 == 0 else "miss",
                "annotated_at": AFTER,
                "price_source": "binance" if i < 4 else "coingecko",
            }
            for i, d in enumerate(docs)
        ],
    )
    rows: list[dict] = []
    for i, d in enumerate(docs):
        rows.append(
            {
                "event_type": "order_filled",
                "document_id": d,
                "filled_at": AFTER,
                "timestamp_utc": AFTER,
            }
        )
        rows.append(
            {
                "event_type": "position_closed",
                "document_id": d,
                "timestamp_utc": AFTER,
                "signal_source": "technical_paper",
                "trade_pnl_usd": 5.0 if i % 3 == 0 else -5.0,
            }
        )
    _write(audit, rows)
    return outcomes, audit


@pytest.mark.parametrize("schema", sorted(EVALUATORS))
def test_every_evaluator_emits_a_decomposition_block(
    schema: str, artifacts: tuple[Path, Path]
) -> None:
    """Pflichtfeld ``decomposition`` — ohne Ausnahme, auch bei geschlossenen Claims."""
    outcomes, audit = artifacts
    result = EVALUATORS[schema](
        outcomes_path=outcomes, exec_audit_path=audit, registered_at_utc=REG
    )

    assert result["schema"] == schema
    assert "decomposition" in result, (
        f"{schema} liefert kein 'decomposition' — ein Aggregat ohne Zerlegung "
        f"darf nicht berichtet werden (Direktive 2026-08-08)."
    )
    dec = result["decomposition"]
    for key in ("n", "rate", "by_group", "concentration", "leave_one_group_out_worst", "flags"):
        assert key in dec, f"{schema}: decomposition ohne Pflichtfeld {key!r}"
    assert isinstance(dec["flags"], list)


@pytest.mark.parametrize("schema", sorted(EVALUATORS))
def test_decomposition_groups_cover_the_aggregate(
    schema: str, artifacts: tuple[Path, Path]
) -> None:
    """Die Gruppen müssen das Aggregat vollständig aufteilen — kein stiller Rest.

    Eine Zerlegung, die weniger Einheiten zählt als das Aggregat, verdeckt
    genau das, was sie offenlegen soll.
    """
    outcomes, audit = artifacts
    result = EVALUATORS[schema](
        outcomes_path=outcomes, exec_audit_path=audit, registered_at_utc=REG
    )
    dec = result["decomposition"]
    if dec["n"] == 0:
        pytest.skip("leere Population in dieser Fixture — Randfall separat getestet")

    assert sum(c["n"] for c in dec["by_group"].values()) == dec["n"]
    assert abs(sum(c["share_of_units"] for c in dec["by_group"].values()) - 1.0) < 0.01


def test_registry_covers_every_quote_eval_in_the_module() -> None:
    """Vollständigkeit: ein neuer Evaluator darf sich nicht am Vertrag vorbeischleichen.

    Wer ``evaluate_*`` in ``quote_evals`` ergänzt, ohne ihn hier einzutragen,
    wird rot — die Registry oben ist damit nachweislich vollständig.
    """
    import app.research.quote_evals as qe

    found = {
        name for name in dir(qe) if name.startswith("evaluate_") and callable(getattr(qe, name))
    }
    registered = {fn.__name__ for fn in EVALUATORS.values()}
    missing = found - registered
    assert not missing, (
        f"Evaluator(en) ohne Zerlegungs-Vertrag: {sorted(missing)}. "
        f"In EVALUATORS eintragen und 'decomposition' liefern."
    )
