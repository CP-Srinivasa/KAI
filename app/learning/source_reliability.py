"""Source-Reliability-Loop — close the first real learning circuit.

Goal-pin 2026-05-16 (operator priority chain: Daten → Risiko → Regime → …):
the alert pipeline already records hit/miss/inconclusive outcomes per source
in ``artifacts/alert_outcomes.jsonl``. ``ph5_feature_analysis.json`` consumes
those into per-source precision buckets — but only as a STATIC SNAPSHOT the
operator reads manually. Today's eligibility filter ignores it entirely;
the only source-level lever is hand-curated ``monitor/source_watch.txt``
(D-181 trust boundary) and a hardcoded ``_LOW_PRECISION_SOURCES`` frozenset.

This module turns the manual feedback into an automatic, data-driven one:
- Wilson Lower Bound (95% confidence) per source over a rolling window.
- Tiered classification (``trusted`` / ``neutral`` / ``watch`` / ``low``)
  with explicit min-sample thresholds so cold-start sources don't get
  demoted on n=1.
- JSON output that ``app/alerts/eligibility.py`` reads with the same
  mtime-cache pattern as ``source_watch.txt`` — no worker restart needed
  when the daily cron rewrites the file.

KAI-no-prediction-rule: Wilson Lower Bound is a confidence interval on
the OBSERVED hit-rate — it does not predict future hit-rate. The eligibility
modifier reads it as "this source has historically been at-or-above X%
precision with 95% confidence", never "this source will hit X% in the future".

Memory cross-refs:
- ``feedback_kai_priority_reorder_20260509`` — "Daten" als Punkt 1
- ``feedback_kai_no_prediction`` — probabilistisches Framing
- ``session_2026_05_16_premium_pipeline_hardening`` — Goal-Pin-Trigger
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.alerts.audit import AlertAuditRecord, AlertOutcomeAnnotation

# Wilson score parameters
_DEFAULT_CONFIDENCE_LEVEL: float = 0.95
_Z_95: float = 1.96  # two-sided 95% normal-approx critical value

# Tier thresholds — tuned for the ~25-45% precision baseline observed
# in ph5_feature_analysis (BTC/USDT 41%, ETH/USDT 53%, ETH 35%, SOL 16%).
# These are intentionally CONSERVATIVE: a source needs strong evidence
# (n>=20) before any reliability modifier is applied, and the magnitude
# is small (priority ±1) so a single bad week doesn't permanently block
# a source.
_MIN_N_FOR_DEMOTE: int = 20
_MIN_N_FOR_PROMOTE: int = 30
_WILSON_LOW_THRESHOLD: float = 0.30  # < 30% lower-bound → soft demote
_WILSON_HIGH_THRESHOLD: float = 0.65  # > 65% lower-bound → soft promote

# Window over which outcomes are considered fresh.
_DEFAULT_WINDOW_DAYS: int = 90

# Validated-sample floor for the public Top-N ranking. Mirrors
# hold_metrics.MIN_PER_SOURCE_RESOLVED (kept as a local literal to avoid an
# import cycle; test_source_reliability asserts the two stay equal). A source
# with n below this floor is RANKED but flagged ``provisional`` — it appears in
# the lifecycle ranking without ever earning an eligibility boost (Rail 5,
# fail-closed: a positive priority_modifier already needs n >= MIN_N_FOR_PROMOTE,
# so provisional sources stay neutral by construction).
_MIN_N_FOR_VALIDATED_RANK: int = 50

# Rank-bucket boundaries for the lifecycle tier (Top-10/50/100 per operator
# request 2026-06-23). Position-based, distinct from the reliability tier.
_RANK_TIER_BOUNDS: tuple[tuple[int, str], ...] = ((10, "top10"), (50, "top50"), (100, "top100"))

# FS-3 (#199): source-name tokens for the pre-attribution / legacy bucket. These
# never count as trusted and never carry a positive modifier — legacy evidence
# is not attributable to an active source and must stay separated.
_LEGACY_SOURCE_TOKENS: frozenset[str] = frozenset({"unknown", ""})

ReliabilityTier = Literal["trusted", "neutral", "watch", "low", "insufficient"]


@dataclass(frozen=True)
class SourceReliabilityScore:
    """Per-source reliability snapshot.

    ``priority_modifier`` is the integer that eligibility applies on top of
    the alert's raw priority. Range: {-2, -1, 0, +1}. Default 0 = neutral.
    """

    source_name: str
    hits: int
    miss: int
    n: int
    point_estimate: float | None  # hits / n, or None when n == 0
    wilson_lower_95: float | None  # the load-bearing number for tier
    tier: ReliabilityTier
    priority_modifier: int

    def to_json_dict(self) -> dict[str, object]:
        return {
            "source_name": self.source_name,
            "hits": self.hits,
            "miss": self.miss,
            "n": self.n,
            "point_estimate": self.point_estimate,
            "wilson_lower_95": self.wilson_lower_95,
            "tier": self.tier,
            "priority_modifier": self.priority_modifier,
        }


def wilson_lower_bound(hits: int, n: int, *, z: float = _Z_95) -> float | None:
    """Wilson score lower bound for a binomial proportion.

    Returns ``None`` for ``n == 0`` (no data to bound). For ``hits > n`` we
    clamp to ``n`` defensively — the loader could produce that on edge-case
    annotation re-writes.

    Formula (z = 1.96 for 95% two-sided):
        p̂ = hits / n
        denominator = 1 + z² / n
        center = p̂ + z² / (2n)
        margin = z * sqrt( ( p̂(1-p̂) + z²/(4n) ) / n )
        lower = (center - margin) / denominator
    """
    if n <= 0:
        return None
    hits_clamped = max(0, min(hits, n))
    p_hat = hits_clamped / n
    z_sq = z * z
    denominator = 1.0 + z_sq / n
    center = p_hat + z_sq / (2.0 * n)
    inner = p_hat * (1.0 - p_hat) / n + z_sq / (4.0 * n * n)
    if inner < 0:
        inner = 0.0
    margin = z * math.sqrt(inner)
    lower = (center - margin) / denominator
    # Clip into [0, 1] — math.sqrt drift can flutter outside under FP arithmetic.
    return max(0.0, min(1.0, lower))


def _classify_tier(
    n: int,
    wilson_lower: float | None,
) -> tuple[ReliabilityTier, int]:
    """Map (n, Wilson-Lower) → (tier label, priority modifier).

    Conservative ramp:
    - n < MIN_N_FOR_DEMOTE       → insufficient data, modifier = 0
    - Wilson-Lower < LOW         → low tier, modifier = -2 (hard demote)
    - LOW <= Wilson < ~0.45      → watch tier, modifier = -1 (soft demote)
    - 0.45 <= Wilson < HIGH      → neutral, modifier = 0
    - Wilson >= HIGH AND
      n >= MIN_N_FOR_PROMOTE     → trusted, modifier = +1 (soft promote)
    """
    if wilson_lower is None or n < _MIN_N_FOR_DEMOTE:
        return "insufficient", 0
    if wilson_lower < _WILSON_LOW_THRESHOLD:
        return "low", -2
    if wilson_lower < 0.45:
        return "watch", -1
    if wilson_lower >= _WILSON_HIGH_THRESHOLD and n >= _MIN_N_FOR_PROMOTE:
        return "trusted", 1
    return "neutral", 0


def _rank_to_lifecycle_tier(rank: int) -> str:
    """Map a 1-based ranking position to its Top-N lifecycle bucket."""
    for bound, label in _RANK_TIER_BOUNDS:
        if rank <= bound:
            return label
    return "ranked"


def _build_ranked(scores: dict[str, SourceReliabilityScore]) -> list[dict[str, object]]:
    """Order non-legacy, evidenced sources into a deterministic Top-N ranking.

    Sort key: Wilson-Lower descending (the confidence floor, load-bearing),
    then n descending (more evidence breaks ties), then source_name ascending
    (stable, reproducible across runs — no Date/random in scope). Legacy/unknown
    and n==0 sources are excluded — they cannot hold a rank. Each entry is
    flagged ``provisional`` when its sample is below the validated floor: it
    ranks, but honestly as not-yet-validated and never as an eligibility boost.
    """
    eligible = [
        s
        for s in scores.values()
        if s.n > 0
        and s.wilson_lower_95 is not None
        and s.source_name.strip().lower() not in _LEGACY_SOURCE_TOKENS
    ]
    eligible.sort(key=lambda s: (-(s.wilson_lower_95 or 0.0), -s.n, s.source_name))
    ranked: list[dict[str, object]] = []
    for idx, s in enumerate(eligible, start=1):
        ranked.append(
            {
                "source_name": s.source_name,
                "rank": idx,
                "lifecycle_tier": _rank_to_lifecycle_tier(idx),
                "provisional": s.n < _MIN_N_FOR_VALIDATED_RANK,
                "wilson_lower_95": s.wilson_lower_95,
                "n": s.n,
                "hits": s.hits,
                "point_estimate": s.point_estimate,
                "reliability_tier": s.tier,
            }
        )
    return ranked


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


def _resolve_record_source(
    rec: AlertAuditRecord,
    source_by_doc: dict[str, str],
) -> str | None:
    """Resolve a source label for one alert audit record.

    Prefer the explicit caller-provided map, but fall back to the audit row's
    own persisted fields. Older source-reliability runs only used
    ``source_by_doc``; that dropped rows whose DB/source-map join was missing
    even though the append-only audit row carried ``source_name`` or
    ``provenance.source``.
    """
    candidates = (
        source_by_doc.get(rec.document_id),
        rec.source_name,
        rec.provenance.source if rec.provenance is not None else None,
    )
    for candidate in candidates:
        if candidate is None:
            continue
        cleaned = candidate.strip()
        if cleaned:
            return cleaned
    return None


def build_source_reliability_report(
    audits: list[AlertAuditRecord],
    annotations: list[AlertOutcomeAnnotation],
    source_by_doc: dict[str, str],
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """Compute per-source reliability over the last ``window_days``.

    Inputs are the same shape as ``app/alerts/feature_analysis.py`` uses,
    so the recalc-script can re-use the existing loaders. Returns a JSON-
    serialisable dict suitable for writing to ``monitor/source_reliability.json``.

    Documents without a known source are excluded — they cannot affect a
    source's tier. Source resolution prefers ``source_by_doc`` but falls back
    to the audit row's own ``source_name`` and persisted ``provenance.source``
    so legacy DB/source-map gaps do not silently erase hard outcomes. The
    dispatched_at timestamp is the inclusion criterion (not the annotation
    timestamp) so a source's reliability reflects when it was acting, not when
    the operator happened to annotate.
    """
    now = now_utc or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)

    # Latest outcome wins per doc — matches feature_analysis dedup contract.
    latest_outcome: dict[str, str] = {}
    for ann in annotations:
        latest_outcome[ann.document_id] = ann.outcome

    # Aggregate (hits, miss) per source over the window.
    hits_per_source: dict[str, int] = {}
    miss_per_source: dict[str, int] = {}
    n_per_source: dict[str, int] = {}
    for rec in audits:
        if rec.is_digest:
            continue
        source = _resolve_record_source(rec, source_by_doc)
        if not source:
            continue
        dispatched = _parse_iso(rec.dispatched_at)
        if dispatched is None or dispatched < cutoff:
            continue
        outcome = latest_outcome.get(rec.document_id)
        if outcome == "hit":
            hits_per_source[source] = hits_per_source.get(source, 0) + 1
            n_per_source[source] = n_per_source.get(source, 0) + 1
        elif outcome == "miss":
            miss_per_source[source] = miss_per_source.get(source, 0) + 1
            n_per_source[source] = n_per_source.get(source, 0) + 1
        # inconclusive / unlabeled are excluded — they contribute to neither
        # numerator nor denominator (matches ph5_feature_analysis convention).

    scores: dict[str, SourceReliabilityScore] = {}
    trusted_count = 0
    active_source_count = 0
    legacy_source_count = 0
    for source in sorted(n_per_source.keys()):
        hits = hits_per_source.get(source, 0)
        miss = miss_per_source.get(source, 0)
        n = hits + miss
        point = hits / n if n > 0 else None
        wilson = wilson_lower_bound(hits, n)
        tier, modifier = _classify_tier(n, wilson)
        # FS-3 (#199): the legacy/pre-attribution bucket ("unknown"/empty) must
        # NEVER be promoted to trusted nor carry a positive modifier — its
        # evidence is not attributable to an active source. Demotes are kept.
        is_legacy = source.strip().lower() in _LEGACY_SOURCE_TOKENS
        if is_legacy:
            legacy_source_count += 1
            if tier == "trusted":
                tier = "neutral"
            if modifier > 0:
                modifier = 0
        else:
            active_source_count += 1
            if tier == "trusted":
                trusted_count += 1
        scores[source] = SourceReliabilityScore(
            source_name=source,
            hits=hits,
            miss=miss,
            n=n,
            point_estimate=point,
            wilson_lower_95=wilson,
            tier=tier,
            priority_modifier=modifier,
        )

    return {
        "report_type": "source_reliability",
        "generated_at": now.isoformat(),
        "window_days": window_days,
        "confidence_level": _DEFAULT_CONFIDENCE_LEVEL,
        "thresholds": {
            "min_n_for_demote": _MIN_N_FOR_DEMOTE,
            "min_n_for_promote": _MIN_N_FOR_PROMOTE,
            "wilson_low": _WILSON_LOW_THRESHOLD,
            "wilson_high": _WILSON_HIGH_THRESHOLD,
        },
        # FS-3: explicit active/legacy separation so 0-trusted-but-evidence-exists
        # never reads as healthy, and legacy never inflates the trusted count.
        "trusted_count": trusted_count,
        "active_source_count": active_source_count,
        "legacy_source_count": legacy_source_count,
        "scores": {s: scores[s].to_json_dict() for s in scores},
        # Deterministic Top-N ranking (operator request 2026-06-23). Distinct
        # from ``scores`` (a keyed snapshot): ``ranked`` is the ordered list the
        # lifecycle engine and dashboard read for Top-10/50/100 + provisional.
        "ranked": _build_ranked(scores),
    }


__all__ = [
    "ReliabilityTier",
    "SourceReliabilityScore",
    "build_source_reliability_report",
    "wilson_lower_bound",
]
