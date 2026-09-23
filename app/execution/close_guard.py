"""Schutzregeln rund um den Phantom-Cap — TP-Klemme, Zweitanbieter, Nachkauf-Grenze.

**Anlass (ARB/USDT, 17.–23.09.2026).** Eine Long-Position mit 14 Nachkaeufen
(Durchschnittseinstieg 0,152135) trug nach dem letzten Nachkauf ein Take-Profit
von 0,182973. Das TP stammte aus der NEUESTEN Order und war relativ zu deren
Fill-Preis gesetzt; der Cap (``phantom_filter``) prueft aber gegen den
DURCHSCHNITTSEINSTIEG: +20,27 % > 20 %. Jeder TP-Treffer musste abgewiesen
werden — 7868-mal in sechs Tagen, waehrend ARB auf allen Venues echt bei
~0,239 stand (+57 %). Die Position band 63 % des Paper-Equity.

Drei Regeln, alle hier, damit ``paper_engine`` (God-File-Ratchet) nicht waechst:

1. ``clamp_take_profit``: nach einem NACHKAUF liegt das TP nie jenseits des
   Caps, gemessen am neuen Durchschnittseinstieg. Die Erst-Eroeffnung behaelt ihr
   TP — ein bewusst weites Ziel faengt Regel 2 ab. Dieselbe Funktion laeuft live
   (``paper_finite_gate``) und im Replay (``audit_replay``) — sonst holte ein
   Rehydrate das ungeklemmte TP aus ``order_created`` zurueck.
2. ``guard_close``: der Cap weist weiter ab, AUSSER ein unabhaengiger zweiter
   Anbieter bestaetigt den Close-Preis innerhalb derselben Toleranz, die der
   Fallback-Adapter fuer Anbieter-Uneinigkeit nutzt. Das ist genau die Evidenz,
   die der Cap nicht hat: die bekannte Artefakt-Klasse (Einstieg und Ausstieg
   von verschiedenen Anbietern bepreist, MATIC 28.05.) scheitert an der
   Bestaetigung. Ein bestaetigter Close schreibt ``close_price_sanity_corroborated``
   — sichtbar, nicht still. Auf der Lese-Seite bleibt er ``REQUIRES_VERIFICATION``
   (``close_classification``); die Bestaetigung ist Evidenz, kein Freispruch.
3. ``max_entry_fills_per_position``: wie viele Entry-Fills eine Position
   hoechstens sammelt. Default 4 — gemessen 23.09. ueber alle geschlossenen
   Positionen: 736 von 767 (96 %) hatten hoechstens vier, der Rest reichte bis 175.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping

from app.execution.models import PaperPosition, PriceEvidence
from app.execution.phantom_filter import implied_close_return, phantom_return_threshold

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRY_FILLS = 4

# Das geklemmte TP liegt knapp UNTER dem Cap, nicht auf ihm: ein TP genau auf
# der Grenze wuerde an Float-Rundung und Slippage wieder scheitern.
_TP_CAP_HEADROOM = 0.99

# Wie ``app.market_data.service._MOCK_SOURCE``: synthetische Daten bestaetigen nie.
_MOCK_SOURCE = "mock"


def clamp_take_profit(
    take_profit: float | None,
    avg_entry_price: float,
    position_side: str,
    *,
    cap: float | None = None,
) -> float | None:
    """TP so begrenzen, dass ein Close darauf den Phantom-Cap nicht reisst."""
    if take_profit is None or avg_entry_price <= 0 or take_profit <= 0:
        return take_profit
    edge = (cap if cap is not None else phantom_return_threshold()) * _TP_CAP_HEADROOM
    if position_side == "short":
        return max(take_profit, avg_entry_price / (1.0 + edge))
    return min(take_profit, avg_entry_price * (1.0 + edge))


def max_entry_fills_per_position() -> int | None:
    """Obergrenze der Entry-Fills je Position; None = keine Grenze (Env <= 0)."""
    raw = os.environ.get("PAPER_MAX_ENTRY_FILLS_PER_POSITION")
    if raw is None:
        return _DEFAULT_MAX_ENTRY_FILLS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_ENTRY_FILLS
    return value if value > 0 else None


def pyramid_rejection(existing: PaperPosition | None) -> dict[str, object] | None:
    """Audit-Felder, wenn ein weiterer Entry-Fill die Nachkauf-Grenze ueberschritte."""
    limit = max_entry_fills_per_position()
    if existing is None or limit is None or existing.entry_fill_count < limit:
        return None
    return {
        "entry_fill_count": existing.entry_fill_count,
        "max_entry_fills": limit,
        "position_quantity": existing.quantity,
        "avg_entry_price": existing.avg_entry_price,
    }


def _within(a: float, b: float, tolerance: float) -> bool:
    lo, hi = min(a, b), max(a, b)
    return lo > 0 and hi / lo - 1.0 <= tolerance


def close_corroboration(
    evidence: PriceEvidence | None, close_price: float
) -> dict[str, object] | None:
    """Audit-Felder, wenn ein unabhaengiger Anbieter ``close_price`` bestaetigt."""
    if evidence is None or evidence.corroborating_price is None:
        return None
    primary = evidence.source.split("|", 1)[0]
    second = evidence.corroborated_by
    if not second or second in (primary, _MOCK_SOURCE) or evidence.observed_price is None:
        return None
    # Lazy: app.market_data.service zieht alle Venue-Adapter nach.
    from app.market_data.service import _provider_disagreement_pct

    tolerance = _provider_disagreement_pct()
    if not _within(evidence.observed_price, close_price, tolerance):
        return None
    if not _within(evidence.corroborating_price, close_price, tolerance):
        return None
    return {
        "corroborated_by": second,
        "corroborating_price": evidence.corroborating_price,
        "observed_price": evidence.observed_price,
        "tolerance_pct": tolerance * 100.0,
    }


def guard_close(
    *,
    symbol: str,
    reason: str,
    entry_price: float,
    close_price: float,
    position_side: str,
    price_source: str,
    evidence: PriceEvidence | None,
    append_audit: Callable[[str, Mapping[str, object]], None],
    cap: float,
    extra: Mapping[str, object] | None = None,
) -> bool:
    """True, wenn der Close gebucht werden darf (DS-20260529-V1 + Zweitanbieter)."""
    implied = implied_close_return(entry_price, close_price, position_side)
    if implied is None or abs(implied) <= cap:
        return True
    payload: dict[str, object] = {
        "symbol": symbol,
        "reason": reason,
        **(extra or {}),
        "entry_price": entry_price,
        "close_price": close_price,
        "implied_return_pct": implied * 100.0,
        "max_close_return_pct": cap * 100.0,
        "position_side": position_side,
        # Ohne diese Zeile ist der Befund nicht zurueckverfolgbar:
        # man sieht, DASS ein unmoeglicher Preis kam, nie WOHER.
        "price_source": price_source,
    }
    corroboration = close_corroboration(evidence, close_price)
    if corroboration is not None:
        append_audit("close_price_sanity_corroborated", {**payload, **corroboration})
        logger.warning(
            "[PAPER] Close over cap ACCEPTED — %s implied %.1f%% > %.1f%%, "
            "price confirmed by %s (%s)",
            symbol,
            implied * 100.0,
            cap * 100.0,
            corroboration["corroborated_by"],
            reason,
        )
        return True
    append_audit("close_price_sanity_rejected", payload)
    logger.error(
        "[PAPER] Close REJECTED (%s) — implied return %.1f%% exceeds cap %.1f%%: "
        "%s entry=%.6g close=%.6g (no independent venue confirms the price)",
        reason,
        implied * 100.0,
        cap * 100.0,
        symbol,
        entry_price,
        close_price,
    )
    return False


__all__ = [
    "clamp_take_profit",
    "close_corroboration",
    "guard_close",
    "max_entry_fills_per_position",
    "pyramid_rejection",
]
