"""KYT rule sets — operator-curated, auditable, configurable.

Defaults are conservative and honest: they encode only well-established public
classifications (privacy coins, the MATIC→POL delisting that caused the
2026-05-28 phantom-PnL incident, BitMEX's stale-instrument data-quality risk).
An optional ``monitor/kyt_rules.yaml`` overrides/extends every list so the
operator can curate without code changes (trust boundary: filesystem ACL, D-181).
No fabricated sanctions data — sanction/blacklist screening requires an external
provider; absent that, those checks return ``unknown``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.security.kyt.models import KytRiskLevel

logger = logging.getLogger(__name__)

_DEFAULT_RULES_PATH = Path("monitor/kyt_rules.yaml")


def _norm_asset(symbol: str) -> str:
    """Base asset of a pair symbol: 'XMR/USDT' -> 'XMR', 'POLUSDT' -> 'POL'-ish."""
    s = symbol.strip().upper()
    if "/" in s:
        return s.split("/", 1)[0]
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


@dataclass(frozen=True)
class KytRules:
    """All configurable KYT classifications + behavioural thresholds."""

    # asset -> risk level for privacy/anonymity-enhanced coins
    privacy_coins: dict[str, KytRiskLevel] = field(default_factory=dict)
    # asset -> risk level for delisted/renamed/stale-price symbols
    delisted_symbols: dict[str, KytRiskLevel] = field(default_factory=dict)
    # explicit operator blocklist (asset -> level, usually CRITICAL)
    blocklisted_symbols: dict[str, KytRiskLevel] = field(default_factory=dict)
    # venue -> data-quality / jurisdiction risk level
    venue_risk: dict[str, KytRiskLevel] = field(default_factory=dict)
    risky_jurisdictions: frozenset[str] = field(default_factory=frozenset)
    # behavioural thresholds
    structuring_window_minutes: int = 60
    structuring_min_count: int = 5
    structuring_max_notional_usd: float = 50.0
    round_trip_window_minutes: int = 60
    round_trip_min_cycles: int = 3
    frequency_spike_per_hour: int = 40
    amount_anomaly_z: float = 4.0

    def symbol_classification(self, symbol: str) -> tuple[KytRiskLevel, str] | None:
        """Return (level, reason_label) if the symbol matches a rule, else None."""
        asset = _norm_asset(symbol)
        if asset in self.blocklisted_symbols:
            return self.blocklisted_symbols[asset], "blocklisted"
        if asset in self.privacy_coins:
            return self.privacy_coins[asset], "privacy_coin"
        if asset in self.delisted_symbols:
            return self.delisted_symbols[asset], "delisted"
        return None

    def venue_classification(self, venue: str | None) -> KytRiskLevel:
        if not venue:
            return KytRiskLevel.UNKNOWN
        return self.venue_risk.get(venue.strip().lower(), KytRiskLevel.UNKNOWN)


def default_rules() -> KytRules:
    return KytRules(
        privacy_coins={
            "XMR": KytRiskLevel.HIGH,
            "ZEC": KytRiskLevel.HIGH,
            "ZEN": KytRiskLevel.HIGH,
            "SCRT": KytRiskLevel.HIGH,
            "ARRR": KytRiskLevel.HIGH,
            "FIRO": KytRiskLevel.HIGH,
            "BEAM": KytRiskLevel.HIGH,
            "GRIN": KytRiskLevel.HIGH,
            # DASH has optional privacy (PrivateSend) — medium, not high.
            "DASH": KytRiskLevel.MEDIUM,
        },
        delisted_symbols={
            # MATIC was renamed to POL; major venues delisted MATIC/USDT and a
            # stale BitMEX instrument booked +364% phantom PnL (DS-20260529-V1).
            "MATIC": KytRiskLevel.MEDIUM,
        },
        blocklisted_symbols={},
        venue_risk={
            "bybit": KytRiskLevel.LOW,
            "binance": KytRiskLevel.LOW,
            "binance_futures": KytRiskLevel.LOW,
            "okx": KytRiskLevel.LOW,
            "coingecko": KytRiskLevel.LOW,
            "paper": KytRiskLevel.LOW,
            # BitMEX kept a stale/delisted MATIC instrument — data-quality risk.
            "bitmex": KytRiskLevel.MEDIUM,
        },
        risky_jurisdictions=frozenset(),
    )


def load_kyt_rules(path: Path | None = None) -> KytRules:
    """Load rules from YAML, falling back to (and merging onto) safe defaults.

    Never raises: a missing/broken file logs a warning and returns defaults so
    KYT degrades to its baked-in classifications rather than failing open.
    """
    base = default_rules()
    target = path or _DEFAULT_RULES_PATH
    if not target.exists():
        return base
    try:
        import yaml  # type: ignore[import-untyped]

        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — config must never crash KYT
        logger.warning("[kyt] rules load failed (%s): using defaults", exc)
        return base
    if not isinstance(raw, dict):
        return base

    def _level_map(key: str, fallback: dict[str, KytRiskLevel]) -> dict[str, KytRiskLevel]:
        section = raw.get(key)
        if not isinstance(section, dict):
            return fallback
        merged = dict(fallback)
        for asset, level in section.items():
            try:
                merged[str(asset).strip().upper()] = KytRiskLevel(str(level).strip().lower())
            except ValueError:
                logger.warning("[kyt] invalid level %r for %s.%s — skipped", level, key, asset)
        return merged

    def _int(key: str, fallback: int) -> int:
        v = raw.get(key)
        return int(v) if isinstance(v, int) else fallback

    def _float(key: str, fallback: float) -> float:
        v = raw.get(key)
        return float(v) if isinstance(v, (int, float)) else fallback

    venue = base.venue_risk
    raw_venue = raw.get("venue_risk")
    if isinstance(raw_venue, dict):
        venue = dict(base.venue_risk)
        for v, level in raw_venue.items():
            try:
                venue[str(v).strip().lower()] = KytRiskLevel(str(level).strip().lower())
            except ValueError:
                logger.warning("[kyt] invalid venue level %r for %s — skipped", level, v)

    raw_juris = raw.get("risky_jurisdictions")
    juris = (
        frozenset(str(j).strip().upper() for j in raw_juris)
        if isinstance(raw_juris, list)
        else base.risky_jurisdictions
    )

    return KytRules(
        privacy_coins=_level_map("privacy_coins", base.privacy_coins),
        delisted_symbols=_level_map("delisted_symbols", base.delisted_symbols),
        blocklisted_symbols=_level_map("blocklisted_symbols", base.blocklisted_symbols),
        venue_risk=venue,
        risky_jurisdictions=juris,
        structuring_window_minutes=_int(
            "structuring_window_minutes", base.structuring_window_minutes
        ),
        structuring_min_count=_int("structuring_min_count", base.structuring_min_count),
        structuring_max_notional_usd=_float(
            "structuring_max_notional_usd", base.structuring_max_notional_usd
        ),
        round_trip_window_minutes=_int("round_trip_window_minutes", base.round_trip_window_minutes),
        round_trip_min_cycles=_int("round_trip_min_cycles", base.round_trip_min_cycles),
        frequency_spike_per_hour=_int("frequency_spike_per_hour", base.frequency_spike_per_hour),
        amount_anomaly_z=_float("amount_anomaly_z", base.amount_anomaly_z),
    )
