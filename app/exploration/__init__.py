"""KAI Source-Intake Exploration sandbox.

ISOLATED, REVERSIBLE EXPLORATION LAYER — see
``docs/strategy/source_intake_exploration_plan.md`` and DECISION DEC-SRC-EXPLORE-001.

This package exists to *measure* what candidate data sources (CoinGlass, Messari,
Dune, CoinGecko, Glassnode, CoinMarketCap, Nansen) actually deliver — via both
official APIs and (operator-authorised, grey-area) scrapers — BEFORE any source is
graduated into the production pipeline (``app/ingestion`` / ``app/market_data`` /
``app/signals``).

Hard isolation rules (enforced by tests/unit/exploration/test_import_isolation.py):
  - No production runtime module (signals, orchestrator, execution, trading, risk,
    alerts, market_data, pipeline) may import ``app.exploration``.
  - ``app.exploration`` may NOT import from those runtime modules either.
  - Only low-level shared utilities (app.security.ssrf, app.core.logging) are allowed.

Hard ethical lines (NOT covered by the §5 override, always in force):
  - No login / paywall / auth bypass, no CAPTCHA-breaking.
  - No DoS-level request rates — throttling + caching are mandatory.
  - No secrets in the repo.

The whole layer is removable with ``rm -rf app/exploration`` without any
production side effects, and is default-off.
"""

from __future__ import annotations

__all__ = ["__doc__"]
