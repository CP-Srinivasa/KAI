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
            status=PROBATION,
            constructions_failed=1,
            evidence=(
                "prereg 5872f817a2d1632d 24h spot construction 2026-07-01: FAILED "
                "(7 sources, no P>=0.95, max n=174<200)",
                "open prereg directional_news_3d_theblock_newsbtc (out-of-sample)",
            ),
            notes="hedged + micro constructions must be pre-registered BEFORE measurement",
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
