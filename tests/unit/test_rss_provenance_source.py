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


def test_rss_signal_path_id_is_stable_path_label() -> None:
    """TL-008-Fix: RSS-Rows tragen künftig eine PIPELINE-Identität (D-125),
    kein None und kein Event-Unikat. Wert ist Vertrag — Änderung = bewusste
    neue Pfad-Version, nie stiller Drift."""
    from app.alerts.service import RSS_SIGNAL_PATH_ID

    assert RSS_SIGNAL_PATH_ID == "rsspath_news_v1"
