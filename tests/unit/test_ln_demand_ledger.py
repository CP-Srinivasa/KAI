"""U2 — L402 demand-telemetry ledger + privacy fingerprint.

The ledger is the capital-free measurement instrument for the G0 demand probe. Core
guarantees under test: correct event schema, fail-soft writes, and that a RAW client
IP never lands in the ledger (only a salted, truncated fingerprint).
"""

from __future__ import annotations

from pathlib import Path

from app.lightning.demand_ledger import (
    ACCESS_GRANTED,
    CHALLENGE_MINTED,
    append_demand_event,
    read_recent_demand_events,
    requester_fingerprint,
)


def test_fingerprint_is_not_the_raw_ip_and_is_stable() -> None:
    fp = requester_fingerprint("203.0.113.7", secret="s3cr3t")
    assert fp and "203.0.113.7" not in fp
    assert fp == requester_fingerprint("203.0.113.7", secret="s3cr3t")  # stable within a deployment


def test_fingerprint_distinguishes_ips_and_depends_on_secret() -> None:
    a = requester_fingerprint("203.0.113.7", secret="s")
    b = requester_fingerprint("203.0.113.8", secret="s")
    c = requester_fingerprint("203.0.113.7", secret="other")
    assert a != b and a != c


def test_fingerprint_empty_ip_is_empty() -> None:
    assert requester_fingerprint("", secret="s") == ""


def test_append_and_read_challenge_event(tmp_path: Path) -> None:
    p = tmp_path / "demand.jsonl"
    assert append_demand_event(
        CHALLENGE_MINTED,
        scope="fee-series",
        requester_fp="abc123",
        price_sat=100,
        payment_hash="ff",
        path=p,
    )
    rows = read_recent_demand_events(p, limit=0)
    assert len(rows) == 1
    r = rows[0]
    assert r["event"] == CHALLENGE_MINTED and r["scope"] == "fee-series"
    assert r["requester_fp"] == "abc123" and r["price_sat"] == 100 and r["payment_hash"] == "ff"
    assert "ts" in r


def test_ledger_never_contains_a_raw_ip(tmp_path: Path) -> None:
    p = tmp_path / "demand.jsonl"
    fp = requester_fingerprint("198.51.100.23", secret="k")
    append_demand_event(
        CHALLENGE_MINTED, scope="fee-series", requester_fp=fp, price_sat=100, path=p
    )
    append_demand_event(ACCESS_GRANTED, scope="fee-series", payment_hash="aa", path=p)
    raw = p.read_text(encoding="utf-8")
    assert "198.51.100.23" not in raw


def test_append_is_fail_soft_on_bad_path() -> None:
    """A telemetry write must NEVER raise — it is best-effort and must not break the
    request that triggered it."""
    bad = Path("\0invalid") / "x.jsonl"
    assert append_demand_event(CHALLENGE_MINTED, scope="s", path=bad) is False
