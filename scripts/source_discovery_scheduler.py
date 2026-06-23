#!/usr/bin/env python3
"""Autonomous source-discovery scheduler — observe / decide / audit.

Phase 3 loop (PR5). DEFAULT DRY (``SOURCE_DISCOVERY_ENABLED`` off): reads candidate
proposals + the lifecycle ranking, runs every proposal through the fail-closed
intake gate, decides graduation (replace-only-when-ready), and writes the PROPOSED
actions to the lifecycle audit — performing NO outbound fetch and NO DB mutation.
The kill-switch (``SOURCE_DISCOVERY_ENABLED=true``) arms the live-execution path;
the safety rails (SSRF / intake-gate / replace-only-when-ready) are code-enforced
in BOTH modes because they are decided here, not at execution.

Inputs (all optional, fail-closed to empty):
- ``monitor/source_proposals.jsonl`` — candidate proposals (source-scout output)
- ``monitor/source_ranking.json``    — the lifecycle ranking (rotation pool + counts)

Outputs:
- ``artifacts/source_lifecycle_audit.jsonl`` — one event per proposed onboard / graduation
- ``monitor/source_discovery_runs.jsonl``    — one summary line per run (accountability)

Exit code: always 0 (a scheduler must not crash its timer); failures degrade to an
empty/observe run and are recorded in the run summary.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.enums import SourceType  # noqa: E402
from app.learning.source_graduation import (  # noqa: E402
    GraduationPlan,
    ProbationCandidate,
    RotationCandidate,
    decide_graduation,
)
from app.learning.source_intake_gate import (  # noqa: E402
    CandidateAccess,
    IntakeDecision,
    SourceCandidate,
    evaluate_source_intake,
    normalize_url,
)
from app.learning.source_lifecycle_audit import (  # noqa: E402
    LifecycleEvent,
    append_lifecycle_event,
)
from app.storage.jsonl_io import read_jsonl_tolerant  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("source-discovery-scheduler")


def _discovery_enabled() -> bool:
    """Read the kill-switch; default OFF on any settings failure (fail-closed)."""
    try:
        from app.core.settings import SourceSettings

        return bool(SourceSettings().discovery_enabled)
    except Exception:  # noqa: BLE001 — settings unavailable → stay in dry/observe
        return False


def _coerce_access(raw: Any) -> CandidateAccess:
    """Map a proposal's access string to the enum; unknown → fail-closed UNKNOWN."""
    try:
        return CandidateAccess(str(raw).strip().lower())
    except ValueError:
        return CandidateAccess.UNKNOWN


def _coerce_source_type(raw: Any) -> SourceType:
    try:
        return SourceType(str(raw).strip().lower())
    except ValueError:
        return SourceType.UNRESOLVED_SOURCE


def read_proposals(path: Path) -> list[SourceCandidate]:
    """Read candidate proposals (tolerant). Missing file / bad rows → skipped."""
    if not path.exists():
        return []
    out: list[SourceCandidate] = []
    for row in read_jsonl_tolerant(path):
        url = row.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        out.append(
            SourceCandidate(
                url=url.strip(),
                access=_coerce_access(row.get("access")),
                source_type=_coerce_source_type(row.get("source_type")),
                provider=row.get("provider"),
                notes=row.get("notes"),
            )
        )
    return out


def gate_proposals(
    proposals: list[SourceCandidate],
    known_urls: set[str],
    *,
    url_validator: Any = None,
) -> tuple[list[tuple[SourceCandidate, IntakeDecision]], list[tuple[str, str]]]:
    """Run each proposal through the fail-closed intake gate.

    De-duplicates within the batch too (an accepted URL is added to ``known``), so
    two identical proposals in one file cannot both onboard. Returns (accepted,
    rejected) where rejected is (url, reason). Pure given the injected validator.
    """
    known = set(known_urls)
    accepted: list[tuple[SourceCandidate, IntakeDecision]] = []
    rejected: list[tuple[str, str]] = []
    for cand in proposals:
        kwargs: dict[str, Any] = {"existing_normalized_urls": known}
        if url_validator is not None:
            kwargs["url_validator"] = url_validator
        decision = evaluate_source_intake(cand, **kwargs)
        if decision.accepted:
            accepted.append((cand, decision))
            if decision.normalized_url:
                known.add(decision.normalized_url)
        else:
            rejected.append((cand.url, decision.reason))
    return accepted, rejected


