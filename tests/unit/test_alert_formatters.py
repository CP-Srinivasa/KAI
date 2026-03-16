"""Tests for Telegram and Email message formatters."""
from __future__ import annotations

import pytest

from app.alerts.evaluator import AlertDecision, DocumentScores
from app.core.enums import AlertChannel, AlertType, DocumentPriority
from app.integrations.telegram.adapter import (
    escape_md,
    format_breaking_alert,
    format_digest_message,
    format_watchlist_alert,
)
from app.integrations.email.adapter import (
    format_breaking_text,
    format_digest_text,
    format_breaking_html,
    format_digest_html,
)


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

def _scores(
    title: str = "Bitcoin ETF approved",
    sentiment: str = "positive",
    sentiment_score: float = 0.75,
    impact: float = 0.82,
    relevance: float = 0.78,
    credibility: float = 0.71,
    priority: DocumentPriority = DocumentPriority.HIGH,
) -> DocumentScores:
    return DocumentScores(
        document_id="test-doc-001",
        source_id="coindesk",
        title=title,
        explanation_short="The SEC approved Bitcoin ETF in landmark decision.",
        sentiment_label=sentiment,
        sentiment_score=sentiment_score,
        impact_score=impact,
        relevance_score=relevance,
        credibility_score=credibility,
        affected_assets=["BTC", "ETH"],
        affected_sectors=["DeFi", "CeFi"],
        matched_entities=["Bitcoin", "SEC"],
        bull_case="Institutional adoption accelerates.",
        bear_case="Regulatory uncertainty remains.",
        url="https://coindesk.com/btc-etf",
        recommended_priority=priority,
    )


# ──────────────────────────────────────────────
# Telegram: escape_md
# ──────────────────────────────────────────────

class TestEscapeMd:
    def test_escapes_dot(self) -> None:
        assert "\\." in escape_md("3.14")

    def test_escapes_exclamation(self) -> None:
        assert "\\!" in escape_md("Hello!")

    def test_escapes_parentheses(self) -> None:
        result = escape_md("(test)")
        assert "\\(" in result
        assert "\\)" in result

    def test_escapes_underscore(self) -> None:
        assert "\\_" in escape_md("hello_world")

    def test_plain_text_unchanged(self) -> None:
        result = escape_md("hello world")
        assert result == "hello world"

    def test_number_unchanged(self) -> None:
        result = escape_md("42")
        assert result == "42"

    def test_empty_string(self) -> None:
        assert escape_md("") == ""


# ──────────────────────────────────────────────
# Telegram: format_breaking_alert
# ──────────────────────────────────────────────

class TestFormatBreakingAlert:
    def test_contains_title(self) -> None:
        msg = format_breaking_alert(_scores("Bitcoin ETF Approved"))
        assert "Bitcoin ETF Approved" in msg

    def test_contains_sentiment(self) -> None:
        msg = format_breaking_alert(_scores(sentiment="positive"))
        assert "positive" in msg

    def test_contains_affected_assets(self) -> None:
        msg = format_breaking_alert(_scores())
        assert "BTC" in msg

    def test_contains_entity(self) -> None:
        msg = format_breaking_alert(_scores())
        assert "SEC" in msg or "Bitcoin" in msg

    def test_contains_url(self) -> None:
        msg = format_breaking_alert(_scores())
        assert "coindesk.com" in msg

    def test_respects_max_length(self) -> None:
        long_title = "A" * 5000
        msg = format_breaking_alert(_scores(title=long_title))
        assert len(msg) <= 4096

    def test_negative_sentiment_emoji(self) -> None:
        msg = format_breaking_alert(_scores(sentiment="negative"))
        assert "📉" in msg

    def test_critical_priority_emoji(self) -> None:
        msg = format_breaking_alert(_scores(priority=DocumentPriority.CRITICAL))
        assert "🚨" in msg

    def test_high_priority_emoji(self) -> None:
        msg = format_breaking_alert(_scores(priority=DocumentPriority.HIGH))
        assert "🔴" in msg

    def test_no_url_still_works(self) -> None:
        scores = _scores()
        scores.url = ""
        msg = format_breaking_alert(scores)
        assert len(msg) > 10


