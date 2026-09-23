"""Pre-analysis crypto-relevance gate (2026-06-16).

Pure, fail-open verdict used by :class:`app.analysis.pipeline.AnalysisPipeline`
to skip the LLM for documents that carry no tradable crypto signal at all.

Why: an empirical sweep of 3000 recently-analyzed documents found ~59% with no
resolved crypto asset (``no_symbol``), dominated by the cryptobriefing site-wide
feed (70% of its docs asset-less: sports / geopolitics / general-finance news).
Of those ``no_symbol`` docs, ~99% had ZERO crypto-category keyword hits — they
were never going to become a crypto trade, yet still consumed LLM analysis
budget. This gate skips that spend.

Fail-OPEN by design: the gate only declares a document *irrelevant* when it has
NO crypto signal whatsoever — no resolved ticker, no crypto asset tag, and not a
single crypto-category keyword hit. Any crypto signal keeps the document. The
goal is to drop obvious non-crypto noise, never to risk dropping a genuine
signal; equity/etf/macro hits do NOT count as crypto-relevance (they are not
tradable crypto assets in this pipeline).

2026-09-23: crypto news that names no asset is a crypto signal too. With the
gate in ``enforce`` on the Pi, at least 790 of 3155 distinct skipped titles
(08.–23.09.) were plainly crypto — stablecoins, DeFi, DEX volume, crypto
regulation, ETF flows, Chinese-language wires — because only asset names are
``crypto`` hits. Two further signals now keep a document: a hit on a
crypto-specific topic (DeFi, Stablecoin) and an unambiguous generic crypto term
in title or text. Ambiguous words (token, wallet, bridge, mining, ether, link,
sol, Layer2's "optimism"/"lightning", Chinese "加密" alone = "encrypt") are
deliberately excluded: a false keep costs one LLM call, a false skip loses the
signal, but the gate must still drop obvious noise.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.analysis.keywords.engine import KeywordHit
from app.core.domain.document import CanonicalDocument

#: Topics from ``monitor/watchlists.yml`` whose every alias is crypto-specific.
CRYPTO_TOPICS = frozenset({"DeFi", "Stablecoin"})

_GENERIC_CRYPTO_TERMS = re.compile(
    r"\b(?:"
    r"crypto(?:s|currenc(?:y|ies)|[- ]?assets?)?|stablecoins?|defi|dex(?:es)?|"
    r"blockchains?|web3|cross-chain|on-?chain|altcoins?|memecoins?|nfts?|"
    r"tokeni[sz](?:e|es|ed|ing|ation)|decentrali[sz]ed (?:finance|exchanges?)|"
    r"binance|coinbase|kraken|bybit|okx|uniswap|aave|hyperliquid|grayscale|microstrategy|"
    r"tether|usdt|usdc|weeth|steth|litecoin|dogecoin|satoshi|staking|"
    r"krypto(?!gra[fp]h)\w*"
    r")\b"
    # Chinese has no word boundaries.
    r"|比特币|以太坊|稳定币|代币|链上|区块链|山寨币|币安|去中心化|狗狗币|莱特币|"
    r"虚拟货币|虚拟资产|数字资产|"
    r"加密(?:货币|资产|交易|市场|行业|领域|基金|监管|公司|企业|钱包|支付|平台|规则|服务|合约|政策|法案|牌照|投资|借贷|期货)",
    re.IGNORECASE,
)


def _mentions_generic_crypto(doc: CanonicalDocument) -> bool:
    parts = (
        getattr(doc, "title", None),
        getattr(doc, "subtitle", None),
        getattr(doc, "cleaned_text", None) or getattr(doc, "raw_text", None),
    )
    return bool(_GENERIC_CRYPTO_TERMS.search(" ".join(p for p in parts if p)))


def crypto_relevance_verdict(
    doc: CanonicalDocument,
    keyword_hits: Iterable[KeywordHit],
) -> tuple[bool, str]:
    """Return ``(relevant, reason)`` for the pre-analysis crypto-relevance gate.

    ``relevant`` is True (fail-open) when ANY of these hold:
      - the document already has a resolved ticker (``doc.tickers``), or
      - a crypto asset tag (``doc.crypto_assets``), or
      - at least one keyword hit in the ``crypto`` category, or
      - a hit on a crypto-specific topic (:data:`CRYPTO_TOPICS`), or
      - an unambiguous generic crypto term in title, subtitle or text.

    Otherwise ``relevant`` is False with reason ``"no_crypto_signal"`` — the
    document carries no crypto signal and is a candidate to skip.
    """
    if list(getattr(doc, "tickers", None) or []):
        return True, "has_tickers"
    if list(getattr(doc, "crypto_assets", None) or []):
        return True, "has_crypto_assets"
    hits = list(keyword_hits)
    for hit in hits:
        if getattr(hit, "category", None) == "crypto":
            return True, "crypto_keyword_hit"
    for hit in hits:
        if getattr(hit, "category", None) == "topic" and hit.canonical in CRYPTO_TOPICS:
            return True, "crypto_topic_hit"
    if _mentions_generic_crypto(doc):
        return True, "generic_crypto_term"
    return False, "no_crypto_signal"


__all__ = ["CRYPTO_TOPICS", "crypto_relevance_verdict"]
