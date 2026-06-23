"""Hypothesis ledger — a persistent record of every tested (hypothesis x config).

Makes the edge search CUMULATIVE: each evaluated hypothesis-under-config is
appended (JSONL), so the engine can avoid blindly re-testing the same thing and
can surface the total number of distinct hypothesis configurations ever tested —
the count that honest multiple-testing accounting (the garden of forking paths)
ultimately needs.

``hypothesis_key`` identifies a hypothesis *configuration* (rule name + market/
search parameters) and deliberately EXCLUDES the data window: the same rule
re-run on fresh data shares a key (so a repeat is detectable), while each entry
still records the window (``as_of_utc`` / ``lookback_days``) it was run over.

The ledger is read-only-safe: a corrupt line is skipped, never crashing a run.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def hypothesis_key(
    *,
    name: str,
    timeframe: str,
    horizon: int,
    round_trip_cost_bps: float,
    universe: Sequence[str],
    min_trades: int,
    alpha: float,
) -> str:
    """Deterministic 16-hex key for a hypothesis configuration (window-agnostic).

    Order-independent in ``universe`` (sorted) so symbol ordering never changes
    the identity of an otherwise-identical search.
    """
    payload = {
        "name": name,
        "timeframe": timeframe,
        "horizon": horizon,
        "round_trip_cost_bps": round(round_trip_cost_bps, 6),
        "universe": sorted(universe),
        "min_trades": min_trades,
        "alpha": round(alpha, 6),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class LedgerEntry:
    """One recorded hypothesis-under-config result."""

    key: str
    name: str
    timeframe: str
    horizon: int
    round_trip_cost_bps: float
    universe: list[str]
    survived: bool
    mean_net_bps: float
    total_trades: int
    n_symbols_survived: int
    as_of_utc: str  # end of the data window this run covered
    lookback_days: int
    recorded_at_utc: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_dict(d: dict[str, Any]) -> LedgerEntry:
        """Reconstruct an entry from a parsed JSON object (explicit, typed coercion)."""
        universe_raw = d.get("universe")
        universe = [str(x) for x in universe_raw] if isinstance(universe_raw, list) else []
        return LedgerEntry(
            key=str(d["key"]),
            name=str(d["name"]),
            timeframe=str(d["timeframe"]),
            horizon=int(d["horizon"]),
            round_trip_cost_bps=float(d["round_trip_cost_bps"]),
            universe=universe,
            survived=bool(d["survived"]),
            mean_net_bps=float(d["mean_net_bps"]),
            total_trades=int(d["total_trades"]),
            n_symbols_survived=int(d["n_symbols_survived"]),
            as_of_utc=str(d["as_of_utc"]),
            lookback_days=int(d["lookback_days"]),
            recorded_at_utc=str(d["recorded_at_utc"]),
        )


class HypothesisLedger:
    """Append-only JSONL ledger of tested hypothesis configurations."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def record(self, entry: LedgerEntry) -> None:
        """Append one entry (creates parent dirs on first write)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(entry.to_json() + "\n")

    def entries(self) -> list[LedgerEntry]:
        """All recorded entries (corrupt lines skipped, never raises)."""
        if not self._path.exists():
            return []
        out: list[LedgerEntry] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    out.append(LedgerEntry.from_dict(parsed))
            except (ValueError, TypeError, KeyError):
                continue  # a single bad line must never break the search
        return out

    def keys(self) -> set[str]:
        """Distinct hypothesis-configuration keys recorded so far."""
        return {e.key for e in self.entries()}

    def was_tested(self, key: str) -> bool:
        """True if this exact hypothesis configuration was recorded before."""
        return key in self.keys()

    def tested_count(self) -> int:
        """Number of distinct hypothesis configurations ever recorded."""
        return len(self.keys())
