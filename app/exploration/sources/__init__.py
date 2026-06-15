"""Probe registry.

``build_registry(settings)`` returns the probes that are *eligible to run* given
the current sandbox settings: a probe is included only when the global
``enabled`` flag is on AND its per-source flag is on. Key-requiring probes that
have no key are still returned but will report ``disabled`` when run, so the
report surfaces "configured but unusable" honestly.

For EXPLORE-S0 only the DummyProbe is registered. Real source probes are added in
S1/S2 and slotted in here behind their settings flags.
"""

from __future__ import annotations

from app.exploration.base import ExplorationProbe
from app.exploration.settings import ExplorationSettings
from app.exploration.sources.dummy import DummyProbe


def build_registry(settings: ExplorationSettings) -> dict[str, ExplorationProbe]:
    """Map probe_id -> probe instance for all eligible probes."""
    probes: list[ExplorationProbe] = []

    # Dummy is independent of the global gate so the durchstich/tests always work.
    if settings.dummy_enabled:
        probes.append(DummyProbe())

    if settings.enabled:
        probes.extend(_build_source_probes(settings))

    return {p.probe_id: p for p in probes}


def _build_source_probes(settings: ExplorationSettings) -> list[ExplorationProbe]:
    """Construct real source probes gated by their per-source flags.

    Imports are local so that S0 has no hard dependency on S1/S2 probe modules
    before they exist, and so importing the registry stays cheap.
    """
    probes: list[ExplorationProbe] = []

    if settings.coinglass_enabled:
        from app.exploration.sources.coinglass import CoinGlassApiProbe

        probes.append(CoinGlassApiProbe(settings))
        if settings.coinglass_scrape_enabled:
            from app.exploration.sources.coinglass import CoinGlassScrapeProbe

            probes.append(CoinGlassScrapeProbe(settings))

    if settings.messari_enabled:
        from app.exploration.sources.messari import MessariApiProbe

        probes.append(MessariApiProbe(settings))
        if settings.messari_scrape_enabled:
            from app.exploration.sources.messari import MessariScrapeProbe

            probes.append(MessariScrapeProbe(settings))

    if settings.dune_enabled:
        from app.exploration.sources.dune import DuneApiProbe

        probes.append(DuneApiProbe(settings))

    if settings.coingecko_enabled:
        from app.exploration.sources.coingecko import CoinGeckoApiProbe

        probes.append(CoinGeckoApiProbe(settings))
        if settings.coingecko_scrape_enabled:
            from app.exploration.sources.coingecko import CoinGeckoScrapeProbe

            probes.append(CoinGeckoScrapeProbe(settings))

    if settings.glassnode_enabled:
        from app.exploration.sources.glassnode import GlassnodeApiProbe

        probes.append(GlassnodeApiProbe(settings))
        if settings.glassnode_scrape_enabled:
            from app.exploration.sources.glassnode import GlassnodeScrapeProbe

            probes.append(GlassnodeScrapeProbe(settings))

    if settings.coinmarketcap_enabled:
        from app.exploration.sources.coinmarketcap import CoinMarketCapApiProbe

        probes.append(CoinMarketCapApiProbe(settings))
        if settings.coinmarketcap_scrape_enabled:
            from app.exploration.sources.coinmarketcap import CoinMarketCapScrapeProbe

            probes.append(CoinMarketCapScrapeProbe(settings))

    if settings.nansen_enabled:
        from app.exploration.sources.nansen import NansenApiProbe

        probes.append(NansenApiProbe(settings))

    return probes
