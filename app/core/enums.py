from enum import Enum, StrEnum


class SourceType(StrEnum):
    RSS_FEED = "rss_feed"
    WEBSITE = "website"
    NEWS_API = "news_api"
    YOUTUBE_CHANNEL = "youtube_channel"
    PODCAST_FEED = "podcast_feed"
    PODCAST_PAGE = "podcast_page"
    REFERENCE_PAGE = "reference_page"
    SOCIAL_API = "social_api"
    MANUAL_SOURCE = "manual_source"
    UNRESOLVED_SOURCE = "unresolved_source"
    NEWS_DOMAIN = "news_domain"


class AnalysisSource(StrEnum):
    RULE = "rule"
    INTERNAL = "internal"
    EXTERNAL_LLM = "external_llm"


class ExecutionMode(StrEnum):
    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PLANNED = "planned"
    DISABLED = "disabled"
    REQUIRES_API = "requires_api"
    MANUAL_RESOLUTION = "manual_resolution"
    UNRESOLVED = "unresolved"


class SentimentLabel(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class NarrativeLabel(StrEnum):
    INSTITUTIONAL_ADOPTION = "institutional_adoption"
    REGULATORY_RISK = "regulatory_risk"
    TECH_UPGRADE = "tech_upgrade"
    MACRO_SHIFT = "macro_shift"
    MARKET_CRASH = "market_crash"
    LIQUIDITY_CRISIS = "liquidity_crisis"
    HACK_EXPLOIT = "hack_exploit"
    ECOSYSTEM_GROWTH = "ecosystem_growth"
    WHALE_ACTIVITY = "whale_activity"
    UNKNOWN = "unknown"


class MarketScope(StrEnum):
    CRYPTO = "crypto"
    EQUITIES = "equities"
    MACRO = "macro"
    ETF = "etf"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class AuthMode(StrEnum):
    NONE = "none"
    API_KEY = "api_key"
    OAUTH = "oauth"
    BASIC = "basic"
    MANUAL = "manual"


class SortBy(StrEnum):
    PUBLISHED_AT = "published_at"
    RELEVANCE = "relevance"
    IMPACT = "impact"
    SENTIMENT = "sentiment"
    CREDIBILITY = "credibility"


class DocumentType(StrEnum):
    ARTICLE = "article"
    PODCAST_EPISODE = "podcast_episode"
    YOUTUBE_VIDEO = "youtube_video"
    SOCIAL_POST = "social_post"
    RESEARCH_REPORT = "research_report"
    REFERENCE = "reference"
    UNKNOWN = "unknown"


class DocumentStatus(str, Enum):  # noqa: UP042
    """Lifecycle status of a CanonicalDocument through the pipeline.

    Rules:
    - must always be explicit
    - must not be implicit
    - must be updated at each stage

    Transitions (one-way, no rollback):

        [adapter]         [ingest]          [analysis]
        PENDING  ──────►  PERSISTED  ─────►  ANALYZED
                                    │
                                    ├──────► DUPLICATE   (dedup gate)
                                    │
                                    └──────► FAILED      (ingest or analysis error)

    Owners (the ONLY code that may set each status):
    - PENDING    → prepare_ingested_document()        in document_ingest.py
    - PERSISTED  → DocumentRepository.save_document() in document_repo.py
    - ANALYZED   → DocumentRepository.update_analysis()     in document_repo.py
    - DUPLICATE  → DocumentRepository.mark_duplicate()      in document_repo.py
    - FAILED     → repo.update_status(FAILED) — called from:
                   persist_fetch_result() (ingest errors),
                   run_rss_pipeline() (analysis/DB errors),
                   analyze_pending CLI (analysis/DB errors)
    """

    PENDING = "pending"
    PERSISTED = "persisted"
    ANALYZED = "analyzed"
    FAILED = "failed"
    DUPLICATE = "duplicate"
