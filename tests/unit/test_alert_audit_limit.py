"""5,4 MB ueber die Leitung, damit die Seite 50 Zeilen zeigt.

2026-09-09 gemessen: /operator/alert-audit lieferte 5.376.687 Bytes in einer
Antwort, alle 30 s neu. Der einzige Konsument ist web/src/pages/Alerts.tsx:99
mit ``entries.slice(-50)``. Die Aggregate (``total_alerts``, ``total_resolved``)
werden getrennt angezeigt und muessen weiter ueber die Vollmenge laufen — nur
die Liste wird geschnitten, und zwar am neuen Ende.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.tools import _helpers
from app.agents.tools.canonical_read import get_alert_audit_summary


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Der Audit-Leser haelt Pfade innerhalb des Workspace — tmp_path wird einer."""
    monkeypatch.setattr(_helpers, "WORKSPACE_ROOT", tmp_path.resolve())


def _write_audit(tmp_path: Path, count: int) -> None:
    lines = []
    for i in range(count):
        lines.append(
            json.dumps(
                {
                    "document_id": f"doc-{i:04d}",
                    "channel": "telegram",
                    "dispatched_at": f"2026-09-09T{i % 24:02d}:00:00+00:00",
                    "priority": 7,
                }
            )
        )
    (tmp_path / "alert_audit.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "alert_outcomes.jsonl").write_text("", encoding="utf-8")


@pytest.mark.asyncio
async def test_limit_returns_only_the_newest_rows(tmp_path: Path) -> None:
    _write_audit(tmp_path, 500)
    out = await get_alert_audit_summary(audit_dir=str(tmp_path), limit=200)
    alerts = out["alerts"]
    assert isinstance(alerts, list)
    assert len(alerts) == 200
    # Das Frontend nimmt slice(-50) und reversed — es braucht die NEUESTEN.
    assert alerts[-1]["document_id"] == "doc-0499"
    assert alerts[0]["document_id"] == "doc-0300"


@pytest.mark.asyncio
async def test_aggregates_stay_over_the_full_population(tmp_path: Path) -> None:
    """Das Limit darf die Gesamtzahlen nicht verfaelschen."""
    _write_audit(tmp_path, 500)
    out = await get_alert_audit_summary(audit_dir=str(tmp_path), limit=10)
    assert out["total_alerts"] == 500
    assert len(out["alerts"]) == 10
    assert out["returned_alerts"] == 10
    assert out["alerts_truncated"] is True


@pytest.mark.asyncio
async def test_without_limit_nothing_changes(tmp_path: Path) -> None:
    """Bestandskonsumenten (CLI, MCP) sehen exakt das bisherige Verhalten."""
    _write_audit(tmp_path, 120)
    out = await get_alert_audit_summary(audit_dir=str(tmp_path))
    assert len(out["alerts"]) == 120
    assert out["alerts_truncated"] is False


@pytest.mark.asyncio
async def test_limit_larger_than_population_is_not_truncated(tmp_path: Path) -> None:
    _write_audit(tmp_path, 30)
    out = await get_alert_audit_summary(audit_dir=str(tmp_path), limit=200)
    assert len(out["alerts"]) == 30
    assert out["alerts_truncated"] is False
