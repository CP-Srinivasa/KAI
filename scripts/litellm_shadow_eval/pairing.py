"""Deterministic, non-heuristic DIRECT/SHADOW pairing."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace

from scripts.litellm_shadow_eval.models import (
    EvidencePair,
    EvidenceRecord,
    PairStatus,
    Side,
    ValidationIssue,
)


@dataclass(frozen=True, slots=True)
class PairingResult:
    pairs: tuple[EvidencePair, ...]
    issues: tuple[ValidationIssue, ...]


def _collapse_attempts(records: list[EvidenceRecord]) -> EvidenceRecord | None:
    """Collapse canonical per-attempt telemetry only when sequence proof is exact."""
    if not records:
        return None
    if len(records) == 1:
        return records[0]
    ordered = sorted(records, key=lambda item: item.attempt_count or 0)
    expected = list(range(1, len(ordered) + 1))
    if [item.attempt_count for item in ordered] != expected:
        return None
    if [item.retry_count for item in ordered] != [number - 1 for number in expected]:
        return None
    latency = (
        sum(item.latency_ms for item in ordered if item.latency_ms is not None)
        if all(item.latency_ms is not None for item in ordered)
        else None
    )
    cost_known = all(item.cost_known for item in ordered)
    cost = (
        sum(item.cost_usd for item in ordered if item.cost_usd is not None)
        if cost_known and all(item.cost_usd is not None for item in ordered)
        else None
    )
    input_tokens = (
        sum(item.input_tokens for item in ordered if item.input_tokens is not None)
        if all(item.input_tokens is not None for item in ordered)
        else None
    )
    output_tokens = (
        sum(item.output_tokens for item in ordered if item.output_tokens is not None)
        if all(item.output_tokens is not None for item in ordered)
        else None
    )
    final = ordered[-1]
    return replace(
        final,
        retry_count=len(ordered) - 1,
        attempt_count=len(ordered),
        latency_ms=latency,
        cost_usd=cost,
        cost_known=cost_known,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def pair_records(records: tuple[EvidenceRecord, ...]) -> PairingResult:
    """Pair by evaluation_id, otherwise by the complete documented fallback key."""
    grouped: dict[str, list[EvidenceRecord]] = defaultdict(list)
    issues: list[ValidationIssue] = []
    for record in records:
        key = record.pair_key
        if key is None:
            issues.append(
                ValidationIssue(
                    "UNPAIRABLE_RECORD",
                    "record has no deterministic pair key",
                    record.record_ref,
                    record.logical_route,
                )
            )
            continue
        grouped[key].append(record)

    pairs: list[EvidencePair] = []
    for key in sorted(grouped):
        members = grouped[key]
        routes = {item.logical_route for item in members}
        if len(routes) != 1:
            for item in members:
                issues.append(
                    ValidationIssue(
                        "AMBIGUOUS_PAIR",
                        "one evaluation_id resolves to multiple logical routes",
                        item.record_ref,
                        item.logical_route,
                    )
                )
            continue
        route = next(iter(routes))
        direct = sorted(
            (item for item in members if item.side is Side.DIRECT),
            key=lambda item: item.record_ref,
        )
        shadow = sorted(
            (item for item in members if item.side is Side.SHADOW),
            key=lambda item: item.record_ref,
        )
        direct_record = _collapse_attempts(direct)
        shadow_record = _collapse_attempts(shadow)
        duplicate = False
        if direct and direct_record is None:
            duplicate = True
            for item in direct:
                issues.append(
                    ValidationIssue(
                        "DUPLICATE_DIRECT",
                        "pair key has more than one DIRECT record",
                        item.record_ref,
                        route,
                    )
                )
        if shadow and shadow_record is None:
            duplicate = True
            for item in shadow:
                issues.append(
                    ValidationIssue(
                        "DUPLICATE_SHADOW",
                        "pair key has more than one SHADOW record",
                        item.record_ref,
                        route,
                    )
                )
        if duplicate:
            continue
        status = (
            PairStatus.VALID
            if direct_record is not None and shadow_record is not None
            else PairStatus.INCOMPLETE
        )
        pairs.append(
            EvidencePair(
                key=key,
                logical_route=route,
                direct=direct_record,
                shadow=shadow_record,
                status=status,
            )
        )
    return PairingResult(tuple(pairs), tuple(issues))


__all__ = ["PairingResult", "pair_records"]
