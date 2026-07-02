#!/usr/bin/env python3
"""Entkoppelter CoinGecko-Overview Refresh-Service (G1, Source-Intake §9).

Zieht den Markt-Kontext (``market_cap_rank`` / ``market_cap`` /
``price_change_pct_30d``) für eine bounded Symbol-Liste über den bestehenden,
bewiesenen ``CoinGeckoAdapter.get_market_overview`` (Free-Tier per Default) und
schreibt ihn atomar in den ``CoinGeckoOverviewStore`` (warmer Snapshot für den
späteren Loop-Pfad, Increment 2) PLUS eine append-only Shadow-Spur
(``shadow_log_path``), damit die Materialität gemessen werden kann, BEVOR die
Daten Universe/Screener beeinflussen.

Warum entkoppelt (identisch zur V5-Begründung)
==============================================
Der Trading-Loop ist ein cron-one-shot. Inline pro Zyklus über viele Symbole
HTTP zu ziehen wäre ein Latenz-/Hänge-Risiko. Dieser Service wärmt periodisch
eine kleine JSON-Datei; der (spätere) Loop liest nur diese — schneller
Disk-Read, kein Netz. Rank/MarketCap bewegen sich langsam → wenige Min Kadenz
genügt.

Bounded / fail-safe
===================
- Per-Call-HTTP-Timeout über den Adapter (``refresh_timeout_seconds``).
- Globaler ``asyncio.wait_for``-Deckel über den Gesamtlauf → nie unbegrenztes
  Hängen, selbst bei toter/throttelnder API.
- Jeder Symbol-Fehler ⇒ Symbol wird übersprungen (kein Abbruch).
- 0 aufgelöste Symbole ⇒ alter Snapshot bleibt unverändert (kein Leerschreiben).

Default-disabled: ``APP_COINGECKO_OVERVIEW_ENABLED`` default False ⇒ no-op
(exit 0). Der systemd-Timer wird installiert aber nicht enabled (Operator-Gate).

Exit codes: 0 ok (auch bei disabled / 0 Symbolen), 2 unerwarteter Fehler.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.market_data.coingecko_adapter import CoinGeckoAdapter  # noqa: E402
from app.market_data.coingecko_overview import (  # noqa: E402
    CoinGeckoMarketOverview,
    CoinGeckoOverviewSettings,
    CoinGeckoOverviewStore,
    append_overview_shadow_log,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("coingecko_overview_refresh")

# Harte Obergrenze für den Gesamtlauf, damit der Service nie hängt.
_GLOBAL_DEADLINE_SECONDS = 120.0


async def _refresh(adapter: CoinGeckoAdapter, symbols: list[str]) -> list[CoinGeckoMarketOverview]:
    # ONE batched /coins/markets call for ALL symbols — not N sequential calls.
    # N single-symbol calls trip the free-tier rate limit (429 backoff) and blow
    # the global deadline, writing nothing. The batch is a single request.
    try:
        out = await adapter.get_market_overview_batch(symbols)
    except Exception as exc:  # noqa: BLE001 — der Batch darf den Lauf nie killen
        logger.warning("[cg-overview-refresh] batch failed: %s", exc)
        return []
    if not out:
        logger.info(
            "[cg-overview-refresh] batch returned 0 (%s)",
            adapter.last_error,
        )
        return []
    seen = {ov.symbol for ov in out}
    missing = [s for s in symbols if s.strip().upper() not in seen]
    if missing:
        logger.info("[cg-overview-refresh] not returned by API: %s", ",".join(missing))
    for ov in out:
        logger.info(
            "[cg-overview-refresh] %s rank=%s mcap=%s chg30d=%s",
            ov.symbol,
            ov.market_cap_rank,
            ov.market_cap,
            ov.price_change_pct_30d,
        )
    return out


def main() -> int:
    settings = CoinGeckoOverviewSettings()
    if not settings.enabled:
        logger.info("[cg-overview-refresh] disabled (APP_COINGECKO_OVERVIEW_ENABLED!=true) — no-op")
        return 0

    symbols = settings.symbols
    # Free-Tier per Default (api_key=None ⇒ Adapter nutzt die keylose Free-Base).
    adapter = CoinGeckoAdapter(
        timeout_seconds=max(1, int(round(settings.refresh_timeout_seconds))),
        api_key=settings.api_key,
    )
    store = CoinGeckoOverviewStore(settings.snapshot_path)

    logger.info(
        "[cg-overview-refresh] start symbols=%s tier=%s snapshot=%s",
        symbols,
        "pro" if adapter.is_pro_tier else "free",
        settings.snapshot_path,
    )
    try:
        overviews = asyncio.run(
            asyncio.wait_for(
                _refresh(adapter, symbols),
                timeout=_GLOBAL_DEADLINE_SECONDS,
            )
        )
    except TimeoutError:
        logger.warning(
            "[cg-overview-refresh] global deadline %ss hit — old snapshot kept",
            _GLOBAL_DEADLINE_SECONDS,
        )
        return 0
    except Exception:  # noqa: BLE001
        logger.exception("[cg-overview-refresh] unexpected error")
        return 2

    if not overviews:
        logger.warning("[cg-overview-refresh] 0 symbols resolved — old snapshot kept")
        return 0

    # Measure-first: Shadow-Spur IMMER schreiben (auch wenn der Snapshot-Write
    # scheitern würde) — das ist die Mess-Grundlage.
    for ov in overviews:
        append_overview_shadow_log(settings.shadow_log_path, overview=ov)
    written = store.write_many(overviews)
    logger.info(
        "[cg-overview-refresh] wrote %d snapshots → %s (+%d shadow lines)",
        written,
        settings.snapshot_path,
        len(overviews),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
