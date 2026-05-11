"""Tests for app.messaging.kai_telegram_renderer."""

from __future__ import annotations

import pytest

from app.messaging.kai_persona import reset_persona_cache
from app.messaging.kai_telegram_renderer import (
    KaiSecurityCard,
    KaiSignalCard,
    KaiWarningCard,
    render_kai_main_menu,
    render_kai_security_card,
    render_kai_signal_card,
    render_kai_warning_card,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_persona_cache()
    yield
    reset_persona_cache()


def _signal_card(**overrides) -> KaiSignalCard:
    base = {
        "asset": "BTC/USDT",
        "mode": "WATCHLIST",
        "direction": "LONG",
        "confidence": 72,
        "risk": "MEDIUM",
        "entry": "78000",
        "stop_loss": "76500",
        "data_basis": ("news", "volume", "structure"),
        "data_quality": "MEDIUM",
        "timestamp": "2026-05-03T14:22:00Z",
        "comment": "Signal lebt. Einstieg noch nicht sauber.",
    }
    base.update(overrides)
    return KaiSignalCard(**base)


def test_signal_card_de_contains_required_blocks():
    rendered = render_kai_signal_card(_signal_card(), language="de")
    assert "*KAI SIGNAL // WATCHLIST*" in rendered
    assert "BTC/USDT" in rendered
    assert "LONG" in rendered
    assert "72%" in rendered
    assert "Risiko MEDIUM" in rendered
    assert "Signal lebt" in rendered
    assert "Entry: 78000" in rendered


def test_signal_card_en_uses_english_labels():
    rendered = render_kai_signal_card(_signal_card(), language="en")
    assert "Risk MEDIUM" in rendered
    assert "Quality MEDIUM" in rendered
    assert "Risiko" not in rendered


def test_signal_card_preserves_umlauts():
    rendered = render_kai_signal_card(
        _signal_card(comment="Das hier ist FOMO mit Lippenstift."),
    )
    assert "FOMO" in rendered


def test_signal_card_blocks_forbidden_claim():
    bad = _signal_card(comment="Das ist ein garantierter Gewinn auf BTC")
    with pytest.raises(ValueError):
        render_kai_signal_card(bad)


def test_signal_card_escapes_markdown_specials():
    asset_with_underscore = _signal_card(asset="BTC_USDT/USDT")
    rendered = render_kai_signal_card(asset_with_underscore)
    assert r"\_" in rendered or "BTC_USDT" not in rendered or r"\_USDT" in rendered


def test_warning_card_contains_target_and_action():
    card = KaiWarningCard(
        target="ETH/USDT",
        problem="Volumen schwach",
        risk="HIGH",
        action="Kein Entry ohne Bestaetigung",
        timestamp="2026-05-03T14:25:00Z",
        comment="Zu viel Laerm. Zu wenig Fundament.",
    )
    rendered = render_kai_warning_card(card)
    assert "*KAI WARNING // RISK*" in rendered
    assert "ETH/USDT" in rendered
    assert "HIGH" in rendered
    assert "Kein Entry ohne Bestaetigung" in rendered
    assert "Laerm" in rendered


def test_security_card_contains_area_and_priority():
    card = KaiSecurityCard(
        area="API Layer",
        status="OK",
        priority="LOW",
        last_check="2026-05-03T14:00:00Z",
        result="clean",
        next_step="continue monitoring",
        comment="System sauber. Keine roten Kabel sichtbar.",
    )
    rendered = render_kai_security_card(card, language="de")
    assert "Bereich: API Layer" in rendered
    assert "Prioritaet: LOW" in rendered
    assert "rote" in rendered


def test_main_menu_de_has_12_buttons():
    menu = render_kai_main_menu("de")
    assert menu["title"] == "KAI // CONTROL PANEL"
    flat = [b for row in menu["rows"] for b in row]
    assert len(flat) == 12
    assert "Markt scannen" in flat
    assert "Einstellungen" in flat


def test_main_menu_en_has_12_buttons():
    menu = render_kai_main_menu("en")
    flat = [b for row in menu["rows"] for b in row]
    assert len(flat) == 12
    assert "Scan Market" in flat
    assert "Settings" in flat
