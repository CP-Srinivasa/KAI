"""Hypothesis-family registry with a codified stop rule (ADR 0012 discipline).

Every signal hypothesis belongs to a FAMILY (momentum, funding, news direction,
...). Families accumulate falsification evidence across constructions; without a
stop rule the backlog silently refills with variations of already-dead ideas —
each new "naive family X at horizon Y" run then only re-confirms the previous
falsification instead of teaching anything new.

The rule, fixed here in code (changing it = a reviewed PR, i.e. operator-gated):

    STOP_RULE_FAILS = 3 pre-registered falsifications across DISTINCT
    constructions => the family is TERMINAL_DEAD. A family can also be marked
    terminal early by a single structurally-terminal falsification (e.g. a
    DSR-gated beta-neutral test) — recorded explicitly with its evidence.

``prereg-register --family <name>`` consults this registry: registering a new
hypothesis in a TERMINAL_DEAD family is refused unless explicitly overridden
(``--force-dead-family``), which is itself recorded in the hypothesis name space
by the operator's explicit action. Record-only otherwise: nothing here gates a
trade or a deploy.

The seeded statuses below encode the falsification history as of 2026-07-01;
evidence strings point at the auditable artifacts (PRs, prereg ids, memory docs).
"""

from __future__ import annotations

from dataclasses import dataclass

STOP_RULE_FAILS = 3

OPEN = "open"
PROBATION = "probation"  # >=1 pre-registered fail; next constructions need stronger priors
TERMINAL_DEAD = "terminal_dead"

_STATUSES = (OPEN, PROBATION, TERMINAL_DEAD)


@dataclass(frozen=True)
class HypothesisFamily:
    """One signal family and its accumulated falsification state."""

    name: str
    status: str  # open | probation | terminal_dead
    constructions_failed: int
    evidence: tuple[str, ...]  # PR/prereg/memory references, newest last
    notes: str = ""

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"invalid status {self.status!r}")
        if self.status == TERMINAL_DEAD and self.constructions_failed < STOP_RULE_FAILS:
            # Early terminal requires explicit structural evidence in notes.
            if "terminal:" not in self.notes:
                raise ValueError(
                    f"{self.name}: terminal_dead below {STOP_RULE_FAILS} fails needs a "
                    "'terminal:' note naming the structurally-terminal evidence"
                )


