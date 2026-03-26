"""KAI Telegram inline-keyboard menu system.

Modular and configurable:
- defaults live in this file
- optional JSON overrides live in `config/telegram_menu.json`
- partial overrides merge by menu id
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MENU_CONFIG_PATH_ENV = "TELEGRAM_MENU_CONFIG_PATH"
_MENU_CONFIG_PATH_DEFAULT = Path("config/telegram_menu.json")

_cache_path: Path | None = None
_cache_mtime_ns: int | None = None
_cache_menus: dict[str, dict[str, Any]] | None = None


def _btn(text: str, callback_data: str) -> dict[str, str]:
    return {"text": text, "callback_data": callback_data}


def _url_btn(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url}


def _row(*buttons: dict[str, str]) -> list[dict[str, str]]:
    return list(buttons)


DEFAULT_MENU_MAIN: dict[str, Any] = {
    "text": (
        "*KAI Trading Intelligence*\n"
        "----------------------------\n"
        "Waehle eine Kategorie."
    ),
    "keyboard": [
        _row(_btn("System-Status", "cmd:status")),
        _row(
            _btn("Trading", "menu:trading"),
            _btn("Signale", "menu:signals"),
        ),
        _row(
            _btn("Alerts", "cmd:alertstatus"),
            _btn("Tagesbericht", "cmd:tagesbericht"),
        ),
        _row(_btn("Signal senden", "menu:signal_send")),
        _row(
            _btn("Steuerung", "menu:control"),
            _btn("Hilfe", "cmd:hilfe"),
        ),
    ],
}

DEFAULT_MENU_TRADING: dict[str, Any] = {
    "text": (
        "*Trading*\n"
        "----------------------------\n"
        "Positionen, Exposure und Portfolio."
    ),
    "keyboard": [
        _row(
            _btn("Positionen", "cmd:positions"),
            _btn("Exposure", "cmd:exposure"),
        ),
        _row(_btn("Hauptmenue", "menu:main")),
    ],
}

DEFAULT_MENU_SIGNALS: dict[str, Any] = {
    "text": (
        "*Signale*\n"
        "----------------------------\n"
        "Aktive Signale und Pipeline-Status."
    ),
    "keyboard": [
        _row(
            _btn("Aktive Signale", "cmd:signals"),
            _btn("Signal-Pipeline", "cmd:signalstatus"),
        ),
        _row(_btn("Hauptmenue", "menu:main")),
    ],
}

DEFAULT_MENU_SIGNAL_SEND: dict[str, Any] = {
    "text": (
        "*Signal senden (3-Typen Standard)*\n"
        "----------------------------\n"
        "Telegram ist Anzeige, JSON ist Wahrheit.\n"
        "Bei SIGNAL gilt fail-closed: ohne Pflichtfelder keine Weiterleitung.\n\n"
        "*SIGNAL (Trade):*\n"
        "`[SIGNAL]`\n"
        "`Signal ID: SIG-20260325-BTCUSDT-001`\n"
        "`Source: Premium Signals`\n"
        "`Exchange Scope: binance_futures, bybit`\n"
        "`Market Type: Futures`\n"
        "`Symbol: BTC/USDT`\n"
        "`Side: BUY`\n"
        "`Direction: LONG`\n"
        "`Entry Rule: BELOW 65000`\n"
        "`Targets: 70000`\n"
        "`Stop Loss: 62000`\n"
        "`Leverage: 10x`\n"
        "`Status: NEW`\n"
        "`Timestamp: 2026-03-25T18:31:00Z`\n\n"
        "*NEWS (Info only):*\n"
        "`[NEWS]`\n"
        "`Source: Quelle`\n"
        "`Title: Titel`\n"
        "`Priority: High`\n\n"
        "*EXCHANGE_RESPONSE (Status):*\n"
        "`[EXCHANGE_RESPONSE]`\n"
        "`Related Signal ID: SIG-...`\n"
        "`Exchange: bybit`\n"
        "`Action: ORDER_CREATED`\n"
        "`Status: SUCCESS`"
    ),
    "keyboard": [
        _row(_btn("Hauptmenue", "menu:main")),
    ],
}

DEFAULT_MENU_CONTROL: dict[str, Any] = {
    "text": (
        "*Steuerung*\n"
        "----------------------------\n"
        "System-Kontrolle und Notfall-Aktionen."
    ),
    "keyboard": [
        _row(
            _btn("Pause", "cmd:pause"),
            _btn("Resume", "cmd:resume"),
        ),
        _row(_btn("Notfall-Stopp", "cmd:kill")),
        _row(_btn("Menue neu laden", "cmd:menu_reload")),
        _row(_btn("Hauptmenue", "menu:main")),
    ],
}

DEFAULT_MENUS: dict[str, dict[str, Any]] = {
    "main": DEFAULT_MENU_MAIN,
    "trading": DEFAULT_MENU_TRADING,
    "signals": DEFAULT_MENU_SIGNALS,
    "signal_send": DEFAULT_MENU_SIGNAL_SEND,
    "control": DEFAULT_MENU_CONTROL,
}

# Backward-compatible alias
MENUS = DEFAULT_MENUS


def _resolve_menu_config_path() -> Path:
    configured = os.getenv(_MENU_CONFIG_PATH_ENV, "").strip()
    if configured:
        return Path(configured)
    return _MENU_CONFIG_PATH_DEFAULT


def _normalize_button(raw_button: Any) -> dict[str, str] | None:
    if not isinstance(raw_button, dict):
        return None
    text = raw_button.get("text")
    if not isinstance(text, str) or not text.strip():
        return None

    callback_data = raw_button.get("callback_data")
    if isinstance(callback_data, str) and callback_data.strip():
        return {"text": text.strip(), "callback_data": callback_data.strip()}

    url = raw_button.get("url")
    if isinstance(url, str) and url.strip():
        return {"text": text.strip(), "url": url.strip()}

    return None


def _normalize_keyboard(raw_keyboard: Any) -> list[list[dict[str, str]]] | None:
    if not isinstance(raw_keyboard, list):
        return None

    normalized_rows: list[list[dict[str, str]]] = []
    for raw_row in raw_keyboard:
        if not isinstance(raw_row, list):
            return None

        normalized_row: list[dict[str, str]] = []
        for raw_button in raw_row:
            button = _normalize_button(raw_button)
            if button is None:
                return None
            normalized_row.append(button)

        if normalized_row:
            normalized_rows.append(normalized_row)

    if not normalized_rows:
        return None
    return normalized_rows


def _normalize_menu(raw_menu: Any) -> dict[str, Any] | None:
    if not isinstance(raw_menu, dict):
        return None

    text = raw_menu.get("text")
    keyboard = raw_menu.get("keyboard")
    if not isinstance(text, str) or not text.strip():
        return None

    normalized_keyboard = _normalize_keyboard(keyboard)
    if normalized_keyboard is None:
        return None

    return {"text": text.strip(), "keyboard": normalized_keyboard}


def _extract_menu_payload(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    menus = raw.get("menus")
    if isinstance(menus, dict):
        return {str(key): value for key, value in menus.items()}
    return {str(key): value for key, value in raw.items()}


def _load_menus_from_json(path: Path) -> dict[str, dict[str, Any]] | None:
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[MENU] Failed to load menu config from %s: %s", path, exc)
        return None

    payload = _extract_menu_payload(raw)
    if payload is None:
        logger.warning("[MENU] Invalid menu config payload type in %s", path)
        return None

    normalized: dict[str, dict[str, Any]] = {}
    for menu_id, raw_menu in payload.items():
        if not isinstance(menu_id, str) or not menu_id.strip():
            continue
        menu = _normalize_menu(raw_menu)
        if menu is None:
            logger.warning("[MENU] Ignoring invalid menu definition: %s", menu_id)
            continue
        normalized[menu_id.strip()] = menu

    if not normalized:
        logger.warning("[MENU] No valid menu definitions found in %s", path)
        return None

    return normalized


def _read_effective_menus() -> dict[str, dict[str, Any]]:
    global _cache_path, _cache_mtime_ns, _cache_menus

    path = _resolve_menu_config_path()
    mtime_ns = path.stat().st_mtime_ns if path.exists() else -1

    if _cache_menus is not None and _cache_path == path and _cache_mtime_ns == mtime_ns:
        return copy.deepcopy(_cache_menus)

    menus = copy.deepcopy(DEFAULT_MENUS)
    external_menus = _load_menus_from_json(path)
    if external_menus:
        # Merge-by-id so partial JSON can override selected menus.
        menus.update(external_menus)

    _cache_path = path
    _cache_mtime_ns = mtime_ns
    _cache_menus = menus
    return copy.deepcopy(menus)


def clear_menu_cache() -> None:
    """Clear in-memory menu cache (useful in tests)."""
    global _cache_path, _cache_mtime_ns, _cache_menus
    _cache_path = None
    _cache_mtime_ns = None
    _cache_menus = None


def get_menu(menu_id: str) -> dict[str, Any] | None:
    """Return one menu definition by id."""
    menus = _read_effective_menus()
    menu = menus.get(menu_id)
    if menu is None:
        return None
    return copy.deepcopy(menu)


def build_inline_keyboard(menu_id: str) -> dict[str, Any] | None:
    """Build Telegram InlineKeyboardMarkup payload for one menu."""
    menu = get_menu(menu_id)
    if menu is None:
        return None
    return {"inline_keyboard": menu["keyboard"]}


def validate_menu_config() -> dict[str, object]:
    """Validate current effective menu configuration and return diagnostics."""
    menus = _read_effective_menus()
    config_path = _resolve_menu_config_path()
    source = "json" if config_path.exists() else "default"
    warnings: list[str] = []
    errors: list[str] = []

    for menu_id, menu in menus.items():
        text = menu.get("text")
        keyboard = menu.get("keyboard")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{menu_id}: missing or invalid 'text'")
        if not isinstance(keyboard, list) or not keyboard:
            errors.append(f"{menu_id}: missing or invalid 'keyboard'")
            continue

        for row_idx, row in enumerate(keyboard):
            if not isinstance(row, list) or not row:
                warnings.append(f"{menu_id}: row {row_idx} is empty or invalid")
                continue
            if len(row) > 3:
                warnings.append(f"{menu_id}: row {row_idx} has >3 buttons")

            for button_idx, button in enumerate(row):
                if not isinstance(button, dict):
                    errors.append(f"{menu_id}: row {row_idx} button {button_idx} invalid")
                    continue
                text_value = button.get("text")
                callback_data = button.get("callback_data")
                url = button.get("url")
                if not isinstance(text_value, str) or not text_value.strip():
                    errors.append(f"{menu_id}: row {row_idx} button {button_idx} missing text")
                if not (
                    (isinstance(callback_data, str) and callback_data.strip())
                    or (isinstance(url, str) and url.strip())
                ):
                    errors.append(
                        f"{menu_id}: row {row_idx} button {button_idx} needs callback_data or url"
                    )

    return {
        "path": str(config_path),
        "source": source,
        "is_valid": len(errors) == 0,
        "menu_count": len(menus),
        "warning_count": len(warnings),
        "error_count": len(errors),
        "warnings": warnings,
        "errors": errors,
    }

