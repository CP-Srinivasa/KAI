# Universe-Eligibility-Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine wiederverwendbare Eligibility-SSOT, die Symbole gegen die kanonische Venue Binance auf Liquidität, Mindest-Historie und Doppel-Paare prüft und strukturell unbrauchbare (u.a. off-Binance) Symbole vorgelagert flaggt — shadow-first, ohne zu filtern.

**Architecture:** Pures Entscheidungsmodul (`symbol_eligibility.py`) + I/O-Fetcher (`symbol_eligibility_fetch.py`, fail-soft gegen `BinanceAdapter`) + Audit-Ledger (`symbol_eligibility_ledger.py`). Der `momentum_universe_refresh` ruft das Gate auf und schreibt Verdikte ins eigene Ledger und additiv ins Universe-Ledger. Filtern (enforce) ist bewusst NICHT Teil dieses PRs.

**Tech Stack:** Python 3.12, asyncio, typer (CLI), pytest. Wiederverwendet `app.trading.asset_universe.base_symbol`, `app.market_data.binance_adapter.BinanceAdapter`, `app.market_data.models.{OHLCV,Ticker}`.

## Global Constraints

- Python 3.12; `from __future__ import annotations` in jedem neuen Modul (Repo-Konvention).
- Honesty-Contract: fehlende Daten ⇒ `None` ⇒ nicht bewertbar ⇒ ineligible. NIE schätzen.
- Fail-soft: kein Netzwerkfehler darf den Refresh crashen (Vertrag wie `build_universe`).
- Shadow-only: dieser PR FILTERT NICHTS. Kein Konsument wird auf enforce geschaltet.
- Tests liegen in `tests/unit/test_<modul>.py`. Pure Logik ohne Netzwerk/Disk testen.
- Turnover-Näherung: `turnover_usd = ticker.volume_24h * ticker.last` (Basis-Volumen × Preis) — kein Adapter-Change.
- Commits: konventionelle `feat:`/`test:`-Prefixes; häufig committen (pro Task).
- Vor PR: `bash ~/KAI-mirror/scripts/kai_preflight.sh` grün (ruff/mypy/pytest/godfile-ratchet).

---

### Task 1: Pure Decision Core — `evaluate_eligibility` + Dataclasses

**Files:**
- Create: `app/trading/symbol_eligibility.py`
- Test: `tests/unit/test_symbol_eligibility.py`

**Interfaces:**
- Consumes: nichts (rein).
- Produces:
  - `@dataclass(frozen=True) SymbolMetrics(symbol: str, base: str, quote: str, turnover_24h_usd: float | None, history_days: int | None)`
  - `@dataclass(frozen=True) EligibilityVerdict(symbol: str, eligible: bool, reasons: list[str])`
  - `DEFAULT_MIN_TURNOVER_USD: float = 10_000_000.0`
  - `DEFAULT_MIN_HISTORY_DAYS: int = 30`
  - `evaluate_eligibility(metrics: SymbolMetrics, *, min_turnover_usd: float = DEFAULT_MIN_TURNOVER_USD, min_history_days: int = DEFAULT_MIN_HISTORY_DAYS, duplicate_of: str | None = None) -> EligibilityVerdict`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_symbol_eligibility.py
"""Tests für das pure Eligibility-Entscheidungs-Core."""

from __future__ import annotations

from app.trading.symbol_eligibility import (
    DEFAULT_MIN_HISTORY_DAYS,
    DEFAULT_MIN_TURNOVER_USD,
    EligibilityVerdict,
    SymbolMetrics,
    evaluate_eligibility,
)


def _m(symbol: str, turnover: float | None, history: int | None) -> SymbolMetrics:
    base, _, quote = symbol.partition("/")
    return SymbolMetrics(
        symbol=symbol, base=base, quote=quote,
        turnover_24h_usd=turnover, history_days=history,
    )


def test_healthy_symbol_is_eligible() -> None:
    v = evaluate_eligibility(_m("BTC/USDT", 5e8, 365))
    assert isinstance(v, EligibilityVerdict)
    assert v.eligible is True
    assert v.reasons == []


def test_no_canonical_venue_data_when_both_missing() -> None:
    v = evaluate_eligibility(_m("SLX/USDT", None, None))
    assert v.eligible is False
    assert v.reasons == ["no_canonical_venue_data"]


def test_below_min_turnover_is_ineligible() -> None:
    v = evaluate_eligibility(_m("FOO/USDT", 1_000.0, 365))
    assert v.eligible is False
    assert "below_min_turnover" in v.reasons


def test_below_min_history_is_ineligible() -> None:
    v = evaluate_eligibility(_m("NEW/USDT", 5e8, 5))
    assert v.eligible is False
    assert "below_min_history" in v.reasons


