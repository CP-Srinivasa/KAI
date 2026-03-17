from enum import StrEnum


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
