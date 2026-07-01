"""Tests for the RaspiBlitz info-mirror endpoint (read-only, default-off, fail-soft)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.api.routers import node_blitz
from app.core.lightning_settings import LightningSettings


def _settings_stub(monkeypatch, **overrides) -> None:
    ln = LightningSettings(**overrides)
    monkeypatch.setattr(node_blitz, "get_settings", lambda: SimpleNamespace(lightning=ln))


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0, hang: bool = False) -> None:
        self._stdout = stdout
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(3600)
        return self._stdout, b""

    def kill(self) -> None:
        self.killed = True


async def test_disabled_is_fail_soft(monkeypatch) -> None:
    _settings_stub(monkeypatch, blitz_info_enabled=False)
    result = await node_blitz._fetch_blitz_info()
    assert result["available"] is False
    assert result["reason"] == "disabled"


async def test_missing_key_is_fail_soft(monkeypatch) -> None:
    _settings_stub(monkeypatch, blitz_info_enabled=True, blitz_info_ssh_key_path="")
    result = await node_blitz._fetch_blitz_info()
    assert result["available"] is False
    assert "not configured" in result["reason"]


async def test_happy_path_parses_node_json(monkeypatch) -> None:
    _settings_stub(
        monkeypatch,
        blitz_info_enabled=True,
        blitz_info_ssh_key_path="/x/key",
        blitz_info_ssh_target="admin@node",
    )
    payload = {"hostname": "raspberrypi", "lnd": {"peers": 4}}

    async def fake_exec(*cmd, **kwargs):
        assert "admin@node" in cmd
        assert "BatchMode=yes" in cmd
        return _FakeProc(json.dumps(payload).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await node_blitz._fetch_blitz_info()
    assert result["available"] is True
    assert result["data"]["hostname"] == "raspberrypi"


async def test_ssh_timeout_is_fail_soft_and_kills(monkeypatch) -> None:
    _settings_stub(
        monkeypatch,
        blitz_info_enabled=True,
        blitz_info_ssh_key_path="/x/key",
        blitz_info_timeout_seconds=0.05,
    )
    proc = _FakeProc(b"", hang=True)

    async def fake_exec(*cmd, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await node_blitz._fetch_blitz_info()
    assert result["available"] is False
    assert "timeout" in result["reason"]
    assert proc.killed is True


async def test_nonzero_exit_is_fail_soft(monkeypatch) -> None:
    _settings_stub(monkeypatch, blitz_info_enabled=True, blitz_info_ssh_key_path="/x/key")

    async def fake_exec(*cmd, **kwargs):
        return _FakeProc(b"", returncode=255)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await node_blitz._fetch_blitz_info()
    assert result["available"] is False
    assert "ssh exit 255" in result["reason"]


async def test_bad_json_is_fail_soft(monkeypatch) -> None:
    _settings_stub(monkeypatch, blitz_info_enabled=True, blitz_info_ssh_key_path="/x/key")

    async def fake_exec(*cmd, **kwargs):
        return _FakeProc(b"not-json")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await node_blitz._fetch_blitz_info()
    assert result["available"] is False
    assert "bad JSON" in result["reason"]


@pytest.fixture(autouse=True)
def _reset_cache():
    node_blitz._cache["ts"] = 0.0
    node_blitz._cache["payload"] = None
    yield
    node_blitz._cache["ts"] = 0.0
    node_blitz._cache["payload"] = None