FAMILIES: dict[str, HypothesisFamily] = {
    f.name: f
    for f in (
        HypothesisFamily(
            name="ta_rules",
            status=TERMINAL_DEAD,
            constructions_failed=6,
            evidence=(
                "#391/#393/#395 edge-discovery: 0 survivors, all 6 TA rules net-negative",
                "doctrine 2026-06-25: naive TA statistically chanceless (n=51 P=16.5% expected)",
            ),
            notes="all 6 rule constructions cost-net negative under BH-FDR control",
        ),
        HypothesisFamily(
            name="momentum",
            status=TERMINAL_DEAD,
            constructions_failed=3,
            evidence=(
                "#466 TS-momentum: 0 survivors",
                "falsify_momentum.py 2026-06-29: n=178 signaled-dir net negative all horizons",
                "canonical-edge cohort 2026-07-01: n=68 P(mu>0)=10.44% NO_GO",
            ),
            notes="cross-sectional, time-series and universe-cohort constructions all falsified",
        ),
        HypothesisFamily(
            name="execution_alpha",
            status=TERMINAL_DEAD,
            constructions_failed=1,
            evidence=(
                "#462 cost truth 2026-06-26: gross_mean -4.4bps PRE-cost, cost_reachable=false",
                "#464 churn sweep: pointless at negative gross edge",
            ),
            notes="terminal: PRE-cost gross edge already negative — no cost model can save it",
        ),
        HypothesisFamily(
            name="unlock_supply",
            status=TERMINAL_DEAD,
            constructions_failed=2,
            evidence=(
                "whale-transfer gates 2026-06-26: 0 BH-FDR survivors",
                "#487 unlock-short beta-neutral: DSR-gated TERMINAL falsification",
            ),
            notes="terminal: beta-neutral DSR-gated construction falsified; ADR-0012 keeps "
            "unlocks as risk/confound markers only (#500/#505/#509)",
        ),
        HypothesisFamily(
            name="funding_carry",
            status=PROBATION,
            constructions_failed=2,
            evidence=(
                "12 funding/TA hypotheses 2026-06-26: BH-FDR all net ~0/negative",
                "V5 funding shadow 2026-07-01: n=758 trust 0.5, no promote",
                "open prereg f676bcf5a7a1bfb6 funding_premium_meanrev_1h (3rd construction)",
            ),
            notes="one pre-registered construction still open; its failure triggers the stop rule",
        ),
        HypothesisFamily(
            name="news_direction",
            status=TERMINAL_DEAD,
            constructions_failed=4,
            evidence=(
                "prereg 5872f817a2d1632d 24h spot 2026-07-02 (full-corpus re-measure): "
                "FAILED — 5 sources n>=200 at 1d, none passes P>=0.95+cost "
                "(overall n=7106, 1d mean 5.2bps vs cost 20.9, P=0.77)",
                "prereg 722f1593ca1d0acd hedged-BTC 4h 2026-07-02: FAILED "
                "(overall 4h mean 0.08bps, P=0.50; pooled z=0.18)",
                "prereg 6e23c6822669f7d5 micro-1m 2026-07-02: FAILED "
                "(1-60min gross means ~1bps, best pooled P=0.949 < 0.9875 Bonferroni; "
                "absence is NOT a latency artifact)",
                "prereg b20ef1487ccba99d hedged-1d-drift v2 2026-08-06: FAILED at "
                "registered gate (p_min, cost_clearing) — n=302/300 stories, exakte "
                "Konstruktion hedged_vs_BTC/USDT (Guard #648); attestiert seq 73, "
                "OTS newsverdict-d6b91b256cde729a",
            ),
            notes="stop rule hit 2026-07-02 (3 pre-registered constructions failed). "
            "Sanctioned exception hedged-1d-drift GESCHLOSSEN 2026-08-06: v2-Nachfolger "
            "b20ef1487ccba99d (ersetzt 4a3b1b0c5a94b73c) FAILED am versiegelten Gate — "
            "terminal per Prä-Reg-Wortlaut. Remaining sanctioned exception: prereg "
            "7e8d66314dd7c64e directional_news_3d_theblock_newsbtc (Verdikt-Report "
            "attestiert seq 72, 2026-08-05: actionable=false in allen vier Zellen; "
            "newsbtc-Arm unter sample_size_target, theblock-Arm reif und verfehlt). "
            "Its closure ends the family for good; new constructions need "
            "--force-dead-family.",
        ),
        HypothesisFamily(
            name="oracle_demand",
            status=PROBATION,
            constructions_failed=1,
            evidence=(
                "prereg 9cab81fae4823482 oracle_demand_probe_fee_truth_v1 2026-08-05: "
                "FAIL = NO_DEMAND — Zahlungs-Zweig 0 Zahlungen / 0 distinkte Payer im "
                "versiegelten C1-Fenster (Regel c1_evaluation_rule_20260802, Evaluator "
                "scripts/c1_payment_branch_eval.py, Fensterstart-Invarianz gerechnet); "
                "attestiert seq 71 + OTS",
            ),
            notes="Familie für zahlungsbasierte Nachfrage-Proben (L402/Oracle, Lightning). "
            "1/3 Richtung Stop-Rule; ODER-Zweig der C1-Regel war nie operationalisiert "
            "und wurde nicht gewertet. Nächste Konstruktionen brauchen stärkere Priors "
            "und prove-by-doing-Kanäle; externe Einnahmen lifetime = 0 sat (Stand 08-06).",
        ),
        HypothesisFamily(
            name="money_path_integrity",
            status=OPEN,
            constructions_failed=0,
            evidence=(
                "W0/PR-D 2026-08-06: first reconciliation shadow construction; no verdict yet",
            ),
            notes="Operational safety/integrity hypotheses only; never an alpha, readiness, "
            "capital or revenue claim.",
        ),
        HypothesisFamily(
            name="l2_microstructure",
            status=OPEN,
            constructions_failed=0,
            evidence=("#412-#418 L2 Bayes evidence shadow-only, no verdict yet",),
        ),
    )
}


def get_family(name: str) -> HypothesisFamily | None:
    """Registry lookup; ``None`` for unknown families (caller warns, not fails)."""
    return FAMILIES.get(name.strip().lower())


def is_terminal_dead(name: str) -> bool:
    fam = get_family(name)
    return fam is not None and fam.status == TERMINAL_DEAD


__all__ = [
    "FAMILIES",
    "OPEN",
    "PROBATION",
    "STOP_RULE_FAILS",
    "TERMINAL_DEAD",
    "HypothesisFamily",
    "get_family",
    "is_terminal_dead",
]
