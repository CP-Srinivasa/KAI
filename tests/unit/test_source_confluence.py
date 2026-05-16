"""Unit tests for the source-confluence (V3) shadow audit.

The score is observation-only — these tests assert observation correctness,
NOT predictive performance. The no-look-ahead invariant matters: an alert
dispatched at 10:00 may only count other-source alerts strictly before
10:00, never after.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.alerts.audit import AlertAuditRecord
from app.analysis.source_confluence import (
    ConfluenceObservation,
    compute_confluence,
    summarize_confluence,
)

# ── helpers ────────────────────────────────────────────────────────────────


def _audit(
    doc_id: str,
    sentiment: str,
    assets: list[str],
    *,
    source: str,
    dispatched_at: str,
    is_digest: bool = False,
) -> AlertAuditRecord:
    return AlertAuditRecord(
        document_id=doc_id,
        channel="telegram",
        message_id="m",
        is_digest=is_digest,
        dispatched_at=dispatched_at,
        sentiment_label=sentiment,
        affected_assets=assets,
        source_name=source,
    )


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── confluence_count semantics ─────────────────────────────────────────────


def test_isolated_alert_has_zero_confluence() -> None:
    audits = [
        _audit("d1", "bullish", ["BTC/USDT"], source="A", dispatched_at=_iso(_NOW)),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    assert len(obs) == 1
    assert obs[0].confluence_count == 0
    assert obs[0].confluence_sources == []
    assert obs[0].direction == "bullish"
    assert obs[0].symbol == "BTC/USDT"


def test_two_sources_same_window_count_each_other() -> None:
    """Symmetric: each of two alerts counts the other as 1 confluence vote."""
    audits = [
        _audit(
            "d1",
            "bullish",
            ["BTC/USDT"],
            source="A",
            dispatched_at=_iso(_NOW - timedelta(minutes=30)),
        ),
        _audit(
            "d2",
            "bullish",
            ["BTC/USDT"],
            source="B",
            dispatched_at=_iso(_NOW - timedelta(minutes=15)),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    by_doc = {o.document_id: o for o in obs}
    assert by_doc["d1"].confluence_count == 0  # nothing before d1
    assert by_doc["d2"].confluence_count == 1  # d1 is in d2's backward window
    assert by_doc["d2"].confluence_sources == ["a"]


def test_same_source_within_window_counts_as_one() -> None:
    """Two CoinDesk alerts inside the window provide ONE vote from CoinDesk."""
    audits = [
        _audit(
            "d1",
            "bullish",
            ["BTC/USDT"],
            source="A",
            dispatched_at=_iso(_NOW - timedelta(minutes=50)),
        ),
        _audit(
            "d2",
            "bullish",
            ["BTC/USDT"],
            source="A",  # SAME source
            dispatched_at=_iso(_NOW - timedelta(minutes=40)),
        ),
        _audit(
            "d3",
            "bullish",
            ["BTC/USDT"],
            source="B",  # different
            dispatched_at=_iso(_NOW - timedelta(minutes=10)),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    by_doc = {o.document_id: o for o in obs}
    # d3 sees source-A twice (d1 + d2) → still ONE source A, plus zero others.
    # But A==d3's source? No — d3's source is B. So d3 counts: distinct other
    # sources = {A} = 1.
    assert by_doc["d3"].confluence_count == 1


def test_alert_does_not_count_itself() -> None:
    """A single alert with multiple assets must not "self-confluence"."""
    audits = [
        _audit(
            "d1",
            "bullish",
            ["BTC/USDT", "ETH/USDT"],
            source="A",
            dispatched_at=_iso(_NOW),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    assert len(obs) == 2  # one per asset
    for o in obs:
        assert o.confluence_count == 0  # self does not count


def test_opposite_direction_does_not_count() -> None:
    """A bearish ETH and bullish ETH at the same time should NOT confluence."""
    audits = [
        _audit(
            "d1",
            "bullish",
            ["ETH/USDT"],
            source="A",
            dispatched_at=_iso(_NOW - timedelta(minutes=30)),
        ),
        _audit(
            "d2",
            "bearish",
            ["ETH/USDT"],
            source="B",
            dispatched_at=_iso(_NOW - timedelta(minutes=10)),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    by_doc = {o.document_id: o for o in obs}
    assert by_doc["d1"].confluence_count == 0
    assert by_doc["d2"].confluence_count == 0


def test_window_cutoff_excludes_older_alerts() -> None:
    """An alert outside the backward window must NOT contribute."""
    audits = [
        _audit(
            "d_old",
            "bullish",
            ["BTC/USDT"],
            source="A",
            dispatched_at=_iso(_NOW - timedelta(hours=3)),  # outside 60min window
        ),
        _audit(
            "d_fresh",
            "bullish",
            ["BTC/USDT"],
            source="B",
            dispatched_at=_iso(_NOW - timedelta(minutes=5)),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW, window_seconds=3600)
    by_doc = {o.document_id: o for o in obs}
    assert by_doc["d_fresh"].confluence_count == 0


def test_no_lookahead_future_alerts_do_not_count() -> None:
    """No-lookahead invariant: an alert at 10:00 cannot count a 10:30 alert."""
    audits = [
        _audit(
            "d_first",
            "bullish",
            ["BTC/USDT"],
            source="A",
            dispatched_at=_iso(_NOW - timedelta(minutes=30)),
        ),
        _audit(
            "d_later",
            "bullish",
            ["BTC/USDT"],
            source="B",
            dispatched_at=_iso(_NOW),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    by_doc = {o.document_id: o for o in obs}
    assert by_doc["d_first"].confluence_count == 0  # cannot see d_later
    assert by_doc["d_later"].confluence_count == 1  # but sees d_first


def test_multi_asset_alert_emits_one_observation_per_asset() -> None:
    audits = [
        _audit(
            "d1",
            "bullish",
            ["BTC/USDT", "ETH/USDT"],
            source="A",
            dispatched_at=_iso(_NOW - timedelta(minutes=10)),
        ),
        _audit(
            "d2",
            "bullish",
            ["BTC/USDT"],  # only BTC
            source="B",
            dispatched_at=_iso(_NOW),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    d2_obs = [o for o in obs if o.document_id == "d2"]
    assert len(d2_obs) == 1
    assert d2_obs[0].symbol == "BTC/USDT"
    assert d2_obs[0].confluence_count == 1
    # d1 emits BOTH BTC and ETH observations
    d1_obs = sorted([o for o in obs if o.document_id == "d1"], key=lambda o: o.symbol)
    assert [o.symbol for o in d1_obs] == ["BTC/USDT", "ETH/USDT"]
    # d1 is earliest for both — both confluence_count = 0
    assert all(o.confluence_count == 0 for o in d1_obs)


def test_digest_audits_are_skipped() -> None:
    audits = [
        _audit(
            "digest-1",
            "bullish",
            ["BTC/USDT"],
            source="A",
            dispatched_at=_iso(_NOW - timedelta(minutes=10)),
            is_digest=True,
        ),
        _audit(
            "d2",
            "bullish",
            ["BTC/USDT"],
            source="B",
            dispatched_at=_iso(_NOW),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    by_doc = {o.document_id: o for o in obs}
    assert "digest-1" not in by_doc  # digest never scored
    assert by_doc["d2"].confluence_count == 0  # digest doesn't vote either


def test_non_directional_sentiment_is_skipped() -> None:
    """neutral/mixed/None do not score and do not count for others."""
    audits = [
        _audit(
            "d_neutral",
            "neutral",
            ["BTC/USDT"],
            source="A",
            dispatched_at=_iso(_NOW - timedelta(minutes=20)),
        ),
        _audit(
            "d_dir",
            "bullish",
            ["BTC/USDT"],
            source="B",
            dispatched_at=_iso(_NOW),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    by_doc = {o.document_id: o for o in obs}
    assert "d_neutral" not in by_doc  # not scored
    assert by_doc["d_dir"].confluence_count == 0  # neutral does not vote


def test_missing_source_is_skipped() -> None:
    """Sourceless audits cannot prove independence — excluded everywhere."""
    audits = [
        _audit(
            "d_nosrc",
            "bullish",
            ["BTC/USDT"],
            source="",
            dispatched_at=_iso(_NOW - timedelta(minutes=10)),
        ),
        _audit(
            "d2",
            "bullish",
            ["BTC/USDT"],
            source="B",
            dispatched_at=_iso(_NOW),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    by_doc = {o.document_id: o for o in obs}
    assert "d_nosrc" not in by_doc
    assert by_doc["d2"].confluence_count == 0


def test_case_insensitive_source_dedup() -> None:
    """Source 'CoinDesk' and 'coindesk' inside window count as ONE vote."""
    audits = [
        _audit(
            "d1",
            "bullish",
            ["BTC/USDT"],
            source="CoinDesk",
            dispatched_at=_iso(_NOW - timedelta(minutes=40)),
        ),
        _audit(
            "d2",
            "bullish",
            ["BTC/USDT"],
            source="coindesk",  # different casing
            dispatched_at=_iso(_NOW - timedelta(minutes=20)),
        ),
        _audit(
            "d3",
            "bullish",
            ["BTC/USDT"],
            source="Decrypt",
            dispatched_at=_iso(_NOW),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    by_doc = {o.document_id: o for o in obs}
    # d3 sees: CoinDesk (or coindesk — same source) + nothing else = 1 vote
    assert by_doc["d3"].confluence_count == 1
    assert by_doc["d3"].confluence_sources == ["coindesk"]


def test_case_insensitive_asset_match() -> None:
    """'BTC/USDT' and 'btc/usdt' must match for confluence."""
    audits = [
        _audit(
            "d1",
            "bullish",
            ["btc/usdt"],
            source="A",
            dispatched_at=_iso(_NOW - timedelta(minutes=20)),
        ),
        _audit(
            "d2",
            "bullish",
            ["BTC/USDT"],
            source="B",
            dispatched_at=_iso(_NOW),
        ),
    ]
    obs = compute_confluence(audits, now_utc=_NOW)
    by_doc = {o.document_id: o for o in obs}
    assert by_doc["d2"].confluence_count == 1


# ── ConfluenceObservation.to_json_dict round-trip ──────────────────────────


def test_observation_round_trips_via_json_dict() -> None:
    obs = ConfluenceObservation(
        document_id="d1",
        symbol="BTC/USDT",
        direction="bullish",
        confluence_count=2,
        confluence_sources=["a", "b"],
        window_seconds=3600,
        dispatched_at="2026-05-16T12:00:00+00:00",
        computed_at="2026-05-16T12:30:00+00:00",
    )
    d = obs.to_json_dict()
    assert d["report_type"] == "source_confluence_observation"
    assert d["confluence_count"] == 2
    assert d["confluence_sources"] == ["a", "b"]
    assert d["window_seconds"] == 3600


# ── summarize_confluence ──────────────────────────────────────────────────


def test_summary_distribution_buckets() -> None:
    observations = [
        ConfluenceObservation("d1", "BTC/USDT", "bullish", 0, [], 3600, "", ""),
        ConfluenceObservation("d2", "BTC/USDT", "bullish", 1, ["a"], 3600, "", ""),
        ConfluenceObservation("d3", "BTC/USDT", "bullish", 3, ["a", "b", "c"], 3600, "", ""),
        ConfluenceObservation("d4", "ETH/USDT", "bullish", 7, list("abcdefg"), 3600, "", ""),
    ]
    s = summarize_confluence(observations)
    assert s["n_observations"] == 4
    assert s["distribution"] == {"0": 1, "1": 1, "2-4": 1, "5+": 1}
    assert s["max_confluence_by_symbol"]["BTC/USDT"] == 3
    assert s["max_confluence_by_symbol"]["ETH/USDT"] == 7


def test_summary_empty_input() -> None:
    s = summarize_confluence([])
    assert s["n_observations"] == 0
    assert s["distribution"] == {}
    assert s["max_confluence_by_symbol"] == {}
