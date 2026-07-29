"""Unit-Test-Defaults.

W2 (2026-07-29): Produktions-Default der Outcome-Preisquelle ist ``binance``
(`ALERTS_OUTCOME_PRICE_SOURCE`). Die bestehenden Annotator-Tests mocken aber
`CoinGeckoAdapter` — ohne Pin würde der Binance-Default sie auf echte
Netzaufrufe schicken. Deshalb hier zentral auf ``coingecko`` gepinnt;
Binance-spezifische Tests überschreiben die Variable explizit per monkeypatch.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pin_outcome_price_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERTS_OUTCOME_PRICE_SOURCE", "coingecko")
