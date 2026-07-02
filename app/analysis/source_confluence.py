"""Cross-Source-Agreement (Confluence) — count independent sources reporting
the same asset+direction inside a backward-looking time window.

Goal-pin 2026-05-16 V3 (P1 in the 7-step plan). Single-source alerts are
weaker than alerts confirmed by multiple independent sources — but the
SignalGenerator and the eligibility filter today treat every alert in
isolation. This module derives a per-alert "confluence_count" from the
alert audit stream without touching live signal flow.

Design contract:
- Backward-looking only. For an alert dispatched at ``t``, we count alerts
  in the half-open interval ``[t - window, t)`` from OTHER sources with
  the same ``(asset, direction)``. No look-ahead — the score is what was
  observable when the alert fired.
- Direction comes from ``sentiment_label`` (only ``bullish``/``bearish``
  count; other sentiments produce ``direction="none"`` and a confluence
  score of 0).
- Independence = distinct ``source_name`` (case-folded). Multi-mentions
  from the same source inside the window count as ONE. Documents without
  ``source_name`` are skipped — they cannot prove independence.
- Multi-asset alerts contribute per-asset. An alert tagged ``[BTC/USDT,
  ETH/USDT]`` joins both BTC and ETH confluence sets independently.

KAI-no-prediction-rule (memory ``feedback_kai_no_prediction``): confluence
is an OBSERVATION ("how many independent sources reported the same
direction in the last hour"), NOT a prediction ("this alert will hit"). The
SignalGenerator integration is deferred until ``confluence_count``-vs-
forward-outcome correlation is measured on the shadow audit stream.

Limitations (documented, V1-acceptable):
- Cluster-Reuters effect: if 5 outlets republish the same press release,
  they all count as 5 distinct sources. A V2 with content-hash dedup or
  embedding-similarity clustering would refine this, but isn't worth the
  ML complexity until the V1 score-vs-outcome curve justifies it.
- Direction inference is sentiment-label-only; "neutral" + actionable
  alerts don't currently feed into confluence. That's a known gap that
  the analysis/sentiment_timeseries (V5) would close — out of scope here.

Output is a list of ``ConfluenceObservation`` dataclasses, ready to be
serialised line-by-line into ``artifacts/source_confluence_audit.jsonl``
by ``scripts/source_confluence_recalc.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.alerts.audit import AlertAuditRecord

# Sentiments treated as directional. Mirrors
# ``app/alerts/feature_analysis._DIRECTIONAL_SENTIMENTS`` so the
# confluence dataset stays comparable to ph5_feature_analysis.
_DIRECTIONAL_SENTIMENTS = frozenset({"bullish", "bearish"})

# Default backward-looking window. 60min matches the "near-miss" framing
# operators use when reading the channel: same-hour cluster = related;
# yesterday's bullish + today's bullish = different events.
DEFAULT_WINDOW_SECONDS: int = 60 * 60

Direction = Literal["bullish", "bearish", "none"]


@dataclass(frozen=True)
class ConfluenceObservation:
    """One confluence record per (document_id, asset)."""

    document_id: str
    symbol: str  # the asset this observation is about (e.g. "BTC/USDT")
    direction: Direction
    confluence_count: int  # # of OTHER sources in window with matching (asset, direction)
    confluence_sources: list[str] = field(default_factory=list)
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    dispatched_at: str = ""
    computed_at: str = ""

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": "v1",
            "report_type": "source_confluence_observation",
            "document_id": self.document_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "confluence_count": self.confluence_count,
            "confluence_sources": list(self.confluence_sources),
            "window_seconds": self.window_seconds,
            "dispatched_at": self.dispatched_at,
            "computed_at": self.computed_at,
        }


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _direction_from_sentiment(sentiment_label: str | None) -> Direction:
    if sentiment_label is None:
        return "none"
    cleaned = sentiment_label.strip().lower()
    if cleaned in _DIRECTIONAL_SENTIMENTS:
        return cleaned  # type: ignore[return-value]
    return "none"


def _normalize_assets(assets: list[str]) -> list[str]:
    """Lower-cased+stripped variant for matching, original capitalisation kept
    in the output. Empty / whitespace-only entries are dropped.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in assets or []:
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip()
        if not cleaned:
            continue
        key = cleaned.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def compute_confluence(
    audits: list[AlertAuditRecord],
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    now_utc: datetime | None = None,
) -> list[ConfluenceObservation]:
    """Compute per-document, per-asset confluence observations.

    Algorithm:
    1. Index audit records by (asset, direction) → sorted [(dt, source)] list.
       Multi-asset records expand to multiple index entries; digests and
       non-directional sentiments are skipped at index-build time.
    2. For each record we want to score (same skip rules), look up the index
       slice in ``[dt - window, dt)`` and count DISTINCT source_names other
       than the current record's source.

    Complexity: O(N log N) build + O(N × k) lookup where k is the average
    number of in-window neighbours. Linear-scan reverse for typical recent-
    first ordering — bisect not needed at N≈7800 alerts.

    Returns one ``ConfluenceObservation`` per (document, asset) pair, even
    when ``confluence_count == 0``. The zero rows are useful: they let the
    operator see "we DID score this alert and found it standalone", which
    is different from "we never scored this alert at all".
    """
    now = now_utc or datetime.now(UTC)
    now_iso = now.isoformat()
    window = timedelta(seconds=window_seconds)

    # Build (asset, direction) → sorted list of (dt, source_lower, doc_id).
    index: dict[tuple[str, Direction], list[tuple[datetime, str, str]]] = {}
    for rec in audits:
        if rec.is_digest:
            continue
        direction = _direction_from_sentiment(rec.sentiment_label)
        if direction == "none":
            continue
        source = (rec.source_name or "").strip().lower()
        if not source:
            continue
        dt = _parse_iso(rec.dispatched_at)
        if dt is None:
            continue
        for asset in _normalize_assets(list(rec.affected_assets or [])):
            key = (asset.upper(), direction)
            index.setdefault(key, []).append((dt, source, rec.document_id))

    for index_key in index:
        index[index_key].sort(key=lambda triple: triple[0])

    # Score every directional, sourced, dispatched alert.
    observations: list[ConfluenceObservation] = []
    for rec in audits:
        if rec.is_digest:
            continue
        direction = _direction_from_sentiment(rec.sentiment_label)
        if direction == "none":
            continue
        source = (rec.source_name or "").strip().lower()
        if not source:
            continue
        dt = _parse_iso(rec.dispatched_at)
        if dt is None:
            continue

        for asset in _normalize_assets(list(rec.affected_assets or [])):
            key = (asset.upper(), direction)
            slice_ = index.get(key, [])
            window_start = dt - window
            confluent_sources: set[str] = set()
            # Linear reverse scan — exits as soon as we drop out of the window.
            for other_dt, other_source, other_doc in reversed(slice_):
                if other_dt >= dt:
                    continue  # strict before-this; tie-broken by doc identity
                if other_dt < window_start:
                    break
                if other_doc == rec.document_id:
                    continue  # self
                if other_source == source:
                    continue  # same source within window: still one vote
                confluent_sources.add(other_source)
            observations.append(
                ConfluenceObservation(
                    document_id=rec.document_id,
                    symbol=asset,
                    direction=direction,
                    confluence_count=len(confluent_sources),
                    confluence_sources=sorted(confluent_sources),
                    window_seconds=window_seconds,
                    dispatched_at=rec.dispatched_at,
                    computed_at=now_iso,
                )
            )

    return observations