def test_partial_missing_data_lists_specific_reason() -> None:
    v = evaluate_eligibility(_m("FOO/USDT", None, 365))
    assert v.eligible is False
    assert v.reasons == ["no_turnover_data"]


def test_duplicate_is_ineligible_with_canonical_reason() -> None:
    v = evaluate_eligibility(_m("BTC/USDC", 5e8, 365), duplicate_of="BTC/USDT")
    assert v.eligible is False
    assert "duplicate_of:BTC/USDT" in v.reasons


def test_duplicate_of_self_is_not_flagged() -> None:
    v = evaluate_eligibility(_m("BTC/USDT", 5e8, 365), duplicate_of="BTC/USDT")
    assert v.eligible is True
    assert v.reasons == []


def test_thresholds_are_parametrised() -> None:
    assert DEFAULT_MIN_TURNOVER_USD == 10_000_000.0
    assert DEFAULT_MIN_HISTORY_DAYS == 30
    v = evaluate_eligibility(_m("FOO/USDT", 2e6, 365), min_turnover_usd=1e6)
    assert v.eligible is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_symbol_eligibility.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.trading.symbol_eligibility'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/trading/symbol_eligibility.py
"""Symbol-Eligibility — pure structural verdict whether a symbol is usable.

Auto-computed counterpart to the operator-curated ``asset_universe``: decides,
from metrics measured against the CANONICAL venue (Binance — where edge is
measured/resolved), whether a symbol is structurally usable. NO directional /
momentum / edge judgement — only "structurally usable" vs "not".

Honesty-Contract (KAI rule "fehlende Daten = nicht bewertbar, niemals
schätzen"): if a metric is ``None`` it counts against eligibility; a symbol
with NO canonical-venue data at all is ineligible with a single explicit reason
(this is how off-Binance symbols like SLX/VELVET fall out without a separate
exchangeInfo gate).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MIN_TURNOVER_USD: float = 10_000_000.0
DEFAULT_MIN_HISTORY_DAYS: int = 30


@dataclass(frozen=True)
class SymbolMetrics:
    """Canonical-venue metrics for one symbol. ``None`` = not measurable."""

    symbol: str
    base: str
    quote: str
    turnover_24h_usd: float | None
    history_days: int | None


@dataclass(frozen=True)
class EligibilityVerdict:
    """Structural verdict. ``reasons`` is empty iff eligible."""

    symbol: str
    eligible: bool
    reasons: list[str]


def evaluate_eligibility(
    metrics: SymbolMetrics,
    *,
    min_turnover_usd: float = DEFAULT_MIN_TURNOVER_USD,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
    duplicate_of: str | None = None,
) -> EligibilityVerdict:
    """Decide structural eligibility (pure, deterministic)."""
    # No canonical-venue data at all → single explicit reason (off-venue).
    if metrics.turnover_24h_usd is None and metrics.history_days is None:
        return EligibilityVerdict(metrics.symbol, False, ["no_canonical_venue_data"])

    reasons: list[str] = []

    if duplicate_of is not None and duplicate_of != metrics.symbol:
        reasons.append(f"duplicate_of:{duplicate_of}")

    if metrics.turnover_24h_usd is None:
        reasons.append("no_turnover_data")
    elif metrics.turnover_24h_usd < min_turnover_usd:
        reasons.append("below_min_turnover")

    if metrics.history_days is None:
        reasons.append("no_history_data")
    elif metrics.history_days < min_history_days:
        reasons.append("below_min_history")

    return EligibilityVerdict(metrics.symbol, not reasons, reasons)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_symbol_eligibility.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add app/trading/symbol_eligibility.py tests/unit/test_symbol_eligibility.py
git commit -m "feat(eligibility): pure symbol-eligibility decision core"
```

---

### Task 2: Duplikat-Resolver — `resolve_duplicates` (pure)

**Files:**
- Modify: `app/trading/symbol_eligibility.py` (add function)
- Test: `tests/unit/test_symbol_eligibility.py` (add cases)

**Interfaces:**
- Consumes: `app.trading.asset_universe.base_symbol(symbol: str) -> str`.
- Produces: `resolve_duplicates(symbols: list[str]) -> dict[str, str]` — maps each input symbol to the canonical symbol for its base (a symbol that is itself canonical maps to itself). Quote preference USDT > USDC > USD > other; spot (no `:`) before perp.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_symbol_eligibility.py
from app.trading.symbol_eligibility import resolve_duplicates


def test_resolve_prefers_usdt_over_usdc() -> None:
    out = resolve_duplicates(["BTC/USDC", "BTC/USDT"])
    assert out["BTC/USDT"] == "BTC/USDT"
    assert out["BTC/USDC"] == "BTC/USDT"


def test_resolve_prefers_spot_over_perp() -> None:
    out = resolve_duplicates(["BTC/USDT:USDT", "BTC/USDT"])
    assert out["BTC/USDT"] == "BTC/USDT"
    assert out["BTC/USDT:USDT"] == "BTC/USDT"


def test_resolve_keeps_distinct_bases_separate() -> None:
    out = resolve_duplicates(["BTC/USDT", "ETH/USDT"])
    assert out["BTC/USDT"] == "BTC/USDT"
    assert out["ETH/USDT"] == "ETH/USDT"


def test_resolve_single_member_is_canonical() -> None:
    out = resolve_duplicates(["SOL/USDC"])
    assert out["SOL/USDC"] == "SOL/USDC"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_symbol_eligibility.py -k resolve -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_duplicates'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to app/trading/symbol_eligibility.py
from collections import defaultdict

from app.trading.asset_universe import base_symbol

_QUOTE_RANK = {"USDT": 0, "USDC": 1, "USD": 2}


def _canonical_sort_key(symbol: str) -> tuple[int, int, str]:
    """Lower wins: preferred quote first, spot before perp, then lexical."""
    s = symbol.strip().upper()
    is_perp = 1 if ":" in s else 0
    # Quote = segment after '/', before any ':' (perp suffix).
    quote = s.split("/", 1)[1].split(":", 1)[0] if "/" in s else ""
    return (_QUOTE_RANK.get(quote, 9), is_perp, s)


def resolve_duplicates(symbols: list[str]) -> dict[str, str]:
    """Map each symbol to the canonical variant of its base (pure)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for s in symbols:
        groups[base_symbol(s)].append(s)
    out: dict[str, str] = {}
    for members in groups.values():
        canonical = min(members, key=_canonical_sort_key)
        for m in members:
            out[m] = canonical
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_symbol_eligibility.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add app/trading/symbol_eligibility.py tests/unit/test_symbol_eligibility.py
git commit -m "feat(eligibility): duplicate-pair resolver (base + quote preference)"
```

---

### Task 3: Metrics-Fetcher + Orchestrator (I/O, fail-soft)

**Files:**
- Create: `app/trading/symbol_eligibility_fetch.py`
- Test: `tests/unit/test_symbol_eligibility_fetch.py`

**Interfaces:**
- Consumes: `SymbolMetrics`, `EligibilityVerdict`, `evaluate_eligibility`, `resolve_duplicates` (Task 1+2); `app.market_data.models.{OHLCV,Ticker}`.
- Produces:
  - `class EligibilitySource(Protocol)` with `async get_ticker(symbol) -> Ticker | None` and `async get_ohlcv(symbol, timeframe=..., limit=...) -> list[OHLCV]`.
  - `async fetch_metrics(source: EligibilitySource, symbol: str, *, min_history_days: int) -> SymbolMetrics`
  - `async build_eligibility(source: EligibilitySource, symbols: list[str], *, min_turnover_usd: float = DEFAULT_MIN_TURNOVER_USD, min_history_days: int = DEFAULT_MIN_HISTORY_DAYS) -> list[EligibilityVerdict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_symbol_eligibility_fetch.py
"""Tests für den fail-soften Eligibility-Fetcher + Orchestrator."""

from __future__ import annotations

import pytest

from app.market_data.models import OHLCV, Ticker
from app.trading.symbol_eligibility_fetch import build_eligibility, fetch_metrics


def _ticker(symbol: str, last: float, volume: float) -> Ticker:
    return Ticker(
        symbol=symbol, timestamp_utc="2026-06-29T00:00:00Z",
        bid=last, ask=last, last=last, volume_24h=volume, change_pct_24h=0.0,
    )


def _candle(close: float) -> OHLCV:
    return OHLCV(
        symbol="X", timestamp_utc="2026-06-01T00:00:00Z", timeframe="1d",
        open=close, high=close, low=close, close=close, volume=1.0,
    )


class FakeSource:
    """Protocol-compatible fake. ``missing`` symbols mimic off-Binance (no data)."""

    def __init__(self, *, missing: set[str] | None = None, history: int = 365) -> None:
        self._missing = missing or set()
        self._history = history

    async def get_ticker(self, symbol: str) -> Ticker | None:
        if symbol in self._missing:
            return None
        return _ticker(symbol, last=100.0, volume=1_000_000.0)  # 100 * 1e6 = 1e8 turnover

    async def get_ohlcv(self, symbol, timeframe="1h", limit=100):  # type: ignore[no-untyped-def]
        if symbol in self._missing:
            return []
        return [_candle(100.0) for _ in range(self._history)]


@pytest.mark.asyncio
async def test_fetch_metrics_computes_turnover_and_history() -> None:
    m = await fetch_metrics(FakeSource(), "BTC/USDT", min_history_days=30)
    assert m.turnover_24h_usd == pytest.approx(1e8)  # 100 * 1_000_000
    assert m.history_days == 365
    assert m.base == "BTC"
    assert m.quote == "USDT"


@pytest.mark.asyncio
async def test_fetch_metrics_offvenue_yields_none() -> None:
    m = await fetch_metrics(FakeSource(missing={"SLX/USDT"}), "SLX/USDT", min_history_days=30)
    assert m.turnover_24h_usd is None
    assert m.history_days is None


@pytest.mark.asyncio
async def test_build_eligibility_flags_offvenue_and_keeps_good() -> None:
    src = FakeSource(missing={"SLX/USDT"})
    verdicts = await build_eligibility(src, ["BTC/USDT", "SLX/USDT"])
    by = {v.symbol: v for v in verdicts}
    assert by["BTC/USDT"].eligible is True
    assert by["SLX/USDT"].eligible is False
    assert by["SLX/USDT"].reasons == ["no_canonical_venue_data"]


@pytest.mark.asyncio
async def test_build_eligibility_flags_duplicate() -> None:
    verdicts = await build_eligibility(FakeSource(), ["BTC/USDT", "BTC/USDC"])
    by = {v.symbol: v for v in verdicts}
    assert by["BTC/USDT"].eligible is True
    assert by["BTC/USDC"].eligible is False
    assert "duplicate_of:BTC/USDT" in by["BTC/USDC"].reasons


@pytest.mark.asyncio
async def test_build_eligibility_short_history_ineligible() -> None:
    verdicts = await build_eligibility(FakeSource(history=10), ["ETH/USDT"], min_history_days=30)
    assert verdicts[0].eligible is False
    assert "below_min_history" in verdicts[0].reasons
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_symbol_eligibility_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.trading.symbol_eligibility_fetch'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/trading/symbol_eligibility_fetch.py
"""I/O layer: fetch canonical-venue (Binance) metrics + orchestrate verdicts.

Fail-soft by contract: any per-symbol fetch error yields ``None`` metrics (→
ineligible via the Honesty-Contract), never an exception — so a venue outage
degrades to "nothing eligible" rather than crashing the caller.
"""

from __future__ import annotations

from typing import Protocol

from app.market_data.models import OHLCV, Ticker
from app.trading.symbol_eligibility import (
    DEFAULT_MIN_HISTORY_DAYS,
    DEFAULT_MIN_TURNOVER_USD,
    EligibilityVerdict,
    SymbolMetrics,
    evaluate_eligibility,
    resolve_duplicates,
)

# Fetch a little more history than the floor so the count is unambiguous.
_HISTORY_BUFFER = 5


class EligibilitySource(Protocol):
    """Structural type satisfied by ``BinanceAdapter``."""

    async def get_ticker(self, symbol: str) -> Ticker | None: ...

    async def get_ohlcv(
        self, symbol: str, timeframe: str = ..., limit: int = ...
    ) -> list[OHLCV]: ...


async def fetch_metrics(
    source: EligibilitySource, symbol: str, *, min_history_days: int
) -> SymbolMetrics:
    """Fetch turnover (volume_24h × last) + history-day count. Fail-soft → None."""
    base, _, quote = symbol.partition("/")
    quote = quote.split(":", 1)[0]

    turnover: float | None = None
    try:
        ticker = await source.get_ticker(symbol)
    except Exception:  # noqa: BLE001 — fail-soft: not measurable
        ticker = None
    if ticker is not None and ticker.last > 0 and ticker.volume_24h >= 0:
        turnover = ticker.volume_24h * ticker.last

    history: int | None = None
    try:
        candles = await source.get_ohlcv(symbol, "1d", min_history_days + _HISTORY_BUFFER)
    except Exception:  # noqa: BLE001 — fail-soft: not measurable
        candles = []
    if candles:
        history = len(candles)

    return SymbolMetrics(
        symbol=symbol, base=base, quote=quote,
        turnover_24h_usd=turnover, history_days=history,
    )


async def build_eligibility(
    source: EligibilitySource,
    symbols: list[str],
    *,
    min_turnover_usd: float = DEFAULT_MIN_TURNOVER_USD,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
) -> list[EligibilityVerdict]:
    """Fetch metrics for every symbol, resolve duplicates, decide eligibility."""
    dup_map = resolve_duplicates(symbols)
    verdicts: list[EligibilityVerdict] = []
    for symbol in symbols:
        metrics = await fetch_metrics(source, symbol, min_history_days=min_history_days)
        verdicts.append(
            evaluate_eligibility(
                metrics,
                min_turnover_usd=min_turnover_usd,
                min_history_days=min_history_days,
                duplicate_of=dup_map.get(symbol),
            )
        )
    return verdicts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_symbol_eligibility_fetch.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/trading/symbol_eligibility_fetch.py tests/unit/test_symbol_eligibility_fetch.py
git commit -m "feat(eligibility): fail-soft Binance metrics fetcher + orchestrator"
```

---

### Task 4: Eligibility-Audit-Ledger

**Files:**
- Create: `app/observability/symbol_eligibility_ledger.py`
- Test: `tests/unit/test_symbol_eligibility_ledger.py`

**Interfaces:**
- Consumes: `EligibilityVerdict` (Task 1).
- Produces:
  - `eligibility_record(verdicts: Sequence[EligibilityVerdict], *, now: datetime) -> dict[str, object]`
  - `append_eligibility_snapshot(path: Path, verdicts: Sequence[EligibilityVerdict], *, now: datetime) -> dict[str, object]`
  - `read_latest_eligibility(path: Path) -> dict[str, object] | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_symbol_eligibility_ledger.py
"""Tests für das append-only Eligibility-Audit-Ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.observability.symbol_eligibility_ledger import (
    append_eligibility_snapshot,
    read_latest_eligibility,
)
from app.trading.symbol_eligibility import EligibilityVerdict


def _verdicts() -> list[EligibilityVerdict]:
    return [
        EligibilityVerdict("BTC/USDT", True, []),
        EligibilityVerdict("SLX/USDT", False, ["no_canonical_venue_data"]),
    ]


def test_append_and_read_latest_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "elig.jsonl"
    now = datetime(2026, 6, 29, tzinfo=UTC)
    rec = append_eligibility_snapshot(p, _verdicts(), now=now)
    assert rec["count"] == 2
    assert rec["eligible_count"] == 1
    latest = read_latest_eligibility(p)
    assert latest is not None
    assert latest["count"] == 2
    syms = {row["symbol"]: row for row in latest["verdicts"]}
    assert syms["BTC/USDT"]["eligible"] is True
    assert syms["SLX/USDT"]["reasons"] == ["no_canonical_venue_data"]


def test_append_is_append_only(tmp_path: Path) -> None:
    p = tmp_path / "elig.jsonl"
    append_eligibility_snapshot(p, _verdicts(), now=datetime(2026, 6, 29, tzinfo=UTC))
    append_eligibility_snapshot(p, _verdicts()[:1], now=datetime(2026, 6, 30, tzinfo=UTC))
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 2
    latest = read_latest_eligibility(p)
    assert latest is not None and latest["count"] == 1  # newest wins


def test_read_latest_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_latest_eligibility(tmp_path / "nope.jsonl") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_symbol_eligibility_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.symbol_eligibility_ledger'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/observability/symbol_eligibility_ledger.py
"""symbol_eligibility_ledger — append-only JSONL of eligibility-verdict snapshots.

Read-only "Sicht"/audit artifact: each evaluation appends one snapshot line.
Mirrors ``momentum_universe_ledger`` (JSONL, no DB migration). No trade state.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from app.trading.symbol_eligibility import EligibilityVerdict


def eligibility_record(
    verdicts: Sequence[EligibilityVerdict], *, now: datetime
) -> dict[str, object]:
    """Build one snapshot record (pure; no I/O)."""
    return {
        "ts": now.isoformat(),
        "count": len(verdicts),
        "eligible_count": sum(1 for v in verdicts if v.eligible),
        "verdicts": [
            {"symbol": v.symbol, "eligible": v.eligible, "reasons": list(v.reasons)}
            for v in verdicts
        ],
    }


def append_eligibility_snapshot(
    path: Path, verdicts: Sequence[EligibilityVerdict], *, now: datetime
) -> dict[str, object]:
    """Append a snapshot line to ``path`` (creating parents). Returns the record."""
    record = eligibility_record(verdicts, now=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return record


def read_latest_eligibility(path: Path) -> dict[str, object] | None:
    """Return the newest valid snapshot record, or ``None`` if missing/empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    latest: dict[str, object] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            latest = obj
    return latest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_symbol_eligibility_ledger.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/observability/symbol_eligibility_ledger.py tests/unit/test_symbol_eligibility_ledger.py
git commit -m "feat(eligibility): append-only eligibility audit ledger"
```

---

### Task 5: Universe-Ledger additive Eligibility-Einbettung

**Files:**
- Modify: `app/observability/momentum_universe_ledger.py:18-44`
- Test: `tests/unit/test_momentum_universe_ledger.py` (add cases)

**Interfaces:**
- Consumes: `RankedSymbol` (existing).
- Produces: extended `snapshot_record(ranked, *, now, eligibility: Mapping[str, dict] | None = None)` and `append_snapshot(path, ranked, *, now, eligibility=None)`. When `eligibility` is given, each universe row gains `eligible: bool` and `reasons: list[str]` for symbols present in the mapping. When omitted, output is byte-identical to before (backwards-compatible).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_momentum_universe_ledger.py
from app.observability.momentum_universe_ledger import snapshot_record
from app.observability.momentum_universe import RankedSymbol
from datetime import UTC, datetime


def _ranked() -> list[RankedSymbol]:
    return [
        RankedSymbol("BTC/USDT", 0.9, 0.9, 0.9, 1),
        RankedSymbol("SLX/USDT", 0.8, 0.8, 0.8, 2),
    ]


def test_snapshot_without_eligibility_has_no_flag() -> None:
    rec = snapshot_record(_ranked(), now=datetime(2026, 6, 29, tzinfo=UTC))
    assert "eligible" not in rec["universe"][0]


def test_snapshot_embeds_eligibility_when_given() -> None:
    elig = {
        "BTC/USDT": {"eligible": True, "reasons": []},
        "SLX/USDT": {"eligible": False, "reasons": ["no_canonical_venue_data"]},
    }
    rec = snapshot_record(_ranked(), now=datetime(2026, 6, 29, tzinfo=UTC), eligibility=elig)
    rows = {r["symbol"]: r for r in rec["universe"]}
    assert rows["BTC/USDT"]["eligible"] is True
    assert rows["SLX/USDT"]["eligible"] is False
    assert rows["SLX/USDT"]["reasons"] == ["no_canonical_venue_data"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_momentum_universe_ledger.py -k eligibility -v`
Expected: FAIL — `TypeError: snapshot_record() got an unexpected keyword argument 'eligibility'`

- [ ] **Step 3: Write minimal implementation**

Replace `snapshot_record` and `append_snapshot` in `app/observability/momentum_universe_ledger.py` with:

```python
from collections.abc import Mapping, Sequence


def snapshot_record(
    ranked: Sequence[RankedSymbol],
    *,
    now: datetime,
    eligibility: Mapping[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build one snapshot record (pure; no I/O).

    When ``eligibility`` is given, each row for a symbol present in it gains
    ``eligible``/``reasons`` (shadow flag, additive — never filters here).
    """

    def _row(r: RankedSymbol) -> dict[str, object]:
        row: dict[str, object] = {
            "symbol": r.symbol,
            "rank": r.rank,
            "universe_score": round(r.universe_score, 6),
            "volume_score": round(r.volume_score, 6),
            "momentum_score": round(r.momentum_score, 6),
        }
        if eligibility is not None and r.symbol in eligibility:
            verdict = eligibility[r.symbol]
            row["eligible"] = verdict.get("eligible")
            row["reasons"] = verdict.get("reasons", [])
        return row

    return {
        "ts": now.isoformat(),
        "count": len(ranked),
        "universe": [_row(r) for r in ranked],
    }


def append_snapshot(
    path: Path,
    ranked: Sequence[RankedSymbol],
    *,
    now: datetime,
    eligibility: Mapping[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Append a snapshot line to ``path`` (creating parents). Returns the record."""
    record = snapshot_record(ranked, now=now, eligibility=eligibility)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return record
```

Remove the now-duplicate top-level `from collections.abc import Sequence` import if it conflicts (merge into the `Mapping, Sequence` import).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_momentum_universe_ledger.py -v`
Expected: PASS (all existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add app/observability/momentum_universe_ledger.py tests/unit/test_momentum_universe_ledger.py
git commit -m "feat(eligibility): additive eligibility flag in universe ledger rows"
```

---

### Task 6: Refresh-Integration (shadow, no filtering)

**Files:**
- Modify: `scripts/momentum_universe_refresh.py:29-55`
- Test: `tests/unit/test_momentum_universe_refresh.py` (add a case)

**Interfaces:**
- Consumes: `build_eligibility` (Task 3), `append_eligibility_snapshot` (Task 4), extended `append_snapshot` (Task 5), `BinanceAdapter`.
- Produces: refresh writes BOTH `artifacts/symbol_eligibility_audit.jsonl` and the eligibility-flagged universe ledger. Does NOT filter the universe.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_momentum_universe_refresh.py
import asyncio
from pathlib import Path

from app.observability.momentum_universe import RankedSymbol
from app.observability.symbol_eligibility_ledger import read_latest_eligibility
from app.trading.symbol_eligibility import EligibilityVerdict


def test_refresh_writes_eligibility_without_filtering(tmp_path, monkeypatch) -> None:
    import scripts.momentum_universe_refresh as refresh

    ranked = [RankedSymbol("BTC/USDT", 0.9, 0.9, 0.9, 1),
              RankedSymbol("SLX/USDT", 0.8, 0.8, 0.8, 2)]

    async def _fake_build_universe(*a, **k):
        return ranked

    async def _fake_build_eligibility(source, symbols, **k):
        return [EligibilityVerdict("BTC/USDT", True, []),
                EligibilityVerdict("SLX/USDT", False, ["no_canonical_venue_data"])]

    monkeypatch.setattr(refresh, "build_universe", _fake_build_universe)
    monkeypatch.setattr(refresh, "build_eligibility", _fake_build_eligibility)

    uni_ledger = tmp_path / "uni.jsonl"
    elig_ledger = tmp_path / "elig.jsonl"
    rc = asyncio.run(refresh._run(object(), uni_ledger, elig_ledger))
    assert rc == 0

    # Universe ledger keeps BOTH symbols (no filtering) but carries the flag.
    import json
    uni = json.loads(uni_ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert uni["count"] == 2
    rows = {r["symbol"]: r for r in uni["universe"]}
    assert rows["SLX/USDT"]["eligible"] is False

    elig = read_latest_eligibility(elig_ledger)
    assert elig is not None and elig["eligible_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_momentum_universe_refresh.py -k eligibility -v`
Expected: FAIL — `_run()` takes 2 positional args / `build_eligibility` attribute missing.

- [ ] **Step 3: Write minimal implementation**

Edit `scripts/momentum_universe_refresh.py`:

```python
# add imports near the other app imports
from app.observability.symbol_eligibility_ledger import (  # noqa: E402
    append_eligibility_snapshot,
)
from app.trading.symbol_eligibility_fetch import build_eligibility  # noqa: E402

# add ledger constant near _LEDGER
_ELIG_LEDGER = Path("artifacts/symbol_eligibility_audit.jsonl")
```

Replace `_run` and `main`:

```python
async def _run(
    source: MomentumUniverseSource,
    ledger: Path,
    elig_ledger: Path,
) -> int:
    ranked = await asyncio.wait_for(
        build_universe(source, top_n=_TOP_N, universe_limit=_UNIVERSE_LIMIT),
        _DEADLINE_S,
    )
    if not ranked:
        print("momentum_universe_refresh: source unavailable — keeping last snapshot")
        return 0

    # Shadow eligibility against the CANONICAL venue (Binance) — flags, never filters.
    elig_map: dict[str, dict[str, object]] = {}
    try:
        from app.market_data.binance_adapter import BinanceAdapter

        verdicts = await build_eligibility(BinanceAdapter(), [r.symbol for r in ranked])
        append_eligibility_snapshot(elig_ledger, verdicts, now=datetime.now(UTC))
        elig_map = {
            v.symbol: {"eligible": v.eligible, "reasons": v.reasons} for v in verdicts
        }
    except Exception as exc:  # noqa: BLE001 — eligibility is shadow; never break the refresh
        print(f"momentum_universe_refresh: eligibility skipped ({exc})", file=sys.stderr)

    record = append_snapshot(ledger, ranked, now=datetime.now(UTC), eligibility=elig_map or None)
    print(f"momentum_universe_refresh: wrote {record['count']} symbols")
    return 0


def main() -> int:
    from app.market_data.bybit_adapter import BybitAdapter

    try:
        return asyncio.run(_run(BybitAdapter(), _LEDGER, _ELIG_LEDGER))
    except Exception as exc:  # noqa: BLE001 — fail-safe: keep the last good snapshot
        print(f"momentum_universe_refresh failed: {exc}", file=sys.stderr)
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_momentum_universe_refresh.py -v`
Expected: PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add scripts/momentum_universe_refresh.py tests/unit/test_momentum_universe_refresh.py
git commit -m "feat(eligibility): refresh writes shadow eligibility (no filtering)"
```

---

### Task 7: CLI — `universe eligibility-run` + `eligibility-show`

**Files:**
- Modify: `app/cli/commands/universe.py` (append two commands)
- Test: `tests/unit/test_universe_cli_eligibility.py`

**Interfaces:**
- Consumes: `build_eligibility` (Task 3), `append_eligibility_snapshot`/`read_latest_eligibility` (Task 4), `read_latest` (universe ledger).
- Produces: `universe eligibility-run` (live: read latest universe snapshot symbols → fetch Binance metrics → write eligibility ledger → print JSON) and `universe eligibility-show` (offline read of the eligibility ledger).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_universe_cli_eligibility.py
"""Smoke tests für die universe eligibility CLI-Commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from app.cli.commands.universe import universe_app

runner = CliRunner()


def test_eligibility_show_no_snapshot(tmp_path: Path) -> None:
    result = runner.invoke(universe_app, ["eligibility-show", "--ledger", str(tmp_path / "x.jsonl")])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["available"] is False


def test_eligibility_show_reads_latest(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from app.observability.symbol_eligibility_ledger import append_eligibility_snapshot
    from app.trading.symbol_eligibility import EligibilityVerdict

    p = tmp_path / "elig.jsonl"
    append_eligibility_snapshot(
        p, [EligibilityVerdict("BTC/USDT", True, [])], now=datetime(2026, 6, 29, tzinfo=UTC)
    )
    result = runner.invoke(universe_app, ["eligibility-show", "--ledger", str(p)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_universe_cli_eligibility.py -v`
Expected: FAIL — `No such command 'eligibility-show'`

- [ ] **Step 3: Write minimal implementation**

Append to `app/cli/commands/universe.py`:

```python
_ELIG_LEDGER = Path("artifacts/symbol_eligibility_audit.jsonl")


@universe_app.command("eligibility-run")
def eligibility_run(
    ledger: Annotated[Path, typer.Option(help="Universe snapshot to source symbols from.")] = _DEFAULT_LEDGER,
    out: Annotated[Path, typer.Option(help="Eligibility ledger path.")] = _ELIG_LEDGER,
    min_turnover_usd: Annotated[float, typer.Option(help="Min 24h turnover (USD).")] = 10_000_000.0,
    min_history_days: Annotated[int, typer.Option(help="Min canonical-venue history (days).")] = 30,
) -> None:
    """Shadow-evaluate the latest universe's symbols against Binance. No trades, no filter."""
    from datetime import UTC, datetime

    from app.market_data.binance_adapter import BinanceAdapter
    from app.observability.momentum_universe_ledger import read_latest
    from app.observability.symbol_eligibility_ledger import append_eligibility_snapshot
    from app.trading.symbol_eligibility_fetch import build_eligibility

    latest = read_latest(ledger)
    universe = latest.get("universe") if isinstance(latest, dict) else None
    if not isinstance(universe, list) or not universe:
        typer.echo(json.dumps({"available": False, "reason": "no_universe"}))
        return
    symbols = [row["symbol"] for row in universe if isinstance(row, dict) and "symbol" in row]
    verdicts = asyncio.run(
        build_eligibility(
            BinanceAdapter(), symbols,
            min_turnover_usd=min_turnover_usd, min_history_days=min_history_days,
        )
    )
    record = append_eligibility_snapshot(out, verdicts, now=datetime.now(UTC))
    typer.echo(json.dumps(record, indent=2))


@universe_app.command("eligibility-show")
def eligibility_show(
    ledger: Annotated[Path, typer.Option(help="Eligibility ledger path.")] = _ELIG_LEDGER,
) -> None:
    """Print the latest persisted eligibility snapshot (offline)."""
    from app.observability.symbol_eligibility_ledger import read_latest_eligibility

    latest = read_latest_eligibility(ledger)
    if latest is None:
        typer.echo(json.dumps({"available": False, "reason": "no_snapshot"}))
        return
    typer.echo(json.dumps(latest, indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_universe_cli_eligibility.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/cli/commands/universe.py tests/unit/test_universe_cli_eligibility.py
git commit -m "feat(eligibility): universe eligibility-run/eligibility-show CLI"
```

---

### Task 8: Full gate + Pi shadow-verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full preflight gate**

Run: `bash ~/KAI-mirror/scripts/kai_preflight.sh`
Expected: ruff, mypy, pytest, godfile-ratchet all PASS.

- [ ] **Step 2: Open the PR** (per KAI workflow; CI must go green — see [[feedback_ci_watch_exit_not_green_proof]]).

- [ ] **Step 3: After merge+deploy — run the shadow check on the Pi**

Run (on Pi): `cd /home/ubuntu/ai_analyst_trading_bot && .venv/bin/python -m app.cli universe eligibility-run`
Then: `.venv/bin/python -m app.cli universe eligibility-show`

Expected (acceptance): known off-venue/microcap names from the live universe (e.g. `VELVET/USDT`, `SLX/USDT`, `ACT/USDT`) carry `eligible=false` with reason `no_canonical_venue_data` (or `below_min_turnover`/`below_min_history`), while established names (`BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `XRP/USDT`) carry `eligible=true`. Cross-check: no `eligible=true` symbol lacks Binance data (Honesty-Contract holds).

---

## Self-Review

**Spec coverage:** Pure core (Task 1) ✓ · duplicate resolver (Task 2) ✓ · fail-soft fetcher against Binance (Task 3) ✓ · audit ledger (Task 4) ✓ · refresh writes flag without filtering (Tasks 5+6) ✓ · CLI inspect (Task 7) ✓ · preflight + Pi shadow verification (Task 8) ✓. Default thresholds (30d / 10M USD) encoded as module constants + CLI options. "Bewusst draußen" (no exchangeInfo gate, no momentum criterion, no dashboard panel, no enforce) — honoured: none of those appear in any task.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command has expected output.

**Type consistency:** `SymbolMetrics`/`EligibilityVerdict` field names identical across Tasks 1/3/4. `evaluate_eligibility(... duplicate_of=)` matches `build_eligibility`'s `dup_map.get(symbol)` usage. `build_eligibility` signature in Task 3 matches its calls in Tasks 6 (refresh) and 7 (CLI). `eligibility` kwarg added to `snapshot_record`/`append_snapshot` in Task 5 matches its use in Task 6. Ledger field names (`verdicts`/`eligible_count`/`count`) consistent across Tasks 4/6/7.
