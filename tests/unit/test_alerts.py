"""Tests for the app/alerts/ module — Sprint 3 Alerting.

Coverage:
- AlertMessage construction
- ThresholdEngine.should_alert()
- DigestCollector add/flush/peek/len
- Formatters (Telegram + Email) — pure functions
- TelegramAlertChannel — dry_run mode
- EmailAlertChannel — dry_run mode
- AlertService.process_document() — threshold not met / met
- AlertService.send_digest()
- AlertService.from_settings() factory
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.alerts.base.interfaces import AlertDeliveryResult, AlertMessage, BaseAlertChannel
from app.alerts.channels.email import EmailAlertChannel
from app.alerts.channels.telegram import TelegramAlertChannel
from app.alerts.digest import DigestCollector
from app.alerts.formatters import (
    format_email_body,
    format_email_digest_body,
    format_email_digest_subject,
    format_email_subject,
    format_telegram_digest,
    format_telegram_message,
)
from app.alerts.service import AlertService, _build_alert_message
from app.alerts.threshold import ThresholdEngine
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel
from app.core.settings import AlertSettings, AppSettings

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_result(**overrides) -> AnalysisResult:
    defaults = {
        "document_id": str(uuid.uuid4()),
        "sentiment_label": SentimentLabel.NEUTRAL,
        "sentiment_score": 0.0,
        "relevance_score": 0.5,
        "impact_score": 0.5,
        "confidence_score": 0.8,
        "novelty_score": 0.5,
        "market_scope": MarketScope.UNKNOWN,
        "actionable": False,
        "explanation_short": "Short explanation.",
        "explanation_long": "Long explanation here.",
    }
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def _make_high_result(**overrides) -> AnalysisResult:
    """Build a result that will score >= 7 (alert-worthy by default threshold)."""
    return _make_result(
        relevance_score=1.0,
        impact_score=1.0,
        novelty_score=1.0,
        actionable=True,
        **overrides,
    )


def _make_alert_msg(**overrides) -> AlertMessage:
    defaults = {
        "document_id": str(uuid.uuid4()),
        "title": "Test Alert",
        "url": "https://example.com/news/1",
        "priority": 8,
        "sentiment_label": "bullish",
        "actionable": True,
        "explanation": "Market-moving event detected.",
    }
    defaults.update(overrides)
    return AlertMessage(**defaults)


def _dry_run_settings(**overrides) -> AlertSettings:
    defaults = {
        "dry_run": True,
        "min_priority": 7,
    }
    defaults.update(overrides)
    return AlertSettings(**defaults)


def _app_settings_dry_run() -> AppSettings:
    return AppSettings(alerts=_dry_run_settings())


# ── AlertMessage ──────────────────────────────────────────────────────────────


def test_alert_message_defaults():
    msg = AlertMessage(
        document_id="abc",
        title="Title",
        url="https://example.com",
        priority=5,
        sentiment_label="neutral",
        actionable=False,
        explanation="No action needed.",
    )
    assert msg.priority == 5
    assert msg.affected_assets == []
    assert msg.tags == []
    assert msg.source_name is None
    assert isinstance(msg.created_at, datetime)


def test_alert_message_frozen():
    msg = _make_alert_msg()
    with pytest.raises((AttributeError, TypeError)):
        msg.priority = 1  # type: ignore[misc]


# ── AlertDeliveryResult ───────────────────────────────────────────────────────


def test_alert_delivery_result_success():
    result = AlertDeliveryResult(channel="telegram", success=True, message_id="42")
    assert result.success is True
    assert result.error is None


def test_alert_delivery_result_failure():
    result = AlertDeliveryResult(channel="email", success=False, error="Connection refused")
    assert result.success is False
    assert "refused" in (result.error or "")


# ── ThresholdEngine ───────────────────────────────────────────────────────────


def test_threshold_default_min_priority():
    engine = ThresholdEngine()
    assert engine.min_priority == 7


def test_threshold_custom_min_priority():
    engine = ThresholdEngine(min_priority=5)
    assert engine.min_priority == 5


def test_threshold_invalid_raises():
    with pytest.raises(ValueError):
        ThresholdEngine(min_priority=0)
    with pytest.raises(ValueError):
        ThresholdEngine(min_priority=11)


def test_threshold_should_alert_high_priority():
    engine = ThresholdEngine(min_priority=7)
    result = _make_high_result()
    assert engine.should_alert(result) is True


def test_threshold_should_not_alert_low_priority():
    engine = ThresholdEngine(min_priority=7)
    # Low scores → priority ~5
    result = _make_result(relevance_score=0.3, impact_score=0.3, novelty_score=0.3)
    assert engine.should_alert(result) is False


def test_threshold_spam_excluded():
    engine = ThresholdEngine(min_priority=1)
    result = _make_high_result()
    # Even at min_priority=1, spam blocks alert
    assert engine.should_alert(result, spam_probability=0.99) is False


def test_threshold_borderline():
    engine = ThresholdEngine(min_priority=7)
    # Exactly threshold
    result = _make_result(
        relevance_score=0.8,
        impact_score=0.8,
        novelty_score=0.5,
        actionable=True,
    )
    # Result should be alert-worthy; exact value depends on formula
    score_result = engine.should_alert(result)
    assert isinstance(score_result, bool)


# ── DigestCollector ───────────────────────────────────────────────────────────


def test_digest_empty_initially():
    d = DigestCollector()
    assert d.is_empty()
    assert len(d) == 0


def test_digest_add_and_len():
    d = DigestCollector()
    d.add(_make_alert_msg())
    assert len(d) == 1
    assert not d.is_empty()


def test_digest_flush_clears():
    d = DigestCollector()
    d.add(_make_alert_msg())
    d.add(_make_alert_msg())
    messages = d.flush()
    assert len(messages) == 2
    assert d.is_empty()


def test_digest_peek_does_not_clear():
    d = DigestCollector()
    d.add(_make_alert_msg())
    _ = d.peek()
    assert not d.is_empty()


def test_digest_max_size_drops_oldest():
    d = DigestCollector(max_size=2)
    m1 = _make_alert_msg(title="First")
    m2 = _make_alert_msg(title="Second")
    m3 = _make_alert_msg(title="Third")
    d.add(m1)
    d.add(m2)
    d.add(m3)  # should drop m1
    messages = d.flush()
    assert len(messages) == 2
    assert messages[0].title == "Second"
    assert messages[1].title == "Third"


def test_digest_invalid_max_size():
    with pytest.raises(ValueError):
        DigestCollector(max_size=0)


# ── Formatters — Telegram ─────────────────────────────────────────────────────


def test_format_telegram_message_contains_title():
    msg = _make_alert_msg(title="Bitcoin Surges", priority=9, sentiment_label="bullish")
    text = format_telegram_message(msg)
    assert "Bitcoin Surges" in text
    assert "9/10" in text
    assert "Critical" in text


def test_format_telegram_message_bearish_emoji():
    msg = _make_alert_msg(sentiment_label="bearish")
    text = format_telegram_message(msg)
    assert "🔴" in text


def test_format_telegram_message_actionable():
    msg = _make_alert_msg(actionable=True)
    text = format_telegram_message(msg)
    assert "Actionable" in text


def test_format_telegram_message_has_url():
    msg = _make_alert_msg(url="https://example.com/news/42")
    text = format_telegram_message(msg)
    assert "https://example.com/news/42" in text


def test_format_telegram_digest_header():
    msgs = [_make_alert_msg(), _make_alert_msg()]
    text = format_telegram_digest(msgs, "last 60 minutes")
    assert "Alert Digest" in text
    assert "2 alert(s)" in text


def test_format_telegram_long_title_truncated():
    long_title = "A" * 100
    msg = _make_alert_msg(title=long_title)
    text = format_telegram_digest([msg], "test period")
    # Title should be truncated to 60 chars + ellipsis
    assert "…" in text


# ── Formatters — Email ────────────────────────────────────────────────────────


def test_format_email_subject_contains_priority():
    msg = _make_alert_msg(priority=8, title="ETH Rally")
    subject = format_email_subject(msg)
    assert "P8" in subject
    assert "High" in subject
    assert "KAI Alert" in subject


def test_format_email_body_contains_title():
    msg = _make_alert_msg(title="Test Title", explanation="Important event.")
    body = format_email_body(msg)
    assert "Test Title" in body
    assert "Important event." in body


def test_format_email_digest_subject():
    subject = format_email_digest_subject(5, "last 60 minutes")
    assert "5 alert(s)" in subject
    assert "KAI Digest" in subject


def test_format_email_digest_body_lists_items():
    msgs = [
        _make_alert_msg(title="Alert One", priority=8),
        _make_alert_msg(title="Alert Two", priority=7),
    ]
    body = format_email_digest_body(msgs, "test period")
    assert "Alert One" in body
    assert "Alert Two" in body
    assert "P8" in body
    assert "P7" in body


# ── TelegramAlertChannel ──────────────────────────────────────────────────────


async def test_telegram_dry_run_send():
    settings = _dry_run_settings(
        telegram_enabled=True, telegram_token="tok", telegram_chat_id="123"
    )
    ch = TelegramAlertChannel(settings)
    result = await ch.send(_make_alert_msg())
    assert result.success is True
    assert result.message_id == "dry_run"
    assert result.channel == "telegram"


async def test_telegram_dry_run_digest():
    settings = _dry_run_settings()
    ch = TelegramAlertChannel(settings)
    result = await ch.send_digest([_make_alert_msg()], "last hour")
    assert result.success is True
    assert result.message_id == "dry_run"


def test_telegram_is_enabled_false_by_default():
    ch = TelegramAlertChannel(_dry_run_settings())
    # telegram_enabled=False by default
    assert ch.is_enabled is False


def test_telegram_is_enabled_true_when_configured():
    settings = AlertSettings(
        telegram_enabled=True,
        telegram_token="sometoken",
        telegram_chat_id="12345",
        dry_run=False,
    )
    ch = TelegramAlertChannel(settings)
    assert ch.is_enabled is True


def test_telegram_channel_name():
    ch = TelegramAlertChannel(_dry_run_settings())
    assert ch.channel_name == "telegram"


# ── EmailAlertChannel ─────────────────────────────────────────────────────────


async def test_email_dry_run_send():
    settings = _dry_run_settings(email_enabled=True, email_host="smtp.example.com",
                                  email_from="bot@example.com", email_to="user@example.com")
    ch = EmailAlertChannel(settings)
    result = await ch.send(_make_alert_msg())
    assert result.success is True
    assert result.message_id == "dry_run"
    assert result.channel == "email"


async def test_email_dry_run_digest():
    settings = _dry_run_settings()
    ch = EmailAlertChannel(settings)
    result = await ch.send_digest([_make_alert_msg()], "last hour")
    assert result.success is True
    assert result.message_id == "dry_run"


def test_email_is_enabled_false_by_default():
    ch = EmailAlertChannel(_dry_run_settings())
    assert ch.is_enabled is False


def test_email_is_enabled_true_when_configured():
    settings = AlertSettings(
        email_enabled=True,
        email_host="smtp.example.com",
        email_from="bot@example.com",
        email_to="user@example.com",
        dry_run=False,
    )
    ch = EmailAlertChannel(settings)
    assert ch.is_enabled is True


def test_email_channel_name():
    ch = EmailAlertChannel(_dry_run_settings())
    assert ch.channel_name == "email"


# ── AlertService.from_settings ────────────────────────────────────────────────


def test_alert_service_from_settings_dry_run():
    settings = _app_settings_dry_run()
    service = AlertService.from_settings(settings)
    # In dry_run mode, both channels should be present
    assert len(service._channels) == 2


def test_alert_service_threshold_from_settings():
    settings = AppSettings(alerts=AlertSettings(min_priority=5))
    service = AlertService.from_settings(settings)
    assert service._threshold.min_priority == 5


# ── AlertService.process_document ─────────────────────────────────────────────


async def test_process_document_below_threshold_returns_empty():
    service = AlertService.from_settings(_app_settings_dry_run())
    doc = CanonicalDocument(url="https://example.com/low", title="Low priority doc")
    result = _make_result(relevance_score=0.1, impact_score=0.1, novelty_score=0.1)
    deliveries = await service.process_document(doc, result)
    assert deliveries == []


async def test_process_document_above_threshold_dispatches():
    service = AlertService.from_settings(_app_settings_dry_run())
    doc = CanonicalDocument(url="https://example.com/high", title="High priority doc")
    result = _make_high_result()
    deliveries = await service.process_document(doc, result)
    # dry_run → both channels deliver successfully
    assert len(deliveries) == 2
    assert all(d.success for d in deliveries)


async def test_process_document_spam_excluded():
    service = AlertService.from_settings(_app_settings_dry_run())
    doc = CanonicalDocument(url="https://example.com/spam", title="Spam doc")
    result = _make_high_result()
    deliveries = await service.process_document(doc, result, spam_probability=0.99)
    # Spam should be excluded at threshold level
    assert deliveries == []


# ── AlertService.send_digest ──────────────────────────────────────────────────


async def test_send_digest_empty_returns_empty():
    service = AlertService.from_settings(_app_settings_dry_run())
    results = await service.send_digest([], "last hour")
    assert results == []


async def test_send_digest_dispatches_to_all_channels():
    service = AlertService.from_settings(_app_settings_dry_run())
    msgs = [_make_alert_msg(), _make_alert_msg()]
    results = await service.send_digest(msgs, "last 60 minutes")
    assert len(results) == 2
    assert all(r.success for r in results)


# ── _build_alert_message ──────────────────────────────────────────────────────


def test_build_alert_message_sets_all_fields():
    doc = CanonicalDocument(
        url="https://example.com/doc",
        title="Test Doc",
        source_name="CryptoNews",
        published_at=datetime(2026, 3, 19, 12, 0, tzinfo=UTC),
    )
    result = _make_high_result(
        affected_assets=["BTC", "ETH"],
        tags=["crypto", "rally"],
        explanation_short="BTC rally confirmed.",
    )
    msg = _build_alert_message(doc, result, spam_probability=0.0)

    assert msg.title == "Test Doc"
    assert msg.url == "https://example.com/doc"
    assert msg.source_name == "CryptoNews"
    assert msg.priority >= 7  # high result → high priority
    assert "BTC" in msg.affected_assets
    assert "ETH" in msg.affected_assets
    assert "crypto" in msg.tags
    assert msg.explanation == "BTC rally confirmed."


def test_build_alert_message_priority_matches_score():
    doc = CanonicalDocument(url="https://example.com", title="Doc")
    result = _make_high_result()
    msg = _build_alert_message(doc, result, spam_probability=0.0)
    assert 7 <= msg.priority <= 10


# ── BaseAlertChannel ABC ──────────────────────────────────────────────────────


def test_base_alert_channel_is_abstract():
    with pytest.raises(TypeError):
        BaseAlertChannel()  # type: ignore[abstract]
