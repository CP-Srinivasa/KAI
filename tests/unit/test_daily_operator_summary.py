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


def _snapshot_patch(
    *,
    position_count: int = 0,
    cash_usd: float = 10_000.0,
    total_equity_usd: float = 10_000.0,
    realized_pnl_usd: float = 0.0,
    raise_error: bool = False,
):
    """Mock the canonical portfolio snapshot helper used by the daily summary.

    Mirrors the real producer's attribute names (snapshot.cash_usd etc.) so
    the test can no longer drift away from reality the way the prior
    exposure-mock did — that drift was the root cause of /status reporting
    "0 positions" while the dashboard showed live trades.
    """
    if raise_error:
        return patch(
            "app.agents.tools.canonical_read.build_paper_portfolio_snapshot_helper",
            AsyncMock(side_effect=RuntimeError("paper audit corrupted")),
        )
    snapshot = MagicMock()
    snapshot.position_count = position_count
    snapshot.cash_usd = cash_usd
    snapshot.total_equity_usd = total_equity_usd
    snapshot.realized_pnl_usd = realized_pnl_usd
    return patch(
        "app.agents.tools.canonical_read.build_paper_portfolio_snapshot_helper",
        AsyncMock(return_value=snapshot),
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
    with (
        _snapshot_patch(position_count=0),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_passthrough,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_passthrough,
        ),
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
    with (
        _snapshot_patch(position_count=0),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_passthrough,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_passthrough,
        ),
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
    with (
        _snapshot_patch(position_count=0),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_passthrough,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_passthrough,
        ),
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
async def test_degraded_when_portfolio_read_fails(tmp_path: Path) -> None:
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    _write_jsonl(tmp_path / "loop.jsonl", [])
    _write_jsonl(tmp_path / "exec.jsonl", [])

    factory_p, repo_p, _ = _db_patches(pending=5)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    with (
        _snapshot_patch(raise_error=True),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_pass,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_pass,
        ),
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
    with (
        _snapshot_patch(position_count=0),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_passthrough,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_passthrough,
        ),
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


@pytest.mark.asyncio
async def test_cycle_status_breakdown_24h(tmp_path: Path) -> None:
    """Surfaces the priority_rejected vs filled vs other-status mix so a
    'loop running but blocked by design' state is distinguishable from
    a 'loop dead' state. Without this, /status reports activity without
    revealing that 100% of cycles are rejecting (V1-fix scenario)."""
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    loop_audit = tmp_path / "loop.jsonl"
    exec_audit = tmp_path / "exec.jsonl"
    now = _now()
    inside_24h = (now - timedelta(hours=2)).isoformat()
    inside_24h_alt = (now - timedelta(hours=10)).isoformat()
    outside_24h = (now - timedelta(hours=30)).isoformat()
    rejected_rows = [
        {"cycle_id": f"r{i}", "started_at": inside_24h, "status": "priority_rejected"}
        for i in range(8)
    ]
    filled_rows = [
        {"cycle_id": f"f{i}", "started_at": inside_24h_alt, "status": "filled"} for i in range(2)
    ]
    outside_rows = [{"cycle_id": "old", "started_at": outside_24h, "status": "filled"}]
    rows = rejected_rows + filled_rows + outside_rows
    _write_jsonl(loop_audit, rows)
    _write_jsonl(exec_audit, [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    with (
        _snapshot_patch(position_count=0),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_pass,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_pass,
        ),
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(loop_audit),
            paper_execution_audit_path=str(exec_audit),
            now=now,
        )

    breakdown = result["cycle_status_breakdown_24h"]
    assert breakdown == {"priority_rejected": 8, "filled": 2}
    assert result["priority_rejected_pct_24h"] == 80.0
    assert result["priority_rejected_alert"] is True


@pytest.mark.asyncio
async def test_cycle_status_breakdown_alert_threshold(tmp_path: Path) -> None:
    """Alert flag is only True when priority_rejected > 50% — exactly 50%
    must NOT trigger (operator gets noise). 51%+ triggers."""
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    loop_audit = tmp_path / "loop.jsonl"
    exec_audit = tmp_path / "exec.jsonl"
    now = _now()
    inside = (now - timedelta(hours=1)).isoformat()
    rejected = [
        {"cycle_id": f"r{i}", "started_at": inside, "status": "priority_rejected"} for i in range(5)
    ]
    other = [{"cycle_id": f"o{i}", "started_at": inside, "status": "no_signal"} for i in range(5)]
    rows = rejected + other
    _write_jsonl(loop_audit, rows)
    _write_jsonl(exec_audit, [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    with (
        _snapshot_patch(position_count=0),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_pass,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_pass,
        ),
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(loop_audit),
            paper_execution_audit_path=str(exec_audit),
            now=now,
        )

    assert result["priority_rejected_pct_24h"] == 50.0
    assert result["priority_rejected_alert"] is False


@pytest.mark.asyncio
async def test_warp_status_present_in_summary(tmp_path: Path) -> None:
    """warp_status must always be present so the dashboard never gets a
    KeyError when WARP is not running. Active vs not-active is environment-
    dependent — we only assert the contract here."""
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    _write_jsonl(tmp_path / "loop.jsonl", [])
    _write_jsonl(tmp_path / "exec.jsonl", [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    with (
        _snapshot_patch(position_count=0),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_pass,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_pass,
        ),
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(tmp_path / "loop.jsonl"),
            paper_execution_audit_path=str(tmp_path / "exec.jsonl"),
            now=_now(),
        )

    warp = result["warp_status"]
    assert isinstance(warp, dict)
    assert "active" in warp and isinstance(warp["active"], bool)
    assert warp["detection_method"] in {"process", "interface", "none"}
    assert "hint" in warp


@pytest.mark.asyncio
async def test_warp_status_active_when_process_present(tmp_path: Path) -> None:
    """When the Cloudflare WARP.exe process is detected, active=True with the
    process detection method and a non-empty hint."""
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    _write_jsonl(tmp_path / "loop.jsonl", [])
    _write_jsonl(tmp_path / "exec.jsonl", [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    fake_run_result = MagicMock()
    fake_run_result.stdout = '"Cloudflare WARP.exe","1234","Console","1","45,678 K"\n'
    with (
        _snapshot_patch(position_count=0),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_pass,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_pass,
        ),
        patch("platform.system", return_value="Windows"),
        patch("subprocess.run", return_value=fake_run_result),
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(tmp_path / "loop.jsonl"),
            paper_execution_audit_path=str(tmp_path / "exec.jsonl"),
            now=_now(),
        )

    warp = result["warp_status"]
    assert warp["active"] is True
    assert warp["detection_method"] == "process"
    assert isinstance(warp["hint"], str) and "WARP" in warp["hint"]


@pytest.mark.asyncio
async def test_cycle_status_breakdown_empty_when_no_recent_cycles(tmp_path: Path) -> None:
    """When the loop audit has no rows in the last 24h, breakdown is {} and
    pct is None ('?') — distinguishable from a real 0% rejected state."""
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    loop_audit = tmp_path / "loop.jsonl"
    exec_audit = tmp_path / "exec.jsonl"
    now = _now()
    outside = (now - timedelta(hours=48)).isoformat()
    _write_jsonl(loop_audit, [{"cycle_id": "old", "started_at": outside, "status": "filled"}])
    _write_jsonl(exec_audit, [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    with (
        _snapshot_patch(position_count=0),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_pass,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_pass,
        ),
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(loop_audit),
            paper_execution_audit_path=str(exec_audit),
            now=now,
        )

    assert result["cycle_status_breakdown_24h"] == {}
    assert result["priority_rejected_pct_24h"] == "?"
    assert result["priority_rejected_alert"] is False


@pytest.mark.asyncio
async def test_summary_surfaces_cash_equity_and_realized_pnl(tmp_path: Path) -> None:
    """Regression guard for the 2026-05-02 /status display bug.

    Before the fix, get_daily_operator_summary read from the exposure-only
    surface whose 'priced_position_count' key was looked up as 'position_count'
    (silent default 0), and cash/equity/realized_pnl were not exposed at all.
    Result: Telegram /status and the dashboard header reported 0 positions
    and Cash == Equity for 7 different consumer surfaces while the underlying
    paper engine was actively trading three open positions.

    This test pins the contract: the daily summary must surface real cash,
    equity, realized PnL and the position count taken straight from the
    canonical portfolio snapshot — same source as /operator/portfolio-snapshot.
    """
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    _write_jsonl(tmp_path / "loop.jsonl", [])
    _write_jsonl(tmp_path / "exec.jsonl", [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    with (
        _snapshot_patch(
            position_count=3,
            cash_usd=16_715.74,
            total_equity_usd=23_028.49,
            realized_pnl_usd=945.45,
        ),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_pass,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_pass,
        ),
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(tmp_path / "loop.jsonl"),
            paper_execution_audit_path=str(tmp_path / "exec.jsonl"),
            now=_now(),
        )

    assert result["position_count"] == 3
    assert result["cash_usd"] == pytest.approx(16_715.74)
    assert result["total_equity_usd"] == pytest.approx(23_028.49)
    assert result["realized_pnl_usd"] == pytest.approx(945.45)
    # Cash MUST NOT equal equity here — that was the symptom of the bug.
    assert result["cash_usd"] != result["total_equity_usd"]


@pytest.mark.asyncio
async def test_summary_marks_portfolio_fields_unknown_when_snapshot_fails(
    tmp_path: Path,
) -> None:
    """When the snapshot helper raises, all four portfolio fields collapse to
    '?' (the canonical 'unknown' marker) instead of silently zero-filling.
    Operator must see the gap, not a fabricated 0/0/0/0."""
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(alerts_dir / "alert_audit.jsonl", [])
    _write_jsonl(tmp_path / "loop.jsonl", [])
    _write_jsonl(tmp_path / "exec.jsonl", [])

    factory_p, repo_p, _ = _db_patches(pending=0)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    with (
        _snapshot_patch(raise_error=True),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_pass,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_pass,
        ),
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(tmp_path / "loop.jsonl"),
            paper_execution_audit_path=str(tmp_path / "exec.jsonl"),
            now=_now(),
        )

    assert result["readiness_status"] == "degraded"
    assert result["position_count"] == "?"
    assert result["cash_usd"] == "?"
    assert result["total_equity_usd"] == "?"
    assert result["realized_pnl_usd"] == "?"


@pytest.mark.asyncio
async def test_decision_pack_surfaces_sample_warnings_and_tables(tmp_path: Path) -> None:
    alerts_dir = tmp_path / "artifacts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)
    recent = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=10)).isoformat()
    _write_jsonl(
        alerts_dir / "alert_audit.jsonl",
        [
            {
                "document_id": "doc-bull",
                "channel": "telegram",
                "message_id": "m1",
                "is_digest": False,
                "dispatched_at": recent,
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
                "source_name": "decrypt",
                "directional_confidence": 0.85,
            },
            {
                "document_id": "doc-bear",
                "channel": "telegram",
                "message_id": "m2",
                "is_digest": False,
                "dispatched_at": recent,
                "sentiment_label": "bearish",
                "affected_assets": ["ETH"],
                "source_name": "unknown",
                "directional_confidence": 0.65,
            },
            {
                "document_id": "doc-neutral",
                "channel": "telegram",
                "message_id": "m3",
                "is_digest": False,
                "dispatched_at": recent,
                "sentiment_label": "neutral",
                "affected_assets": ["SOL"],
                "source_name": "decrypt",
            },
            {
                "document_id": "doc-old",
                "channel": "telegram",
                "message_id": "m4",
                "is_digest": False,
                "dispatched_at": old,
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
                "source_name": "old_source",
            },
        ],
    )
    _write_jsonl(
        alerts_dir / "alert_outcomes.jsonl",
        [
            {"document_id": "doc-bull", "outcome": "hit", "annotated_at": recent},
            {"document_id": "doc-bear", "outcome": "miss", "annotated_at": recent},
            {"document_id": "doc-neutral", "outcome": "inconclusive", "annotated_at": recent},
        ],
    )
    _write_jsonl(
        alerts_dir / "blocked_alerts.jsonl",
        [
            {
                "document_id": "doc-block-bull",
                "blocked_at": recent,
                "block_reason": "reactive_price_narrative",
                "sentiment_label": "bullish",
                "source_name": "cointelegraph",
                "directional_confidence": 0.70,
            },
            {
                "document_id": "doc-block-bear",
                "blocked_at": recent,
                "block_reason": "low_directional_confidence",
                "sentiment_label": "bearish",
                "source_name": "decrypt",
                "directional_confidence": 0.42,
            },
            {
                "document_id": "doc-block-old",
                "blocked_at": old,
                "block_reason": "reactive_price_narrative",
                "sentiment_label": "bullish",
                "source_name": "old_source",
            },
        ],
    )
    _write_jsonl(
        tmp_path / "loop.jsonl",
        [{"cycle_id": "c1", "started_at": recent, "status": "ok"}],
    )
    _write_jsonl(
        tmp_path / "exec.jsonl",
        [{"event_type": "position_closed", "trade_pnl_usd": 12.5, "timestamp_utc": recent}],
    )

    factory_p, repo_p, _ = _db_patches(pending=0)
    _pass = lambda p, **kw: Path(p)  # noqa: E731
    with (
        _snapshot_patch(position_count=1),
        factory_p,
        repo_p as repo_cls,
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_dir",
            side_effect=_pass,
        ),
        patch(
            "app.agents.tools.canonical_read.resolve_workspace_path",
            side_effect=_pass,
        ),
    ):
        repo_cls.return_value.count_pending_documents = AsyncMock(return_value=0)
        result = await get_daily_operator_summary(
            alert_audit_dir=str(alerts_dir),
            loop_audit_path=str(tmp_path / "loop.jsonl"),
            paper_execution_audit_path=str(tmp_path / "exec.jsonl"),
            now=now,
        )

    pack = result["decision_pack_2026_05_30"]
    assert pack["target_date"] == "2026-05-30"
    assert pack["days_to_target"] == 6
    assert pack["snapshot_robustness"] == {
        "portfolio_snapshot": "ok",
        "alert_audit": "ok",
        "blocked_alerts": "ok",
        "trading_loop_audit": "ok",
        "paper_execution_audit": "ok",
    }
    assert pack["sample_sizes"] == {
        "alerts_7d": 3,
        "directional_alerts_7d": 2,
        "blocked_alerts_7d": 2,
        "classifier_directional_candidates_7d": 4,
        "resolved_directional_hit_miss_7d": 2,
        "inconclusive_directional_7d": 0,
        "paper_closed_positions_with_pnl": 1,
    }
    assert {w["code"] for w in pack["sample_size_warnings"]} == {
        "directional_sample_low",
        "resolved_directional_sample_low",
        "paper_pnl_sample_low",
        "unknown_source_share_high",
    }
    assert pack["source_table_7d"][:2] == [
        {"source": "decrypt", "alerts": 2, "directional_alerts": 1, "share_pct": 66.7},
        {"source": "unknown", "alerts": 1, "directional_alerts": 1, "share_pct": 33.3},
    ]
    assert pack["sentiment_table_7d"] == [
        {"sentiment": "bullish", "alerts": 1, "share_pct": 33.3},
        {"sentiment": "bearish", "alerts": 1, "share_pct": 33.3},
        {"sentiment": "neutral", "alerts": 1, "share_pct": 33.3},
    ]
    assert pack["dispatch_filter_7d"] == {
        "dispatched_directional_alerts": 2,
        "blocked_alerts": 2,
        "classifier_directional_candidates": 4,
        "dispatch_pass_rate_pct": 50.0,
    }
    assert pack["confidence_collection_7d"] == {
        "dispatched_with_confidence": 2,
        "blocked_with_confidence": 2,
        "dispatched_avg_confidence_pct": 75.0,
        "blocked_avg_confidence_pct": 56.0,
        "coverage_pct": 80.0,
    }
    assert pack["blocked_reason_table_7d"] == [
        {
            "reason": "reactive_price_narrative",
            "blocked_alerts": 1,
            "share_pct": 50.0,
        },
        {
            "reason": "low_directional_confidence",
            "blocked_alerts": 1,
            "share_pct": 50.0,
        },
    ]
    assert pack["blocked_sentiment_table_7d"] == [
        {"sentiment": "bullish", "blocked_alerts": 1, "share_pct": 50.0},
        {"sentiment": "bearish", "blocked_alerts": 1, "share_pct": 50.0},
    ]
    assert pack["blocked_source_table_7d"] == [
        {"source": "cointelegraph", "blocked_alerts": 1, "share_pct": 50.0},
        {"source": "decrypt", "blocked_alerts": 1, "share_pct": 50.0},
    ]
