"""Tests for get_daily_operator_summary — the backing surface for /status
and the daily operator dashboard.

Verifies that the summary actually populates the fields /status reads,
that degraded state is flagged when a source fails, and that unmeasured
telemetry is explicitly labeled rather than silently zero-filled.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.tools.canonical_read import get_daily_operator_summary


def _now() -> datetime:
    return datetime(2026, 4, 17, 22, 30, 0, tzinfo=UTC)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


class _AsyncCM:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    async def __aenter__(self) -> object:
        return self._inner

    async def __aexit__(self, *args: object) -> None:
        return None


def _db_patches(*, pending: int = 0):
    """Patch the DB layer so count_pending_documents returns `pending`.

    build_session_factory returns a factory whose .begin() yields a fake
    session; DocumentRepository is replaced so its count_pending_documents
    returns the configured value without touching SQLAlchemy.
    """
    factory_mock = MagicMock()
    factory_mock.begin.return_value = _AsyncCM(MagicMock())

    repo_patch = patch("app.agents.tools.canonical_read.DocumentRepository")
    factory_patch = patch(
        "app.agents.tools.canonical_read.build_session_factory",
        return_value=factory_mock,
    )
    return factory_patch, repo_patch, pending


def _exposure_patch(*, position_count: int = 0, raise_error: bool = False):
    if raise_error:
        return patch(
            "app.agents.tools.canonical_read.get_paper_exposure_summary",
            AsyncMock(side_effect=RuntimeError("paper audit corrupted")),
        )
    return patch(
        "app.agents.tools.canonical_read.get_paper_exposure_summary",
        AsyncMock(return_value={"position_count": position_count}),
    )


@pytest.mark.asyncio
async def test_summary_has_required_keys_for_status_command(tmp_path: Path) -> None:
    """/status reads these exact keys — missing any of them means display
    falls back to unknown/?, which is the regression this test guards against."""
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    _write_jsonl(tmp_path / "loop.jsonl", [])
    _write_jsonl(tmp_path / "exec.jsonl", [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _passthrough = lambda p, **kw: Path(p)  # noqa: E731
    with _exposure_patch(position_count=0), factory_p, repo_p as repo_cls, patch(
        "app.agents.tools.canonical_read.resolve_workspace_dir",
        side_effect=_passthrough,
    ), patch(
        "app.agents.tools.canonical_read.resolve_workspace_path",
        side_effect=_passthrough,
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(tmp_path / "loop.jsonl"),
            paper_execution_audit_path=str(tmp_path / "exec.jsonl"),
            now=_now(),
        )

    required = {
        "readiness_status",
        "cycle_count_today",
        "position_count",
        "ingestion_backlog_documents",
        "alert_fire_rate_docs_per_hour_24h",
        "llm_provider_failure_rate_24h",
        "rss_to_alert_latency_p95_seconds_24h",
        "generated_at_utc",
    }
    assert required.issubset(result.keys())
    assert result["readiness_status"] == "operational"
    assert result["position_count"] == 0
    assert result["ingestion_backlog_documents"] == 0


@pytest.mark.asyncio
async def test_alert_rate_counts_only_last_24h(tmp_path: Path) -> None:
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    now = _now()
    rows = [
        {
            "document_id": f"doc-{i}",
            "channel": "telegram",
            "message_id": "dry_run",
            "is_digest": False,
            "dispatched_at": (now - timedelta(hours=12)).isoformat(),
            "sentiment_label": "bullish",
            "affected_assets": ["BTC"],
        }
        for i in range(48)
    ] + [
        {
            "document_id": f"doc-old-{i}",
            "channel": "telegram",
            "message_id": "dry_run",
            "is_digest": False,
            "dispatched_at": (now - timedelta(hours=48)).isoformat(),
            "sentiment_label": "bullish",
            "affected_assets": ["BTC"],
        }
        for i in range(100)
    ]
    _write_jsonl(alerts_dir / "alert_audit.jsonl", rows)
    _write_jsonl(tmp_path / "loop.jsonl", [])
    _write_jsonl(tmp_path / "exec.jsonl", [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _passthrough = lambda p, **kw: Path(p)  # noqa: E731
    with _exposure_patch(position_count=0), factory_p, repo_p as repo_cls, patch(
        "app.agents.tools.canonical_read.resolve_workspace_dir",
        side_effect=_passthrough,
    ), patch(
        "app.agents.tools.canonical_read.resolve_workspace_path",
        side_effect=_passthrough,
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(tmp_path / "loop.jsonl"),
            paper_execution_audit_path=str(tmp_path / "exec.jsonl"),
            now=now,
        )

    # 48 events within the last 24h -> 2.0/h
    assert result["alert_fire_rate_docs_per_hour_24h"] == 2.0


@pytest.mark.asyncio
async def test_cycles_today_counts_only_todays_started_at(tmp_path: Path) -> None:
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    loop_audit = tmp_path / "loop.jsonl"
    exec_audit = tmp_path / "exec.jsonl"
    now = _now()
    today_iso = now.replace(hour=5).isoformat()
    yesterday_iso = (now - timedelta(days=1)).isoformat()
    _write_jsonl(
        loop_audit,
        [
            {"cycle_id": "c1", "started_at": today_iso, "status": "ok"},
            {"cycle_id": "c2", "started_at": today_iso, "status": "ok"},
            {"cycle_id": "c3", "started_at": yesterday_iso, "status": "ok"},
        ],
    )
    _write_jsonl(exec_audit, [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _passthrough = lambda p, **kw: Path(p)  # noqa: E731
    with _exposure_patch(position_count=0), factory_p, repo_p as repo_cls, patch(
        "app.agents.tools.canonical_read.resolve_workspace_dir",
        side_effect=_passthrough,
    ), patch(
        "app.agents.tools.canonical_read.resolve_workspace_path",
        side_effect=_passthrough,
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(loop_audit),
            paper_execution_audit_path=str(exec_audit),
            now=now,
        )

    assert result["cycle_count_today"] == 2


@pytest.mark.asyncio
async def test_degraded_when_exposure_read_fails(tmp_path: Path) -> None:
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    _write_jsonl(tmp_path / "loop.jsonl", [])
    _write_jsonl(tmp_path / "exec.jsonl", [])

    factory_p, repo_p, _ = _db_patches(pending=5)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    with _exposure_patch(raise_error=True), factory_p, repo_p as repo_cls, patch(
        "app.agents.tools.canonical_read.resolve_workspace_dir",
        side_effect=_pass,
    ), patch(
        "app.agents.tools.canonical_read.resolve_workspace_path",
        side_effect=_pass,
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=5)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(tmp_path / "loop.jsonl"),
            paper_execution_audit_path=str(tmp_path / "exec.jsonl"),
            now=_now(),
        )

    assert result["readiness_status"] == "degraded"
    assert result["position_count"] == "?"
    # Other sources still resolved — backlog should carry the real number.
    assert result["ingestion_backlog_documents"] == 5


@pytest.mark.asyncio
async def test_unimplemented_fields_flagged_not_zero(tmp_path: Path) -> None:
    """Regression guard: LLM failure rate and latency must NEVER be fabricated
    as 0/null. They must carry the 'not_implemented' sentinel so dashboards
    and operators see an honest gap."""
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    _write_jsonl(tmp_path / "loop.jsonl", [])
    _write_jsonl(tmp_path / "exec.jsonl", [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _passthrough = lambda p, **kw: Path(p)  # noqa: E731
    with _exposure_patch(position_count=0), factory_p, repo_p as repo_cls, patch(
        "app.agents.tools.canonical_read.resolve_workspace_dir",
        side_effect=_passthrough,
    ), patch(
        "app.agents.tools.canonical_read.resolve_workspace_path",
        side_effect=_passthrough,
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(tmp_path / "loop.jsonl"),
            paper_execution_audit_path=str(tmp_path / "exec.jsonl"),
            now=_now(),
        )

    assert result["llm_provider_failure_rate_24h"] == "not_implemented"
    assert result["rss_to_alert_latency_p95_seconds_24h"] == "not_implemented"