def summarize_confluence(
    observations: list[ConfluenceObservation],
) -> dict[str, object]:
    """High-level operator summary of a batch of observations.

    Returns the aggregate distribution so the operator can ask:
    - "what fraction of alerts is standalone (confluence=0)?"
    - "how often do we see 2+ source agreement?"
    Plus per-symbol max-confluence so concentration is visible.
    """
    if not observations:
        return {
            "n_observations": 0,
            "distribution": {},
            "max_confluence_by_symbol": {},
        }

    distribution: dict[str, int] = {}
    for obs in observations:
        bucket = (
            "0"
            if obs.confluence_count == 0
            else "1"
            if obs.confluence_count == 1
            else "2-4"
            if obs.confluence_count <= 4
            else "5+"
        )
        distribution[bucket] = distribution.get(bucket, 0) + 1

    max_by_symbol: dict[str, int] = {}
    for obs in observations:
        prev = max_by_symbol.get(obs.symbol, -1)
        if obs.confluence_count > prev:
            max_by_symbol[obs.symbol] = obs.confluence_count

    return {
        "n_observations": len(observations),
        "distribution": distribution,
        "max_confluence_by_symbol": max_by_symbol,
    }


__all__ = [
    "DEFAULT_WINDOW_SECONDS",
    "ConfluenceObservation",
    "Direction",
    "compute_confluence",
    "summarize_confluence",
]
