"""KAI Telegram Inline-Keyboard Menu System.

Modular, konfigurierbar, erweiterbar. Jede Sektion ist ein dict mit
Text-Header und Button-Rows. Buttons verweisen per callback_data auf
Commands oder Submenu-IDs.

Designprinzipien:
- Maximal 2 Buttons pro Zeile (Lesbarkeit auf Mobilgeraeten)
- Primaere Aktionen volle Breite
- Emojis sparsam, funktional (nicht dekorativ)
- Deutsche Labels, kurz und klar
- Jedes Submenu hat einen Zurueck-Button
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

# ---------------------------------------------------------------------------
# Button helper
# ---------------------------------------------------------------------------

def _btn(text: str, callback_data: str) -> dict[str, str]:
    """Create one InlineKeyboardButton dict."""
    return {"text": text, "callback_data": callback_data}


def _row(*buttons: dict[str, str]) -> list[dict[str, str]]:
    """Create one keyboard row."""
    return list(buttons)


# ---------------------------------------------------------------------------
# Menu definitions
# ---------------------------------------------------------------------------

# callback_data prefixes:
#   cmd:<command>   -> dispatch to existing _cmd_* handler
#   menu:<menu_id>  -> show submenu
#   noop            -> do nothing (placeholder)

DEFAULT_MENU_MAIN: dict[str, Any] = {
    "text": (
        "*KAI Trading Intelligence*\n"
        "Waehle eine Kategorie:"
    ),
    "keyboard": [
        _row(_btn("\U0001f4ca Status", "cmd:status")),
        _row(
            _btn("\U0001f4bc Portfolio", "menu:portfolio"),
            _btn("\U0001f4e1 Signale", "menu:signals"),
        ),
        _row(
            _btn("\U0001f514 Alerts", "cmd:alertstatus"),
            _btn("\U0001f4cb Tagesbericht", "cmd:tagesbericht"),
        ),
        _row(_btn("\U0001f4e8 Signal senden", "menu:signal_send")),
        _row(
            _btn("\u2699\ufe0f Steuerung", "menu:control"),
            _btn("\u2753 Hilfe", "cmd:hilfe"),
        ),
    ],
}

DEFAULT_MENU_PORTFOLIO: dict[str, Any] = {
    "text": "*Portfolio* (Paper, read-only)",
    "keyboard": [
        _row(
            _btn("\U0001f4c8 Positionen", "cmd:positions"),
            _btn("\U0001f6e1\ufe0f Exposure", "cmd:exposure"),
        ),
        _row(_btn("\u2b05\ufe0f Hauptmenue", "menu:main")),
    ],
}

DEFAULT_MENU_SIGNALS: dict[str, Any] = {
    "text": "*Signale*",
    "keyboard": [
        _row(
            _btn("\U0001f4e1 Aktive Signale", "cmd:signals"),
            _btn("\U0001f504 Pipeline", "cmd:signalstatus"),
        ),
        _row(_btn("\u2b05\ufe0f Hauptmenue", "menu:main")),
    ],
}

DEFAULT_MENU_SIGNAL_SEND: dict[str, Any] = {
    "text": (
        "*Signal senden*\n\n"
        "*Kurzformat:*\n"
        "`/signal BUY BTC 65000 SL=62000 TP=70000`\n\n"
        "*Strukturiert:*\n"
        "`[SIGNAL]`\n"
        "`Symbol: BTC/USDT`\n"
        "`Side: BUY`\n"
        "`Direction: LONG`\n"
        "`Entry Rule: BELOW 65000`\n"
        "`Targets: 70000`\n"
        "`Stop Loss: 62000`\n"
        "`Leverage: 10x`\n\n"
        "Oder sprich ein Signal als Sprachnachricht ein."
    ),
    "keyboard": [
        _row(_btn("\u2b05\ufe0f Hauptmenue", "menu:main")),
    ],
}

DEFAULT_MENU_CONTROL: dict[str, Any] = {
    "text": "*Steuerung*",
    "keyboard": [
        _row(
            _btn("\u23f8\ufe0f Pause", "cmd:pause"),
            _btn("\u25b6\ufe0f Resume", "cmd:resume"),
        ),
        _row(_btn("\u26d4 Notfall-Stopp", "cmd:kill")),
        _row(
            _btn("\u267b\ufe0f Menue neu laden", "cmd:menu_reload"),
            _btn("\u2705 Menue pruefen", "cmd:menu_validate"),
        ),
        _row(_btn("\u2b05\ufe0f Hauptmenue", "menu:main")),
    ],
}

# Default registry (Python fallback, used when no valid JSON config is present)
DEFAULT_MENUS: dict[str, dict[str, Any]] = {
    "main": DEFAULT_MENU_MAIN,
    "portfolio": DEFAULT_MENU_PORTFOLIO,
    "signals": DEFAULT_MENU_SIGNALS,
    "signal_send": DEFAULT_MENU_SIGNAL_SEND,
    "control": DEFAULT_MENU_CONTROL,
}

# Backward-compatible alias for tests/imports.
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


def _normalize_menu_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    normalized: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for menu_id, raw_menu in payload.items():
        if not isinstance(menu_id, str) or not menu_id.strip():
            errors.append("invalid_menu_id")
            continue
        menu = _normalize_menu(raw_menu)
        if menu is None:
            errors.append(f"invalid_menu_definition:{menu_id}")
            continue
        normalized[menu_id.strip()] = menu

    return normalized, errors


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

    normalized, errors = _normalize_menu_payload(payload)
    for error in errors:
        logger.warning("[MENU] Ignoring invalid config entry in %s: %s", path, error)

    if not normalized:
        logger.warning("[MENU] No valid menu definitions found in %s", path)
        return None

    return normalized


def validate_menu_config() -> dict[str, object]:
    """Validate menu JSON config and report diagnostics for operator commands."""
    path = _resolve_menu_config_path()
    result: dict[str, object] = {
        "path": str(path),
        "exists": path.exists(),
        "source": "defaults",
        "is_valid": True,
        "menu_count": len(DEFAULT_MENUS),
        "warning_count": 0,
        "error_count": 0,
        "warnings": [],
        "errors": [],
    }

    if not path.exists():
        warnings = ["menu_config_missing_using_defaults"]
        result["warnings"] = warnings
        result["warning_count"] = len(warnings)
        return result

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors = [f"menu_config_read_failed:{exc}"]
        result["source"] = "json"
        result["is_valid"] = False
        result["menu_count"] = 0
        result["errors"] = errors
        result["error_count"] = len(errors)
        return result

    payload = _extract_menu_payload(raw)
    if payload is None:
        errors = ["menu_config_payload_invalid"]
        result["source"] = "json"
        result["is_valid"] = False
        result["menu_count"] = 0
        result["errors"] = errors
        result["error_count"] = len(errors)
        return result

    normalized, errors = _normalize_menu_payload(payload)
    if not normalized:
        errors = [*errors, "menu_config_has_no_valid_menus"]

    result["source"] = "json"
    result["is_valid"] = not errors
    result["menu_count"] = len(normalized)
    result["errors"] = errors
    result["error_count"] = len(errors)
    return result


def _read_effective_menus() -> dict[str, dict[str, Any]]:
    global _cache_path, _cache_mtime_ns, _cache_menus

    path = _resolve_menu_config_path()
    mtime_ns = path.stat().st_mtime_ns if path.exists() else -1

    if _cache_menus is not None and _cache_path == path and _cache_mtime_ns == mtime_ns:
        return copy.deepcopy(_cache_menus)

    menus = copy.deepcopy(DEFAULT_MENUS)
    external_menus = _load_menus_from_json(path)
    if external_menus:
        # Merge-by-id so a partial JSON can override only selected menus.
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
    """Return menu definition by ID, or None if not found."""
    menus = _read_effective_menus()
    menu = menus.get(menu_id)
    if menu is None:
        return None
    return copy.deepcopy(menu)


def build_inline_keyboard(menu_id: str) -> dict[str, Any] | None:
    """Build Telegram InlineKeyboardMarkup payload for a menu."""
    menu = get_menu(menu_id)
    if menu is None:
        return None
    return {
        "inline_keyboard": menu["keyboard"],
    }
