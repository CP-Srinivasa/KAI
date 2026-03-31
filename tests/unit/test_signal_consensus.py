"""Tests for the multi-model signal consensus validator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.market_data.models import MarketDataPoint
from app.signals.models import SignalCandidate, SignalDirection
from app.trading.signal_consensus import (
    GEMINI_OPENAI_BASE_URL,
    ConsensusResult,
    SignalConsensusValidator,
    ValidatorConfig,
)


def _make_signal(**overrides) -> SignalCandidate:
    defaults = {
        "decision_id": "dec_test123",
        "timestamp_utc": "2026-03-31T12:00:00+00:00",
        "symbol": "BTC/USDT",
        "market": "crypto",
        "venue": "paper",
        "mode": "paper",
        "direction": SignalDirection.LONG,
        "thesis": "Bullish momentum continues",
        "supporting_factors": ("high impact", "strong sentiment"),
        "contradictory_factors": ("low volume",),
        "confidence_score": 0.85,
        "confluence_count": 4,
        "market_regime": "trending",
        "volatility_state": "normal",
        "liquidity_state": "adequate",
        "entry_price": 67500.0,
        "stop_loss_price": 65800.0,
        "take_profit_price": 70900.0,
        "invalidation_condition": "Price closes below 65800",
        "risk_assessment": "Long entry at 67500",
        "position_size_rationale": "Risk-based sizing",
        "max_loss_estimate_pct": 2.5,
        "data_sources_used": ("coingecko",),
        "source_document_id": "doc-123",
        "model_version": "test",
        "prompt_version": "test",
    }
    defaults.update(overrides)
    return SignalCandidate(**defaults)


def _make_market_data(**overrides) -> MarketDataPoint:
    defaults = {
        "symbol": "BTC/USDT",
        "timestamp_utc": "2026-03-31T12:00:00+00:00",
        "price": 67600.0,
        "volume_24h": 15_000_000_000.0,
        "change_pct_24h": 2.1,
        "source": "coingecko",
        "is_stale": False,
        "freshness_seconds": 5.0,
    }
    defaults.update(overrides)
    return MarketDataPoint(**defaults)


def _mock_openai_response(content: str) -> MagicMock:
    """Build a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


# ── Single model (backward compatible) ────────────────────────────


async def test_consensus_agree() -> None:
    """Validator agrees with signal."""
    validator = SignalConsensusValidator(api_key="test-key")
    mock_resp = _mock_openai_response(
        '{"agree": true, "confidence": 0.85, "reasoning": "Momentum aligns"}'
    )

    with patch(
        "app.trading.signal_consensus.AsyncOpenAI",
    ) as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_resp,
        )
        mock_cls.return_value = mock_client

        result = await validator.validate(_make_signal(), _make_market_data())

    assert result.agreed is True
    assert result.confidence == 0.85
    assert result.error is None
    assert len(result.validator_results) == 1


async def test_consensus_disagree() -> None:
    """Validator disagrees with signal."""
    validator = SignalConsensusValidator(api_key="test-key")
    mock_resp = _mock_openai_response(
        '{"agree": false, "confidence": 0.70, "reasoning": "Overextended"}'
    )

    with patch(
        "app.trading.signal_consensus.AsyncOpenAI",
    ) as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_resp,
        )
        mock_cls.return_value = mock_client

        result = await validator.validate(_make_signal(), _make_market_data())

    assert result.agreed is False
    assert result.confidence == 0.70


async def test_consensus_api_error_fails_closed() -> None:
    """API error results in disagree (fail-closed)."""
    validator = SignalConsensusValidator(api_key="test-key")

    with patch(
        "app.trading.signal_consensus.AsyncOpenAI",
    ) as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("API timeout"),
        )
        mock_cls.return_value = mock_client

        result = await validator.validate(_make_signal(), _make_market_data())

    assert result.agreed is False
    assert result.error is not None
    assert "API timeout" in result.error


async def test_consensus_invalid_json_fails_closed() -> None:
    """Invalid JSON response results in disagree (fail-closed)."""
    validator = SignalConsensusValidator(api_key="test-key")
    mock_resp = _mock_openai_response("Sure, I agree with this trade!")

    with patch(
        "app.trading.signal_consensus.AsyncOpenAI",
    ) as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_resp,
        )
        mock_cls.return_value = mock_client

        result = await validator.validate(_make_signal(), _make_market_data())

    assert result.agreed is False
    assert result.error is not None
    assert "invalid_json" in result.error


