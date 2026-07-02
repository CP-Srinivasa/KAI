"""Stablecoin risk registry — the reserve sleeve is managed, not assumed.

KAI may hold USDT/approved stablecoins as a liquidity/settlement reserve to
execute trades quickly. A stablecoin is not risk-free, so this module exposes
the operator-curated risk dimensions (depeg, issuer, reserves, transparency,
custody, regulation, liquidity) from ``config/stablecoin_risk.yaml`` as a typed,
read-only registry.

Honesty contract (KAI rule "fehlende Daten = nicht bewertbar, niemals
schätzen"):
    * Any dimension not curated is ``"unknown"`` — never silently treated as
      safe.
    * ``overall_risk_tier`` is an operator JUDGEMENT carried verbatim from the
      config, not a fabricated composite score.
    * ``evaluable`` is False when too few dimensions are known, and an unknown
      stablecoin yields a not-evaluable stub rather than an optimistic default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.trading.asset_universe import base_symbol

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_REGISTRY_PATH = _REPO_ROOT / "config" / "stablecoin_risk.yaml"

# The seven curated risk dimensions (besides issuer/peg/name/notes).
_RISK_DIMENSIONS = (
    "depeg_risk",
    "reserves_quality",
    "transparency",
    "custody_model",
    "regulatory_status",
    "liquidity_tier",
    "overall_risk_tier",
)

# Soft vocabularies — values outside these are accepted but flagged "unknown".
_VOCAB: dict[str, frozenset[str]] = {
    "depeg_risk": frozenset({"low", "medium", "high"}),
    "reserves_quality": frozenset({"strong", "adequate", "weak", "opaque"}),
    "transparency": frozenset({"audited", "attested", "self_reported", "opaque"}),
    "custody_model": frozenset({"regulated_custodian", "mixed", "offshore", "onchain"}),
    "regulatory_status": frozenset({"regulated", "partial", "unregulated"}),
    "liquidity_tier": frozenset({"very_high", "high", "medium", "low"}),
    "overall_risk_tier": frozenset({"low", "medium", "high"}),
}

# A stablecoin must have at least this many KNOWN risk dimensions to be
# considered evaluable as a reserve candidate.
_MIN_KNOWN_DIMENSIONS = 3


def _norm(value: object, *, allowed: frozenset[str] | None = None) -> str:
    if value is None:
        return UNKNOWN
    text = str(value).strip().lower()
    if not text:
        return UNKNOWN
    if allowed is not None and text not in allowed:
        return UNKNOWN
    return text


@dataclass(frozen=True)
class StablecoinRisk:
    """One stablecoin's curated reserve-risk profile."""

    symbol: str
    name: str
    issuer: str
    peg_target: str
    depeg_risk: str
    reserves_quality: str
    transparency: str
    custody_model: str
    regulatory_status: str
    liquidity_tier: str
    overall_risk_tier: str
    notes: str
    evaluable: bool

    def known_dimensions(self) -> int:
        return sum(1 for d in _RISK_DIMENSIONS if getattr(self, d) != UNKNOWN)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "issuer": self.issuer,
            "peg_target": self.peg_target,
            "depeg_risk": self.depeg_risk,
            "reserves_quality": self.reserves_quality,
            "transparency": self.transparency,
            "custody_model": self.custody_model,
            "regulatory_status": self.regulatory_status,
            "liquidity_tier": self.liquidity_tier,
            "overall_risk_tier": self.overall_risk_tier,
            "evaluable": self.evaluable,
            "known_dimensions": self.known_dimensions(),
            "notes": self.notes,
        }


def _build_risk(symbol: str, raw: dict[str, Any]) -> StablecoinRisk:
    dims = {d: _norm(raw.get(d), allowed=_VOCAB.get(d)) for d in _RISK_DIMENSIONS}
    known = sum(1 for v in dims.values() if v != UNKNOWN)
    return StablecoinRisk(
        symbol=symbol,
        name=str(raw.get("name") or symbol),
        issuer=_norm(raw.get("issuer")),
        peg_target=str(raw.get("peg_target") or UNKNOWN).strip().upper(),
        notes=str(raw.get("notes") or "").strip(),
        evaluable=known >= _MIN_KNOWN_DIMENSIONS,
        **dims,
    )


def _unknown_stub(symbol: str) -> StablecoinRisk:
    sym = base_symbol(symbol)
    return StablecoinRisk(
        symbol=sym,
        name=sym,
        issuer=UNKNOWN,
        peg_target=UNKNOWN,
        depeg_risk=UNKNOWN,
        reserves_quality=UNKNOWN,
        transparency=UNKNOWN,
        custody_model=UNKNOWN,
        regulatory_status=UNKNOWN,
        liquidity_tier=UNKNOWN,
        overall_risk_tier=UNKNOWN,
        notes="",
        evaluable=False,
    )


class StablecoinRiskRegistry:
    """Read-only registry of :class:`StablecoinRisk`, keyed by base symbol."""

    def __init__(self, entries: dict[str, StablecoinRisk]) -> None:
        self._entries = entries

    @classmethod
    def load(cls, *, path: str | Path | None = None) -> StablecoinRiskRegistry:
        p = Path(path) if path else _DEFAULT_REGISTRY_PATH
        doc = _load_doc(p)
        raw = doc.get("stablecoins", {}) if isinstance(doc, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        entries: dict[str, StablecoinRisk] = {}
        for sym, body in raw.items():
            if not isinstance(body, dict):
                continue
            base = base_symbol(str(sym))
            if base:
                entries[base] = _build_risk(base, body)
        return cls(entries)

    def get(self, symbol: str) -> StablecoinRisk | None:
        return self._entries.get(base_symbol(symbol))

    def assess(self, symbol: str) -> StablecoinRisk:
        """Always return a profile — an uncurated stablecoin yields a
        not-evaluable stub (never an optimistic default)."""
        return self.get(symbol) or _unknown_stub(symbol)

    def all(self) -> list[StablecoinRisk]:
        return list(self._entries.values())


def _load_doc(path: Path) -> dict[str, Any]:
    if not path.exists():
        logger.info("[STABLE-RISK] registry %s missing; empty registry", path)
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            doc = yaml.safe_load(handle) or {}
        return doc if isinstance(doc, dict) else {}
    except Exception as exc:  # noqa: BLE001 — config must never crash callers
        logger.warning("[STABLE-RISK] registry %s unreadable (%s); empty", path, exc)
        return {}


_CACHED: StablecoinRiskRegistry | None = None


def get_stablecoin_risk_registry(*, reload: bool = False) -> StablecoinRiskRegistry:
    """Process-cached default registry. Pass ``reload=True`` to rebuild."""
    global _CACHED
    if _CACHED is None or reload:
        _CACHED = StablecoinRiskRegistry.load()
    return _CACHED


__all__ = [
    "StablecoinRisk",
    "StablecoinRiskRegistry",
    "get_stablecoin_risk_registry",
]
