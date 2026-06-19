"""Unit tests for the on-chain fee shadow recorder (KAI L1, default-off).

Covers: disabled/unavailable → no record + no file, ok → appends records
(append-only, not overwrite), and the captured fields mirror the chain status.
The recorder is decoupled from the trading CostModel and never raises.
"""

from __future__ import annotations

import json

import pytest

from app.chain import fee_shadow as fs
from app.chain.adapter import ChainStatus
from app.chain.fee_shadow import record_onchain_fee_shadow


@pytest.mark.asyncio
async def test_disabled_records_nothing(tmp_path, monkeypatch) -> None:
    async def _disabled(cfg=None):
        return ChainStatus.disabled()

    monkeypatch.setattr(fs, "get_chain_status", _disabled)
    out = tmp_path / "shadow.jsonl"
    assert await record_onchain_fee_shadow(path=out) is None
    assert not out.exists()


@pytest.mark.asyncio
async def test_unavailable_records_nothing(tmp_path, monkeypatch) -> None:
    async def _unavail(cfg=None):
        return ChainStatus.unavailable("node down")

    monkeypatch.setattr(fs, "get_chain_status", _unavail)
    out = tmp_path / "shadow.jsonl"
    assert await record_onchain_fee_shadow(path=out) is None
    assert not out.exists()


@pytest.mark.asyncio
async def test_ok_appends_record_with_fields(tmp_path, monkeypatch) -> None:
    async def _ok(cfg=None):
        return ChainStatus(
            state="ok",
            reachable=True,
            chain="main",
            blocks=953902,
            fee_sat_vb=2.5,
            mempool_tx=7,
        )

    monkeypatch.setattr(fs, "get_chain_status", _ok)
    out = tmp_path / "shadow.jsonl"

    rec = await record_onchain_fee_shadow(path=out)
    assert rec is not None
    assert rec.fee_sat_vb == 2.5 and rec.mempool_tx == 7 and rec.chain == "main"

    await record_onchain_fee_shadow(path=out)  # append, not overwrite
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["chain"] == "main"
    assert first["blocks"] == 953902
    assert first["fee_sat_vb"] == 2.5
    assert first["mempool_tx"] == 7


@pytest.mark.asyncio
async def test_ok_with_missing_fee_records_none_fee(tmp_path, monkeypatch) -> None:
    async def _ok_no_fee(cfg=None):
        return ChainStatus(state="ok", reachable=True, chain="main", blocks=10, fee_sat_vb=None)

    monkeypatch.setattr(fs, "get_chain_status", _ok_no_fee)
    out = tmp_path / "shadow.jsonl"
    rec = await record_onchain_fee_shadow(path=out)
    assert rec is not None and rec.fee_sat_vb is None
    assert json.loads(out.read_text(encoding="utf-8").strip())["fee_sat_vb"] is None
