"""EXPLORE-S1/S2 — source probe parsing + key-gating, all with mocked HTTP.

No network is touched: each test monkeypatches the ``fetch`` symbol in the probe
module with an async stub returning a crafted ``HttpResponse``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.exploration.http import HttpResponse
from app.exploration.settings import ExplorationSettings


def _settings(**overrides: Any) -> ExplorationSettings:
    base: dict[str, Any] = {"enabled": True, "min_request_interval_seconds": 0.0}
    base.update(overrides)
    return ExplorationSettings(**base)


def _stub(response: HttpResponse):
    async def _fetch(*args: Any, **kwargs: Any) -> HttpResponse:
        return response

    return _fetch


def _stub_sequence(responses: list[HttpResponse]):
    calls = {"i": 0}

    async def _fetch(*args: Any, **kwargs: Any) -> HttpResponse:
        idx = min(calls["i"], len(responses) - 1)
        calls["i"] += 1
        return responses[idx]

    return _fetch


# ── CoinGecko (works without key) ──────────────────────────────────────────


async def test_coingecko_api_parses_markets_and_trending(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exploration.sources import coingecko

    markets = HttpResponse(
        ok=True,
        status=200,
        json=[
            {
                "id": "bitcoin",
                "symbol": "btc",
                "current_price": 65000,
                "market_cap": 1_200_000_000_000,
                "market_cap_rank": 1,
                "total_volume": 30_000_000_000,
                "price_change_percentage_24h": 1.5,
                "last_updated": "2026-06-15T00:00:00Z",
            }
        ],
        latency_ms=12.0,
    )
    trending = HttpResponse(
        ok=True,
        status=200,
        json={"coins": [{"item": {"id": "pepe", "symbol": "pepe", "market_cap_rank": 40}}]},
    )
    monkeypatch.setattr(coingecko, "fetch", _stub_sequence([markets, trending]))

    probe = coingecko.CoinGeckoApiProbe(_settings(sample_symbol="BTC"))
    result = await probe.probe()

    assert result.success is True
    endpoints = {r["_endpoint"] for r in result.records}
    assert endpoints == {"coins/markets", "search/trending"}
    btc = next(r for r in result.records if r["_endpoint"] == "coins/markets")
    assert btc["current_price"] == 65000


async def test_coingecko_api_fails_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exploration.sources import coingecko

    monkeypatch.setattr(
        coingecko, "fetch", _stub(HttpResponse(ok=False, status=429, error="http_429"))
    )
    result = await coingecko.CoinGeckoApiProbe(_settings()).probe()
    assert result.success is False
    assert "429" in (result.error or "")


async def test_coingecko_scrape_parses_html(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exploration.sources import coingecko

    html = (
        "<html><head><title>Bitcoin Price</title>"
        '<meta name="description" content="BTC live price">'
        '<meta property="og:title" content="Bitcoin">'
        "</head><body>$65,000.00</body></html>"
    )
    monkeypatch.setattr(coingecko, "fetch", _stub(HttpResponse(ok=True, status=200, text=html)))
    result = await coingecko.CoinGeckoScrapeProbe(_settings()).probe()
    assert result.success is True
    rec = result.records[0]
    assert rec["title"] == "Bitcoin Price"
    assert rec["meta_description"] == "BTC live price"
    assert rec["first_price_guess"] == "65,000.00"


# ── Key-gated sources: disabled without key ────────────────────────────────


@pytest.mark.parametrize(
    ("module_name", "class_name"),
    [
        ("coinglass", "CoinGlassApiProbe"),
        ("glassnode", "GlassnodeApiProbe"),
        ("coinmarketcap", "CoinMarketCapApiProbe"),
        ("nansen", "NansenApiProbe"),
        ("dune", "DuneApiProbe"),
    ],
)
async def test_key_required_probe_disabled_without_key(module_name: str, class_name: str) -> None:
    import importlib

    module = importlib.import_module(f"app.exploration.sources.{module_name}")
    probe = getattr(module, class_name)(_settings())
    result = await probe.probe()
    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("disabled")


async def test_dune_disabled_without_query_id() -> None:
    from app.exploration.sources import dune

    probe = dune.DuneApiProbe(_settings(dune_api_key="k"))
    result = await probe.probe()
    assert result.error == "disabled_no_query_id"


# ── CoinGlass parsing with key ─────────────────────────────────────────────


async def test_coinglass_api_parses_funding(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exploration.sources import coinglass

    payload = {
        "data": [
            {"exchangeName": "Binance", "fundingRate": 0.0001, "symbol": "BTC"},
            {"exchangeName": "OKX", "fundingRate": -0.0002, "symbol": "BTC"},
        ]
    }
    monkeypatch.setattr(
        coinglass, "fetch", _stub(HttpResponse(ok=True, status=200, json=payload))
    )
    probe = coinglass.CoinGlassApiProbe(_settings(coinglass_api_key="k", sample_symbol="BTC"))
    result = await probe.probe()
    assert result.success is True
    assert len(result.records) == 2
    assert result.records[0]["exchange"] == "Binance"


# ── Messari parsing ────────────────────────────────────────────────────────


async def test_messari_api_parses_market_data_and_news(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exploration.sources import messari

    md = HttpResponse(
        ok=True,
        status=200,
        json={"data": {"market_data": {"price_usd": 65000, "volume_last_24_hours": 1e9}}},
    )
    news = HttpResponse(
        ok=True,
        status=200,
        json={"data": [{"title": "BTC news", "published_at": "2026-06-15", "url": "http://x"}]},
    )
    monkeypatch.setattr(messari, "fetch", _stub_sequence([md, news]))
    result = await messari.MessariApiProbe(_settings(sample_symbol="BTC")).probe()
    assert result.success is True
    endpoints = {r["_endpoint"] for r in result.records}
    assert endpoints == {"metrics/market-data", "news"}


# ── Registry wiring: all flags on -> all probes constructible ──────────────


def test_registry_builds_all_probes_when_enabled() -> None:
    from app.exploration.sources import build_registry

    settings = _settings(
        enabled=True,
        coinglass_enabled=True,
        coinglass_scrape_enabled=True,
        messari_enabled=True,
        messari_scrape_enabled=True,
        dune_enabled=True,
        coingecko_enabled=True,
        coingecko_scrape_enabled=True,
        glassnode_enabled=True,
        glassnode_scrape_enabled=True,
        coinmarketcap_enabled=True,
        coinmarketcap_scrape_enabled=True,
        nansen_enabled=True,
    )
    registry = build_registry(settings)
    expected = {
        "dummy:api",
        "coinglass:api",
        "coinglass:scrape",
        "messari:api",
        "messari:scrape",
        "dune:api",
        "coingecko:api",
        "coingecko:scrape",
        "glassnode:api",
        "glassnode:scrape",
        "coinmarketcap:api",
        "coinmarketcap:scrape",
        "nansen:api",
    }
    assert set(registry) == expected
