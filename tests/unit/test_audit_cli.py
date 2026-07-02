"""Smoke tests for the audit CLI subapp (trail / verify / list)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from app.audit.structured_reasoning import (
    PHASE_CONFIDENCE_CHANGE,
    PHASE_INVALIDATION,
    ReasoningJournal,
)
from app.cli.commands.audit import audit_app

runner = CliRunner()


# --------------------------------------------------------------------- helpers


def _seed_journal(path: Path) -> tuple[str, str]:
    rj = ReasoningJournal(path)
    rj.log_step(
        decision_id="dec_alpha",
        phase=PHASE_CONFIDENCE_CHANGE,
        actor="ActiveCalibrator",
        rationale_summary="alpha squash",
        confidence_before=0.85,
        confidence_after=0.75,
    )
    rj.log_step(
        decision_id="dec_beta",
        phase=PHASE_INVALIDATION,
        actor="SignalGenerator.bayes_gate",
        rationale_summary="beta rejected: confidence below threshold",
    )
    return "dec_alpha", "dec_beta"


# ============================================================================
# trail
# ============================================================================


def test_trail_renders_table_for_existing_decision(tmp_path: Path):
    journal = tmp_path / "reasoning.jsonl"
    alpha, _beta = _seed_journal(journal)
    result = runner.invoke(
        audit_app,
        ["trail", alpha, "--journal", str(journal)],
        # Force a wide terminal so rich doesn't truncate column content
        env={"COLUMNS": "200"},
    )
    assert result.exit_code == 0, result.output
    assert "Reasoning trail" in result.output
    # rich may still wrap inside narrow columns — check for stable substrings
    assert "ActiveCalibr" in result.output
    assert "0.8500" in result.output and "0.7500" in result.output


def test_trail_unknown_decision_exits_with_error(tmp_path: Path):
    journal = tmp_path / "empty.jsonl"
    journal.touch()
    result = runner.invoke(audit_app, ["trail", "dec_nonexistent", "--journal", str(journal)])
    assert result.exit_code == 1
    assert "No data found" in result.output


def test_trail_without_steps_but_no_other_streams(tmp_path: Path):
    """Empty journal + no decision_journal/bayes_audit → exit 1 with message."""
    journal = tmp_path / "empty.jsonl"
    result = runner.invoke(audit_app, ["trail", "dec_anything", "--journal", str(journal)])
    assert result.exit_code == 1


# ============================================================================
# verify
# ============================================================================


def test_verify_clean_chain_exits_zero(tmp_path: Path):
    journal = tmp_path / "reasoning.jsonl"
    _seed_journal(journal)
    result = runner.invoke(audit_app, ["verify", "--journal", str(journal)])
    assert result.exit_code == 0
    assert "ok" in result.output


def test_verify_tampered_chain_exits_one(tmp_path: Path):
    journal = tmp_path / "reasoning.jsonl"
    _seed_journal(journal)
    # Tamper with the first row
    lines = journal.read_text(encoding="utf-8").splitlines()
    import json

    payload = json.loads(lines[0])
    payload["rationale_summary"] = "tampered"
    lines[0] = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # Append a fresh row so the chain has a successor that catches the change
    rj = ReasoningJournal(journal)
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rj.log_step(
        decision_id="dec_gamma",
        phase=PHASE_CONFIDENCE_CHANGE,
        actor="x",
        rationale_summary="seal it",
    )
    # Now manipulate again to break the chain
    lines = journal.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["rationale_summary"] = "tampered again"
    lines[0] = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = runner.invoke(audit_app, ["verify", "--journal", str(journal)])
    assert result.exit_code == 1
    assert "BROKEN" in result.output


# ============================================================================
# list
# ============================================================================


def test_list_recent_decisions(tmp_path: Path):
    journal = tmp_path / "reasoning.jsonl"
    _seed_journal(journal)
    result = runner.invoke(audit_app, ["list", "--journal", str(journal)])
    assert result.exit_code == 0
    assert "dec_alpha" in result.output
    assert "dec_beta" in result.output


def test_list_filtered_by_phase(tmp_path: Path):
    journal = tmp_path / "reasoning.jsonl"
    _seed_journal(journal)
    result = runner.invoke(
        audit_app,
        ["list", "--journal", str(journal), "--phase", PHASE_INVALIDATION],
    )
    assert result.exit_code == 0
    assert "dec_beta" in result.output
    # alpha was confidence_change, not invalidation
    assert "dec_alpha" not in result.output


def test_list_empty_journal(tmp_path: Path):
    journal = tmp_path / "empty.jsonl"
    result = runner.invoke(audit_app, ["list", "--journal", str(journal)])
    assert result.exit_code == 0
    assert "No reasoning steps" in result.output
