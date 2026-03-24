"""Tests for research operator commands: escalation, blocking, action-queue,
decision-pack, daily-summary, operator-runbook variants, backtest-run.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Sprint 27: escalation-summary / blocking-summary / operator-action-summary
# ---------------------------------------------------------------------------


def test_research_escalation_summary_prints_table(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "escalation-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Escalation Summary" in result.output
    assert "Execution Enabled" in result.output


def test_research_blocking_summary_prints_table(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "blocking-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Blocking Summary" in result.output
    assert "Blocking Count" in result.output
    assert "Execution Enabled" in result.output


def test_research_operator_action_summary_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "operator-action-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Action Summary" in result.output
    assert "Operator Action Count" in result.output
    assert "Execution Enabled" in result.output


# ---------------------------------------------------------------------------
# Sprint 28: action-queue-summary / blocking-actions / prioritized-actions /
#            review-required-actions
# ---------------------------------------------------------------------------


def test_research_action_queue_summary_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "action-queue-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Action Queue Summary" in result.output
    assert "Queue Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_blocking_actions_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "blocking-actions",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Blocking Actions" in result.output
    assert "Blocking Count" in result.output
    assert "Execution Enabled" in result.output


def test_research_prioritized_actions_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "prioritized-actions",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Prioritized Actions" in result.output
    assert "Queue Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_review_required_actions_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "review-required-actions",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Review Required Actions" in result.output
    assert "Review Required Count" in result.output
    assert "Execution Enabled" in result.output


# ---------------------------------------------------------------------------
# Sprint 29: decision-pack-summary / operator-decision-pack / daily-summary
# ---------------------------------------------------------------------------


def test_research_decision_pack_summary_in_help() -> None:
    """decision-pack-summary must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "decision-pack-summary" in result.output
    assert "operator-decision-pack" in result.output
    assert "daily-summary" in result.output


def test_research_decision_pack_summary_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "decision-pack-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Decision Pack Summary" in result.output
    assert "Overall Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_decision_pack_summary_saves_json(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "pack.json"

    result = runner.invoke(
        app,
        [
            "research",
            "decision-pack-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--out",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["report_type"] == "operator_decision_pack"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


def test_research_operator_decision_pack_alias_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "operator-decision-pack" in result.output

    alias_result = runner.invoke(
        app,
        [
            "research",
            "operator-decision-pack",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert alias_result.exit_code == 0
    assert "Operator Decision Pack Summary" in alias_result.output


def test_research_daily_summary_prints_human_readable_output(tmp_path, monkeypatch) -> None:
    async def fake_daily_summary(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "daily_operator_summary",
            "readiness_status": "warning",
            "cycle_count_today": 2,
            "last_cycle_status": "no_signal",
            "last_cycle_symbol": "BTC/USDT",
            "last_cycle_at": "2026-03-22T08:15:00+00:00",
            "position_count": 1,
            "total_exposure_pct": 18.5,
            "mark_to_market_status": "ok",
            "decision_pack_status": "warning",
            "open_incidents": 1,
            "aggregated_at": "2026-03-22T08:30:00+00:00",
            "execution_enabled": False,
            "write_back_allowed": False,
            "sources": ["readiness_summary"],
        }

    from app.agents import mcp_server

    monkeypatch.setattr(mcp_server, "get_daily_operator_summary", fake_daily_summary)

    result = runner.invoke(app, ["research", "daily-summary"])

    assert result.exit_code == 0
    assert "Daily Operator View" in result.output
    assert "Readiness:" in result.output
    assert "Cycles today:" in result.output
    assert "Portfolio:" in result.output
    assert "Decision Pack:" in result.output
    assert "Incidents:" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output
    assert '"report_type": "daily_operator_summary"' not in result.output


def test_research_daily_summary_json_flag_prints_canonical_payload(monkeypatch) -> None:
    async def fake_daily_summary(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "daily_operator_summary",
            "readiness_status": "ok",
            "cycle_count_today": 0,
            "position_count": 0,
            "total_exposure_pct": 0.0,
            "mark_to_market_status": "unknown",
            "decision_pack_status": "clear",
            "open_incidents": 0,
            "aggregated_at": "2026-03-22T08:30:00+00:00",
            "execution_enabled": False,
            "write_back_allowed": False,
            "sources": [],
        }

    from app.agents import mcp_server

    monkeypatch.setattr(mcp_server, "get_daily_operator_summary", fake_daily_summary)

    result = runner.invoke(app, ["research", "daily-summary", "--json"])

    assert result.exit_code == 0
    assert '"report_type": "daily_operator_summary"' in result.output
    assert '"execution_enabled": false' in result.output
    assert '"write_back_allowed": false' in result.output


# ---------------------------------------------------------------------------
# Sprint 35: backtest-run CLI
# ---------------------------------------------------------------------------


def test_research_backtest_run_produces_result_json(tmp_path) -> None:
    import json as _json

    from app.core.enums import MarketScope, SentimentLabel
    from app.research.signals import SignalCandidate

    _runner = CliRunner()
    signals_path = tmp_path / "signals.jsonl"
    out_path = tmp_path / "result.json"
    audit_path = tmp_path / "audit.jsonl"

    sig = SignalCandidate(
        signal_id="s_bt_1",
        document_id="doc_bt_1",
        target_asset="BTC/USDT",
        direction_hint="bullish",
        confidence=0.9,
        supporting_evidence="Strong uptrend",
        contradicting_evidence="None",
        risk_notes="Standard",
        source_quality=0.95,
        recommended_next_step="Monitor",
        analysis_source="RULE",
        priority=9,
        sentiment=SentimentLabel.BULLISH,
        affected_assets=["BTC/USDT"],
        market_scope=MarketScope.CRYPTO,
        published_at=None,
    )
    signals_path.write_text(_json.dumps(sig.to_json_dict()) + "\n", encoding="utf-8")

    result = _runner.invoke(
        app,
        [
            "research",
            "backtest-run",
            "--signals-path",
            str(signals_path),
            "--out",
            str(out_path),
            "--audit-path",
            str(audit_path),
            "--min-confidence",
            "0.5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "signals_received=1" in result.output
    assert "result_written=" in result.output
    assert out_path.exists()

    payload = _json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["signals_received"] == 1
    assert "execution_records" in payload


def test_research_backtest_run_missing_signals_file_exits_nonzero(tmp_path) -> None:
    _runner = CliRunner()
    result = _runner.invoke(
        app,
        [
            "research",
            "backtest-run",
            "--signals-path",
            str(tmp_path / "nonexistent.jsonl"),
            "--out",
            str(tmp_path / "out.json"),
            "--audit-path",
            str(tmp_path / "audit.jsonl"),
        ],
    )
    assert result.exit_code != 0


def test_research_backtest_run_registered_in_command_names() -> None:
    from app.cli.main import get_registered_research_command_names

    names = get_registered_research_command_names()
    assert "backtest-run" in names
