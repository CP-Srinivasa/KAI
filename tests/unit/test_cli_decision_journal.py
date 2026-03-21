"""CLI tests: research decision-journal-append, decision-journal-summary, loop-cycle-summary."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()

# ── decision-journal-append ───────────────────────────────────────────────────


def test_decision_journal_append_creates_file(tmp_path: Path) -> None:
    journal = str(tmp_path / "dj.jsonl")
    result = runner.invoke(
        app,
        [
            "research",
            "decision-journal-append",
            "BTC/USDT",
            "--thesis",
            "BTC is breaking out on strong ETF inflow data.",
            "--journal-path",
            journal,
        ],
    )
    assert result.exit_code == 0, result.output
    assert Path(journal).exists()


def test_decision_journal_append_writes_valid_jsonl(tmp_path: Path) -> None:
    journal = str(tmp_path / "dj.jsonl")
    runner.invoke(
        app,
        [
            "research",
            "decision-journal-append",
            "ETH/USDT",
            "--thesis",
            "ETH upgrade increases staking yield.",
            "--journal-path",
            journal,
        ],
    )
    lines = Path(journal).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["symbol"] == "ETH/USDT"
    assert "decision_id" in record
    assert "report_type" not in record
    assert isinstance(record["entry_logic"], dict)
    assert record["approval_state"] == "audit_only"


def test_decision_journal_append_prints_decision_id(tmp_path: Path) -> None:
    journal = str(tmp_path / "dj.jsonl")
    result = runner.invoke(
        app,
        [
            "research",
            "decision-journal-append",
            "SOL/USDT",
            "--thesis",
            "SOL DeFi TVL hit all-time high this week.",
            "--journal-path",
            journal,
        ],
    )
    assert "decision_id=" in result.output


def test_decision_journal_append_prints_execution_disabled(tmp_path: Path) -> None:
    journal = str(tmp_path / "dj.jsonl")
    result = runner.invoke(
        app,
        [
            "research",
            "decision-journal-append",
            "BTC/USDT",
            "--thesis",
            "BTC rally on institutional inflows confirmed.",
            "--journal-path",
            journal,
        ],
    )
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


def test_decision_journal_append_is_additive(tmp_path: Path) -> None:
    journal = str(tmp_path / "dj.jsonl")
    for i in range(3):
        runner.invoke(
            app,
            [
                "research",
                "decision-journal-append",
                "BTC/USDT",
                f"--thesis=Thesis {i}: BTC breaking resistance at key level.",
                "--journal-path",
                journal,
            ],
        )
    lines = Path(journal).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_decision_journal_append_mode_paper(tmp_path: Path) -> None:
    journal = str(tmp_path / "dj.jsonl")
    result = runner.invoke(
        app,
        [
            "research",
            "decision-journal-append",
            "BTC/USDT",
            "--thesis",
            "BTC forming higher low pattern in paper mode.",
            "--mode",
            "paper",
            "--journal-path",
            journal,
        ],
    )
    assert result.exit_code == 0
    record = json.loads(Path(journal).read_text().strip().splitlines()[0])
    assert record["mode"] == "paper"
    assert record["execution_state"] == "paper_only"


def test_decision_journal_append_rejects_short_thesis(tmp_path: Path) -> None:
    """Thesis < 10 chars must fail."""
    journal = str(tmp_path / "dj.jsonl")
    result = runner.invoke(
        app,
        [
            "research",
            "decision-journal-append",
            "BTC/USDT",
            "--thesis",
            "Short",
            "--journal-path",
            journal,
        ],
    )
    assert result.exit_code != 0 or not Path(journal).exists()


def test_decision_journal_summary_fails_closed_on_malformed_rows(tmp_path: Path) -> None:
    journal = tmp_path / "dj.jsonl"
    journal.write_text('{"decision_id":"bad"}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        ["research", "decision-journal-summary", "--journal-path", str(journal)],
    )

    assert result.exit_code == 1
    assert "Decision journal summary failed" in result.output


# ── decision-journal-summary ──────────────────────────────────────────────────


def test_decision_journal_summary_empty_journal(tmp_path: Path) -> None:
    journal = str(tmp_path / "dj.jsonl")
    result = runner.invoke(
        app,
        ["research", "decision-journal-summary", "--journal-path", journal],
    )
    assert result.exit_code == 0
    assert "total_count=0" in result.output


def test_decision_journal_summary_counts_entries(tmp_path: Path) -> None:
    journal = str(tmp_path / "dj.jsonl")
    for i in range(5):
        runner.invoke(
            app,
            [
                "research",
                "decision-journal-append",
                "BTC/USDT",
                f"--thesis=Summary test {i}: BTC macro support confirmed.",
                "--journal-path",
                journal,
            ],
        )
    result = runner.invoke(
        app,
        ["research", "decision-journal-summary", "--journal-path", journal],
    )
    assert "total_count=5" in result.output


def test_decision_journal_summary_shows_safety_flags(tmp_path: Path) -> None:
    journal = str(tmp_path / "dj.jsonl")
    result = runner.invoke(
        app,
        ["research", "decision-journal-summary", "--journal-path", journal],
    )
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


# ── loop-cycle-summary ────────────────────────────────────────────────────────


def test_loop_cycle_summary_missing_file(tmp_path: Path) -> None:
    audit = str(tmp_path / "no_audit.jsonl")
    result = runner.invoke(
        app,
        ["research", "loop-cycle-summary", "--audit-path", audit],
    )
    assert result.exit_code == 0
    assert "No loop audit" in result.output


def test_loop_cycle_summary_reads_records(tmp_path: Path) -> None:
    audit_path = tmp_path / "loop_audit.jsonl"
    records = [
        {
            "cycle_id": f"cyc_{i:04d}",
            "started_at": "2026-03-21T10:00:00+00:00",
            "completed_at": "2026-03-21T10:00:01+00:00",
            "symbol": "BTC/USDT",
            "status": "completed",
            "market_data_fetched": True,
            "signal_generated": True,
            "risk_approved": True,
            "order_created": True,
            "fill_simulated": True,
            "decision_id": None,
            "risk_check_id": None,
            "order_id": None,
            "notes": [],
        }
        for i in range(3)
    ]
    audit_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    result = runner.invoke(
        app,
        ["research", "loop-cycle-summary", "--audit-path", str(audit_path)],
    )
    assert result.exit_code == 0
    assert "3 total" in result.output
    assert "completed" in result.output


def test_loop_cycle_summary_shows_safety_flags(tmp_path: Path) -> None:
    audit_path = tmp_path / "loop_audit.jsonl"
    audit_path.write_text(
        json.dumps({
            "cycle_id": "cyc_001",
            "status": "no_signal",
            "symbol": "BTC/USDT",
            "signal_generated": False,
            "risk_approved": False,
            "fill_simulated": False,
        }) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["research", "loop-cycle-summary", "--audit-path", str(audit_path)],
    )
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


def test_loop_cycle_summary_status_counts(tmp_path: Path) -> None:
    audit_path = tmp_path / "loop_audit.jsonl"
    statuses = ["completed", "completed", "no_signal", "risk_rejected"]
    lines = [json.dumps({"cycle_id": f"c{i}", "status": s}) for i, s in enumerate(statuses)]
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["research", "loop-cycle-summary", "--audit-path", str(audit_path)],
    )
    assert "completed" in result.output
    assert "no_signal" in result.output


def test_loop_cycle_summary_last_n_limits_table(tmp_path: Path) -> None:
    audit_path = tmp_path / "loop_audit.jsonl"
    lines = [json.dumps({"cycle_id": f"c{i:04d}", "status": "completed"}) for i in range(50)]
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["research", "loop-cycle-summary", "--audit-path", str(audit_path), "--last-n", "5"],
    )
    assert result.exit_code == 0
    assert "50 total" in result.output
    # Only 5 rows shown
    assert "showing last 5 of 50" in result.output
