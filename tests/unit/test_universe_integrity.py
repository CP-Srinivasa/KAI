r"""Was versiegelt wird, muss vorher aufgeloest sein.

Ausloeser (2026-08-20, Haeufigkeitsmessung vor T0): ``MATIC/USDT`` lieferte
**0 Feuerungen** bei 4.301 auswertbaren Kerzen. Das sind zwei voellig
verschiedene Aussagen —

    "die Regel feuerte auf diesem Asset nie"          (statistischer Befund)
    "der Ticker liefert nur noch Legacy-Daten"        (Datendefekt)

— und ohne Aufloesung waere die falsche davon still in ein versiegeltes
Universum gewandert. Polygon hat MATIC auf POL migriert.

Der zweite Grund ist unabhaengig davon: ``technical_screener_feed.DEFAULT_UNIVERSE``
ist eine **dynamische Referenz**. Ihr Inhalt kann sich waehrend eines
90-Tage-OOS-Fensters aendern, und dann waere hinterher nicht mehr feststellbar,
worauf sich das Verdikt bezog. Versiegelt wird deshalb eine ausgeschriebene
Liste plus Hash.
"""

from __future__ import annotations

from app.research.universe_integrity import (
    STATUS_TRADING,
    UNIVERSE_SPEC_VERSION,
    DataFacts,
    ProviderSymbol,
    evaluate_universe,
    report_to_dict,
    to_provider_pair,
    universe_sha256,
)


def _listed(*pairs: str, status: str = STATUS_TRADING) -> dict[str, ProviderSymbol]:
    out: dict[str, ProviderSymbol] = {}
    for pair in pairs:
        base = pair[: -len("USDT")]
        out[pair] = ProviderSymbol(pair=pair, status=status, base_asset=base, quote_asset="USDT")
    return out


# ── Namensabbildung ─────────────────────────────────────────────────────────


def test_provider_pair_is_separator_free_and_upper() -> None:
    assert to_provider_pair("BTC/USDT") == "BTCUSDT"
    assert to_provider_pair("btc-usdt") == "BTCUSDT"
    assert to_provider_pair("BTCUSDT") == "BTCUSDT"


# ── Der MATIC/POL-Fall ──────────────────────────────────────────────────────


def test_renamed_ticker_is_canonicalised_not_dropped() -> None:
    """MATIC nicht handelbar, POL handelbar -> kanonisieren, Groesse bleibt.

    Ausdruecklich NICHT "MATIC entfernen" und auch nicht "POL zusaetzlich
    aufnehmen" — beides aenderte die Familiengroesse still.
    """
    provider = {
        **_listed("BTCUSDT", "POLUSDT"),
        **_listed("MATICUSDT", status="BREAK"),
    }

    report = evaluate_universe(["BTC/USDT", "MATIC/USDT"], provider)

    assert report.canonical_universe == ("BTC/USDT", "POL/USDT")
    assert len(report.canonical_universe) == 2, "keine stille 3er- oder 1er-Familie"
    matic = report.symbols[1]
    assert matic.canonical_symbol == "POL/USDT"
    assert matic.provider_pair == "POLUSDT"
    assert "renamed" in matic.issues
    assert matic.ok, "eine aufgeloeste Umbenennung blockiert nicht"


def test_rename_target_that_is_not_trading_blocks() -> None:
    """Eine veraltete Alias-Tabelle darf nicht als Erfolg durchgehen."""
    provider = _listed("MATICUSDT", status="BREAK")

    report = evaluate_universe(["MATIC/USDT"], provider)

    assert not report.ok
    assert any("rename_target_not_trading" in b for b in report.blocking)


def test_alias_collision_is_blocking() -> None:
    """Waeren MATIC UND POL deklariert, waere POL doppelt gewichtet."""
    provider = {**_listed("POLUSDT"), **_listed("MATICUSDT", status="BREAK")}

    report = evaluate_universe(["MATIC/USDT", "POL/USDT"], provider)

    assert not report.ok
    blocking = " ".join(report.blocking)
    assert "alias_collision" in blocking or "duplicate_after_canonicalisation" in blocking


