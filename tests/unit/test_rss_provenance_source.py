"""RSS-alert provenance source resolution (Phase 0b of the source-lifecycle plan).

The 'rss-1' alert path tagged provenance.source as the literal "unknown" when a
feed carried no source_name, dumping those RSS docs into the attribution-filtered
unknown bucket. They are honestly RSS — map them to a real generic 'rss' source.
"""

from __future__ import annotations

from app.alerts.service import _resolve_rss_source


def test_resolve_rss_source_keeps_real_feed_name() -> None:
    assert _resolve_rss_source("cointelegraph") == "cointelegraph"
    assert _resolve_rss_source("  btc-echo  ") == "btc-echo"


def test_resolve_rss_source_maps_unknown_and_empty_to_rss() -> None:
    assert _resolve_rss_source("unknown") == "rss"
    assert _resolve_rss_source("UNKNOWN") == "rss"
    assert _resolve_rss_source("") == "rss"
    assert _resolve_rss_source("   ") == "rss"
    assert _resolve_rss_source(None) == "rss"
