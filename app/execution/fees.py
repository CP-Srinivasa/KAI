"""Venue-Fee-Lookup fuer NEO-P-106 (V14, 90/10-Variante).

Venue-Fee pro Fill-Rolle, geladen aus config/venue_fees.yaml. Kein Volume-Tier.
Market-Orders werden als Taker modelliert, Limit-Orders mit Limit-Preis als
Maker. Ohne Orderbook-Sim bleibt das konservativ und auditierbar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "venue_fees.yaml"

# Hard fallback wenn YAML komplett unbenutzbar ist (corrupt, missing).
# Bewusst worst-case (Coinbase-Niveau) damit Paper-PnL nicht zu optimistisch wird.
_HARD_FALLBACK_TAKER_PCT = 0.60
_HARD_FALLBACK_MAKER_PCT = 0.60
_VALID_ROLES = {"maker", "taker"}


@dataclass(frozen=True)
class VenueFee:
    """Resolved fee record fuer ein Fill."""

    venue: str
    role: str  # "maker" | "taker"
    bps_applied: float  # Fee in basis points (1bps = 0.01%)
    table_version: str
    table_effective_until: str | None


@lru_cache(maxsize=1)
def _load_table(path: Path = _DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Lazy-load the YAML table once per process."""
    if not path.exists():
        logger.warning("[fees] config not found at %s; using hard fallback", path)
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.error("[fees] failed to load %s: %s; using hard fallback", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.error("[fees] %s is not a YAML mapping; using hard fallback", path)
        return {}
    _check_effective_until(data)
    return data


def _check_effective_until(table: dict[str, Any]) -> None:
    """Warn if the fee table is past its effective_until date."""
    raw = table.get("effective_until")
    if not raw:
        return
    try:
        until = date.fromisoformat(str(raw))
    except ValueError:
        logger.warning("[fees] effective_until=%r is not ISO-8601 date", raw)
        return
    today = datetime.now(UTC).date()
    if today > until:
        logger.warning(
            "[fees] table version %s expired %s ago (effective_until=%s); "
            "fee assumptions are stale, schedule a quarterly review",
            table.get("version", "?"),
            (today - until).days,
            until.isoformat(),
        )


def lookup_fee(
    venue: str,
    role: str,
    *,
    config_path: Path | None = None,
) -> VenueFee:
    """Return the fee for a venue/role pair.

    venue is normalized to lowercase. Unknown venues fall back to
    `default_{role}_pct` from the YAML, then to the hard fallback. Invalid
    roles are treated as taker so callers fail conservative.
    """
    table = _load_table(config_path) if config_path else _load_table()
    venue_norm = (venue or "").strip().lower() or "paper"
    role_norm = (role or "").strip().lower()
    if role_norm not in _VALID_ROLES:
        logger.warning("[fees] invalid role=%r for venue=%s; using taker", role, venue_norm)
        role_norm = "taker"

    venues = table.get("venues", {}) if isinstance(table.get("venues"), dict) else {}
    entry = venues.get(venue_norm)
    pct_key = f"{role_norm}_pct"
    pct: float
    if isinstance(entry, dict) and isinstance(entry.get(pct_key), (int, float)):
        pct = float(entry[pct_key])
    else:
        # default_{role}_pct, dann role-spezifischer hard fallback.
        default = table.get(f"default_{role_norm}_pct")
        hard_fallback = (
            _HARD_FALLBACK_MAKER_PCT if role_norm == "maker" else _HARD_FALLBACK_TAKER_PCT
        )
        pct = float(default) if isinstance(default, (int, float)) else hard_fallback

    return VenueFee(
        venue=venue_norm,
        role=role_norm,
        bps_applied=pct * 100.0,  # 0.10% -> 10 bps
        table_version=str(table.get("version", "fallback")),
        table_effective_until=(
            str(table["effective_until"]) if "effective_until" in table else None
        ),
    )


def infer_fee_role(order_type: str, limit_price: float | None) -> str:
    """Infer maker/taker from the paper order shape."""
    if (order_type or "").strip().lower() == "limit" and limit_price is not None:
        return "maker"
    return "taker"


def lookup_order_fee(
    venue: str,
    *,
    order_type: str,
    limit_price: float | None,
    config_path: Path | None = None,
) -> VenueFee:
    """Return the fee record implied by an order's execution shape."""
    role = infer_fee_role(order_type, limit_price)
    return lookup_fee(venue, role, config_path=config_path)


def lookup_taker_fee(
    venue: str,
    *,
    config_path: Path | None = None,
) -> VenueFee:
    """Backward-compatible taker lookup for older call-sites/tests."""
    return lookup_fee(venue, "taker", config_path=config_path)


def reset_cache() -> None:
    """Drop the cached YAML table — primarily for tests."""
    _load_table.cache_clear()