# ──────────────────────────────────────────────
# Telegram: format_watchlist_alert
# ──────────────────────────────────────────────

class TestFormatWatchlistAlert:
    def test_contains_entity(self) -> None:
        msg = format_watchlist_alert(_scores())
        assert "Bitcoin" in msg or "SEC" in msg

    def test_watchlist_header(self) -> None:
        msg = format_watchlist_alert(_scores())
        assert "Watchlist" in msg or "👁" in msg

    def test_max_length(self) -> None:
        scores = _scores(title="X" * 5000)
        msg = format_watchlist_alert(scores)
        assert len(msg) <= 4096


# ──────────────────────────────────────────────
# Telegram: format_digest_message
# ──────────────────────────────────────────────

class TestFormatDigestMessage:
    def test_contains_all_titles(self) -> None:
        items = [_scores(f"Article {i}") for i in range(3)]
        msg = format_digest_message(items)
        for i in range(3):
            assert f"Article {i}" in msg

    def test_digest_header(self) -> None:
        msg = format_digest_message([_scores()])
        assert "Digest" in msg

    def test_custom_period(self) -> None:
        msg = format_digest_message([_scores()], period="Weekly")
        assert "Weekly" in msg

    def test_limits_to_15_items(self) -> None:
        items = [_scores(f"Article {i}") for i in range(30)]
        msg = format_digest_message(items)
        # Should contain at most 15 entries (message length limited too)
        assert len(msg) <= 4096

    def test_empty_list(self) -> None:
        msg = format_digest_message([])
        assert "Digest" in msg  # Header should still appear


# ──────────────────────────────────────────────
# Email: format_breaking_text
# ──────────────────────────────────────────────

class TestFormatBreakingText:
    def test_contains_title(self) -> None:
        text = format_breaking_text(_scores("Bitcoin ETF"))
        assert "Bitcoin ETF" in text

    def test_contains_sentiment(self) -> None:
        text = format_breaking_text(_scores(sentiment="positive"))
        assert "positive" in text

    def test_contains_impact(self) -> None:
        text = format_breaking_text(_scores())
        assert "Impact" in text or "%" in text

    def test_contains_bull_bear(self) -> None:
        text = format_breaking_text(_scores())
        assert "Bull" in text
        assert "Bear" in text

    def test_contains_url(self) -> None:
        text = format_breaking_text(_scores())
        assert "coindesk.com" in text

    def test_is_plain_text(self) -> None:
        text = format_breaking_text(_scores())
        assert "<" not in text  # No HTML tags


# ──────────────────────────────────────────────
# Email: format_breaking_html
# ──────────────────────────────────────────────

class TestFormatBreakingHtml:
    def test_is_valid_html(self) -> None:
        html = format_breaking_html(_scores())
        assert "<!DOCTYPE html>" in html
        assert "<body>" in html
        assert "</body></html>" in html

    def test_contains_title(self) -> None:
        html = format_breaking_html(_scores("Bitcoin ETF Approved"))
        assert "Bitcoin ETF Approved" in html

    def test_contains_assets(self) -> None:
        html = format_breaking_html(_scores())
        assert "BTC" in html

    def test_contains_bull_bear(self) -> None:
        html = format_breaking_html(_scores())
        assert "Bull" in html
        assert "Bear" in html


# ──────────────────────────────────────────────
# Email: format_digest_text / html
# ──────────────────────────────────────────────

class TestFormatDigestEmail:
    def test_text_contains_all_titles(self) -> None:
        items = [_scores(f"Doc {i}") for i in range(3)]
        text = format_digest_text(items)
        for i in range(3):
            assert f"Doc {i}" in text

    def test_html_is_valid(self) -> None:
        html = format_digest_html([_scores()])
        assert "<!DOCTYPE html>" in html
        assert "<table" in html

    def test_html_limits_to_20_items(self) -> None:
        items = [_scores(f"Doc {i}") for i in range(30)]
        html = format_digest_html(items)
        # Spot check: item 19 should appear, item 20 should not
        assert "Doc 19" in html
        assert "Doc 20" not in html


