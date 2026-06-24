"""U4 — G0 demand evaluator (capital-free verdict).

Joins the demand ledger (challenges/access-grants + requester fingerprints) with the
earnings ledger (settled payments) and renders the PRE-REGISTERED G0 verdict.

Pre-registration (spec §5): G0-PASS = ≥``min_payments`` settled ``kai-oracle:<scope>``
payments AND from ≥``min_fingerprints`` distinct requester fingerprints AND on
≥``min_days`` distinct calendar days, within the window. The fingerprint/day floors
are the fraud guard — a single actor self-paying N× cannot pass.

Read-only over the two JSONL ledgers; no node, no funds.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.lightning.demand_ledger import (
    ACCESS_GRANTED,
    CHALLENGE_MINTED,
    read_recent_demand_events,
)
from app.lightning.earnings_ledger import read_recent_ln_earnings


def _payment_date(rec: dict[str, Any]) -> date | None:
    """UTC calendar date of a settled payment: lnd ``settled_at`` (unix) preferred,
    else the booking ``ts`` (ISO). ``None`` if neither parses."""
    sa = str(rec.get("settled_at", "")).strip()
    if sa.isdigit():
        return datetime.fromtimestamp(int(sa), tz=UTC).date()
    try:
        return datetime.fromisoformat(str(rec.get("ts", ""))).date()
    except ValueError:
        return None


def evaluate_l402_demand(
    *,
    demand_path: Path | None = None,
    earnings_path: Path | None = None,
    scope: str = "fee-series",
    window_start: str | None = None,
    window_days: int = 14,
    min_payments: int = 3,
    min_fingerprints: int = 2,
    min_days: int = 2,
) -> dict[str, Any]:
    """Compute G0 demand metrics + verdict. ``window_start`` is an ISO date (or None =
    all-time); payments outside ``[window_start, window_start + window_days]`` are
    excluded."""
    demand = read_recent_demand_events(demand_path, limit=0)
    earnings = read_recent_ln_earnings(earnings_path, limit=0)

    # --- interest signal: challenges for this scope + fingerprint map by payment_hash
    challenges = [
        r for r in demand if r.get("event") == CHALLENGE_MINTED and r.get("scope") == scope
    ]
    fp_by_hash: dict[str, str] = {}
    for c in challenges:
        ph, fp = str(c.get("payment_hash", "")), str(c.get("requester_fp", ""))
        if ph and fp:
            fp_by_hash.setdefault(ph, fp)
    distinct_challenge_fps = len(
        {c.get("requester_fp") for c in challenges if c.get("requester_fp")}
    )
    access_granted = sum(
        1 for r in demand if r.get("event") == ACCESS_GRANTED and r.get("scope") == scope
    )

    # --- settled payments for this scope within the window
    memo_prefix = f"kai-oracle:{scope}"
    win_start = date.fromisoformat(window_start) if window_start else None

    def _in_window(d: date | None) -> bool:
        if win_start is None:
            return True
        if d is None:
            return False
        return win_start <= d <= date.fromordinal(win_start.toordinal() + window_days)

    settled: list[tuple[dict[str, Any], date | None]] = []
    for r in earnings:
        if str(r.get("source", "")) != "oracle-l402":
            continue
        if not str(r.get("memo", "")).startswith(memo_prefix):
            continue
        d = _payment_date(r)
        if not _in_window(d):
            continue
        settled.append((r, d))

    settled_payments = len(settled)
    payer_fps = {fp_by_hash.get(str(r.get("payment_hash", ""))) for r, _ in settled}
    payer_fps.discard(None)
    payer_fps.discard("")
    distinct_payer_fps = len(payer_fps)
    distinct_days = len({d for _, d in settled if d is not None})

    reasons: list[str] = []
    if settled_payments < min_payments:
        reasons.append(f"too few settled payments ({settled_payments} < {min_payments})")
    if distinct_payer_fps < min_fingerprints:
        reasons.append(f"too few distinct fingerprints ({distinct_payer_fps} < {min_fingerprints})")
    if distinct_days < min_days:
        reasons.append(f"too few distinct days ({distinct_days} < {min_days})")

    return {
        "scope": scope,
        "window_start": window_start,
        "window_days": window_days,
        "challenges": len(challenges),
        "distinct_challenge_fps": distinct_challenge_fps,
        "access_granted": access_granted,
        "settled_payments": settled_payments,
        "distinct_payer_fps": distinct_payer_fps,
        "distinct_days": distinct_days,
        "thresholds": {
            "min_payments": min_payments,
            "min_fingerprints": min_fingerprints,
            "min_days": min_days,
        },
        "verdict": "G0-PASS" if not reasons else "NO-PASS",
        "reasons": reasons,
    }


__all__ = ["evaluate_l402_demand"]
