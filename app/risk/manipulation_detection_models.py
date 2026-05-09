"""Manipulation Detection Engine — typed inputs, outputs, configuration.

Detects manipulation across three domains:
- Social   — coordinated shilling, fake engagement, bot networks
- Market   — wash trading, spoofing, pump-and-dump
- On-chain — abnormal wallet flows, insider-like behavior

Per-source output: trust_score, manipulation_probability, historical_reliability.

The engine is *analytical*, not enforcement: outputs feed into source-quality
weighting (e.g. portfolio_optimizer.signal_quality) and downstream alerting.
All hard gating remains with `app/risk/engine.py::RiskEngine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# --- Pattern constants (stable JSON keys) ---

PATTERN_COORDINATED_SHILLING: Final[str] = "coordinated_shilling"
PATTERN_FAKE_ENGAGEMENT: Final[str] = "fake_engagement"
PATTERN_BOT_NETWORK: Final[str] = "bot_network"
PATTERN_WASH_TRADING: Final[str] = "wash_trading"
PATTERN_SPOOFING: Final[str] = "spoofing"
PATTERN_PUMP_AND_DUMP: Final[str] = "pump_and_dump"
PATTERN_ABNORMAL_WALLET: Final[str] = "abnormal_wallet"
PATTERN_INSIDER_BEHAVIOR: Final[str] = "insider_behavior"

ALL_PATTERNS: Final[tuple[str, ...]] = (
    PATTERN_COORDINATED_SHILLING,
    PATTERN_FAKE_ENGAGEMENT,
    PATTERN_BOT_NETWORK,
    PATTERN_WASH_TRADING,
    PATTERN_SPOOFING,
    PATTERN_PUMP_AND_DUMP,
    PATTERN_ABNORMAL_WALLET,
    PATTERN_INSIDER_BEHAVIOR,
)

# --- Source-type constants ---
SOURCE_SOCIAL_ACCOUNT: Final[str] = "social_account"
SOURCE_WALLET: Final[str] = "wallet"
SOURCE_MARKET_ACCOUNT: Final[str] = "market_account"


# ============================================================================
# Inputs
# ============================================================================


@dataclass(frozen=True)
class Post:
    """A single social-media post."""

    post_id: str
    source_id: str               # the account that posted
    timestamp_utc: str           # ISO 8601
    text: str
    asset_mentions: tuple[str, ...] = ()
    sentiment_score: float = 0.0  # −1 (bearish) … +1 (bullish)
    engagement_count: int = 0    # likes + reposts + replies
    follower_count_at_post: int = 0


@dataclass(frozen=True)
class Account:
    """A social-media account profile snapshot."""

    account_id: str
    platform: str = "twitter"
    follower_count: int = 0
    following_count: int = 0
    account_age_days: int = 0
    post_count: int = 0
    has_default_avatar: bool = False
    bio_length: int = 0
    verified: bool = False


@dataclass(frozen=True)
class Trade:
    """A single market trade."""

    trade_id: str
    symbol: str
    timestamp_utc: str
    price: float
    size: float
    side: str                       # "buy" | "sell"
    buyer_id: str | None = None
    seller_id: str | None = None
    venue: str = "binance"


@dataclass(frozen=True)
class OrderEvent:
    """A single order lifecycle event (place / cancel / fill)."""

    event_id: str
    symbol: str
    timestamp_utc: str
    account_id: str | None
    side: str                       # "buy" | "sell"
    price: float
    size: float
    event_type: str                 # "placed" | "canceled" | "filled" | "partially_filled"


@dataclass(frozen=True)
class WalletTx:
    """An on-chain transfer."""

    tx_id: str
    timestamp_utc: str
    from_wallet: str
    to_wallet: str
    asset: str
    amount: float
    usd_value: float = 0.0


@dataclass(frozen=True)
class PriceBar:
    """Aggregated OHLCV bar for pump-and-dump scanning."""

    symbol: str
    timestamp_utc: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class HistoricalCall:
    """A past call by a source plus its realized outcome."""

    source_id: str
    timestamp_utc: str
    asset: str
    direction: str                  # "bullish" | "bearish" | "neutral"
    realized_pnl_pct_30d: float | None = None


# ============================================================================
# Outputs
# ============================================================================


@dataclass(frozen=True)
class SourceTrustReport:
    """Per-source trust assessment."""

    source_id: str
    source_type: str                # SOURCE_*
    trust_score: float              # 0..1, higher = more trustworthy
    manipulation_probability: float  # 0..1, higher = manipulative
    historical_reliability: float   # 0..1, accuracy track record (0.5 = unknown)
    detected_patterns: list[str] = field(default_factory=list)  # PATTERN_*
    pattern_evidence: dict[str, float] = field(default_factory=dict)
    sample_size: int = 0
    notes: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ManipulationReport:
    """Complete manipulation surveillance report for a window."""

    timestamp_utc: str
    target_symbol: str | None

    # Aggregated event counts / signatures (None when input unavailable)
    coordinated_shilling_events: int = 0
    fake_engagement_events: int = 0
    bot_networks_detected: int = 0
    wash_trading_signature: float | None = None    # 0..1
    spoofing_signature: float | None = None        # 0..1
    pump_and_dump_signature: float | None = None   # 0..1
    abnormal_wallet_flows: int = 0
    insider_behavior_signature: float | None = None  # 0..1

    # Per-source assessments
    sources: list[SourceTrustReport] = field(default_factory=list)

    # Diagnostics
    inputs_summary: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    inputs_hash: str = ""

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "manipulation_detection",
            "timestamp_utc": self.timestamp_utc,
            "target_symbol": self.target_symbol,
            "events": {
                "coordinated_shilling": self.coordinated_shilling_events,
                "fake_engagement": self.fake_engagement_events,
                "bot_networks": self.bot_networks_detected,
                "abnormal_wallet_flows": self.abnormal_wallet_flows,
            },
            "signatures": {
                "wash_trading": self.wash_trading_signature,
                "spoofing": self.spoofing_signature,
                "pump_and_dump": self.pump_and_dump_signature,
                "insider_behavior": self.insider_behavior_signature,
            },
            "sources": [
                {
                    "source_id": s.source_id,
                    "source_type": s.source_type,
                    "trust_score": s.trust_score,
                    "manipulation_probability": s.manipulation_probability,
                    "historical_reliability": s.historical_reliability,
                    "detected_patterns": list(s.detected_patterns),
                    "pattern_evidence": dict(s.pattern_evidence),
                    "sample_size": s.sample_size,
                    "notes": dict(s.notes),
                }
                for s in self.sources
            ],
            "inputs_summary": dict(self.inputs_summary),
            "warnings": list(self.warnings),
            "inputs_hash": self.inputs_hash,
        }


# ============================================================================
# Configuration
# ============================================================================


@dataclass(frozen=True)
class ManipulationDetectionConfig:
    """Detection thresholds. All defaults are crypto-realistic baselines.

    Operators should calibrate against their own historical false-positive
    tolerance — these numbers are deliberately conservative (high precision,
    moderate recall).
    """

    # Coordinated shilling
    shilling_text_similarity_threshold: float = 0.70
    shilling_time_window_seconds: int = 300
    shilling_min_cluster_size: int = 3

    # Fake engagement
    engagement_z_threshold: float = 3.0
    engagement_to_follower_ratio_threshold: float = 0.50

    # Bot network
    bot_max_account_age_days: int = 30          # young accounts = suspect
    bot_min_post_count_for_analysis: int = 10
    bot_interval_entropy_threshold: float = 0.50  # low → too regular → bot
    bot_default_avatar_weight: float = 0.20
    bot_min_follower_following_ratio: float = 0.10

    # Wash trading
    wash_volume_to_impact_threshold: float = 1000.0  # vol / |Δprice%| > this → wash
    wash_min_trades: int = 50
    wash_self_trade_pair_threshold: float = 0.10  # share of trades that are self-pairs

    # Spoofing
    spoof_cancel_ratio_threshold: float = 0.95
    spoof_size_multiplier_threshold: float = 5.0  # order size vs. mean book size
    spoof_min_orders: int = 20

    # Pump-and-dump
    pump_window_bars: int = 24
    pump_price_increase_threshold: float = 0.30
    dump_window_bars: int = 12
    dump_price_decrease_threshold: float = 0.20
    pump_volume_zscore_threshold: float = 3.0

    # Abnormal wallet flows
    wallet_volume_z_threshold: float = 4.0
    wallet_dormancy_days: int = 90
    wallet_funnel_min_sources: int = 5  # ≥ N sources → 1 destination = funnel

    # Insider-like
    insider_lead_window_bars: int = 48
    insider_correlation_threshold: float = 0.30
    insider_min_observations: int = 30

    # Historical reliability
    history_min_samples: int = 5
    history_neutral_score: float = 0.50
    history_smoothing_alpha: float = 0.10  # Bayesian smoothing toward neutral

    # Aggregation weights — how strongly each pattern contributes to the
    # combined manipulation_probability (per source). Sum need not equal 1.
    severity_weight_shilling: float = 0.20
    severity_weight_fake_engagement: float = 0.10
    severity_weight_bot: float = 0.15
    severity_weight_wash: float = 0.15
    severity_weight_spoof: float = 0.10
    severity_weight_pump: float = 0.10
    severity_weight_wallet: float = 0.10
    severity_weight_insider: float = 0.10

    def __post_init__(self) -> None:
        if not (0.0 < self.shilling_text_similarity_threshold <= 1.0):
            raise ValueError("shilling_text_similarity_threshold must be in (0, 1]")
        if self.shilling_min_cluster_size < 2:
            raise ValueError("shilling_min_cluster_size must be >= 2")
        if not (0.0 < self.bot_interval_entropy_threshold <= 1.0):
            raise ValueError("bot_interval_entropy_threshold must be in (0, 1]")
        if not (0.0 <= self.spoof_cancel_ratio_threshold <= 1.0):
            raise ValueError("spoof_cancel_ratio_threshold must be in [0, 1]")