# ── Listing und Status ──────────────────────────────────────────────────────


def test_unlisted_symbol_blocks() -> None:
    report = evaluate_universe(["GHOST/USDT"], _listed("BTCUSDT"))

    assert not report.ok
    assert any("not_listed" in b for b in report.blocking)


def test_halted_symbol_blocks() -> None:
    report = evaluate_universe(["BTC/USDT"], _listed("BTCUSDT", status="HALT"))

    assert not report.ok
    assert any("not_trading" in b for b in report.blocking)


def test_duplicate_in_the_declared_list_blocks() -> None:
    report = evaluate_universe(["BTC/USDT", "BTC/USDT"], _listed("BTCUSDT"))

    assert not report.ok
    assert any("duplicate_after_canonicalisation" in b for b in report.blocking)


# ── Datenverfuegbarkeit ─────────────────────────────────────────────────────


def test_short_history_blocks() -> None:
    report = evaluate_universe(
        ["BTC/USDT"],
        _listed("BTCUSDT"),
        {"BTC/USDT": DataFacts(bars=100, positive_volume_bars=100)},
        min_bars=4000,
    )

    assert not report.ok
    assert any("insufficient_history" in b for b in report.blocking)


def test_all_zero_volume_blocks() -> None:
    """Ein Ticker mit Kerzen, aber ohne Volumen, kann ``volume_z_20`` nie erzeugen."""
    report = evaluate_universe(
        ["BTC/USDT"],
        _listed("BTCUSDT"),
        {"BTC/USDT": DataFacts(bars=4321, positive_volume_bars=0)},
        min_bars=4000,
    )

    assert not report.ok
    assert any("volume_unusable" in b for b in report.blocking)


def test_facts_are_looked_up_under_the_canonical_name_not_the_declared_one() -> None:
    """Sonst wuerden die Legacy-Daten des alten Tickers geprueft.

    Genau diese Verwechslung ist der Kern des MATIC-Falls: haette der Backfill
    unter ``MATIC/USDT`` stattgefunden, saehe alles gesund aus — 4.301 Kerzen
    mit Volumen — und das versiegelte Universum enthielte ein totes Asset.
    """
    provider = {**_listed("POLUSDT"), **_listed("MATICUSDT", status="BREAK")}

    report = evaluate_universe(
        ["MATIC/USDT"],
        provider,
        {"POL/USDT": DataFacts(bars=4321, positive_volume_bars=4321)},
        min_bars=4000,
    )

    assert report.ok
    assert report.symbols[0].facts.bars == 4321


def test_healthy_universe_passes() -> None:
    """Gegenprobe — ohne sie waere der Preflight nur ein Verhinderer."""
    report = evaluate_universe(
        ["BTC/USDT", "ETH/USDT"],
        _listed("BTCUSDT", "ETHUSDT"),
        {
            "BTC/USDT": DataFacts(bars=4321, positive_volume_bars=4321),
            "ETH/USDT": DataFacts(bars=4321, positive_volume_bars=4320),
        },
        min_bars=4000,
    )

    assert report.ok
    assert report.blocking == ()
    assert all(s.issues == () for s in report.symbols)


# ── Der Hash ────────────────────────────────────────────────────────────────


def test_hash_is_order_independent() -> None:
    """Eine umsortierte Quellliste ist dasselbe Universum."""
    assert universe_sha256(["B/USDT", "A/USDT"]) == universe_sha256(["A/USDT", "B/USDT"])


def test_hash_changes_when_a_symbol_changes() -> None:
    assert universe_sha256(["A/USDT", "B/USDT"]) != universe_sha256(["A/USDT", "C/USDT"])
    assert universe_sha256(["A/USDT"]) != universe_sha256(["A/USDT", "B/USDT"])


