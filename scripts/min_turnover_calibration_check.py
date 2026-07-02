#!/usr/bin/env python3
"""Monthly read-only calibration check for the Universe-Eligibility-Gate's
``DEFAULT_MIN_TURNOVER_USD`` floor.

The turnover floor was calibrated (2026-06-30, #513) to sit inside the p80-p90
band of the live Binance 24h turnover distribution across all USDT spot pairs.
That distribution drifts slowly over time. This check re-pulls the distribution,
recomputes the p80/p90 band, and reports whether the live floor still sits inside
it. It NEVER changes code — on drift it writes a recommendation to an artifact and
logs a WARNING so the operator/assistant can ship a deliberate one-line PR.

READ-ONLY: one public Binance REST call, no key, no trades, no capital. Fail-soft:
any network/parse error logs a warning and exits 0 without overwriting the last
good record.
"""

from __future__ import annotations

import json
import logging
import math
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from app.trading.symbol_eligibility import DEFAULT_MIN_TURNOVER_USD

logger = logging.getLogger("min_turnover_calibration")

BINANCE_24H = "https://api.binance.com/api/v3/ticker/24hr"
ARTIFACT = Path("artifacts/min_turnover_calibration.jsonl")
_STABLE_BASES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "TRY", "BRL"}
_LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")


def percentile(values_sorted: list[float], q: float) -> float:
    """Nearest-rank percentile (q in [0,1]) of an ascending-sorted list."""
    if not values_sorted:
        raise ValueError("empty")
    n = len(values_sorted)
    i = max(0, min(n - 1, round(q * (n - 1))))
    return values_sorted[i]


def round_nice(x: float) -> float:
    """Round to the nearest 'nice' liquidity threshold (1/2/3/5 x 10^k),
    chosen by closeness in log-space so a threshold reads cleanly to a human."""
    if x <= 0:
        return 0.0
    candidates: list[float] = []
    k = math.floor(math.log10(x)) - 1
    for exp in range(k, k + 3):
        for m in (1.0, 2.0, 3.0, 5.0):
            candidates.append(m * (10.0**exp))
    return min(candidates, key=lambda c: abs(math.log(c) - math.log(x)))


def assess_floor(p80: float, p90: float, current_floor: float) -> dict:
    """Decide whether ``current_floor`` still sits inside the [p80, p90] band.

    In-band → ok, no change. Out-of-band → drift, recommend the rounded
    geometric mid of the band (a stable target that re-centres the floor)."""
    in_band = p80 <= current_floor <= p90
    if in_band:
        return {"in_band": True, "status": "ok", "recommended_floor_usd": None}
    recommended = round_nice(math.sqrt(p80 * p90))
    return {"in_band": False, "status": "drift", "recommended_floor_usd": recommended}


def _fetch_turnovers() -> list[float]:
    req = urllib.request.Request(BINANCE_24H, headers={"User-Agent": "kai-calib/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (public, read-only)
        data = json.loads(resp.read().decode("utf-8"))
    out: list[float] = []
    for row in data:
        sym = row.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if any(x in base for x in _LEVERAGED) or base in _STABLE_BASES:
            continue
        try:
            qv = float(row.get("quoteVolume"))
        except (TypeError, ValueError):
            continue
        out.append(qv)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        turnovers = _fetch_turnovers()
    except Exception as exc:  # noqa: BLE001 — fail-soft: keep last good record
        logger.warning("calibration check skipped (fetch failed): %s", exc)
        return 0
    if len(turnovers) < 50:
        logger.warning("calibration check skipped (too few pairs: %d)", len(turnovers))
        return 0

    turnovers.sort()
    p80 = percentile(turnovers, 0.80)
    p90 = percentile(turnovers, 0.90)
    verdict = assess_floor(p80, p90, DEFAULT_MIN_TURNOVER_USD)

    record = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "n_pairs": len(turnovers),
        "current_floor_usd": DEFAULT_MIN_TURNOVER_USD,
        "p80_usd": p80,
        "p90_usd": p90,
        **verdict,
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    if verdict["status"] == "drift":
        logger.warning(
            "DRIFT: min_turnover floor %.0f outside p80-p90 band [%.0f, %.0f] "
            "-> recommend %.0f (ship a calibration PR)",
            DEFAULT_MIN_TURNOVER_USD,
            p80,
            p90,
            verdict["recommended_floor_usd"],
        )
    else:
        logger.info(
            "OK: min_turnover floor %.0f within p80-p90 band [%.0f, %.0f] (n=%d)",
            DEFAULT_MIN_TURNOVER_USD,
            p80,
            p90,
            len(turnovers),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
