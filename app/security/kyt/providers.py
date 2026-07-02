"""KYT screening providers — provider-agnostic abstraction.

A provider turns a ``TransactionContext`` into zero or more ``KytFlag``s. The
local provider screens symbol + venue + an operator address blocklist; it never
fabricates sanction data. External blockchain-analytics providers (Chainalysis,
TRM, Elliptic, …) can be added behind the same Protocol without touching the
engine. Providers must never raise — failures are surfaced as a flag by the
engine (conservative fallback), not as a crash.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.security.kyt.models import KytFlag, KytReasonCode, KytRiskLevel, TransactionContext
from app.security.kyt.rules import KytRules, default_rules


class KytProviderError(RuntimeError):
    """Raised internally by a provider; the engine maps it to a conservative flag."""


@runtime_checkable
class KytScreeningProvider(Protocol):
    @property
    def name(self) -> str: ...

    def screen(self, context: TransactionContext) -> list[KytFlag]: ...


class NullProvider:
    """No external intelligence — everything it could screen is ``unknown``.

    Used as the default when no real provider is configured, so the system is
    honest about what it cannot assess instead of pretending an address is clean.
    """

    name = "null"

    def screen(self, context: TransactionContext) -> list[KytFlag]:
        flags: list[KytFlag] = []
        if context.wallet_address or context.counterparty:
            flags.append(
                KytFlag(
                    code=KytReasonCode.INSUFFICIENT_DATA,
                    level=KytRiskLevel.UNKNOWN,
                    detail="No screening provider configured for address/counterparty.",
                    source=self.name,
                    data_available=False,
                )
            )
        return flags


class LocalListProvider:
    """Rule-based screening from operator-curated lists (no network)."""

    name = "local_lists"

    def __init__(self, rules: KytRules | None = None) -> None:
        self._rules = rules or default_rules()

    def screen(self, context: TransactionContext) -> list[KytFlag]:
        flags: list[KytFlag] = []

        if context.symbol:
            hit = self._rules.symbol_classification(context.symbol)
            if hit is not None:
                level, label = hit
                code = {
                    "blocklisted": KytReasonCode.BLOCKLISTED_SYMBOL,
                    "privacy_coin": KytReasonCode.PRIVACY_COIN,
                    "delisted": KytReasonCode.DELISTED_SYMBOL,
                }[label]
                flags.append(
                    KytFlag(
                        code=code,
                        level=level,
                        detail=f"Symbol {context.symbol} classified as {label}.",
                        source=self.name,
                    )
                )

        venue_level = self._rules.venue_classification(context.venue)
        if venue_level == KytRiskLevel.UNKNOWN and context.venue:
            flags.append(
                KytFlag(
                    code=KytReasonCode.VENUE_RISK,
                    level=KytRiskLevel.UNKNOWN,
                    detail=f"Venue {context.venue} has no risk classification.",
                    source=self.name,
                    data_available=False,
                )
            )
        elif venue_level.rank >= KytRiskLevel.MEDIUM.rank:
            flags.append(
                KytFlag(
                    code=KytReasonCode.VENUE_RISK,
                    level=venue_level,
                    detail=f"Venue {context.venue} carries {venue_level.value} data-quality risk.",
                    source=self.name,
                )
            )

        if (
            context.jurisdiction
            and context.jurisdiction.strip().upper() in self._rules.risky_jurisdictions
        ):
            flags.append(
                KytFlag(
                    code=KytReasonCode.RISKY_JURISDICTION,
                    level=KytRiskLevel.HIGH,
                    detail=f"Jurisdiction {context.jurisdiction} is on the risky list.",
                    source=self.name,
                )
            )

        # Address/counterparty: only the operator blocklist is local. Real
        # sanction/mixer/darknet intelligence needs an external provider →
        # without one we cannot clear an address, so mark unknown.
        if context.wallet_address or context.counterparty:
            flags.append(
                KytFlag(
                    code=KytReasonCode.INSUFFICIENT_DATA,
                    level=KytRiskLevel.UNKNOWN,
                    detail="Address/counterparty present but no on-chain provider configured.",
                    source=self.name,
                    data_available=False,
                )
            )

        return flags
