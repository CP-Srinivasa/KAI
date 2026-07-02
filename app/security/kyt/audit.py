"""KYT audit trail + agent alerting.

Every assessment is appended to ``artifacts/kyt/assessments.jsonl`` with reason
codes. Warn+ assessments raise a finding in the SENTR dropbox (SENTR owns the
security decision/escalation/re-checks); high/critical also notify Neo (which
must weigh KYT before trade/transfer decisions and seek alternatives).

Privacy by design: raw wallet addresses are never persisted — only a truncated
salted hash (pseudonym) goes into the audit. No personal data is stored; the
context fields kept are operational (symbol/venue/side/notional). Retention is
operator-configurable (``KytSettings.retention_days``); pruning is a separate
maintenance job, not done here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from app.security.kyt.models import KytAssessment, KytDecision, KytRiskLevel, TransactionContext

logger = logging.getLogger(__name__)

_KYT_AUDIT = Path("artifacts/kyt/assessments.jsonl")
_AGENT_DIR = Path("artifacts/agents")
# Salt for address pseudonymisation. A per-deployment salt (env) prevents
# trivial rainbow-table reversal of the truncated hash across deployments.
_ADDR_SALT = os.environ.get("KYT_ADDR_SALT", "kai-kyt-v1")


def pseudonymize_address(address: str | None) -> str | None:
    if not address:
        return None
    digest = hashlib.sha256((_ADDR_SALT + address.strip().lower()).encode("utf-8")).hexdigest()
    return f"addr_{digest[:16]}"


def _append_jsonl(path: Path, record: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from app.core.file_lock import append_lock

        with append_lock(path):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — audit write must never crash the gate
        logger.error("[kyt] audit write failed (%s): %s", path, exc)


def write_assessment(
    assessment: KytAssessment,
    context: TransactionContext,
    *,
    audit_path: Path | None = None,
) -> None:
    """Append the assessment + pseudonymised operational context to the audit."""
    record = {
        **assessment.to_dict(),
        "context": {
            "symbol": context.symbol,
            "venue": context.venue,
            "side": context.side,
            "notional_usd": context.notional_usd,
            "source": context.source,
            "correlation_id": context.correlation_id,
            "counterparty_present": bool(context.counterparty),
            "wallet_pseudonym": pseudonymize_address(context.wallet_address),
            "chain": context.chain,
        },
    }
    _append_jsonl(audit_path or _KYT_AUDIT, record)


_DECISION_SEVERITY = {
    KytDecision.ALLOW: None,
    KytDecision.WARN: "info",
    KytDecision.HOLD: "warn",
    KytDecision.MANUAL_REVIEW: "warn",
    KytDecision.BLOCK: "crit",
}

_DECISION_PRIORITY = {
    KytDecision.WARN: "P3",
    KytDecision.HOLD: "P1",
    KytDecision.MANUAL_REVIEW: "P1",
    KytDecision.BLOCK: "P0",
}


def emit_agent_alerts(
    assessment: KytAssessment,
    context: TransactionContext,
    *,
    agent_dir: Path | None = None,
) -> list[str]:
    """Raise SENTR (always on warn+) and Neo (high/critical) findings.

    Returns the slugs alerted (for tests/telemetry). ALLOW assessments are
    audit-only — no alert noise.
    """
    severity = _DECISION_SEVERITY.get(assessment.decision)
    if severity is None:
        return []

    base = agent_dir or _AGENT_DIR
    priority = _DECISION_PRIORITY.get(assessment.decision, "P2")
    codes = ", ".join(c.value for c in assessment.reason_codes)
    title = f"kyt_{assessment.decision.value}_{context.symbol or 'tx'}"
    detail = (
        f"[{priority}] KYT {assessment.decision.value} (risk={assessment.risk_level.value}, "
        f"score={assessment.score}) tx={assessment.tx_id} symbol={context.symbol} "
        f"venue={context.venue} reasons=[{codes}] → {assessment.recommended_next_step}"
    )
    ts = datetime.now(UTC).isoformat()
    alerted: list[str] = []

    # SENTR owns the security verdict + escalation + re-checks.
    _append_jsonl(
        base / "sentr" / "findings.jsonl",
        {"ts": ts, "severity": severity, "title": title, "detail": detail, "source": "kyt"},
    )
    alerted.append("sentr")

    # Neo must weigh KYT before trade/transfer decisions — notify on high/critical.
    if assessment.risk_level.rank >= KytRiskLevel.HIGH.rank:
        _append_jsonl(
            base / "neo" / "findings.jsonl",
            {
                "ts": ts,
                "severity": severity,
                "title": title,
                "detail": detail + " | Neo: seek lower-risk alternative before executing.",
                "source": "kyt",
            },
        )
        alerted.append("neo")

    return alerted
