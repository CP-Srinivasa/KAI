"""Source Reputation Engine — multi-dimensional, learning, advisory-only.

Watchdog goal (2026-06-05): turn the existing single-axis source signals into
one explicit, auditable *reputation* score per source and gate how that source
may be USED — never whether it may *act*.

This module deliberately does **not** invent new evidence. It is a pure scoring
core that fuses signals KAI already produces:

- ``app/learning/source_reliability.py``     → historical_accuracy
  (Wilson lower-bound on the realised hit-rate — a confidence interval on the
  OBSERVED hit-rate, never a prediction; KAI-no-prediction rule).
- ``app/risk/manipulation_detection_models.SourceTrustReport``
  → manipulation_probability, trust_score, historical_reliability.
- ``app/observability/edge_report`` / outcome linking
  → realized_signal_quality.
- ``app/analysis/source_confluence`` / dedup → conflict_rate, independence.

Every dimension is optional. Missing dimensions fall back to an explicit,
conservative neutral default and are flagged in ``provided`` / ``data_completeness``
so the operator can always see how much of a score is real evidence versus
cold-start neutrality. A source with little evidence lands in the conservative
middle band (supporting evidence only), never in a high-trust band.

Score (exact operator-specified weights; do NOT renormalise — the usage-gate
thresholds are calibrated against these exact weights):

    source_reputation =
        0.22 * historical_accuracy
      + 0.15 * timeliness
      + 0.14 * originality
      + 0.12 * independence
      + 0.12 * domain_relevance
      + 0.10 * realized_signal_quality
      - 0.08 * conflict_rate
      - 0.07 * manipulation_probability

Positive weights sum to 0.85, so the maximum achievable reputation is 0.85 —
the ">0.80 high-trust" band is intentionally reachable only by sources that are
near-perfect on every positive axis with zero conflict/manipulation. That is by
design: high trust must be earned, not assumed.

``correction_history`` and ``bot_probability`` are tracked and reported (they
inform the operator and can feed future weighting) but are **not** part of the
load-bearing score, matching the operator's exact formula.

SAFETY INVARIANT (non-negotiable, enforced + tested):
    No source — at ANY reputation, including >0.80 — may alone trigger
    execution. This engine emits an advisory *usage tier* only. Every score
    carries ``can_trigger_execution_alone = False`` and ``max_role = "support"``.
    Execution authority lives in the risk-gate chain and the entry-mode kill
    switch, never here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

# ---------------------------------------------------------------------------
# Weights — operator-specified, exact. Keys map 1:1 to scored dimensions.
# ---------------------------------------------------------------------------
WEIGHTS: dict[str, float] = {
    "historical_accuracy": 0.22,
    "timeliness": 0.15,
    "originality": 0.14,
    "independence": 0.12,
    "domain_relevance": 0.12,
    "realized_signal_quality": 0.10,
    "conflict_rate": -0.08,
    "manipulation_probability": -0.07,
}

# Dimensions that count toward ``data_completeness`` (the eight scored axes).
_SCORED_DIMS: tuple[str, ...] = tuple(WEIGHTS.keys())

# Positive-axis unknown default. 0.5 == "unknown", matching the
# ``SourceTrustReport.historical_reliability`` convention (0.5 = unknown).
_NEUTRAL_POSITIVE: float = 0.5
# Negative-axis unknown default. Absence of detected conflict / manipulation
# evidence is scored as 0.0 — we never fabricate manipulation probability.
_NEUTRAL_NEGATIVE: float = 0.0

# Maximum achievable reputation given the positive weights (0.85). Documented
# so the >0.80 band semantics are explicit, not surprising.
MAX_REPUTATION: float = sum(w for w in WEIGHTS.values() if w > 0)

# Usage-gate thresholds (operator-specified). Boundary convention: a boundary
# value is assigned to the HIGHER band (>= lower edge).
_GATE_RESEARCH_MAX: float = 0.30  # < 0.30          → research_only
_GATE_SUPPORT_MAX: float = 0.60  # [0.30, 0.60)     → supporting_evidence
_GATE_SIGNAL_MAX: float = 0.80  # [0.60, 0.80)      → signal_support
#                                  >= 0.80           → high_trust_support

# Below this completeness fraction the score is flagged low-confidence.
_LOW_CONFIDENCE_BELOW: float = 0.5

UsageTier = Literal[
    "research_only",
    "supporting_evidence",
    "signal_support",
    "high_trust_support",
]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass(frozen=True)
class SourceReputationInputs:
    """Per-source evidence. Every dimension is optional (None == no data).

    Values are expected in [0, 1]; out-of-range inputs are clamped defensively.
    ``correction_history`` and ``bot_probability`` are tracked/reported but not
    part of the weighted score (matches the operator's exact formula).
    """

    source_id: str
    source_type: str = "unresolved_source"
    historical_accuracy: float | None = None
    timeliness: float | None = None
    originality: float | None = None
    independence: float | None = None
    domain_relevance: float | None = None
    realized_signal_quality: float | None = None
    conflict_rate: float | None = None
    manipulation_probability: float | None = None
    # Tracked-only (not weighted):
    correction_history: float | None = None
    bot_probability: float | None = None
    sample_size: int = 0


@dataclass(frozen=True)
class SourceReputationScore:
    """Auditable per-source reputation result."""

    source_id: str
    source_type: str
    source_reputation: float  # clamped [0, 1]
    raw_score: float  # pre-clamp weighted sum (can be < 0)
    usage_tier: UsageTier
    # Hard safety invariant — always False, for every tier.
    can_trigger_execution_alone: bool
    max_role: Literal["support"]
    dimensions: dict[str, float]  # effective values used in the score
    tracked: dict[str, float | None]  # correction_history, bot_probability
    provided: dict[str, bool]  # which scored dims had real evidence
    data_completeness: float  # fraction of scored dims with real evidence
    low_confidence: bool
    sample_size: int
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_reputation": round(self.source_reputation, 6),
            "raw_score": round(self.raw_score, 6),
            "usage_tier": self.usage_tier,
            "can_trigger_execution_alone": self.can_trigger_execution_alone,
            "max_role": self.max_role,
            "dimensions": {k: round(v, 6) for k, v in self.dimensions.items()},
            "tracked": self.tracked,
            "provided": self.provided,
            "data_completeness": round(self.data_completeness, 6),
            "low_confidence": self.low_confidence,
            "sample_size": self.sample_size,
            "notes": list(self.notes),
        }


def classify_usage_tier(reputation: float) -> UsageTier:
    """Map a reputation in [0, 1] to its advisory usage tier.

    Boundaries belong to the higher band:
        < 0.30            → research_only
        [0.30, 0.60)      → supporting_evidence
        [0.60, 0.80)      → signal_support
        >= 0.80           → high_trust_support
    """
    if reputation < _GATE_RESEARCH_MAX:
        return "research_only"
    if reputation < _GATE_SUPPORT_MAX:
        return "supporting_evidence"
    if reputation < _GATE_SIGNAL_MAX:
        return "signal_support"
    return "high_trust_support"


def _effective(value: float | None, *, negative: bool) -> tuple[float, bool]:
    """Resolve a dimension to (effective_value, was_provided)."""
    if value is None:
        return (_NEUTRAL_NEGATIVE if negative else _NEUTRAL_POSITIVE), False
    return _clamp01(float(value)), True


def score_source_reputation(inp: SourceReputationInputs) -> SourceReputationScore:
    """Compute the multi-dimensional reputation for one source.

    Pure function — no IO, deterministic. Missing dimensions use conservative
    neutral defaults and are flagged in ``provided`` / ``data_completeness``.
    """
    negative_dims = {"conflict_rate", "manipulation_probability"}
    dimensions: dict[str, float] = {}
    provided: dict[str, bool] = {}
    raw_value_by_dim = {
        "historical_accuracy": inp.historical_accuracy,
        "timeliness": inp.timeliness,
        "originality": inp.originality,
        "independence": inp.independence,
        "domain_relevance": inp.domain_relevance,
        "realized_signal_quality": inp.realized_signal_quality,
        "conflict_rate": inp.conflict_rate,
        "manipulation_probability": inp.manipulation_probability,
    }
    for dim in _SCORED_DIMS:
        eff, was = _effective(raw_value_by_dim[dim], negative=dim in negative_dims)
        dimensions[dim] = eff
        provided[dim] = was

    raw_score = sum(WEIGHTS[dim] * dimensions[dim] for dim in _SCORED_DIMS)
    reputation = _clamp01(raw_score)
    tier = classify_usage_tier(reputation)

    n_provided = sum(1 for dim in _SCORED_DIMS if provided[dim])
    completeness = n_provided / len(_SCORED_DIMS)
    low_conf = completeness < _LOW_CONFIDENCE_BELOW

    notes: list[str] = []
    if low_conf:
        notes.append(
            f"low_confidence: only {n_provided}/{len(_SCORED_DIMS)} scored "
            "dimensions backed by real evidence; remainder uses neutral defaults"
        )
    if not provided["manipulation_probability"]:
        notes.append("manipulation_probability unobserved → defaulted to 0.0")
    if reputation >= _GATE_SIGNAL_MAX and inp.sample_size < 20:
        notes.append(
            f"high reputation on thin sample (n={inp.sample_size}); treat tier "
            "as provisional until more outcomes accrue"
        )

    return SourceReputationScore(
        source_id=inp.source_id,
        source_type=inp.source_type,
        source_reputation=reputation,
        raw_score=raw_score,
        usage_tier=tier,
        can_trigger_execution_alone=False,
        max_role="support",
        dimensions=dimensions,
        tracked={
            "correction_history": (
                None if inp.correction_history is None else _clamp01(float(inp.correction_history))
            ),
            "bot_probability": (
                None if inp.bot_probability is None else _clamp01(float(inp.bot_probability))
            ),
        },
        provided=provided,
        data_completeness=completeness,
        low_confidence=low_conf,
        sample_size=inp.sample_size,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Thin adapters — map existing KAI reports into reputation inputs without
# duplicating their logic. These are the wiring points to live data.
# ---------------------------------------------------------------------------
def merge_trust_into_inputs(
    base: SourceReputationInputs,
    *,
    trust_score: float | None = None,
    manipulation_probability: float | None = None,
    historical_reliability: float | None = None,
) -> SourceReputationInputs:
    """Fold a ``SourceTrustReport``'s axes into reputation inputs.

    - ``historical_reliability`` (accuracy track record) → historical_accuracy
      (only when ``base`` has none, so a realised Wilson bound always wins).
    - ``manipulation_probability`` → manipulation_probability.
    - ``trust_score`` → independence proxy when ``base`` has none (trust
      captures source-integrity signals beyond raw hit-rate).
    """
    from dataclasses import replace

    updates: dict[str, float | None] = {}
    if base.historical_accuracy is None and historical_reliability is not None:
        updates["historical_accuracy"] = historical_reliability
    if manipulation_probability is not None:
        updates["manipulation_probability"] = manipulation_probability
    if base.independence is None and trust_score is not None:
        updates["independence"] = trust_score
    return replace(base, **updates)  # type: ignore[arg-type]


def reliability_tier_to_accuracy(wilson_lower_95: float | None) -> float | None:
    """Use the realised Wilson lower bound directly as historical_accuracy.

    The Wilson lower bound is already a conservative, bounded [0, 1] estimate
    of realised precision — exactly the semantics ``historical_accuracy`` needs.
    Returns None when the source has insufficient data (n == 0).
    """
    if wilson_lower_95 is None:
        return None
    return _clamp01(float(wilson_lower_95))


def build_source_reputation_report(
    inputs: list[SourceReputationInputs],
    *,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """Score a batch of sources into a JSON-serialisable monitoring report."""
    now = now_utc or datetime.now(UTC)
    scores = [score_source_reputation(i) for i in inputs]
    scores_sorted = sorted(scores, key=lambda s: s.source_reputation, reverse=True)
    return {
        "report_type": "source_reputation",
        "generated_at": now.isoformat(),
        "weights": dict(WEIGHTS),
        "max_reputation": round(MAX_REPUTATION, 6),
        "gate_thresholds": {
            "research_only_below": _GATE_RESEARCH_MAX,
            "supporting_evidence_below": _GATE_SUPPORT_MAX,
            "signal_support_below": _GATE_SIGNAL_MAX,
        },
        "invariant": "no_source_triggers_execution_alone",
        "n_sources": len(scores_sorted),
        "sources": [s.to_json_dict() for s in scores_sorted],
    }


__all__ = [
    "MAX_REPUTATION",
    "WEIGHTS",
    "SourceReputationInputs",
    "SourceReputationScore",
    "UsageTier",
    "build_source_reputation_report",
    "classify_usage_tier",
    "merge_trust_into_inputs",
    "reliability_tier_to_accuracy",
    "score_source_reputation",
]
