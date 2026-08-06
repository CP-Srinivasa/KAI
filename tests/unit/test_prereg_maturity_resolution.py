"""Terminale Truth-Verdikte müssen die Reifeanzeige schließen.

Voll-Audit 2026-08-06: ND-v2 war als ``verdict`` in der verifizierbaren
Truth-Kette attestiert (seq 73), ``prereg-maturity`` meldete denselben Claim
dennoch weiter als ``JUDGEABLE``. Das lädt zu einer zweiten Auswertung eines
bereits terminal entschiedenen Claims ein.

Die Tests pinnen die fachliche Grenze: Nur eine vollständig verifizierte
Truth-Kette darf einen Claim schließen. Beschädigte, widersprüchliche oder
nicht maschinenlesbar terminale Evidenz führt sichtbar zu HOLD und niemals zu
einer erneuten Handlungsaufforderung.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.cli.commands.research_verdicts import _render_maturity_state
from app.research.prereg_maturity import (
    STATE_EVAL_CHECK_DUE,
    STATE_RESOLUTION_HOLD,
    STATE_RESOLVED,
    TRUTH_LEDGER_RELPATH,
    compute_maturity,
)
from app.truth.attestation import compute_attestation
from app.truth.ledger import append_attestation

_PID = "b20ef1487ccba99d"
_SPEC: dict[str, Any] = {
    "name": "directional_news_hedged_1d_drift",
    "prereg_id": _PID,
    "kind": "deadline",
    "since_utc": "2026-07-02T05:43:32+00:00",
    "window_end_utc": "2026-08-06T00:00:00+00:00",
    "n_target": 0,
}
_NOW = datetime(2026, 8, 6, 4, 0, tzinfo=UTC)


def _truth_path(root: Path) -> Path:
    return root / TRUTH_LEDGER_RELPATH


def _attest(root: Path, verdict: str, *, subject: str | None = None) -> None:
    payload = {
        "schema_version": 1,
        "prereg_id": _PID,
        "hypothesis": _SPEC["name"],
        "verdict": verdict,
    }
    append_attestation(
        "verdict",
        subject or compute_attestation(payload)["hash"],
        payload,
        path=_truth_path(root),
        mirror_audit=False,
        attested_at_utc="2026-08-06T03:00:00+00:00",
    )


async def _row(root: Path, *, spec: dict[str, Any] = _SPEC) -> dict[str, Any]:
    rows = await compute_maturity(
        None,  # type: ignore[arg-type] -- deadline-Spec berührt die Session nie
        specs=(spec,),
        artifacts_dir=root,
        now=_NOW,
    )
    assert len(rows) == 1
    return rows[0]


async def test_verified_terminal_verdict_closes_judgeable_claim(tmp_path: Path) -> None:
    _attest(tmp_path, "FAILED at registered gate (p_min, cost_clearing)")

    row = await _row(tmp_path)

    assert row["state"] == STATE_RESOLVED
    assert row["state_source"] == "truth_ledger"
    assert row["due"] is False
    assert row["resolution"]["verdict_class"] == "NOT_MET"
    assert row["resolution"]["seq"] == 1
    assert "ABGESCHLOSSEN" in _render_maturity_state(row)
    assert "Verdikt-Kette fahren" not in _render_maturity_state(row)


async def test_explicit_insufficient_n_does_not_close_claim(tmp_path: Path) -> None:
    _attest(tmp_path, "INSUFFICIENT_N: sample below target")

    row = await _row(tmp_path)

    assert row["state"] == STATE_EVAL_CHECK_DUE
    assert row["due"] is True
    assert row["resolution"] is None


async def test_unknown_verdict_wording_is_visible_hold_not_a_rerun(tmp_path: Path) -> None:
    _attest(tmp_path, "ROBUSTNESS ANNEX — manual review required")

    row = await _row(tmp_path)

    assert row["state"] == STATE_RESOLUTION_HOLD
    assert row["due"] is False
    assert row["resolution"]["status"] == "unclassified"
    assert "HOLD" in _render_maturity_state(row)
    assert "Verdikt-Kette fahren" not in _render_maturity_state(row)


@pytest.mark.parametrize("verdict", ["METADATA refreshed", "FAILOVER rehearsal completed"])
async def test_terminal_prefix_requires_a_real_token_boundary(tmp_path: Path, verdict: str) -> None:
    """MET/FAIL inside another word is ambiguous evidence, never a terminal result."""
    _attest(tmp_path, verdict)

    row = await _row(tmp_path)

    assert row["state"] == STATE_RESOLUTION_HOLD
    assert row["due"] is False
    assert row["resolution"]["status"] == "unclassified"


async def test_conflicting_terminal_verdicts_are_visible_hold(tmp_path: Path) -> None:
    _attest(tmp_path, "FAILED at registered gate")
    _attest(tmp_path, "PASSED at registered gate")

    row = await _row(tmp_path)

    assert row["state"] == STATE_RESOLUTION_HOLD
    assert row["due"] is False
    assert row["resolution"]["status"] == "conflict"
    assert row["resolution"]["verdict_classes"] == ["MET", "NOT_MET"]


async def test_tampered_truth_chain_holds_instead_of_recommending_action(tmp_path: Path) -> None:
    _attest(tmp_path, "FAILED at registered gate")
    path = _truth_path(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["verdict"] = "PASSED after tamper"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    row = await _row(tmp_path)

    assert row["state"] == STATE_RESOLUTION_HOLD
    assert row["due"] is False
    assert row["resolution"]["status"] == "invalid_ledger"
    assert row["resolution"]["errors"]


async def test_missing_truth_ledger_preserves_existing_maturity_semantics(tmp_path: Path) -> None:
    row = await _row(tmp_path)

    assert row["state"] == STATE_EVAL_CHECK_DUE
    assert row["state_source"] == "window"
    assert row["due"] is True
    assert row["resolution"] is None


async def test_unrelated_terminal_verdict_does_not_close_claim(tmp_path: Path) -> None:
    other = dict(_SPEC, prereg_id="another-prereg")
    _attest(tmp_path, "FAILED at registered gate")

    row = await _row(tmp_path, spec=other)

    assert row["state"] == STATE_EVAL_CHECK_DUE
    assert row["due"] is True


async def test_untrusted_verdict_subject_is_visible_hold(tmp_path: Path) -> None:
    """A manual kind=verdict append is not a canonical attested report."""
    _attest(tmp_path, "FAILED at registered gate", subject="manual-operator-label")

    row = await _row(tmp_path)

    assert row["state"] == STATE_RESOLUTION_HOLD
    assert row["due"] is False
    assert row["resolution"]["status"] == "untrusted_attestation"
    assert row["resolution"]["seqs"] == [1]
