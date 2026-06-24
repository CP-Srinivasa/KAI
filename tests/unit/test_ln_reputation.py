"""Unit tests for Lightning routing-fee report + node reputation telemetry.

Sprint-1 LN-observability (read-only, default-off, no capital path):

  * ``get_fee_report`` — routing-income summary from lnd ``feereport``;
    disabled/unreachable → ``available=False`` (fail-closed), ok → parsed sums.
  * ``record_ln_reputation`` — append-only health snapshot. Unlike the L1 fee
    shadow, a reachable-failure (``unavailable``) IS recorded because downtime is
    a reputation signal (uptime%); only the default-off ``disabled`` case is a
    no-op. Routing income is best-effort and never blocks the record.
  * ``read_recent_ln_reputation`` — tolerant tail reader for the endpoint.
"""

from __future__ import annotations

import json

import httpx

from app.core.settings import LightningSettings
from app.lightning import adapter as adapter_mod
from app.lightning import reputation as rep
from app.lightning.adapter import LightningFeeReport, LightningNodeStatus, get_fee_report
from app.lightning.client import LndRestClient
from app.lightning.reputation import read_recent_ln_reputation, record_ln_reputation


def _client_with(transport: httpx.MockTransport):
    return lambda cfg: LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=transport
    )


# --- adapter.get_fee_report ------------------------------------------------------


async def test_fee_report_disabled_is_unavailable_no_network() -> None:
    fr = await get_fee_report(LightningSettings(enabled=False))
    assert fr.available is False
    assert fr.day_fee_sat == 0 and fr.week_fee_sat == 0 and fr.month_fee_sat == 0


async def test_fee_report_ok_parses_sums(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/fees"
        return httpx.Response(
            200, json={"day_fee_sum": "10", "week_fee_sum": "70", "month_fee_sum": "300"}
        )

    monkeypatch.setattr(adapter_mod, "_build_client", _client_with(httpx.MockTransport(handler)))
    fr = await get_fee_report(LightningSettings(enabled=True, macaroon_hex="ab"))
    assert fr.available is True
    assert fr.day_fee_sat == 10
    assert fr.week_fee_sat == 70
    assert fr.month_fee_sat == 300


async def test_fee_report_node_error_is_fail_closed(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="node starting")

    monkeypatch.setattr(adapter_mod, "_build_client", _client_with(httpx.MockTransport(handler)))
    fr = await get_fee_report(LightningSettings(enabled=True, macaroon_hex="ab"))
    assert fr.available is False


# --- reputation recorder ---------------------------------------------------------


async def test_reputation_disabled_records_nothing(tmp_path, monkeypatch) -> None:
    async def _disabled(cfg=None):
        return LightningNodeStatus.disabled()

    monkeypatch.setattr(rep, "get_node_status", _disabled)
    out = tmp_path / "rep.jsonl"
    assert await record_ln_reputation(path=out) is None
    assert not out.exists()


async def test_reputation_unavailable_records_downtime(tmp_path, monkeypatch) -> None:
    async def _unavail(cfg=None):
        return LightningNodeStatus.unavailable("node down")

    async def _fee_must_not_run(cfg=None):
        raise AssertionError("fee_report must not be called when node is unavailable")

    monkeypatch.setattr(rep, "get_node_status", _unavail)
    monkeypatch.setattr(rep, "get_fee_report", _fee_must_not_run)
    out = tmp_path / "rep.jsonl"

    record = await record_ln_reputation(path=out)
    assert record is not None
    assert record.state == "unavailable"
    assert record.reachable is False
    assert record.routing_fee_day_sat is None
    line = json.loads(out.read_text(encoding="utf-8").strip())
    assert line["state"] == "unavailable"
    assert line["reachable"] is False


async def test_reputation_ok_records_full_with_routing(tmp_path, monkeypatch) -> None:
    async def _ok(cfg=None):
        return LightningNodeStatus(
            state="ok",
            reachable=True,
            info_available=True,
            synced_to_chain=True,
            synced_to_graph=True,
            num_peers=4,
            num_active_channels=2,
            num_pending_channels=1,
            channel_local_sat=5000,
            channel_remote_sat=1500,
            wallet_confirmed_sat=798269,
            wallet_total_sat=800000,
            balances_available=True,
            alias="FlashGordancom",
            identity_pubkey="024a7f",
        )

    async def _fee(cfg=None):
        return LightningFeeReport(available=True, day_fee_sat=5, week_fee_sat=70, month_fee_sat=300)

    monkeypatch.setattr(rep, "get_node_status", _ok)
    monkeypatch.setattr(rep, "get_fee_report", _fee)
    out = tmp_path / "rep.jsonl"

    record = await record_ln_reputation(path=out)
    assert record is not None
    assert record.state == "ok"
    assert record.num_peers == 4
    assert record.num_active_channels == 2
    assert record.channel_remote_sat == 1500
    assert record.wallet_confirmed_sat == 798269
    assert record.routing_fee_day_sat == 5
    assert record.routing_fee_month_sat == 300
    assert record.alias == "FlashGordancom"

    await record_ln_reputation(path=out)  # append, not overwrite
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


async def test_reputation_ok_fee_report_failure_is_best_effort(tmp_path, monkeypatch) -> None:
    async def _ok(cfg=None):
        return LightningNodeStatus(state="ok", reachable=True, info_available=True, num_peers=1)

    async def _fee_boom(cfg=None):
        raise RuntimeError("fees endpoint exploded")

    monkeypatch.setattr(rep, "get_node_status", _ok)
    monkeypatch.setattr(rep, "get_fee_report", _fee_boom)
    out = tmp_path / "rep.jsonl"

    record = await record_ln_reputation(path=out)
    assert record is not None
    assert record.state == "ok"
    assert record.routing_fee_day_sat is None  # routing best-effort, record survives


# --- reader ----------------------------------------------------------------------


def test_read_recent_missing_file_returns_empty(tmp_path) -> None:
    assert read_recent_ln_reputation(path=tmp_path / "nope.jsonl") == []


def test_read_recent_returns_records_newest_last_and_honours_limit(tmp_path) -> None:
    out = tmp_path / "rep.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for i in range(5):
            fh.write(json.dumps({"ts": f"t{i}", "state": "ok", "num_peers": i}) + "\n")
    out.write_text(out.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # blank tail line

    recs = read_recent_ln_reputation(path=out, limit=3)
    assert [r["num_peers"] for r in recs] == [2, 3, 4]  # last 3, newest last, blank skipped


# --- ops ledger reader (audit trail; writer is gated/Sprint 4-5) -----------------


def test_ops_reader_missing_file_returns_empty(tmp_path) -> None:
    from app.lightning.ops_ledger import read_recent_ln_ops

    assert read_recent_ln_ops(path=tmp_path / "ops.jsonl") == []


def test_ops_reader_reads_records(tmp_path) -> None:
    from app.lightning.ops_ledger import read_recent_ln_ops

    out = tmp_path / "ops.jsonl"
    out.write_text(
        json.dumps({"action": "create_invoice", "state": "planned"}) + "\n", encoding="utf-8"
    )
    ops = read_recent_ln_ops(path=out)
    assert len(ops) == 1 and ops[0]["action"] == "create_invoice"
