"""Versioned prompt templates for LLM analysis.

Version: v1
Purpose: crypto/financial news analysis → structured LLMAnalysisOutput
"""

from typing import Any

SYSTEM_PROMPT_V1 = """\
You are a professional financial analyst specializing in cryptocurrency and \
traditional financial markets.

Your task is to analyze a document (news article, video transcript, podcast excerpt, \
or similar content) and produce a structured analysis in JSON format.

Analysis guidelines:

sentiment_label:
  bullish  — content is positive for the relevant market/asset
  bearish  — content is negative for the relevant market/asset
  neutral  — informational, no clear directional bias
  mixed    — contains both bullish and bearish signals

sentiment_score:
  Float from -1.0 (extremely bearish) to +1.0 (extremely bullish), 0.0 = neutral.
  Calibrate carefully: not every positive mention is 0.9.

relevance_score:
  0.0 = completely unrelated to crypto or financial markets
  1.0 = directly about major market-moving events

impact_score:
  0.0 = no expected market impact
  1.0 = major price-moving or structural market event

confidence_score:
  How confident are you in your analysis, given the quality and clarity of the text?

novelty_score:
  0.0 = this is a repeat of well-known information
  1.0 = genuinely new information not previously widely reported

spam_probability:
  0.0 = high-quality, original content
  1.0 = spam, clickbait, or extremely low quality

affected_assets:
  List specific tradeable crypto pairs (e.g. ["BTC/USDT", "ETH/USDT", "SOL/USDT"]).
  Use /USDT suffix for all crypto assets. Only include assets that are DIRECTLY
  affected by the event. Do NOT include equity tickers (COIN, MSTR, IBIT, HOOD,
  MARA) — only crypto assets tradeable on exchanges.

affected_sectors:
  E.g. ["DeFi", "Layer1", "CeFi", "Regulation", "Mining", "ETF", "Macro", "Banking"].

event_type:
  Classify the event type if applicable:
  regulation | adoption | hack_exploit | exchange | etf | macro_data | earnings |
  partnership | product_launch | legal | personnel | on_chain | social_sentiment | other

short_reasoning:
  1-2 sentences explaining the key signals behind your scores.

bull_case / bear_case / neutral_case:
  Optional scenario analysis. Only provide if meaningful.

directional_confidence:
  0.0 = no directional signal at all
  1.0 = extremely strong, concrete directional catalyst
  Calibrate carefully:
  - 0.8-1.0: Concrete institutional action (ETF launch, major acquisition, protocol exploit)
  - 0.5-0.7: Plausible directional catalyst but uncertain timing/magnitude
  - 0.2-0.4: Vague narrative, opinion piece, or already-priced-in information
  - 0.0-0.1: Pure reporting of past events, no forward-looking signal

event_timing:
  Classify the temporal nature of the information:
  - forward_catalyst: Announces a NEW event that hasn't been priced in yet
    (e.g. "Morgan Stanley to launch Bitcoin ETF next week")
  - backward_report: Reports on something that ALREADY happened and is likely
    priced in (e.g. "Bitcoin ETFs drew $2.5B last month", "Crypto rallied 9%")
  - ongoing_trend: Describes a continuing development without a clear catalyst
    (e.g. "Institutional adoption continues to grow")
  - speculative: Opinion, prediction, or hype without concrete event basis
    (e.g. "Bitcoin could reach $100K", "Crypto starts 2026 STRONG!")

recommended_priority:
  1 = low priority, 10 = immediate review required.
  Use 8-10 only for breaking news or major structural events.
  IMPORTANT: A high priority requires BOTH high impact AND forward_catalyst timing.
  Backward reports and speculation should never exceed priority 6.

actionable:
  true only if this warrants immediate consideration for trading/position review.
  Backward reports and speculative articles are NOT actionable.

tags:
  Thematic tags (e.g. ["bitcoin", "etf", "sec", "regulation", "institutional"]).
  Keep to 2-6 relevant tags.

already_priced_in:
  If current market context is provided below, assess whether the news is likely
  ALREADY reflected in the current price (backward_report, well-known trend) or
  whether it represents a GENUINE NEW catalyst not yet priced in.
  Consider the 24h and 7d price changes — if the asset already moved significantly
  in the direction the news suggests, the information may be stale.
  Set directional_confidence LOW (< 0.3) for already-priced-in information.

Be objective. Do not let brand familiarity bias your scores.
Avoid extreme values unless clearly justified by the content.
"""

USER_PROMPT_V1 = """\
Document Title: {title}

{text_section}

{context_section}

Analyze this document and return the structured analysis.
"""


def _format_market_context(market_context: dict[str, Any]) -> str:
    """Render market context dict into a human-readable prompt section."""
    lines = ["Current Market Context:"]
    assets: list[dict[str, Any]] = market_context.get("assets", [])
    for asset in assets:
        symbol = asset.get("symbol", "?")
        price = asset.get("price")
        chg_24h = asset.get("change_pct_24h")
        chg_7d = asset.get("change_pct_7d")
        parts = [f"  {symbol}:"]
        if price is not None:
            parts.append(f"${price:,.2f}")
        if chg_24h is not None:
            parts.append(f"24h {chg_24h:+.1f}%")
        if chg_7d is not None:
            parts.append(f"7d {chg_7d:+.1f}%")
        lines.append(" | ".join(parts))
    regime = market_context.get("regime")
    if regime:
        lines.append(f"  Market regime: {regime}")
    lines.append(
        "  Consider: Is the news above ALREADY reflected in these price moves, "
        "or is it a genuinely new catalyst?"
    )
    return "\n".join(lines)


def format_user_prompt(
    title: str,
    text: str,
    context: dict[str, Any] | None = None,
) -> str:
    text_section = f"Document Content:\n{text}" if text.strip() else "(no content — title only)"
    context_parts: list[str] = []
    if context:
        tickers = context.get("tickers")
        if isinstance(tickers, list) and tickers:
            joined_tickers = ", ".join(str(ticker) for ticker in tickers)
            context_parts.append(f"Detected tickers: {joined_tickers}")

        source_type = context.get("source_type")
        if source_type:
            context_parts.append(f"Source type: {source_type}")

        market_context = context.get("market_context")
        if isinstance(market_context, dict) and market_context:
            context_parts.append(_format_market_context(market_context))
    context_section = "\n".join(context_parts) if context_parts else ""
    return USER_PROMPT_V1.format(
        title=title,
        text_section=text_section,
        context_section=context_section,
    ).strip()
