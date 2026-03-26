"""Runtime JSON Schema validation for Telegram NEWS/SIGNAL/EXCHANGE_RESPONSE."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from app.messaging.message_models import ExchangeResponse, NewsMessage, TradingSignal


class MessageSchemaValidationError(ValueError):
    """Raised when a structured message violates the canonical JSON schema."""

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


_FORMAT_CHECKER = FormatChecker()
_MESSAGE_TYPES = {"news", "signal", "exchange_response"}
_MARKET_TYPES = ["spot", "futures", "margin", "options"]
_PRIORITIES = ["low", "medium", "high", "critical"]
_SIGNAL_SIDES = ["buy", "sell"]
_SIGNAL_DIRECTIONS = ["long", "short"]
_ENTRY_TYPES = [
    "market",
    "at",
    "below",
    "above",
    "range",
    "breakout_above",
    "breakdown_below",
]
_SIGNAL_STATUSES = ["new", "active", "filled", "cancelled", "expired"]
_RISK_MODES = ["isolated", "cross"]
_EXCHANGE_ACTIONS = [
    "received",
    "validated",
    "rejected",
    "order_created",
    "partially_filled",
    "filled",
    "stop_loss_set",
    "take_profit_set",
    "take_profit_hit",
    "stop_loss_hit",
    "position_closed",
    "cancelled",
    "error",
]
_RESPONSE_STATUSES = ["success", "error", "pending"]


def _news_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "message_type",
            "title",
            "priority",
            "timestamp_utc",
        ],
        "properties": {
            "message_type": {"const": "news"},
            "source": {"type": "string"},
            "title": {"type": "string", "minLength": 1},
            "message": {"type": "string"},
            "market": {"type": "string"},
            "symbol": {"type": "string"},
            "priority": {"enum": _PRIORITIES},
            "timestamp_utc": {"type": "string", "format": "date-time"},
        },
    }


def _signal_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "message_type",
            "signal_id",
            "market_type",
            "symbol",
            "side",
            "direction",
            "entry_type",
            "leverage",
            "risk_mode",
            "status",
            "timestamp_utc",
        ],
        "properties": {
            "message_type": {"const": "signal"},
            "signal_id": {"type": "string", "minLength": 1},
            "source": {"type": "string"},
            "exchange_scope": {
                "type": "array",
                "items": {"type": "string"},
            },
            "market_type": {"enum": _MARKET_TYPES},
            "symbol": {"type": "string", "minLength": 2},
            "display_symbol": {"type": "string"},
            "side": {"enum": _SIGNAL_SIDES},
            "direction": {"enum": _SIGNAL_DIRECTIONS},
            "entry_type": {"enum": _ENTRY_TYPES},
            "entry_value": {"type": "number", "exclusiveMinimum": 0},
            "entry_min": {"type": "number", "exclusiveMinimum": 0},
            "entry_max": {"type": "number", "exclusiveMinimum": 0},
            "targets": {
                "type": "array",
                "items": {"type": "number", "exclusiveMinimum": 0},
            },
            "stop_loss": {"type": ["number", "null"]},
            "leverage": {"type": "integer", "minimum": 1},
            "risk_mode": {"enum": _RISK_MODES},
            "status": {"enum": _SIGNAL_STATUSES},
            "timestamp_utc": {"type": "string", "format": "date-time"},
            "notes": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "strategy_tag": {"type": "string"},
            "reduce_only": {"type": "boolean"},
            "position_size_suggestion": {"type": "number", "exclusiveMinimum": 0},
        },
        "allOf": [
            {
                "if": {"properties": {"entry_type": {"const": "range"}}},
                "then": {
                    "required": ["entry_min", "entry_max"],
                    "properties": {
                        "entry_min": {"type": "number", "exclusiveMinimum": 0},
                        "entry_max": {"type": "number", "exclusiveMinimum": 0},
                    },
                },
            },
            {
                "if": {
                    "properties": {
                        "entry_type": {
                            "enum": [
                                "at",
                                "below",
                                "above",
                                "breakout_above",
                                "breakdown_below",
                            ]
                        }
                    }
                },
                "then": {"required": ["entry_value"]},
            },
            {
                "if": {"required": ["entry_min", "entry_max"]},
                "then": {
                    "properties": {
                        "entry_min": {"type": "number"},
                        "entry_max": {"type": "number"},
                    }
                },
            },
        ],
    }


def _exchange_response_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "message_type",
            "response_id",
            "market_type",
            "action",
            "status",
            "timestamp_utc",
        ],
        "properties": {
            "message_type": {"const": "exchange_response"},
            "response_id": {"type": "string"},
            "related_signal_id": {"type": "string"},
            "exchange": {"type": "string"},
            "symbol": {"type": "string"},
            "market_type": {"enum": _MARKET_TYPES},
            "action": {"enum": _EXCHANGE_ACTIONS},
            "status": {"enum": _RESPONSE_STATUSES},
            "order_side": {"enum": _SIGNAL_SIDES},
            "position_side": {"enum": _SIGNAL_DIRECTIONS},
            "entry_price": {"type": "number", "exclusiveMinimum": 0},
            "order_type": {"type": "string"},
            "quantity": {"type": "number", "exclusiveMinimum": 0},
            "leverage": {"type": "integer", "minimum": 1},
            "stop_loss": {"type": "number", "exclusiveMinimum": 0},
            "take_profit": {"type": "number", "exclusiveMinimum": 0},
            "exchange_order_id": {"type": "string"},
            "result": {"type": "string"},
            "realized_profit": {"type": "string"},
            "error_code": {"type": "string"},
            "message": {"type": "string"},
            "timestamp_utc": {"type": "string", "format": "date-time"},
        },
    }


_MESSAGE_SCHEMAS: dict[str, dict[str, object]] = {
    "news": _news_schema(),
    "signal": _signal_schema(),
    "exchange_response": _exchange_response_schema(),
}


@lru_cache(maxsize=8)
def _validator_for_message_type(message_type: str) -> Draft202012Validator:
    schema = _MESSAGE_SCHEMAS.get(message_type)
    if schema is None:
        raise MessageSchemaValidationError(
            "Unsupported message_type "
            f"'{message_type}'. Expected one of: {sorted(_MESSAGE_TYPES)}"
        )
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def _format_schema_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{path}: {error.message}"


def validate_message_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one structured message payload fail-closed."""
    candidate = dict(payload)
    raw_message_type = candidate.get("message_type")
    if not isinstance(raw_message_type, str):
        raise MessageSchemaValidationError("message_type must be a string")

    message_type = raw_message_type.strip().lower()
    if message_type not in _MESSAGE_TYPES:
        raise MessageSchemaValidationError(
            "Unsupported message_type "
            f"'{raw_message_type}'. Expected one of: {sorted(_MESSAGE_TYPES)}"
        )

    candidate["message_type"] = message_type
    errors = sorted(
        _validator_for_message_type(message_type).iter_errors(candidate),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error_texts = [_format_schema_error(error) for error in errors[:8]]
        first_error = error_texts[0]
        raise MessageSchemaValidationError(
            f"{message_type} schema validation failed: {first_error}",
            errors=error_texts,
        )
    return candidate


def validate_message_model(
    message: NewsMessage | TradingSignal | ExchangeResponse,
) -> dict[str, Any]:
    """Validate a message model by its canonical `to_dict` payload."""
    return validate_message_payload(message.to_dict())
