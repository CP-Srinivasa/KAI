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


class EntryMode(StrEnum):
    """Autonomous-loop entry-cadence gate (Goal 2026-06-01: Entry-Safety-Mode).

    Orthogonal to ``ExecutionMode`` (paper vs live *venue*): this governs whether
    — and at what cadence — the TradingLoop is allowed to OPEN new risk-increasing
    positions. Exit- and risk-reduction paths are never gated by this.

    Rationale: the current entry signal has a demonstrably negative cost-adjusted
    edge (2026-06-01: 22 closes lose ~-283 USD even at 0 bp fees; 4/22 gross
    winners). New full-cadence entries must not be treated as a normally tradable
    signal until a cost-adjusted edge gate is passed.

    Ladder (least → most permissive):
      - DISABLED:     no new autonomous entries at all (exits still managed).
      - PAPER_PREMIUM_LIMITED: ONLY the premium paper route is open (with
                      route limits); autonomous loop + learning feeder closed.
                      (#181 explicit-mode consolidation, Sprint S3 2026-06-11.)
      - PAPER_LEARNING: premium paper + real-analysis paper-learning routes
                      open (with route limits); autonomous loop closed.
      - PAPER:        paper entries allowed (legacy default; preserves behavior).
      - PROBE:        throttled paper entries to gather forward-edge evidence
                      (rate/turnover throttle lands with the churn-killer sprint).
      - LIVE_LIMITED: live entries with hard drawdown/churn caps (requires
                      ExecutionMode.LIVE + edge gate >= 0.80).
      - LIVE_NORMAL:  full-cadence live (requires edge gate >= 0.95 + OOS stable).

    Never auto-promote to LIVE_NORMAL — that is an explicit operator decision.
    Per-route truth lives in ``app.execution.entry_policy.resolve_entry_policy``
    — the properties below stay the coarse kill-switch layer.
    """

    DISABLED = "disabled"
    PAPER_PREMIUM_LIMITED = "paper_premium_limited"
    PAPER_LEARNING = "paper_learning"
    PAPER = "paper"
    PROBE = "probe"
    LIVE_LIMITED = "live_limited"
    LIVE_NORMAL = "live_normal"

    @property
    def allows_autonomous_loop_entry(self) -> bool:
        """True when the AUTONOMOUS loop may open NEW positions in this mode.

        ``DISABLED`` is the hard stop; the two limited paper modes
        (``PAPER_PREMIUM_LIMITED``/``PAPER_LEARNING``) keep the loop closed by
        design — they open only their named bridge/feeder routes (#181).
        ``PROBE``/``LIVE_LIMITED`` allow entries but are throttled by the
        churn-killer (separate gate); that throttle does not live here so this
        property stays a single, testable kill-switch.
        """
        return self in (
            EntryMode.PAPER,
            EntryMode.PROBE,
            EntryMode.LIVE_LIMITED,
            EntryMode.LIVE_NORMAL,
        )

    @property
    def allows_risk_increasing_entry(self) -> bool:
        """Source-AGNOSTIC kill-switch: True when ANY path may OPEN new
        risk-increasing exposure (autonomous loop, premium/promoted bridge,
        future live wiring). ``DISABLED`` is a hard global stop; exits and
        risk-reductions are never gated by this.

        ``disabled`` means *no new entries anywhere* (modulo the explicitly
        armed three-arm migration aliases resolved in ``entry_policy``), not
        merely no autonomous entries (2026-06-02 safety-contract: a partial
        kill-switch is not a kill-switch). The two limited paper modes return
        True here — SOME risk-increasing entries are allowed — while the
        per-route refinement (which routes exactly) lives in
        ``app.execution.entry_policy`` (#181).
        """
        return self is not EntryMode.DISABLED

    @property
    def is_live(self) -> bool:
        """True for the two live entry modes (require ExecutionMode.LIVE)."""
        return self in (EntryMode.LIVE_LIMITED, EntryMode.LIVE_NORMAL)

    @property
    def is_paper_learning(self) -> bool:
        """True for paper entry modes that feed the paper-learning stream.

        Goal 2026-06-10: a mode that opens NEW risk-increasing exposure on a
        NON-live route — i.e. PAPER and PROBE. DISABLED (no entries) and the two
        LIVE modes are all False. Used to gate the bearish paper-learning
        relaxation: bearish directional signals are only un-blocked when the
        active entry mode is paper-learning, never for live or disabled.
        """
        return self.allows_risk_increasing_entry and not self.is_live


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PLANNED = "planned"
    DISABLED = "disabled"
    REQUIRES_API = "requires_api"
    MANUAL_RESOLUTION = "manual_resolution"
    UNRESOLVED = "unresolved"
    # Source-lifecycle system (2026-06-23): autonomous rotation/ranking.
    PROBATION = "probation"  # newly onboarded, shadow/eval, never trust-boosted
    SILENT = "silent"  # stopped delivering signals (auto-detected)
    ARCHIVED = "archived"  # rotated out — only via replace-when-ready gate
    PINNED = "pinned"  # durable top performer, never auto-demoted


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
