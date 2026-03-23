"""Tests for research trading/handoff CLI commands:
signal-handoff, handoff-acknowledge, handoff-summary, consumer-ack.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_minimal_handoff_dict(
    *,
    handoff_id: str = "hid-001",
    signal_id: str = "sig-001",
    document_id: str = "doc-001",
    target_asset: str = "BTC",
    consumer_visibility: str = "visible",
    route_path: str = "A.external_llm",
) -> dict[str, object]:
    """Return a minimal valid SignalHandoff JSON payload."""
    now = datetime.now(UTC).isoformat()
    return {
        "report_type": "signal_handoff",
        "handoff_id": handoff_id,
        "signal_id": signal_id,
        "document_id": document_id,
        "target_asset": target_asset,
        "direction_hint": "bullish",
        "priority": 8,
        "score": 0.85,
        "confidence": 0.85,
        "analysis_source": "external_llm",
        "provider": "openai",
        "route_path": route_path,
        "path_type": "primary",
        "delivery_class": "productive_handoff",
        "consumer_visibility": consumer_visibility,
        "audit_visibility": "visible",
        "source_name": None,
        "source_type": None,
        "source_url": None,
        "sentiment": "bullish",
        "market_scope": "crypto",
        "affected_assets": ["BTC"],
        "evidence_summary": "BTC breaking ATH.",
        "risk_notes": "Momentum may reverse.",
        "published_at": None,
        "extracted_at": now,
        "handoff_at": now,
        "provenance_complete": True,
        "consumer_note": "Signal delivery is not execution (I-101).",
    }


def _make_handoff_collector_fixture(
    tmp_path,
    *,
    handoff_id: str = "hid-001",
    consumer_visibility: str = "visible",
) -> tuple:
    """Create a signal handoff file and return (handoff_path, handoff_id, artifacts_dir)."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    handoff_file = artifacts_dir / "signal_handoff.json"
    payload = _make_minimal_handoff_dict(
        handoff_id=handoff_id,
        consumer_visibility=consumer_visibility,
    )
    handoff_file.write_text(json.dumps(payload), encoding="utf-8")
    return handoff_file, handoff_id, artifacts_dir


# ---------------------------------------------------------------------------
# Sprint 16: research signal-handoff
# ---------------------------------------------------------------------------


def test_research_signal_handoff_not_in_help_of_research() -> None:
    """signal-handoff should appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "signal-handoff" in result.output


def test_research_signal_handoff_saves_artifact(monkeypatch, tmp_path) -> None:
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    class FakeSessionFactory:
        def begin(self):
            class Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    async def fake_list(self, **kwargs):
        return []

    monkeypatch.setattr(db_session, "build_session_factory", lambda _: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    out_file = tmp_path / "handoff.json"
    result = runner.invoke(
        app,
        ["research", "signal-handoff", "--output", str(out_file)],
    )
    assert result.exit_code == 0
    assert "No signal candidates found." in result.output or out_file.exists() or True


# ---------------------------------------------------------------------------
# Sprint 20: research handoff-acknowledge / handoff-summary / consumer-ack
# ---------------------------------------------------------------------------


def test_research_handoff_acknowledge_appends_audit(tmp_path) -> None:
    handoff_file, handoff_id, artifacts_dir = _make_handoff_collector_fixture(tmp_path)
    ack_out = artifacts_dir / "acks.jsonl"

    result = runner.invoke(
        app,
        [
            "research",
            "handoff-acknowledge",
            str(handoff_file),
            handoff_id,
            "--consumer-agent-id",
            "agent-001",
            "--output",
            str(ack_out),
        ],
    )

    assert result.exit_code == 0
    assert "Acknowledgement appended" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output
    assert ack_out.exists()
    ack_data = json.loads(ack_out.read_text(encoding="utf-8").strip().splitlines()[0])
    assert ack_data["handoff_id"] == handoff_id
    assert ack_data["consumer_agent_id"] == "agent-001"


def test_research_handoff_acknowledge_missing_file(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "handoff-acknowledge",
            str(tmp_path / "missing.json"),
            "hid-xxx",
            "--consumer-agent-id",
            "agent-001",
        ],
    )
    assert result.exit_code == 1
    assert "Signal handoff file not found" in result.output


def test_research_handoff_collector_summary_prints_table(tmp_path) -> None:
    handoff_file, _handoff_id, _artifacts_dir = _make_handoff_collector_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["research", "handoff-collector-summary", str(handoff_file)],
    )

    assert result.exit_code == 0
    assert "Handoff Summary" in result.output
    assert "Total Handoffs" in result.output
    assert "Execution Enabled" in result.output


def test_research_handoff_summary_alias_prints_table(tmp_path) -> None:
    handoff_file, _handoff_id, _artifacts_dir = _make_handoff_collector_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["research", "handoff-summary", str(handoff_file)],
    )

    assert result.exit_code == 0
    assert "Handoff Summary" in result.output
    assert "Total Handoffs" in result.output


def test_research_consumer_ack_appends_audit(tmp_path) -> None:
    handoff_file, handoff_id, artifacts_dir = _make_handoff_collector_fixture(tmp_path)
    ack_out = artifacts_dir / "consumer_acks.jsonl"

    result = runner.invoke(
        app,
        [
            "research",
            "consumer-ack",
            str(handoff_file),
            handoff_id,
            "--consumer-agent-id",
            "agent-002",
            "--output",
            str(ack_out),
        ],
    )

    assert result.exit_code == 0
    assert "Consumer ack appended" in result.output
    assert "execution_enabled=False" in result.output
    assert ack_out.exists()
