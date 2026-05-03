"""Venue-Fee-Lookup fuer NEO-P-106 Phase 1 (V14, 90/10-Variante).

Single-Taker-Fee pro Venue, geladen aus config/venue_fees.yaml. Kein Volume-
Tier, kein Maker-Discount. Tier-1 / Taker-Rate als ehrliche Default-Annahme
fuer Paper-Mode.

Phase 2 (separat): Bridge/trading_loop venue-Durchleitung an PaperOrder.
Phase 3 (post Live): Maker/Taker-Differenzierung via Orderbook-Sim.
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


@dataclass(frozen=True)
class VenueFee:
    """Resolved fee record fuer ein Fill."""

    venue: str
    role: str  # "taker" in Phase 1; spaeter "maker" optional
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


def lookup_taker_fee(
    venue: str,
    *,
    config_path: Path | None = None,
) -> VenueFee:
    """Return the taker fee for a venue.

    venue is normalized to lowercase. Unknown venues fall back to
    `default_taker_pct` from the YAML, then to the hard fallback.
    Phase 1 always returns role="taker"; Phase 3 will branch on order_type.
    """
    table = _load_table(config_path) if config_path else _load_table()
    venue_norm = (venue or "").strip().lower() or "paper"

    venues = table.get("venues", {}) if isinstance(table.get("venues"), dict) else {}
    entry = venues.get(venue_norm)
    pct: float
    if isinstance(entry, dict) and isinstance(entry.get("taker_pct"), (int, float)):
        pct = float(entry["taker_pct"])
    else:
        # default_taker_pct, dann hard fallback.
        default = table.get("default_taker_pct")
        pct = (
            float(default)
            if isinstance(default, (int, float))
            else _HARD_FALLBACK_TAKER_PCT
        )

    return VenueFee(
        venue=venue_norm,
        role="taker",
        bps_applied=pct * 100.0,  # 0.10% -> 10 bps
        table_version=str(table.get("version", "fallback")),
        table_effective_until=(
            str(table["effective_until"])
            if "effective_until" in table
            else None
        ),
    )


def reset_cache() -> None:
    """Drop the cached YAML table — primarily for tests."""
    _load_table.cache_clear()