def test_hash_carries_the_spec_version() -> None:
    """Aendert sich die Serialisierung, darf nicht still derselbe Hash entstehen."""
    import hashlib

    expected = hashlib.sha256(f"{UNIVERSE_SPEC_VERSION}\nA/USDT\n".encode()).hexdigest()

    assert universe_sha256(["A/USDT"]) == expected


def test_hash_is_over_the_canonical_names_not_the_declared_ones() -> None:
    """Der Seal muss das beschreiben, was gemessen wird."""
    provider = {**_listed("POLUSDT"), **_listed("MATICUSDT", status="BREAK")}

    report = evaluate_universe(["MATIC/USDT"], provider)

    assert report.universe_sha256 == universe_sha256(["POL/USDT"])
    assert report.universe_sha256 != universe_sha256(["MATIC/USDT"])


# ── Artefakt ────────────────────────────────────────────────────────────────


def test_report_serialises_every_field_needed_for_the_seal() -> None:
    provider = _listed("BTCUSDT")

    payload = report_to_dict(
        evaluate_universe(
            ["BTC/USDT"], provider, {"BTC/USDT": DataFacts(bars=4321, positive_volume_bars=4321)}
        )
    )

    assert payload["universe_sha256"]
    assert payload["canonical_universe"] == ["BTC/USDT"]
    assert payload["n_symbols"] == 1
    assert payload["ok"] is True
    assert payload["symbols"][0]["provider_pair"] == "BTCUSDT"
    assert payload["symbols"][0]["status"] == STATUS_TRADING


def test_hash_is_computed_even_when_blocked() -> None:
    """Damit sichtbar ist, WORUEBER gestritten wird — nicht nur DASS es hakt."""
    report = evaluate_universe(["GHOST/USDT"], _listed("BTCUSDT"))

    assert not report.ok
    assert len(report.universe_sha256) == 64


# ── Das committete Artefakt ─────────────────────────────────────────────────


def test_sealed_universe_artifact_matches_its_own_hash() -> None:
    """Wer die Liste editiert, muss den Hash mit aendern — und wird dabei gesehen.

    Das Artefakt liegt unter ``docs/`` und nicht unter ``artifacts/``, weil
    letzteres gitignored ist: ein versiegeltes Universum, das nicht im Diff
    auftaucht, ist nicht versiegelt, sondern nur abgelegt.
    """
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2] / "docs" / "research" / "universe_rsi_reentry_v1.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["universe_sha256"] == universe_sha256(payload["canonical_universe"])
    assert payload["n_symbols"] == len(payload["canonical_universe"])
    assert payload["ok"] is True, "ein blockierter Preflight darf nicht als Kandidat liegen"


def test_sealed_universe_has_no_duplicates_and_no_dead_tickers() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2] / "docs" / "research" / "universe_rsi_reentry_v1.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    canonical = payload["canonical_universe"]
    assert len(set(canonical)) == len(canonical)
    for entry in payload["symbols"]:
        assert entry["status"] == STATUS_TRADING, entry["research_symbol"]
        assert entry["positive_volume_bars"] > 0, entry["research_symbol"]


def test_the_three_live_resolved_renames_are_recorded() -> None:
    """MATIC/RNDR/MKR standen am 2026-08-20 live auf BREAK.

    Ohne Aufloesung waeren drei tote Ticker im Universum gelandet — zwei davon
    haette die Haeufigkeitsmessung gar nicht als Problem gezeigt, weil sie
    schlicht keine Kerzen liefern.
    """
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2] / "docs" / "research" / "universe_rsi_reentry_v1.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    renamed = {
        entry["research_symbol"]: entry["canonical_symbol"]
        for entry in payload["symbols"]
        if "renamed" in entry["issues"]
    }

    assert renamed == {
        "MATIC/USDT": "POL/USDT",
        "RNDR/USDT": "RENDER/USDT",
        "MKR/USDT": "SKY/USDT",
    }
