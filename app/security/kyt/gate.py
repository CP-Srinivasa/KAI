"""KYT execution-path entrypoint — flag-gated, non-breaking.

When ``settings.kyt.enabled`` is False (default) every entrypoint returns None
and the execution path behaves exactly as before. When enabled in ``shadow``
mode the transaction is assessed + audited but never blocked. In ``enforce``
mode the caller refuses execution iff ``assessment.decision.blocks_execution``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

from app.security.kyt.audit import emit_agent_alerts, write_assessment
from app.security.kyt.engine import KytEngine
from app.security.kyt.models import KytAssessment, KytCheckPhase, TransactionContext
from app.security.kyt.providers import LocalListProvider, NullProvider
from app.security.kyt.rules import load_kyt_rules

logger = logging.getLogger(__name__)

_PAPER_AUDIT = Path("artifacts/paper_execution_audit.jsonl")


def _kyt_settings() -> object | None:
    try:
        from app.core.settings import get_settings

        return getattr(get_settings(), "kyt", None)
    except Exception:  # noqa: BLE001
        return None


def kyt_enabled() -> bool:
    s = _kyt_settings()
    return bool(getattr(s, "enabled", False)) if s is not None else False


def kyt_mode() -> str:
    s = _kyt_settings()
    mode = getattr(s, "mode", "shadow") if s is not None else "shadow"
    return str(mode)


def build_engine() -> KytEngine:
    """Construct the engine from settings (provider-agnostic)."""
    s = _kyt_settings()
    rules = load_kyt_rules()
    provider_name = str(getattr(s, "provider", "local_lists")) if s is not None else "local_lists"
    behavioral = bool(getattr(s, "behavioral_enabled", True)) if s is not None else True
    fail_mode = str(getattr(s, "fail_mode", "conservative")) if s is not None else "conservative"
    provider = NullProvider() if provider_name == "null" else LocalListProvider(rules)
    return KytEngine([provider], rules=rules, behavioral_enabled=behavioral, fail_mode=fail_mode)


def load_recent_history(
    limit: int = 500, *, audit_path: Path | None = None
) -> list[dict[str, object]]:
    """Read recent fills/closes from the paper audit for behavioural analysis."""
    path = audit_path or _PAPER_AUDIT
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("event_type") in (
            "order_filled",
            "position_closed",
            "position_partial_closed",
        ):
            rows.append(rec)
    return rows


def assess_transaction(
    context: TransactionContext,
    *,
    history: Sequence[dict[str, object]] | None = None,
    audit: bool = True,
    alert: bool = True,
) -> KytAssessment:
    """Run KYT for one transaction, persist audit + agent alerts. Always returns."""
    engine = build_engine()
    if history is None:
        history = load_recent_history()
    assessment = engine.assess(context, history=history)
    if audit:
        write_assessment(assessment, context)
    if alert:
        emit_agent_alerts(assessment, context)
    return assessment


def screen_order(
    *,
    tx_id: str,
    symbol: str | None,
    venue: str | None,
    side: str | None,
    quantity: float | None,
    entry_price: float | None,
    source: str = "",
    correlation_id: str = "",
    phase: KytCheckPhase = KytCheckPhase.PRE_TRANSACTION,
) -> KytAssessment | None:
    """Execution-path hook. Returns None when KYT is disabled (non-breaking).

    Caller contract: in enforce mode refuse execution iff the returned
    ``assessment.decision.blocks_execution`` is True. In shadow mode the caller
    ignores the verdict (assessment is still audited/alerted).
    """
    if not kyt_enabled():
        return None
    notional = (
        quantity * entry_price if (quantity is not None and entry_price is not None) else None
    )
    context = TransactionContext(
        tx_id=tx_id,
        phase=phase,
        symbol=symbol,
        venue=venue,
        side=side,
        quantity=quantity,
        notional_usd=notional,
        entry_price=entry_price,
        source=source,
        correlation_id=correlation_id,
    )
    try:
        return assess_transaction(context)
    except Exception as exc:  # noqa: BLE001 — the gate must never crash a trade
        logger.error("[kyt] screen_order failed for %s: %s", tx_id, exc)
        return None


def enforce_blocks(assessment: KytAssessment | None) -> bool:
    """True only when KYT is in enforce mode AND the decision blocks execution."""
    if assessment is None:
        return False
    return kyt_mode() == "enforce" and assessment.decision.blocks_execution
