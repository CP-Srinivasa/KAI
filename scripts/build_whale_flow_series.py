"""Build a compact whale exchange-flow events artifact from the free Whale Alert
archive (read-only, $0, offline).

Source: https://whale-alert.io/whale-alerts-archive.json.gzip — historical alerts
posted to Whale Alert's social channels (BTC/ETH/SOL/USDT/USDC + more), an
explicitly research-licensed dataset. Each record carries a confirmation
``timestamp`` (point-in-time honest — the transfer is public knowledge at/after
confirmation; we use it as the event time, which is conservative since the alert
posts a few minutes later) and ``from``/``to`` owner labels.

We reduce each record to signed exchange flow per asset:
    to ∈ exchange,  from ∉ exchange  →  +value_usd   (inflow)
    from ∈ exchange, to ∉ exchange   →  -value_usd   (outflow)
    both / neither exchange           →  excluded (internal or non-exchange)

Output (artifacts/research/whale_flow_events.json):
    {"schema": 1, "coin": {"BTC": [[event_ms, signed_usd], ...], ...},
     "stable": [[event_ms, signed_usd], ...]}   # stable = market-wide USDT+USDC

This is a Phase-0 research preprocessor, not a production path. Run:
    python scripts/build_whale_flow_series.py --archive wa_archive.json.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

COIN_SYMBOLS = ("BTC", "ETH", "SOL")
STABLE_SYMBOLS = ("USDT", "USDC")

# Named centralised exchanges (lowercased owner labels as they appear in the
# archive). Treasuries (tether/usdc treasury), custody (xapo), and "unknown
# wallet" are intentionally NOT exchanges — only a transfer with exactly one
# exchange endpoint is a directional exchange flow.
EXCHANGES = frozenset(
    {
        "binance",
        "coinbase",
        "coinbase institutional",
        "bitfinex",
        "huobi",
        "htx",
        "ftx",
        "okex",
        "okx",
        "bitstamp",
        "kraken",
        "cryptocom",
        "crypto.com",
        "gemini",
        "bitso",
        "poloniex",
        "robinhood",
        "kucoin",
        "bybit",
        "gate.io",
        "gateio",
        "bittrex",
        "hitbtc",
        "bithumb",
        "upbit",
        "mexc",
        "bitflyer",
        "korbit",
        "liquid",
        "deribit",
    }
)

DEFAULT_ARCHIVE = "wa_archive.json.gz"
DEFAULT_OUT = Path("artifacts/research/whale_flow_events.json")


def _signed_usd(frm: str, to: str, value_usd: float) -> float | None:
    """Signed exchange flow for one transfer, or None if not a directional flow."""
    f_ex = frm in EXCHANGES
    t_ex = to in EXCHANGES
    if t_ex and not f_ex:
        return value_usd  # inflow to exchange
    if f_ex and not t_ex:
        return -value_usd  # outflow from exchange
    return None  # internal (both) or non-exchange (neither)


def build(archive: str, out: Path) -> int:
    """Reduce the archive to per-asset signed exchange-flow events; write JSON."""
    coin: dict[str, list[list[float]]] = {s: [] for s in COIN_SYMBOLS}
    stable: list[list[float]] = []
    n_records = 0
    n_coin = 0
    n_stable = 0

    with gzip.open(archive, "rt", encoding="utf-8") as fh:
        data = json.load(fh)

    for rec in data:
        n_records += 1
        ts = rec.get("timestamp")
        if not isinstance(ts, (int, float)) or ts <= 0:
            continue
        event_ms = int(ts) * 1000
        frm = (rec.get("from") or "").strip().lower()
        to = (rec.get("to") or "").strip().lower()
        for amt in rec.get("amounts") or []:
            sym = amt.get("symbol")
            val = amt.get("value_usd")
            if not isinstance(val, (int, float)) or val <= 0:
                continue
            signed = _signed_usd(frm, to, float(val))
            if signed is None:
                continue
            if sym in COIN_SYMBOLS:
                coin[sym].append([event_ms, signed])
                n_coin += 1
            elif sym in STABLE_SYMBOLS:
                stable.append([event_ms, signed])
                n_stable += 1

    for s in COIN_SYMBOLS:
        coin[s].sort(key=lambda p: p[0])
    stable.sort(key=lambda p: p[0])

    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": 1,
        "built_at_utc": datetime.now(UTC).isoformat(),
        "source": "whale-alert-archive",
        "exchanges": sorted(EXCHANGES),
        "coin": coin,
        "stable": stable,
    }
    out.write_text(json.dumps(doc), encoding="utf-8")
    logger.info(
        "records=%d coin_events=%d (BTC=%d ETH=%d SOL=%d) stable_events=%d -> %s",
        n_records,
        n_coin,
        len(coin["BTC"]),
        len(coin["ETH"]),
        len(coin["SOL"]),
        n_stable,
        out,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build whale exchange-flow events artifact.")
    parser.add_argument("--archive", default=DEFAULT_ARCHIVE, help="path to whale-alert .json.gz")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output JSON path")
    args = parser.parse_args()
    return build(args.archive, Path(args.out))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
