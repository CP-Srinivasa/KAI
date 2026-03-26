"""Unit tests for Telegram inline menu definitions."""

from __future__ import annotations

import json

from app.messaging.telegram_menu import (
    MENUS,
    build_inline_keyboard,
    clear_menu_cache,
    get_menu,
)


def test_main_menu_exists_and_has_keyboard_rows() -> None:
    clear_menu_cache()
    menu = get_menu("main")
    assert menu is not None
    assert isinstance(menu["keyboard"], list)
    assert len(menu["keyboard"]) >= 1


def test_all_registered_menus_build_inline_keyboard() -> None:
    clear_menu_cache()
    for menu_id in MENUS:
        keyboard = build_inline_keyboard(menu_id)
        assert keyboard is not None
        assert "inline_keyboard" in keyboard
        assert isinstance(keyboard["inline_keyboard"], list)


def test_unknown_menu_returns_none() -> None:
    clear_menu_cache()
    assert get_menu("unknown-menu") is None
    assert build_inline_keyboard("unknown-menu") is None


def test_control_menu_contains_reload_button() -> None:
    clear_menu_cache()
    control = get_menu("control")
    assert control is not None
    callbacks = [
        button.get("callback_data", "")
        for row in control["keyboard"]
        for button in row
    ]
    assert "cmd:menu_reload" in callbacks


def test_menu_can_be_overridden_via_json_config(tmp_path, monkeypatch) -> None:
    menu_config = tmp_path / "telegram_menu.json"
    menu_config.write_text(
        json.dumps(
            {
                "menus": {
                    "main": {
                        "text": "*Custom Main*",
                        "keyboard": [[{"text": "Custom", "callback_data": "cmd:status"}]],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TELEGRAM_MENU_CONFIG_PATH", str(menu_config))
    clear_menu_cache()

    menu = get_menu("main")

    assert menu is not None
    assert menu["text"] == "*Custom Main*"
    assert menu["keyboard"][0][0]["text"] == "Custom"


def test_invalid_json_falls_back_to_defaults(tmp_path, monkeypatch) -> None:
    menu_config = tmp_path / "telegram_menu_invalid.json"
    menu_config.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_MENU_CONFIG_PATH", str(menu_config))
    clear_menu_cache()

    menu = get_menu("main")

    assert menu is not None
    assert "KAI Trading Intelligence" in menu["text"]


def test_partial_json_override_keeps_default_submenus(tmp_path, monkeypatch) -> None:
    menu_config = tmp_path / "telegram_menu_partial.json"
    menu_config.write_text(
        json.dumps(
            {
                "menus": {
                    "main": {
                        "text": "*Main Only Override*",
                        "keyboard": [[{"text": "A", "callback_data": "cmd:status"}]],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TELEGRAM_MENU_CONFIG_PATH", str(menu_config))
    clear_menu_cache()

    main = get_menu("main")
    trading = get_menu("trading")

    assert main is not None
    assert main["text"] == "*Main Only Override*"
    assert trading is not None
    assert "Trading" in trading["text"]


def test_main_menu_has_signal_send_entry() -> None:
    """Ensure the main menu has a Signal senden button."""
    clear_menu_cache()
    main = get_menu("main")
    assert main is not None
    callbacks = [
        button.get("callback_data", "")
        for row in main["keyboard"]
        for button in row
    ]
    assert "menu:signal_send" in callbacks


def test_signal_send_menu_shows_structured_format() -> None:
    """Signal send menu includes [SIGNAL] format help."""
    clear_menu_cache()
    menu = get_menu("signal_send")
    assert menu is not None
    assert "[SIGNAL]" in menu["text"]
    assert "BUY" in menu["text"]
    assert "[NEWS]" in menu["text"]


def test_trading_menu_exists() -> None:
    """Trading submenu is available."""
    clear_menu_cache()
    menu = get_menu("trading")
    assert menu is not None
    callbacks = [
        button.get("callback_data", "")
        for row in menu["keyboard"]
        for button in row
    ]
    assert "cmd:positions" in callbacks
    assert "cmd:exposure" in callbacks
