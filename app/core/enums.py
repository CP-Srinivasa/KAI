"""Core Enumerations — shared across all modules."""

from __future__ import annotations

from enum import Enum


class SourceType(str, Enum):
    RSS_FEED = "rss_feed"
    WEBSITE = "website"
    NEWS_API = "news_api"
    SOCIAL_API = "social_api"
    YOUTUBE_CHANNEL = "youtube_channel"
    PODCAST_FEED = "podcast_feed"
    PODCAST_PAGE = "podcast_page"
    REFERENCE_PAGE = "reference_page"
    MARKET_DATA = "market_data"
    MANUAL_SOURCE = "manual_source"
    UNRESOLVED_SOURCE = "unresolved_source"


class SourceStatus(str, Enum):
    ACTIVE = "active"
    PLANNED = "planned"
    DISABLED = "disabled"
    REQUIRES_API = "requires_api"
    MANUAL_RESOLUTION = "manual_resolution"
    RSS_RESOLUTION_NEEDED = "rss_resolution_needed"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class AuthMode(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    BASIC = "basic"
    BEARER = "bearer"
    COOKIE = "cookie"


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class MarketScope(str, Enum):
    CRYPTO = "crypto"
    EQUITIES = "equities"
    MACRO = "macro"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    REGULATORY = "regulatory"
    EARNINGS = "earnings"
    MACRO_ECONOMIC = "macro_economic"
    TECHNICAL = "technical"
    SOCIAL_SENTIMENT = "social_sentiment"
    HACK_EXPLOIT = "hack_exploit"
    PARTNERSHIP = "partnership"
    LISTING_DELISTING = "listing_delisting"
    FORK_UPGRADE = "fork_upgrade"
    LEGAL = "legal"
    MERGER_ACQUISITION = "merger_acquisition"
    PRODUCT_LAUNCH = "product_launch"
    MARKET_MANIPULATION = "market_manipulation"
    WHALE_MOVEMENT = "whale_movement"
    OTHER = "other"
    UNKNOWN = "unknown"


class AlertType(str, Enum):
    BREAKING = "breaking"
    DIGEST = "digest"
    DAILY_BRIEF = "daily_brief"
    WATCHLIST_HIT = "watchlist_hit"
    ANOMALY = "anomaly"


class AlertChannel(str, Enum):
    TELEGRAM = "telegram"
    EMAIL = "email"
    WEBHOOK = "webhook"


class DocumentPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NOISE = "noise"


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Language(str, Enum):
    EN = "en"
    DE = "de"
    FR = "fr"
    ES = "es"
    IT = "it"
    PT = "pt"
    JA = "ja"
    ZH = "zh"
    KO = "ko"
    RU = "ru"
    UNKNOWN = "unknown"


class WatchlistCategory(str, Enum):
    CRYPTO = "crypto"
    EQUITIES = "equities"
    ETFS = "etfs"
    PERSONS = "persons"
    TOPICS = "topics"
    DOMAINS = "domains"


class DirectionHint(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class SignalUrgency(str, Enum):
    IMMEDIATE = "immediate"       # Act within hours
    SHORT_TERM = "short_term"     # 1–7 days
    MEDIUM_TERM = "medium_term"   # 1–4 weeks
    LONG_TERM = "long_term"       # Months
    MONITOR = "monitor"           # Watch, no urgency


class NarrativeLabel(str, Enum):
    REGULATORY_RISK = "regulatory_risk"
    INSTITUTIONAL_ADOPTION = "institutional_adoption"
    MARKET_CRASH = "market_crash"
    RECOVERY = "recovery"
    MACRO_SHIFT = "macro_shift"
    LIQUIDITY_CRISIS = "liquidity_crisis"
    TECH_UPGRADE = "tech_upgrade"
    ECOSYSTEM_GROWTH = "ecosystem_growth"
    SENTIMENT_SHIFT = "sentiment_shift"
    HACK_EXPLOIT = "hack_exploit"
    UNKNOWN = "unknown"