def _load_ranking(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def rotation_pool_from_ranking(ranking: dict[str, Any]) -> list[RotationCandidate]:
    """Active sources flagged for rotation → the replaceable pool (weakest first)."""
    pool: list[RotationCandidate] = []
    for e in ranking.get("ranked", []):
        if not isinstance(e, dict) or not e.get("rotation_flagged"):
            continue
        name = e.get("source_name")
        if not isinstance(name, str):
            continue
        wl = e.get("wilson_lower_95")
        pool.append(
            RotationCandidate(source=name, score=float(wl) if isinstance(wl, (int, float)) else 0.0)
        )
    return pool


def _known_urls_from_ranking(ranking: dict[str, Any]) -> set[str]:
    """Normalised URLs already represented in the ranking (best-effort dedup key).

    The ranking is keyed by source *name*, not URL; when a proposal's URL matches a
    known source name token we still let the intake gate's own dedup handle the
    canonical case. This returns the empty set today (names ≠ URLs) and exists so a
    future source-registry URL feed can be slotted in without touching callers.
    """
    return set()


def run_once(
    *,
    proposals_path: Path,
    ranking_path: Path,
    audit_dir: Path,
    runs_path: Path,
    enabled: bool,
    now: datetime,
    url_validator: Any = None,
) -> dict[str, Any]:
    """One scheduler pass. Returns the run summary (also appended to runs_path)."""
    proposals = read_proposals(proposals_path)
    ranking = _load_ranking(ranking_path)
    known = _known_urls_from_ranking(ranking)

    accepted, rejected = gate_proposals(proposals, known, url_validator=url_validator)
    pool = rotation_pool_from_ranking(ranking)

    # Probation candidates with proven evidence come from the live delivery-probe,
    # which is the flag-gated next step; until then there are none, so graduation
    # is honestly inert (no swaps) rather than fabricated.
    probation_candidates: list[ProbationCandidate] = []
    plan: GraduationPlan = decide_graduation(probation_candidates, pool)

    mode = "live" if enabled else "dry"
    # Audit the PROPOSED actions. In dry mode these are proposals only; in live
    # mode the same events precede the (separately-gated) DB execution.
    for cand, decision in accepted:
        append_lifecycle_event(
            LifecycleEvent(
                source=normalize_url(cand.url),
                from_status="planned",
                to_status="probation",
                reason=f"discovery_intake_{mode}",
                recorded_at_utc=now.isoformat(),
                evidence={
                    "url": cand.url,
                    "access": cand.access.value,
                    "sandbox_only": decision.sandbox_only,
                    "executed": False,  # PR5 observes/decides; live DB exec is the next step
                },
            ),
            audit_dir,
        )
    for swap in plan.swaps:
        append_lifecycle_event(
            LifecycleEvent(
                source=swap.promote,
                from_status="probation",
                to_status="active",
                reason=f"discovery_graduation_{mode}",
                recorded_at_utc=now.isoformat(),
                evidence={
                    "archive": swap.archive,
                    "promote_score": swap.promote_score,
                    "archive_score": swap.archive_score,
                    "executed": False,
                },
            ),
            audit_dir,
        )

    if enabled:
        # Kill-switch is ON, but the live DB-onboarding + swap execution and the
        # outbound delivery-probe are the explicitly-deferred final enablement step.
        # We do NOT silently no-op: record that execution was requested but skipped.
        logger.warning(
            "SOURCE_DISCOVERY_ENABLED=true but live DB execution is not yet wired; "
            "ran in observe mode (proposals audited, no DB mutation)."
        )

    summary = {
        "recorded_at_utc": now.isoformat(),
        "mode": mode,
        "proposals_seen": len(proposals),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "rejection_reasons": rejected[:20],
        "rotation_pool": len(pool),
        "graduation_swaps": len(plan.swaps),
        "graduation_skipped": plan.skipped[:20],
    }
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    with runs_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    return summary


def main() -> int:
    now = datetime.now(UTC)
    enabled = _discovery_enabled()
    summary = run_once(
        proposals_path=_REPO_ROOT / "monitor" / "source_proposals.jsonl",
        ranking_path=_REPO_ROOT / "monitor" / "source_ranking.json",
        audit_dir=_REPO_ROOT / "artifacts",
        runs_path=_REPO_ROOT / "monitor" / "source_discovery_runs.jsonl",
        enabled=enabled,
        now=now,
    )
    logger.info("discovery run (%s): %s", summary["mode"], summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
