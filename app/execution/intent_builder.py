"""Builder: operator/premium envelope payload → ``ExecutableOrderIntent``.

S7-Extraktion aus ``envelope_to_paper_bridge.py`` (God-File-Ratchet, D-234):
das vom document_id-Fix berührte Segment — Payload-Koercion + Intent-Bau —
wandert in dieses Modul, die Bridge importiert es.

V2-Nebenbefund 2026-06-12 (PR #222-Analyse): Premium-Closes waren im Audit
unlabeled (``document_id=""``), weil die Bridge die stabile Signal-Identität
nie in den Intent schrieb — Joins zurück zum Signal gingen nur über
``correlation_id``. ``payload.signal_id`` (z.B. ``SIG-TGCH-…``) ist laut
``premium_dedupe`` die raw↔approved-stabile Geschäfts-Identität und wird hier
als ``document_id`` durchgereicht (Fallback ``source_uid``, sonst leer wie
bisher).
"""

from __future__ import annotations

from app.execution.order_intent import ExecutableOrderIntent


def float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def entry_bounds(payload: dict[str, object]) -> tuple[float | None, float | None]:
    if payload.get("entry_type") != "range":
        return None, None
    emin = float_or_none(payload.get("entry_min"))
    emax = float_or_none(payload.get("entry_max"))
    if emin is None or emax is None or emax <= emin <= 0:
        return None, None
    return emin, emax


def document_id_from_payload(payload: dict[str, object]) -> str:
    """Stable business-signal identity for audit attribution.

    Priority mirrors ``premium_dedupe``: ``signal_id`` is identical on the raw
    and the approved envelope of one premium signal; ``source_uid``
    (``telegram:<chat>:<msg>``) is the next-stable fallback. Non-premium
    payloads (dashboard paste, structured text) usually carry neither and keep
    the previous empty-string behaviour.
    """
    for key in ("signal_id", "source_uid"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def build_executable_intent(
    *,
    envelope_id: str,
    correlation_id: str | None = None,
    source: str,
    payload: dict[str, object],
    symbol: str,
    side: str,
    entry_price: float | None,
    stop_loss: float,
    targets: list[float],
    quantity: float | None = None,
) -> ExecutableOrderIntent:
    leverage = float_or_none(payload.get("leverage")) or 1.0
    risk_allocation_pct = float_or_none(payload.get("margin_pct"))
    if risk_allocation_pct is None:
        risk_allocation_pct = float_or_none(payload.get("position_size_suggestion"))
    entry_min, entry_max = entry_bounds(payload)
    entry_type = str(payload.get("entry_type") or "market").lower()
    order_type = "market" if entry_type == "market" else "limit"
    return ExecutableOrderIntent(
        symbol=symbol,
        side=side.upper(),
        order_type=order_type,
        entry_type=entry_type,
        entry_value=entry_price,
        entry_min=entry_min,
        entry_max=entry_max,
        quantity=quantity,
        risk_allocation_pct=risk_allocation_pct,
        leverage=leverage,
        margin_mode=str(payload.get("risk_mode") or "isolated"),
        stop_loss=stop_loss,
        take_profit_targets=tuple(targets),
        reduce_only=bool(payload.get("reduce_only", False)),
        source=source,
        correlation_id=correlation_id or envelope_id,
        idempotency_key=f"opbridge:{envelope_id}",
        document_id=document_id_from_payload(payload),
    )