async def test_consensus_result_failed_factory() -> None:
    """ConsensusResult.failed() builds fail-closed result."""
    result = ConsensusResult.failed("test_error", model="gpt-4o-mini")
    assert result.agreed is False
    assert result.confidence == 0.0
    assert result.error == "test_error"
    assert result.validator_model == "gpt-4o-mini"


async def test_consensus_short_signal() -> None:
    """Validator handles short signals correctly."""
    validator = SignalConsensusValidator(api_key="test-key")
    signal = _make_signal(direction=SignalDirection.SHORT)
    mock_resp = _mock_openai_response(
        '{"agree": true, "confidence": 0.60, "reasoning": "Bearish ok"}'
    )

    with patch(
        "app.trading.signal_consensus.AsyncOpenAI",
    ) as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_resp,
        )
        mock_cls.return_value = mock_client

        result = await validator.validate(signal, _make_market_data())

    assert result.agreed is True


# ── Multi-model consensus ─────────────────────────────────────────


async def test_multi_model_both_agree() -> None:
    """Both validators agree -> consensus agreed."""
    validator = SignalConsensusValidator.multi(
        ValidatorConfig(api_key="key1", model="gpt-4o-mini", label="openai"),
        ValidatorConfig(
            api_key="key2",
            model="gemini-2.5-flash",
            label="gemini",
            base_url=GEMINI_OPENAI_BASE_URL,
        ),
    )

    resp_agree = _mock_openai_response(
        '{"agree": true, "confidence": 0.80, "reasoning": "Looks good"}'
    )

    with patch(
        "app.trading.signal_consensus.AsyncOpenAI",
    ) as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=resp_agree,
        )
        mock_cls.return_value = mock_client

        result = await validator.validate(_make_signal(), _make_market_data())

    assert result.agreed is True
    assert len(result.validator_results) == 2
    assert result.error is None


async def test_multi_model_one_disagrees() -> None:
    """One disagrees -> consensus rejected (unanimous required)."""
    validator = SignalConsensusValidator.multi(
        ValidatorConfig(api_key="key1", model="gpt-4o-mini", label="openai"),
        ValidatorConfig(
            api_key="key2",
            model="gemini-2.5-flash",
            label="gemini",
            base_url=GEMINI_OPENAI_BASE_URL,
        ),
    )

    resp_agree = _mock_openai_response(
        '{"agree": true, "confidence": 0.85, "reasoning": "Yes"}'
    )
    resp_disagree = _mock_openai_response(
        '{"agree": false, "confidence": 0.70, "reasoning": "No"}'
    )

    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        return resp_agree if call_count == 1 else resp_disagree

    with patch(
        "app.trading.signal_consensus.AsyncOpenAI",
    ) as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=side_effect,
        )
        mock_cls.return_value = mock_client

        result = await validator.validate(_make_signal(), _make_market_data())

    assert result.agreed is False
    assert len(result.validator_results) == 2


async def test_multi_model_one_errors() -> None:
    """One validator errors -> consensus rejected (fail-closed)."""
    validator = SignalConsensusValidator.multi(
        ValidatorConfig(api_key="key1", model="gpt-4o-mini", label="openai"),
        ValidatorConfig(
            api_key="key2",
            model="gemini-2.5-flash",
            label="gemini",
            base_url=GEMINI_OPENAI_BASE_URL,
        ),
    )

    resp_agree = _mock_openai_response(
        '{"agree": true, "confidence": 0.90, "reasoning": "Fine"}'
    )

    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return resp_agree
        raise Exception("Gemini API down")

    with patch(
        "app.trading.signal_consensus.AsyncOpenAI",
    ) as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=side_effect,
        )
        mock_cls.return_value = mock_client

        result = await validator.validate(_make_signal(), _make_market_data())

    assert result.agreed is False
    assert result.error is not None
    assert "Gemini API down" in result.error


async def test_validator_config_defaults() -> None:
    """ValidatorConfig has sensible defaults."""
    cfg = ValidatorConfig(api_key="test")
    assert cfg.model == "gpt-4o-mini"
    assert cfg.base_url is None
    assert cfg.display_label == "gpt-4o-mini"

    cfg2 = ValidatorConfig(api_key="test", label="custom")
    assert cfg2.display_label == "custom"


async def test_models_property() -> None:
    """models property returns list of labels."""
    validator = SignalConsensusValidator.multi(
        ValidatorConfig(api_key="k1", model="gpt-4o-mini", label="openai"),
        ValidatorConfig(api_key="k2", model="gemini-2.5-flash", label="gemini"),
    )
    assert validator.models == ["openai", "gemini"]