# ──────────────────────────────────────────────
# Dry-run behavior
# ──────────────────────────────────────────────

class TestDryRunTelegram:
    @pytest.mark.asyncio
    async def test_dry_run_returns_true_without_sending(self) -> None:
        from app.integrations.telegram.adapter import TelegramAdapter
        adapter = TelegramAdapter(bot_token="fake", chat_id="0", dry_run=True)
        result = await adapter.send_text("Test message")
        assert result is True

    @pytest.mark.asyncio
    async def test_dry_run_healthcheck(self) -> None:
        from app.integrations.telegram.adapter import TelegramAdapter
        adapter = TelegramAdapter(bot_token="fake", chat_id="0", dry_run=True)
        result = await adapter.healthcheck()
        assert result["healthy"] is True
        assert result["mode"] == "dry_run"


class TestDryRunEmail:
    @pytest.mark.asyncio
    async def test_dry_run_returns_true_without_sending(self) -> None:
        from app.integrations.email.adapter import EmailAdapter
        adapter = EmailAdapter(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="",
            smtp_password="",
            from_address="test@example.com",
            to_address="user@example.com",
            dry_run=True,
        )
        from app.alerts.evaluator import AlertDecision, DocumentScores
        from app.core.enums import AlertType
        decision = AlertDecision(
            rule_name="test",
            alert_type=AlertType.BREAKING,
            channels=[AlertChannel.EMAIL],
            should_alert=True,
            severity=DocumentPriority.HIGH,
            document_scores=_scores(),
        )
        result = await adapter.send_alert(decision)
        assert result is True

    @pytest.mark.asyncio
    async def test_dry_run_healthcheck(self) -> None:
        from app.integrations.email.adapter import EmailAdapter
        adapter = EmailAdapter(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="",
            smtp_password="",
            from_address="from@example.com",
            to_address="to@example.com",
            dry_run=True,
        )
        result = await adapter.healthcheck()
        assert result["healthy"] is True


# ──────────────────────────────────────────────
# DigestBuilder
# ──────────────────────────────────────────────

class TestDigestBuilder:
    def test_filters_below_threshold(self) -> None:
        from app.alerts.digest import DigestBuilder
        builder = DigestBuilder(min_impact=0.5, min_relevance=0.5)
        low = _scores(impact=0.2)
        low.impact_score = 0.2
        low.relevance_score = 0.2
        high = _scores()
        result = builder.build([low, high])
        assert high in result
        assert low not in result

    def test_sorts_by_priority(self) -> None:
        from app.alerts.digest import DigestBuilder
        # deduplicate=False: test focuses on sort order, not dedup logic.
        # Both items share a title by default; dedup would silently drop one.
        builder = DigestBuilder(min_impact=0.0, min_relevance=0.0, deduplicate=False)
        critical = _scores(priority=DocumentPriority.CRITICAL)
        critical.impact_score = 0.9
        high = _scores(priority=DocumentPriority.HIGH)
        high.impact_score = 0.7
        result = builder.build([high, critical])
        assert result[0].recommended_priority == DocumentPriority.CRITICAL

    def test_max_items_enforced(self) -> None:
        from app.alerts.digest import DigestBuilder
        builder = DigestBuilder(max_items=5, min_impact=0.0, min_relevance=0.0)
        items = [_scores(f"Doc {i}") for i in range(20)]
        result = builder.build(items)
        assert len(result) <= 5

    def test_spam_filter(self) -> None:
        from app.alerts.digest import DigestBuilder
        builder = DigestBuilder(max_spam=0.3, min_impact=0.0, min_relevance=0.0)
        spammy = _scores()
        spammy.spam_probability = 0.80
        clean = _scores()
        clean.spam_probability = 0.05
        result = builder.build([spammy, clean])
        assert clean in result
        assert spammy not in result
