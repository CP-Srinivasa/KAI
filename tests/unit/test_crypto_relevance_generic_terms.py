"""The crypto-relevance gate must keep crypto news that names no asset (2026-09-23).

Production evidence (Pi, ``SOURCE_CRYPTO_RELEVANCE_GATE_MODE=enforce``,
08.–23.09.): of 3155 distinct titles the gate skipped, at least 790 (25 %) were
plainly about crypto — stablecoins, DeFi, DEX volume, crypto regulation, ETF
flows, Chinese-language crypto wires. Only asset names count as ``crypto``
keyword hits; "crypto", "stablecoin", "DeFi", "DEX" were ``keyword``/``topic``
hits or no hit at all, and Chinese terms did not exist. The titles below are
real skipped titles from that window.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.analysis.crypto_relevance import crypto_relevance_verdict
from app.analysis.keywords.engine import KeywordEngine, KeywordHit
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline
from app.core.domain.document import CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel


def _doc(title: str, text: str = "") -> CanonicalDocument:
    return CanonicalDocument(url="https://example.invalid/x", title=title, raw_text=text)


SKIPPED_IN_PRODUCTION = [
    "Stablecoins surge past $300 billion, forcing a reckoning over the dollar's future",
    "UK FCA sets crypto authorization guidance ahead of September application window",
    "Feds Have Now Frozen $938M in Scam Crypto After Telegram Market Bust",
    "Onchain credit reaches new all-time high as DeFi lending eclipses $41 billion",
    "Binance tops August 2026 exchange spot volume at $243B as crypto trading rebound",
    "Aster DEX lists Lisk and Power Ledger perpetual futures with 5x leverage",
    "Uniswap leads tokenized stock DeFi TVL growth, adds $83M in 30 days",
    "weETH surpasses $4B in deposits on Aave V3, becoming the protocol's second-largest",
    "Altcoins see highest 7-day inflow transactions in months as Binance leads the charge",
    "Grayscale files to rename Litecoin Trust as Grayscale Litecoin Trust ETF",
    "美东时间 9 月 16 日比特币现货 ETF 总净流出 2.96 亿美元",
    "泰国 SEC 拟限制稳定币向他人钱包充提，单日单向上限约 15 万美元",
    "Robinhood 8 月加密交易量环比增长 61%，平台总资产增至 3,837 亿美元",
    "Linera 宣布因资金不足停止运营，代币销售认购款已全额退还",
    "Eine Kryptobörse stoppt nach einer Störung vorübergehend alle Auszahlungen",
    "Nasdaq Invests $100 Million in Kraken Parent Payward",
    "Canary Staked TRX ETF launches as first US spot TRX fund with built-in staking",
    "印度金融情报部门向 15 家加密平台发出反洗钱违规通知",
    "CFTC 主席 Michael Selig：将依据现有法定权限推进加密规则",
]


@pytest.mark.parametrize("title", SKIPPED_IN_PRODUCTION)
def test_generic_crypto_news_without_asset_name_is_kept(title: str) -> None:
    relevant, reason = crypto_relevance_verdict(_doc(title), [])
    assert relevant is True, title
    assert reason == "generic_crypto_term"


@pytest.mark.parametrize(
    "text",
    [
        # The six asset-less development cases of the Jev corpus (#1052).
        "A fictional dollar-backed stablecoin issuer delays redemptions.",
        "A fictional cross-chain bridge reports unauthorized withdrawals from its token "
        "reserve contracts.",
        "A proposed rule changes how cryptocurrency custodians must segregate client holdings.",
        "Token holders of a decentralized exchange propose changing liquidity-provider fees.",
        "A fictional cryptocurrency exchange announces that it will delist a token pair.",
        "A fictional cryptocurrency wallet update fixes a transaction-signing vulnerability.",
    ],
)
def test_jev_corpus_false_negatives_are_kept(text: str) -> None:
    assert crypto_relevance_verdict(_doc("", text), [])[0] is True


def test_crypto_specific_topic_hit_counts() -> None:
    hits = [KeywordHit(canonical="Stablecoin", category="topic", occurrences=1)]
    assert crypto_relevance_verdict(_doc("t", "x"), hits) == (True, "crypto_topic_hit")


def test_general_topic_hit_still_does_not_count() -> None:
    hits = [
        KeywordHit(canonical="Regulation", category="topic", occurrences=3),
        KeywordHit(canonical="AI", category="topic", occurrences=2),
        KeywordHit(canonical="FED", category="topic", occurrences=1),
    ]
    assert crypto_relevance_verdict(_doc("t", "x"), hits) == (False, "no_crypto_signal")


@pytest.mark.parametrize(
    "title",
    [
        # Ambiguous words deliberately NOT treated as crypto terms.
        "A broken link on a library website has been repaired",
        "The class practised do re mi fa sol la ti in a singing lesson",
        "Students discuss the historical use of ether as an anesthetic",
        "Security token required for the new banking login",
        "Luxury wallet maker opens a flagship store",
        "Copper mining output falls in Chile",
        "City council approves new bridge over the river",
        "Researchers publish a cryptography breakthrough for post-quantum TLS",
        "Kryptographie-Konferenz in Bochum eröffnet",
        "Global refinery crunch drives diesel prices to record highs",
        "Investor optimism lifts European stocks as lightning storms hit the coast",
        "端到端加密通信应用发布新版本",
        "The local football league announces the dates of its opening matches",
    ],
)
def test_ambiguous_or_unrelated_words_stay_irrelevant(title: str) -> None:
    assert crypto_relevance_verdict(_doc(title), []) == (False, "no_crypto_signal")


def test_body_text_is_considered_not_only_the_title() -> None:
    doc = _doc("Markets update", "Analysts expect stablecoin inflows to continue this week.")
    assert crypto_relevance_verdict(doc, []) == (True, "generic_crypto_term")


def test_existing_precedence_is_unchanged() -> None:
    doc = CanonicalDocument(url="u", title="crypto", raw_text="x", tickers=["BTC"])
    assert crypto_relevance_verdict(doc, []) == (True, "has_tickers")


def _engine() -> KeywordEngine:
    return KeywordEngine(
        keywords=frozenset({"etf"}),
        watchlist_entries=[
            WatchlistEntry(
                symbol="BTC",
                name="Bitcoin",
                aliases=frozenset({"bitcoin"}),
                tags=(),
                category="crypto",
            )
        ],
        entity_aliases=[],
    )


def _provider() -> AsyncMock:
    from app.analysis.base.interfaces import LLMAnalysisOutput

    provider = AsyncMock()
    provider.provider_name = "openai"
    provider.model = "gpt-4o"
    provider.analyze = AsyncMock(
        return_value=LLMAnalysisOutput(
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            relevance_score=0.5,
            impact_score=0.3,
            confidence_score=0.5,
            novelty_score=0.3,
            spam_probability=0.01,
            market_scope=MarketScope.CRYPTO,
            affected_assets=[],
            short_reasoning="x",
            recommended_priority=3,
            actionable=False,
        )
    )
    return provider


@pytest.mark.asyncio
async def test_enforce_keeps_asset_less_stablecoin_news() -> None:
    provider = _provider()
    pipe = AnalysisPipeline(
        keyword_engine=_engine(), provider=provider, run_llm=True, crypto_gate_mode="enforce"
    )
    doc = CanonicalDocument(
        url="https://example.com/stable",
        title="Stablecoins surge past $300 billion",
        raw_text="The stablecoin market keeps growing as issuers expand reserves and payments.",
        tags=["markets", "payments", "dollar", "banks"],
    )
    await pipe.run(doc)
    provider.analyze.assert_awaited()
