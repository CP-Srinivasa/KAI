"""Preistabelle: UNKNOWN ist nicht 0, und ein Name wird nicht erraten."""

from __future__ import annotations

import pytest

from app.ai.pricing import (
    PRICE_SOURCE,
    PRICE_TABLE,
    PRICE_TABLE_VERSION,
    estimate_cost_usd,
    normalize_model,
    price_for,
    resolve_priced_model,
    unconfirmed_models,
)


def test_known_model_prices_input_and_output_separately() -> None:
    """1 Mio. Ein- und 1 Mio. Ausgabetoken ergeben genau Listenpreis + Listenpreis."""
    estimate = estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert estimate.status == "OK"
    assert estimate.usd == pytest.approx(3.00 + 15.00)
    assert estimate.source == f"{PRICE_SOURCE}:{PRICE_TABLE_VERSION}"


def test_cheap_model_is_cheaper_on_both_sides() -> None:
    teuer = estimate_cost_usd("gpt-4o", 1_000_000, 1_000_000).usd
    billig = estimate_cost_usd("gpt-4o-mini", 1_000_000, 1_000_000).usd
    assert teuer is not None and billig is not None
    assert billig < teuer


def test_unknown_model_is_none_never_zero() -> None:
    """Der Kern der Direktive: ein unbekanntes Modell hat KEINE Kosten von 0."""
    estimate = estimate_cost_usd("llama-99-turbo", 1000, 1000)
    assert estimate.usd is None
    assert estimate.usd != 0
    assert estimate.status == "COST_UNKNOWN"
    assert estimate.reason == "unknown_model"
    assert estimate.known is False


def test_missing_tokens_are_unknown_not_free() -> None:
    """Ein bezahlter Aufruf ohne Usage-Block ist unbekannt teuer, nicht gratis."""
    for eingabe, ausgabe in ((None, 10), (10, None), (None, None)):
        estimate = estimate_cost_usd("gpt-4o", eingabe, ausgabe)
        assert estimate.usd is None
        assert estimate.status == "COST_UNKNOWN"
        assert estimate.reason == "no_tokens"


def test_zero_tokens_is_a_real_measurement_not_unknown() -> None:
    """0 Token sind eine Messung: 0 USD mit Status OK, nicht COST_UNKNOWN."""
    estimate = estimate_cost_usd("gpt-4o", 0, 0)
    assert estimate.status == "OK"
    assert estimate.usd == 0.0


def test_negative_tokens_are_refused_not_clamped() -> None:
    estimate = estimate_cost_usd("gpt-4o", -5, 10)
    assert estimate.usd is None
    assert estimate.reason == "negative_tokens"


def test_empty_model_name_is_unknown() -> None:
    assert estimate_cost_usd(None, 10, 10).reason == "no_model"
    assert estimate_cost_usd("", 10, 10).reason == "no_model"


def test_vendor_prefix_is_stripped_but_date_suffix_is_not_guessed() -> None:
    """Anbieter-Präfix ja, Ratespiel nein."""
    assert normalize_model("Anthropic/Claude-Sonnet-4-6") == "claude-sonnet-4-6"
    assert price_for("anthropic/claude-sonnet-4-6") is not None
    # Ein datierter Bezeichner wird NICHT auf `gpt-4o` gebogen.
    assert price_for("gpt-4o-2024-08-06") is None
    assert estimate_cost_usd("gpt-4o-2024-08-06", 10, 10).reason == "unknown_model"


def test_actual_model_beats_alias_and_alias_alone_stays_unknown() -> None:
    """Die Alias-Auflösung nutzt die vorhandenen Felder, keine zweite Tabelle."""
    assert resolve_priced_model(requested_model_alias="kai-standard", actual_model="gpt-4o") == (
        "gpt-4o"
    )
    # Ein Alias ohne aufgeloestes Modell hat keinen Preis -- ehrlich unbekannt.
    alias_only = resolve_priced_model(requested_model_alias="kai-standard", actual_model=None)
    assert alias_only == "kai-standard"
    assert estimate_cost_usd(alias_only, 10, 10).status == "COST_UNKNOWN"


def test_price_table_covers_the_models_the_repo_actually_configures() -> None:
    """Was KAI heute konfiguriert, muss bepreisbar sein — sonst ist die Tabelle Deko."""
    for model in ("gpt-4o", "claude-sonnet-4-6", "gemini-2.5-flash"):
        assert model in PRICE_TABLE


def test_only_the_invoice_checked_model_counts_as_confirmed() -> None:
    """Die Liste der unbestätigten Preise ist der Auftrag an den Operator."""
    offen = unconfirmed_models()
    assert "claude-sonnet-4-6" not in offen
    assert "gpt-4o" in offen
    assert "claude-sonnet-5" in offen


def test_sonnet5_entry_names_the_open_contradiction() -> None:
    """Die Abweichung zwischen Auftragswert und Audit steht IM Eintrag."""
    eintrag = PRICE_TABLE["claude-sonnet-5"]
    assert eintrag.confirmed is False
    assert "2.00/10.00" in eintrag.note
