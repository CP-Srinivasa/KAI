"""Onboarding-Execution für autonome Discovery (Phase 3b).

Schließt die Schleife: der Scheduler entscheidet (intake-gate + graduation), DIESE
Funktionen führen die DB-Mutation aus — aber NUR wenn der Operator den Kill-Switch
``SOURCE_DISCOVERY_ENABLED`` scharf gestellt hat. Alles reversibel (PROBATION/
ARCHIVED zurückdrehbar), FSM-validiert, idempotent.

* ``onboard_accepted`` — akzeptierte Kandidaten als ``PROBATION`` anlegen
  (idempotent: bereits registrierte URL/provider werden übersprungen). Eine neue
  Quelle landet NIE direkt ``ACTIVE`` — Rail 2 (intake-gate) erlaubt nur Probation.
* ``build_probation_candidates`` — DB-PROBATION-Quellen + ihre Ranking-Evidenz
  (Wilson=score, n=deliveries) + den Probation-Run-Zähler zu Graduation-Inputs.
* ``execute_swaps`` — die replace-only-when-ready-Swaps der Graduation ausführen:
  Promote ``PROBATION→ACTIVE`` gepaart mit Archive ``ACTIVE→ARCHIVED`` (1-in-1-out).

Funktionen nehmen ein injiziertes ``SourceRepository`` → gegen eine In-Memory-DB
testbar, kein verstecktes I/O. Das Audit schreibt der Aufrufer (Scheduler).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.learning.source_graduation import (
    GraduationSwap,
    ProbationCandidate,
    RotationCandidate,
)
from app.learning.source_intake_gate import IntakeDecision, SourceCandidate, normalize_url
from app.learning.source_lifecycle import can_transition
from app.storage.repositories.source_repo import SourceRepository
from app.storage.schemas.source import SourceCreate, SourceUpdate


@dataclass(frozen=True)
class OnboardResult:
    """Ergebnis eines Onboarding-Versuchs für eine Quelle."""

    provider: str | None
    url: str
    created: bool
    reason: str  # "onboarded_probation" | "duplicate_url" | "duplicate_provider" | "invalid"


@dataclass(frozen=True)
class SwapResult:
    """Ergebnis eines ausgeführten Graduation-Swaps."""

    promote: str
    archive: str
    promoted: bool
    archived: bool
    reason: str


async def _existing(repo: SourceRepository) -> tuple[set[str], set[str]]:
    urls: set[str] = set()
    providers: set[str] = set()
    for s in await repo.list():
        if s.original_url:
            urls.add(normalize_url(s.original_url))
        if s.normalized_url:
            urls.add(normalize_url(s.normalized_url))
        if s.provider:
            providers.add(s.provider.strip().lower())
    return urls, providers


async def onboard_accepted(
    repo: SourceRepository,
    accepted: list[tuple[SourceCandidate, IntakeDecision]],
) -> list[OnboardResult]:
    """Lege akzeptierte Kandidaten als PROBATION an (idempotent gegen die Registry)."""
    known_urls, known_providers = await _existing(repo)
    results: list[OnboardResult] = []
    for cand, decision in accepted:
        nu = normalize_url(cand.url)
        provider = (cand.provider or "").strip().lower() or None
        if not nu:
            results.append(OnboardResult(provider, cand.url, False, "invalid"))
            continue
        if nu in known_urls:
            results.append(OnboardResult(provider, cand.url, False, "duplicate_url"))
            continue
        if provider and provider in known_providers:
            results.append(OnboardResult(provider, cand.url, False, "duplicate_provider"))
            continue
        await repo.create(
            SourceCreate(
                source_type=cand.source_type or SourceType.UNRESOLVED_SOURCE,
                provider=cand.provider,
                original_url=cand.url,
                normalized_url=decision.normalized_url,
                status=SourceStatus.PROBATION,
                auth_mode=AuthMode.NONE,
                notes=cand.notes,
            )
        )
        known_urls.add(nu)
        if provider:
            known_providers.add(provider)
        results.append(OnboardResult(provider, cand.url, True, "onboarded_probation"))
    return results


async def build_probation_candidates(
    repo: SourceRepository,
    *,
    evidence_by_source: dict[str, dict[str, Any]],
    runs_by_source: dict[str, int],
) -> list[ProbationCandidate]:
    """DB-PROBATION-Quellen + Ranking-Evidenz + Run-Zähler → Graduation-Inputs.

    ``evidence_by_source`` ist nach ``source_name`` (== provider) gekeyt; score =
    Wilson-Untergrenze, deliveries = n (aufgelöste Signale), delivering = sustained
    document delivery (Boolean-Floor, speist das delivery-reclamation-Tor). Quellen
    ohne Evidenz bekommen score 0 / deliveries 0 / delivering False (fail-closed).
    """
    out: list[ProbationCandidate] = []
    for s in await repo.list(status=SourceStatus.PROBATION):
        name = (s.provider or "").strip()
        if not name:
            continue
        ev = evidence_by_source.get(name) or {}
        wl = ev.get("wilson_lower_95")
        n = ev.get("n")
        out.append(
            ProbationCandidate(
                source=name,
                score=float(wl) if isinstance(wl, (int, float)) else 0.0,
                deliveries=int(n) if isinstance(n, (int, float)) else 0,
                runs=int(runs_by_source.get(name, 0)),
                delivering=bool(ev.get("delivering")),
            )
        )
    return out


async def _set_status(
    repo: SourceRepository, provider: str, target: SourceStatus
) -> tuple[bool, str]:
    """Setze den Status der Quelle mit diesem provider (FSM-validiert). (ok, reason)."""
    matches = await repo.list(provider=provider)
    if not matches:
        return (False, "source_not_found")
    src = matches[0]
    if src.status == target:
        return (True, "already_in_state")
    if not can_transition(src.status, target):
        return (False, f"illegal_{src.status.value}->{target.value}")
    await repo.update(src.source_id, SourceUpdate(status=target))
    return (True, "ok")


async def execute_swaps(
    repo: SourceRepository,
    swaps: list[GraduationSwap],
) -> list[SwapResult]:
    """Führe replace-only-when-ready-Swaps aus: promote PROBATION→ACTIVE, archive
    ACTIVE→ARCHIVED. Jede Hälfte FSM-validiert; ein illegaler/nicht gefundener
    Partner blockt nur diesen Swap, nicht die anderen."""
    results: list[SwapResult] = []
    for swap in swaps:
        promoted, p_reason = await _set_status(repo, swap.promote, SourceStatus.ACTIVE)
        archived, a_reason = (False, "skipped_promote_failed")
        if promoted:
            archived, a_reason = await _set_status(repo, swap.archive, SourceStatus.ARCHIVED)
        results.append(
            SwapResult(
                promote=swap.promote,
                archive=swap.archive,
                promoted=promoted,
                archived=archived,
                reason=f"promote:{p_reason} archive:{a_reason}",
            )
        )
    return results


__all__ = [
    "OnboardResult",
    "RotationCandidate",
    "SwapResult",
    "build_probation_candidates",
    "execute_swaps",
    "onboard_accepted",
]
