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
    # _env_file=None keeps unit tests hermetic — never read the developer's .env
    # (which carries real keys/ids that would mask the no-key/invalid paths).
    return ExplorationSettings(_env_file=None, **base)  # type: ignore[call-arg]


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


def test_scrape_user_agent_toggle() -> None:
    honest = _settings(scrape_browser_ua_enabled=False)
    assert "KAI-Exploration" in honest.scrape_user_agent
    browser = _settings(scrape_browser_ua_enabled=True)
    assert "Mozilla" in browser.scrape_user_agent


def test_scrape_util_extracts_price_and_volume_from_meta() -> None:
    from app.exploration.scrape_util import parse_html_signals

    html = (
        "<html><head><title>Bitcoin</title>"
        '<meta name="description" content="The live Bitcoin price today is '
        "$66,814.13 USD with a 24-hour trading volume of $36,855,080,681.02 USD.\">"
        "</head><body></body></html>"
    )
    rec = parse_html_signals(html)
    assert rec["meta_price_usd"] == 66814.13
    assert rec["meta_volume_usd"] == 36855080681.02


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


def test_dune_query_id_resolver() -> None:
    from app.exploration.sources.dune import DEFAULT_SAMPLE_QUERY_ID, _resolve_query_id

    # numeric id used as-is
    assert _resolve_query_id("3493826") == ("3493826", False)
    # non-numeric (username pasted by mistake) -> public sample fallback
    assert _resolve_query_id("Cryptopheonix80") == (DEFAULT_SAMPLE_QUERY_ID, True)
    # missing -> public sample fallback (Dune still demonstrable)
    assert _resolve_query_id(None) == (DEFAULT_SAMPLE_QUERY_ID, True)


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


async def test_messari_api_parses_assets_keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exploration.sources import messari

    assets = HttpResponse(
        ok=True,
        status=200,
        json={
            "data": [
                {"symbol": "ETH", "name": "Ethereum", "rank": 2, "tags": ["Smart Contract"]},
                {"symbol": "BTC", "name": "Bitcoin", "rank": 1, "tags": ["Proof-of-Work"]},
            ]
        },
    )
    monkeypatch.setattr(messari, "fetch", _stub(assets))
    result = await messari.MessariApiProbe(_settings(sample_symbol="BTC")).probe()
    assert result.success is True
    # sample symbol is surfaced first
    assert result.records[0]["symbol"] == "BTC"
    assert result.records[0]["_endpoint"] == "metrics/v2/assets"
    assert result.records[0]["tags"] == "Proof-of-Work"


async def test_coinglass_api_surfaces_plan_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exploration.sources import coinglass

    gated = HttpResponse(ok=True, status=200, json={"code": "401", "msg": "Upgrade plan"})
    monkeypatch.setattr(coinglass, "fetch", _stub(gated))
    result = await coinglass.CoinGlassApiProbe(_settings(coinglass_api_key="k")).probe()
    assert result.success is False
    assert result.error == "api_error:401:Upgrade plan"


async def test_dune_parses_rows_with_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exploration.sources import dune

    payload = {
        "result": {"rows": [{"contract_address": "0xabc", "hour": "2026-06-15", "price": 1.0}]}
    }
    monkeypatch.setattr(dune, "fetch", _stub(HttpResponse(ok=True, status=200, json=payload)))
    # non-numeric configured value -> probe falls back to the public sample and parses
    probe = dune.DuneApiProbe(_settings(dune_api_key="k", dune_query_id="Cryptopheonix80"))
    result = await probe.probe()
    assert result.success is True
    assert result.records[0]["price"] == 1.0
    assert result.meta.extra.get("used_public_sample") is True


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
        nansen_scrape_enabled=True,
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
        "nansen:scrape",
    }
    assert set(registry) == expected
