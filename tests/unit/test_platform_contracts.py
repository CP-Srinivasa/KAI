from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_required_platform_docs_exist() -> None:
    # D-107: living architecture is AGENTS.md, CLAUDE.md, CHANGELOG.md,
    # DECISION_LOG.md, README.md, RUNBOOK.md.  All others are archived.
    required = [
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "CHANGELOG.md",
        "DECISION_LOG.md",
        "RUNBOOK.md",
        "DECISION_SCHEMA.json",
        "CONFIG_SCHEMA.json",
    ]
    for name in required:
        assert (ROOT / name).exists(), name


def test_config_schema_declares_required_groups() -> None:
    schema = json.loads((ROOT / "CONFIG_SCHEMA.json").read_text(encoding="utf-8"))
    expected_groups = {
        "system_runtime",
        "llm_agent",
        "market_data",
        "risk",
        "strategy_decision",
        "execution",
        "memory_learning",
        "security",
        "messaging_ux",
    }

    assert schema["type"] == "object"
    assert expected_groups.issubset(schema["properties"])
    assert expected_groups.issubset(set(schema["required"]))
    assert "mode" in schema["properties"]["system_runtime"]["properties"]
    assert "live_execution_enabled" in schema["properties"]["execution"]["properties"]


def test_decision_schema_requires_core_fields() -> None:
    schema = json.loads((ROOT / "DECISION_SCHEMA.json").read_text(encoding="utf-8"))
    required = {
        "decision_id",
        "timestamp_utc",
        "symbol",
        "mode",
        "thesis",
        "supporting_factors",
        "contradictory_factors",
        "confidence_score",
        "risk_assessment",
        "invalidation_condition",
        "approval_state",
        "execution_state",
    }

    assert schema["type"] == "object"
    assert required.issubset(set(schema["required"]))
    assert schema["properties"]["mode"]["enum"] == [
        "research",
        "backtest",
        "paper",
        "shadow",
        "live",
    ]


def test_telegram_interface_lists_first_class_commands() -> None:
    text = (ROOT / "docs" / "archive" / "TELEGRAM_INTERFACE.md").read_text(encoding="utf-8")
    for command in (
        "/status",
        "/health",
        "/positions",
        "/exposure",
        "/risk",
        "/signals",
        "/journal",
        "/approve",
        "/reject",
        "/pause",
        "/resume",
        "/kill",
        "/daily_summary",
        "/incident",
    ):
        assert command in text

