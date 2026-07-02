"""Falsification-verdict record — the tamper-evident output of the edge-validation gate.

NORTH_STAR (ADR 0012): KAI's proven core value is an *auditable, cost-honest
falsification process*. Each edge-validation run produces a verdict; this module
turns it into an append-only, hashable record and (optionally) anchors that hash
on-chain through the EXISTING OTS layer (:mod:`app.integrity.anchor`).

The proof attests the verdict's EXISTENCE-TIME and IMMUTABILITY — it does NOT
attest pre-registration (that the hypothesis preceded the data). That is a
separate, deliberately-unbuilt guarantee; never claim it from an anchor alone.

Verification by a third party: take a line from ``falsification_verdicts.jsonl``,
``json.loads`` it, re-serialise with ``verdict_record_digest`` (canonical sort-keys
form), and check the resulting hex against the ``<digest16>`` of the matching
``verdict-*.ots`` proof.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.core.integrity_settings import IntegritySettings
from app.integrity.anchor import AnchorResult, anchor_record_digest
from app.observability.edge_validation_gate import EdgeValidationVerdict, ResolvedTrials

VERDICT_SCHEMA = "falsification_verdict/v1"
DEFAULT_VERDICTS_PATH = Path("artifacts/research/falsification_verdicts.jsonl")


def _net_bps_sha256(net_bps: Sequence[float]) -> str:
    """Bind the verdict to its EXACT cost-adjusted input series, compactly.

    Rounded to 8 dp so the same series hashes identically across platforms; the
    raw series stays out of the record (compact + no need to republish the data
    to make the proof verifiable — the hash is the commitment)."""
    blob = json.dumps([round(float(x), 8) for x in net_bps], separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_verdict_record(
    verdict: EdgeValidationVerdict,
    *,
    resolved: ResolvedTrials,
    exec_audit_path: str,
    venue: str,
    net_bps: Sequence[float],
    ledger_path: Path | str,
    recorded_at_utc: str,
) -> dict[str, Any]:
    """Assemble the canonical, anchorable verdict record.

    Binds the trial-count provenance (so a too-low count can't be hidden later),
    the exact net_bps input hash, and every gate criterion — everything needed to
    reproduce and audit the ``ready`` verdict.
    """
    return {
        "schema": VERDICT_SCHEMA,
        "recorded_at_utc": recorded_at_utc,
        "exec_audit_path": exec_audit_path,
        "venue": venue,
        "n": verdict.trade_count,
        "ready": verdict.ready,
        "mean_net_bps": round(verdict.mean_net_bps, 4),
        "deflated_sharpe": verdict.deflated_sharpe,
        "trials_used": resolved.trials,
        "trials_source": resolved.source,
        "trials_clamped": resolved.clamped,
        "ledger_count": resolved.ledger_count,
        "ledger_path": str(ledger_path),
        "net_bps_sha256": _net_bps_sha256(net_bps),
        "criteria": [{"name": c.name, "passed": c.passed} for c in verdict.criteria],
    }


def verdict_record_digest(record: dict[str, Any]) -> str:
    """SHA256 over the canonical (sort-keys, compact) JSON form of the record."""
    blob = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def record_and_anchor_verdict(
    record: dict[str, Any],
    *,
    verdicts_path: Path | str = DEFAULT_VERDICTS_PATH,
    settings: IntegritySettings | None = None,
) -> tuple[str, AnchorResult]:
    """Append the verdict record (always) and anchor its digest (per settings).

    Writing uses the SAME canonical serialisation as :func:`verdict_record_digest`
    so the persisted line re-hashes to the anchored digest — that round-trip is
    what makes the proof verifiable against the ledger. Returns ``(digest, result)``.
    """
    path = Path(verdicts_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    digest = verdict_record_digest(record)
    cfg = settings if settings is not None else IntegritySettings()
    result = anchor_record_digest(digest, settings=cfg, prefix="verdict")
    return digest, result


__all__ = [
    "DEFAULT_VERDICTS_PATH",
    "VERDICT_SCHEMA",
    "build_verdict_record",
    "record_and_anchor_verdict",
    "verdict_record_digest",
]
