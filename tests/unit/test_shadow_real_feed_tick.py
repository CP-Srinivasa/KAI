"""Wiring contract of the shadow-real-feed tick entrypoint (Issue #175, S4).

The driver itself (`run_shadow_real_feed`) is covered by its own suite; these
tests pin the ENTRYPOINT contract:
  - flag OFF → exit 0, the DB fetch callable is NEVER invoked (cheap no-op),
  - flag ON → the tick passes a fetch callable + bounded limit to the driver
    and exits 0 on success / 1 on an unexpected driver error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import shadow_real_feed_tick as tick  # noqa: E402


@pytest.mark.asyncio
async def test_flag_off_is_dbfree_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("EXECUTION_SHADOW_REAL_GENERATOR", raising=False)
    monkeypatch.chdir(tmp_path)  # funnel artifact lands in tmp

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("DB must not be touched when the flag is off")

    monkeypatch.setattr(tick, "build_session_factory", _boom)
    assert await tick._main() == 0
    # the driver wrote its honest flag_off funnel record
    funnel = tmp_path / "artifacts" / "shadow_real_feed_funnel.jsonl"
    assert funnel.exists()
    assert '"flag_off"' in funnel.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_flag_on_invokes_driver_with_fetch_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    async def _fake_driver(**kw):
        seen.update(kw)
        return {"enabled": True, "seen": 0, "eligible": 0, "injected": 0}

    monkeypatch.setattr(tick, "run_shadow_real_feed", _fake_driver)
    assert await tick._main() == 0
    assert callable(seen["fetch_recent_analyzed"])
    assert seen["limit"] == tick._LIMIT_PER_TICK


def test_unexpected_driver_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _explode(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(tick, "run_shadow_real_feed", _explode)
    assert tick.main() == 1
