"""Exchange order preflight — central price/qty/notional validation + normalization.

Why this module exists
----------------------
The forensic analysis of env ENV-TG-001275462917-23879-502ef70a found that the
channel targets ``0.008415 / 0.008455 / 0.008495`` are **off-grid** for a
``0.00001`` tick size (price % tickSize != 0). On a real venue that is a
``PRICE_FILTER`` rejection. KAI is paper-only today, so that rejection was never
reached — but the moment a live order send is wired, an un-normalized price is a
guaranteed exchange reject (or worse, a silently mangled order).

This module is the single, provider-agnostic gate that EVERY future live order
send MUST pass through. No order may be sent to an exchange without a successful
``preflight_order`` result. It is also useful today as a quality signal on
incoming premium signals (flagging off-grid targets before they ever queue).

Design contract
---------------
- Provider-agnostic core (``SymbolFilters`` + ``preflight_order``). Thin
  adapters map Binance Futures ``exchangeInfo`` filters and Bybit
  ``instruments-info`` into ``SymbolFilters``. No exchange is hardcoded in the
  core; CCXT-style ``market['precision']`` also maps cleanly.
- Normalization is **opt-in and bounded**. A price is only snapped to the grid
  when ``allow_normalization=True`` AND the snap stays within ``tolerance_pct``.
  Every adjustment is recorded with ``field``, ``original``, ``normalized``,
  ``rounding_direction``, ``tolerance_pct`` and ``risk_impact`` so nothing is
  silently changed. Stop-loss and take-profit snap in the **conservative**
  (less-favourable) direction so normalization can never quietly loosen risk.
- When a price is off-grid and normalization is disallowed or out of tolerance,
  the order is rejected with ``REJECT_INVALID_TICK_SIZE``. Other filter failures
  (min notional, lot size, percent-price band, leverage) reject with
  ``REJECT_EXCHANGE_FILTER``.
- Decimal arithmetic throughout — float modulo on prices like 0.00001 is
  unreliable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from typing import Any, Literal

from app.risk.reason_codes import RejectCode

Side = Literal["buy", "sell", "long", "short"]
RoundDir = Literal["up", "down", "nearest", "none"]


def _dec(value: float | str | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True)
class SymbolFilters:
    """Normalized exchange trading rules for one symbol.

    All fields optional: a missing filter means "not enforced here". Construct
    via the ``from_*`` adapters or directly in tests.
    """

    symbol: str
    tick_size: Decimal | None = None
    step_size: Decimal | None = None
    min_qty: Decimal | None = None
    max_qty: Decimal | None = None
    min_notional: Decimal | None = None
    max_leverage: Decimal | None = None
    # PERCENT_PRICE band relative to a reference price (multiplicative).
    percent_price_up: Decimal | None = None
    percent_price_down: Decimal | None = None
    status: str = "TRADING"

    # ----------------------------------------------------------------- #
    # Adapters
    # ----------------------------------------------------------------- #
    @classmethod
    def from_binance_futures(cls, info: dict[str, Any]) -> SymbolFilters:
        """Map one Binance Futures ``exchangeInfo.symbols[]`` entry."""
        filters = {f.get("filterType"): f for f in info.get("filters", [])}
        pf = filters.get("PRICE_FILTER", {})
        lot = filters.get("LOT_SIZE", {}) or filters.get("MARKET_LOT_SIZE", {})
        notional = filters.get("MIN_NOTIONAL", {}) or filters.get("NOTIONAL", {})
        pp = filters.get("PERCENT_PRICE", {})
        return cls(
            symbol=str(info.get("symbol", "")),
            tick_size=_dec(pf.get("tickSize")),
            step_size=_dec(lot.get("stepSize")),
            min_qty=_dec(lot.get("minQty")),
            max_qty=_dec(lot.get("maxQty")),
            min_notional=_dec(notional.get("notional") or notional.get("minNotional")),
            percent_price_up=_dec(pp.get("multiplierUp")),
            percent_price_down=_dec(pp.get("multiplierDown")),
            status=str(info.get("status", "TRADING")),
        )

    @classmethod
    def from_bybit_instrument(cls, info: dict[str, Any]) -> SymbolFilters:
        """Map one Bybit ``instruments-info.list[]`` entry."""
        price_filter = info.get("priceFilter", {})
        lot_filter = info.get("lotSizeFilter", {})
        lev_filter = info.get("leverageFilter", {})
        return cls(
            symbol=str(info.get("symbol", "")),
            tick_size=_dec(price_filter.get("tickSize")),
            step_size=_dec(lot_filter.get("qtyStep")),
            min_qty=_dec(lot_filter.get("minOrderQty")),
            max_qty=_dec(lot_filter.get("maxOrderQty")),
            min_notional=_dec(lot_filter.get("minNotionalValue")),
            max_leverage=_dec(lev_filter.get("maxLeverage")),
            status=str(info.get("status", "Trading")),
        )


@dataclass
class PriceAdjustment:
    """Audit record for a single normalization step. Never silent."""

    field_name: str
    original: float
    normalized: float
    rounding_direction: RoundDir
    tolerance_pct: float
    risk_impact: str


@dataclass
class PreflightResult:
    ok: bool
    symbol: str
    reason_code: str | None = None
    violations: list[str] = field(default_factory=list)
    adjustments: list[PriceAdjustment] = field(default_factory=list)
    normalized_entry: float | None = None
    normalized_stop_loss: float | None = None
    normalized_targets: list[float] = field(default_factory=list)
    normalized_qty: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "symbol": self.symbol,
            "reason_code": self.reason_code,
            "violations": list(self.violations),
            "adjustments": [a.__dict__ for a in self.adjustments],
            "normalized_entry": self.normalized_entry,
            "normalized_stop_loss": self.normalized_stop_loss,
            "normalized_targets": list(self.normalized_targets),
            "normalized_qty": self.normalized_qty,
        }


def is_on_grid(value: Decimal, grid: Decimal) -> bool:
    """True when ``value`` is an exact multiple of ``grid`` (tick/step)."""
    if grid <= 0:
        return True
    return (value % grid) == 0


def _snap(value: Decimal, grid: Decimal, direction: RoundDir) -> Decimal:
    if grid <= 0:
        return value
    if direction == "down":
        return (value / grid).to_integral_value(rounding=ROUND_DOWN) * grid
    if direction == "up":
        return (value / grid).to_integral_value(rounding=ROUND_UP) * grid
    # nearest
    return (value / grid).to_integral_value(rounding=ROUND_HALF_UP) * grid


def _conservative_price_dir(field_name: str, side: str) -> RoundDir:
    """Snap direction that never loosens risk.

    - stop_loss: move it CLOSER to entry (tighter stop = smaller loss budget but
      never a larger one). For a long, SL is below entry -> round UP toward
      entry; for a short, SL above entry -> round DOWN toward entry.
    - take_profit: make it HARDER to reach (require more move). For a long, TP
      above entry -> round UP; short, TP below entry -> round DOWN.
    - entry: nearest (entry is a target price, neither direction is "safer").
    """
    s = side.lower()
    is_long = s in {"buy", "long"}
    if field_name == "stop_loss":
        return "up" if is_long else "down"
    if field_name == "take_profit":
        return "up" if is_long else "down"
    return "nearest"


def _normalize_one(
    *,
    field_name: str,
    value: Decimal,
    tick: Decimal,
    side: str,
    allow_normalization: bool,
    tolerance_pct: float,
    result: PreflightResult,
) -> Decimal | None:
    """Return on-grid value, recording an adjustment, or None if it must reject."""
    if tick is None or tick <= 0 or is_on_grid(value, tick):
        return value
    if not allow_normalization:
        result.violations.append(f"price_off_grid:{field_name}={value}|tick={tick}")
        result.reason_code = RejectCode.INVALID_TICK_SIZE.value
        return None
    direction = _conservative_price_dir(field_name, side)
    snapped = _snap(value, tick, direction)
    if snapped <= 0:
        result.violations.append(f"price_off_grid:{field_name}_snaps_to_zero|tick={tick}")
        result.reason_code = RejectCode.INVALID_TICK_SIZE.value
        return None
    drift_pct = abs(snapped - value) / value * Decimal(100) if value > 0 else Decimal(0)
    if drift_pct > Decimal(str(tolerance_pct)):
        result.violations.append(
            f"price_off_grid:{field_name}_snap_exceeds_tolerance:"
            f"{drift_pct:.4g}%>{tolerance_pct:.4g}%"
        )
        result.reason_code = RejectCode.INVALID_TICK_SIZE.value
        return None
    result.adjustments.append(
        PriceAdjustment(
            field_name=field_name,
            original=float(value),
            normalized=float(snapped),
            rounding_direction=direction,
            tolerance_pct=tolerance_pct,
            risk_impact=("tighter" if field_name == "stop_loss" else "harder_to_reach")
            if field_name in {"stop_loss", "take_profit"}
            else "neutral",
        )
    )
    return snapped


def preflight_order(
    *,
    filters: SymbolFilters,
    side: Side,
    entry_price: float,
    stop_loss: float | None = None,
    targets: list[float] | None = None,
    quantity: float | None = None,
    reference_price: float | None = None,
    leverage: float | None = None,
    allow_normalization: bool = True,
    tolerance_pct: float = 0.1,
) -> PreflightResult:
    """Validate (and optionally normalize) an order against exchange filters.

    Returns a ``PreflightResult``. ``ok=False`` carries a ``reason_code`` of
    ``REJECT_INVALID_TICK_SIZE`` or ``REJECT_EXCHANGE_FILTER`` and the specific
    violations. This is the mandatory gate before any live order send.
    """
    result = PreflightResult(ok=True, symbol=filters.symbol)

    # Symbol must be tradeable.
    if filters.status.upper() not in {"TRADING"}:
        result.violations.append(f"symbol_not_trading:status={filters.status}")
        result.reason_code = RejectCode.EXCHANGE_FILTER.value

    tick = filters.tick_size or Decimal(0)
    entry_d = _dec(entry_price) or Decimal(0)

    norm_entry = _normalize_one(
        field_name="entry",
        value=entry_d,
        tick=tick,
        side=side,
        allow_normalization=allow_normalization,
        tolerance_pct=tolerance_pct,
        result=result,
    )
    result.normalized_entry = float(norm_entry) if norm_entry is not None else None

    if stop_loss is not None:
        sl_d = _dec(stop_loss) or Decimal(0)
        norm_sl = _normalize_one(
            field_name="stop_loss",
            value=sl_d,
            tick=tick,
            side=side,
            allow_normalization=allow_normalization,
            tolerance_pct=tolerance_pct,
            result=result,
        )
        result.normalized_stop_loss = float(norm_sl) if norm_sl is not None else None

    if targets:
        norm_targets: list[float] = []
        for t in targets:
            t_d = _dec(t) or Decimal(0)
            norm_t = _normalize_one(
                field_name="take_profit",
                value=t_d,
                tick=tick,
                side=side,
                allow_normalization=allow_normalization,
                tolerance_pct=tolerance_pct,
                result=result,
            )
            if norm_t is not None:
                norm_targets.append(float(norm_t))
        result.normalized_targets = norm_targets

    # Quantity / lot-size + notional.
    if quantity is not None:
        qty_d = _dec(quantity) or Decimal(0)
        step = filters.step_size or Decimal(0)
        if step > 0 and not is_on_grid(qty_d, step):
            if allow_normalization:
                qty_d = _snap(qty_d, step, "down")  # never over-fill
                result.adjustments.append(
                    PriceAdjustment(
                        field_name="quantity",
                        original=float(quantity),
                        normalized=float(qty_d),
                        rounding_direction="down",
                        tolerance_pct=tolerance_pct,
                        risk_impact="smaller_size",
                    )
                )
            else:
                result.violations.append(f"qty_off_step:{quantity}|step={step}")
                result.reason_code = RejectCode.EXCHANGE_FILTER.value
        if filters.min_qty is not None and qty_d < filters.min_qty:
            result.violations.append(f"qty_below_min:{qty_d}<{filters.min_qty}")
            result.reason_code = RejectCode.EXCHANGE_FILTER.value
        if filters.max_qty is not None and qty_d > filters.max_qty:
            result.violations.append(f"qty_above_max:{qty_d}>{filters.max_qty}")
            result.reason_code = RejectCode.EXCHANGE_FILTER.value
        result.normalized_qty = float(qty_d)

        # Notional uses the (normalized) entry price.
        if filters.min_notional is not None and norm_entry is not None:
            notional = qty_d * norm_entry
            if notional < filters.min_notional:
                result.violations.append(
                    f"notional_below_min:{notional:.6g}<{filters.min_notional}"
                )
                result.reason_code = RejectCode.EXCHANGE_FILTER.value

    # PERCENT_PRICE band (entry vs reference/mark price).
    ref = _dec(reference_price)
    if ref is not None and ref > 0 and norm_entry is not None:
        if filters.percent_price_up is not None and norm_entry > ref * filters.percent_price_up:
            result.violations.append(
                f"percent_price_exceeded:entry>{ref * filters.percent_price_up:.6g}"
            )
            result.reason_code = RejectCode.EXCHANGE_FILTER.value
        if filters.percent_price_down is not None and norm_entry < ref * filters.percent_price_down:
            result.violations.append(
                f"percent_price_exceeded:entry<{ref * filters.percent_price_down:.6g}"
            )
            result.reason_code = RejectCode.EXCHANGE_FILTER.value

    # Leverage bracket.
    lev = _dec(leverage)
    if lev is not None and filters.max_leverage is not None and lev > filters.max_leverage:
        result.violations.append(f"leverage_exceeds_max:{lev}>{filters.max_leverage}")
        result.reason_code = RejectCode.EXCHANGE_FILTER.value

    result.ok = len(result.violations) == 0
    if result.ok:
        result.reason_code = None
    return result


__all__ = [
    "PreflightResult",
    "PriceAdjustment",
    "SymbolFilters",
    "is_on_grid",
    "preflight_order",
]
