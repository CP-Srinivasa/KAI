"""Directional alert eligibility helpers.

Fail-closed rule:
- Directional sentiment without a tradeable crypto asset mapping is ineligible.
- Eligible assets must resolve to supported CoinGecko symbols.
- Weak signals (low sentiment magnitude or low impact) are blocked to reduce
  false positives in hit-rate tracking (D-111).
- Reactive price narratives (bearish titles describing past moves) are blocked
  to reduce false-positive pollution (D-113).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.market_data.coingecko_adapter import _resolve_symbol

_log = get_logger(__name__)

_DIRECTIONAL_SENTIMENTS = frozenset({"bullish", "bearish"})
BLOCK_REASON_MISSING_ASSETS = "missing_affected_assets"
BLOCK_REASON_UNSUPPORTED_ASSETS = "unsupported_or_non_crypto_assets"
BLOCK_REASON_WEAK_SIGNAL = "weak_directional_signal"
BLOCK_REASON_REACTIVE_NARRATIVE = "reactive_price_narrative"
BLOCK_REASON_MAJORITY_NON_CRYPTO = "majority_non_crypto_assets"
BLOCK_REASON_LOW_DIRECTIONAL_CONFIDENCE = "low_directional_confidence"
BLOCK_REASON_PRICE_TREND_DIVERGENCE = "price_trend_divergence"
BLOCK_REASON_NOT_ACTIONABLE = "not_actionable"
BLOCK_REASON_LOW_PRIORITY = "low_priority"
BLOCK_REASON_BEARISH_DISABLED = "bearish_directional_disabled"
BLOCK_REASON_LOW_PRECISION_SOURCE = "low_precision_source"
BLOCK_REASON_NAKED_ASSET = "naked_asset_no_trading_pair"
BLOCK_REASON_PROMO_PATTERN = "promotional_or_speculative_listicle"

# D-133/D-139: Sources with persistently low directional precision.
# Based on 229 resolved directional outcomes (2026-04-14 after D-138 backfill):
#   decrypt:          28.57% precision (10 hit / 25 miss, 35 resolved)
#   bitcoin_magazine: 47.83% precision (11 hit / 12 miss, 23 resolved)
#   unknown:          17.50% precision (14 hit / 66 miss, 80 resolved)
# The ``unknown`` token is the lower-cased fallback used by _load_doc_metadata
# when both source_name and provider columns are null in the DB — typically
# legacy records dispatched before source attribution was wired up, or records
# originating from pipelines that never set source_name.  Their signal quality
# is indistinguishable from noise (17.5% ≈ random with bullish bias).
# Block from directional eligibility until source-specific signal quality
# improves or the record can be re-attributed.
_LOW_PRECISION_SOURCES: frozenset[str] = frozenset(
    {
        "decrypt",
        "bitcoin_magazine",
        "unknown",
        "tradingview_webhook",
    }
)


# V-DB4c 2026-05-08 + V-DB5 Calibration 2026-05-08:
# Soft-Source-Confidence-Adjuster. Operator pflegt monitor/source_watch.txt
# (eine source_name pro Zeile, lower-case). Eine Source auf der Liste bekommt
# priority -= 1 in der Eligibility-Pruefung — damit greift der LOW_PRIORITY-Gate
# (P<=7 → block) härter und schwache Sources verdraengen sich weniger ins
# Forward-Signal. Datei nicht vorhanden oder leer → kein Effekt (default-off).
#
# V-DB5 Calibration (audit C-4 / F-006):
#   - Vorher @lru_cache(maxsize=1) → Operator-File-Edits unsichtbar bis Worker-
#     Restart. Jetzt: mtime-basierter Cache. Datei-Edit ist beim naechsten
#     Eligibility-Check sofort wirksam (innerhalb der Stat-call-Latenz, ~ms).
#   - Vorher relative Path → CWD-Abhaengigkeit (Settings-monitor_dir wurde
#     ignoriert). Jetzt: Settings-monitor_dir mit Lazy-Load-Fallback auf
#     "monitor" wenn Settings nicht verfügbar (z.B. in Tests).
_watch_cache: dict = {
    "mtime": -1.0,
    "data": frozenset(),
    "path": None,
    "resolved_path": None,
}


def _resolve_watchlist_path() -> Path:
    """Resolve monitor/source_watch.txt via get_settings, fallback to relative."""
    cached_path = _watch_cache.get("resolved_path")
    if isinstance(cached_path, str) and cached_path:
        return Path(cached_path)

    try:
        from app.core.settings import get_settings  # lazy to avoid import cycles

        path = Path(get_settings().monitor_dir) / "source_watch.txt"
    except Exception:  # noqa: BLE001 — settings not available (tests, early-boot)
        path = Path("monitor") / "source_watch.txt"

    _watch_cache["resolved_path"] = str(path)
    return path


def _load_source_watchlist() -> frozenset[str]:
    """Read source_watch.txt with mtime-based reload (no Worker-restart needed)."""
    path = _resolve_watchlist_path()

    try:
        mtime = path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        return _watch_cache["data"] if _watch_cache["path"] == str(path) else frozenset()

    if _watch_cache["path"] == str(path) and mtime == _watch_cache["mtime"]:
        return _watch_cache["data"]

    if not path.exists():
        _watch_cache.update({"mtime": 0.0, "data": frozenset(), "path": str(path)})
        return _watch_cache["data"]

    sources: set[str] = set()
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            sources.add(line.split("|", 1)[0].strip().lower())
    except OSError:
        return _watch_cache["data"] if _watch_cache["path"] == str(path) else frozenset()

    data = frozenset(sources)
    _watch_cache.update({"mtime": mtime, "data": data, "path": str(path)})
    _log.info(
        "source_watchlist.loaded",
        count=len(data),
        path=str(path),
        sources=sorted(data) if len(data) <= 10 else f"{len(data)}_entries",
    )
    return data


def _invalidate_source_watchlist_cache() -> None:
    """Test/Reload-Hook — clears the watchlist cache."""
    _watch_cache.update(
        {
            "mtime": -1.0,
            "data": frozenset(),
            "path": None,
            "resolved_path": None,
        }
    )


# D-142: Bearish directional disabled based on 50 eligible resolved outcomes.
# Bearish precision: 4% (1 hit / 24 miss). Bullish precision: 76% (19/25).
# Bearish news in trending markets is almost never price-predictive — reactive
# narratives describe past moves, and even actor-action bearish titles fail.
# Disable bearish directional tracking entirely until market-context-aware
# analysis (regime detection, real-time sentiment) can make bearish viable.
BEARISH_DIRECTIONAL_DISABLED = True

# D-116 / D-119: Minimum directional confidence from LLM analysis.
# Asymmetric thresholds (D-121): bearish alerts had 4% precision (1/25)
# vs bullish 75% (18/24).  Bearish requires near-certain catalyst events
# (hacks, bans, exploits); bullish threshold stays at proven level.
# D-122: Bearish confidence raised from 0.92→0.95 based on 22% precision
# (vs bullish 50%).  Only near-certain adverse events pass.
MIN_DIRECTIONAL_CONFIDENCE_BULLISH = 0.8
MIN_DIRECTIONAL_CONFIDENCE_BEARISH = 0.95

# Directional strength thresholds — alerts below these are excluded from
# directional hit-rate tracking to reduce false-positive pollution.
# D-119: Impact raised from 0.55 to 0.60.  Empirical: low-impact
# directional signals (P7/P10 cluster) had <25% precision.
# D-122: Bearish impact raised from 0.75→0.80 based on 22% precision.
MIN_SENTIMENT_MAGNITUDE = 0.55
MIN_IMPACT_SCORE_BULLISH = 0.60
MIN_IMPACT_SCORE_BEARISH = 0.80

# D-113: Reactive price narrative patterns.
# Bearish alerts whose titles match these describe *past* price moves,
# not predictive events.  Empirical FP rate: 100% on P9/P10 bearish.
# Hits come from actor-action titles ("Firm X sells Y"), never from
# reactive market commentary ("Bitcoin drops/slides/dips/collapses").
_REACTIVE_BEARISH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:drops?|dropped|dropping)\b", re.IGNORECASE),
    re.compile(r"\b(?:dips?|dipped|dipping)\b", re.IGNORECASE),
    re.compile(r"\b(?:slides?|slid|sliding)\b", re.IGNORECASE),
    re.compile(r"\b(?:sinks?|sank|sunk|sinking)\b", re.IGNORECASE),
    re.compile(r"\b(?:collapses?|collapsed|collapsing)\b", re.IGNORECASE),
    re.compile(r"\b(?:plunges?|plunged|plunging)\b", re.IGNORECASE),
    re.compile(r"\b(?:crashes?|crashed|crashing)\b", re.IGNORECASE),
    re.compile(r"\b(?:tumbles?|tumbled|tumbling)\b", re.IGNORECASE),
    re.compile(r"\b(?:falls?|fell|falling)\b", re.IGNORECASE),
    re.compile(r"\b(?:sell[\s-]?off|selloff|sold off)\b", re.IGNORECASE),
    re.compile(r"\b(?:wipeout|wiped out)\b", re.IGNORECASE),
    re.compile(r"\b(?:liquidation)s?\b", re.IGNORECASE),
    re.compile(r"\bhits?\s+(?:new\s+)?(?:low|monthly low|weekly low)\b", re.IGNORECASE),
    re.compile(r"\bextreme\s+fear\b", re.IGNORECASE),
    re.compile(r"\boutflows?\b", re.IGNORECASE),
    re.compile(r"\bweakens?\b", re.IGNORECASE),
    re.compile(r"\bheading\s+for\s+.*(?:collapse|crash|drop)\b", re.IGNORECASE),
)


# D-115: Reactive bullish price narrative patterns.
# Symmetric to bearish: bullish titles describing past price moves
# ("surges", "rallies", "soars") have the same FP problem — the move
# already happened, making the "prediction" a lagging indicator.
_REACTIVE_BULLISH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:surges?|surged|surging)\b", re.IGNORECASE),
    re.compile(r"\b(?:rallies|rallied|rallying|rally)\b", re.IGNORECASE),
    re.compile(r"\b(?:soars?|soared|soaring)\b", re.IGNORECASE),
    re.compile(r"\b(?:jumps?|jumped|jumping)\b", re.IGNORECASE),
    re.compile(r"\b(?:spikes?|spiked|spiking)\b", re.IGNORECASE),
    re.compile(r"\b(?:rockets?|rocketed|rocketing)\b", re.IGNORECASE),
    re.compile(r"\b(?:moons?|mooned|mooning)\b", re.IGNORECASE),
    re.compile(r"\b(?:skyrockets?|skyrocketed|skyrocketing)\b", re.IGNORECASE),
    re.compile(r"\b(?:pumps?|pumped|pumping)\b", re.IGNORECASE),
    re.compile(r"\b(?:explodes?|exploded|exploding)\b", re.IGNORECASE),
    re.compile(
        r"\bhits?\s+(?:new\s+)?(?:high|all.time.high|ATH|monthly high|weekly high)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:breakout|broke out|breaking out)\b", re.IGNORECASE),
    # V-DB5 Calibration 2026-05-08 (audit B-A2):
    # "inflows" allein blockt ETF-Substanz-News ("Bitcoin spot ETF inflows hit $245M"),
    # die direktional handelbar ist (kein lagging price-move). Pattern entfernt;
    # echte reactive bullish narratives sind durch surge/rally/jump abgedeckt.
)


# V-DB4b 2026-05-08: Promotional / speculative listicle patterns.
# Empirisch dominante Spam-Familie aus NewsData/AMBCrypto/Aggregator-Reposts:
# Pre-Sale-Promos ("Pepeto Eyes 100x Before Listing"), Listicles ("Top 3 Cryptos
# to Buy Now"), spekulative Preis-Ziele ("Could hit $X by July 2026"), AI-
# generierte Pump-Narrative ohne Substanz. Diese Headlines haben keine
# vorhersagbare Marktbewegung — sie SIND die Bewegung, die jemand erzeugen will.
# Hit-Rate dieser Familie historisch <10%, sie verzerrt Source-Hit-Rates ohne
# eigene Evidenz. Filter wirkt VOR den reactive-narrative-Filtern, weil ein
# Promo-Titel z.B. "surges" enthalten kann ohne darum reactive zu sein.
_PROMO_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Pre-Sale / Listing-Pump-Narrative — diskriminative Markers, keine FP bekannt.
    re.compile(r"\bpresale\b", re.IGNORECASE),
    re.compile(r"\bpre[-\s]sale\b", re.IGNORECASE),
    re.compile(r"\bbefore\s+(?:binance\s+)?listing\b", re.IGNORECASE),
    re.compile(r"\bbest\s+crypto\s+(?:to\s+buy|presale)\b", re.IGNORECASE),
    # Listicle-Marker — "Top N Cryptos To Buy" ist klar Promo, "Top 5 facts" nicht.
    re.compile(r"\btop\s+\d+\s+(?:crypto|coin|altcoin|token)s?\s+to\s+buy\b", re.IGNORECASE),
    re.compile(r"\b(?:could\s+be\s+)?one\s+of\s+the\s+top\s+\d+\s+crypto", re.IGNORECASE),
    # V-DB5 Calibration 2026-05-08 (audit C-2):
    # Multiplikator-Ziele MIT Promo-Substanz-Marker (gain/return/pump/moon/rally).
    # Vorher zu breit: "Trump targets 200x export tariffs" oder "Visa eyes 1000x scaling"
    # wurden als Promo geblockt. Jetzt fordert das Pattern eine Promo-typische
    # Rendite-/Pump-Phrase als Trailer.
    re.compile(
        r"\b(?:eyes?|targets?|aims?)\s+\d+0+x\s+"
        r"(?:gain|gains|return|returns|rally|pump|surge|growth|moon|profit)",
        re.IGNORECASE,
    ),
    # \d{2,4}x mit konkretem Zeit-Anker (Listing/Quartal/Wochen/Monate) statt freiem
    # "before/by/in" — das matchte vorher seriöse "100x in three years study finds".
    re.compile(
        r"\b\d{2,4}x\s+"
        r"(?:before\s+listing|by\s+Q\d|in\s+\d+\s+(?:weeks?|days?|months?))\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{2,4}x\s+potential\b", re.IGNORECASE),
    # "Catch up to / second chance" Pump-Phrasen
    re.compile(r"\bcatch\s+up\s+to\s+what\b", re.IGNORECASE),
    re.compile(r"\bsecond\s+chance\s+entry\b", re.IGNORECASE),
    # V-DB5 Calibration 2026-05-08 (audit F-003):
    # "could hit $X" mit konkretem Zeit-Anker — verhindert FP bei Analyst-Konsens
    # ("Bitcoin could hit $80,000 — analysts split on timing" hat keinen Zeit-Anker).
    re.compile(
        r"\bcould\s+hit\s+\$\d[\d,]*\s+(?:by|before|this)\s+"
        r"(?:Q\d|month|week|day|year|20\d{2}|"
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
        r"spring|summer|fall|autumn|winter)",
        re.IGNORECASE,
    ),
    # V-DB5 Calibration 2026-05-08 (audit C-1):
    # "price prediction" allein war zu breit (Mainstream-SEO). Patternsentfernt.
    # Pre-Sale-Multipliziere + Listicles fangen die echte Promo-Familie ab.
    # Burn / Frenzy / Rally-Hype ohne konkretes Ereignis
    re.compile(r"\bburn\s+frenzy\b", re.IGNORECASE),
    # V-DB5 Calibration 2026-05-08 (audit C-3):
    # "offers while ... and|grin|grinds" matchte normales Englisch
    # ("Robinhood offers while Coinbase grinds out gains"). Pattern entfernt;
    # Pre-Sale-Familie (Index 0-3) + Catch-Up (Index 9) decken Pump-Promos ab.
)


def _is_promotional(title: str) -> bool:
    """Return True if the title matches a known promotional/listicle pattern.

    Filter wirkt fail-closed: Treffer = directional_eligible=False mit
    BLOCK_REASON_PROMO_PATTERN. Echte direktional-handelnde Headlines wie
    "Coinbase Q1 miss" oder "Aptos Foundation commits $50M" matchen nicht.
    """
    for pattern in _PROMO_PATTERNS:
        if pattern.search(title):
            return True
    return False


def _is_reactive_bearish(title: str) -> bool:
    """Return True if the title describes a past/ongoing price decline."""
    for pattern in _REACTIVE_BEARISH_PATTERNS:
        if pattern.search(title):
            return True
    return False


def _is_reactive_bullish(title: str) -> bool:
    """Return True if the title describes a past/ongoing price rise."""
    for pattern in _REACTIVE_BULLISH_PATTERNS:
        if pattern.search(title):
            return True
    return False


def check_price_trend_alignment(
    sentiment: str,
    change_pct_24h: float,
    change_pct_7d: float = 0.0,
    *,
    regime_threshold_7d_bullish: float = 3.0,
    regime_threshold_7d_bearish: float = 1.5,
) -> bool:
    """Return True if the price trend confirms the sentiment direction.

    D-118: Gate dispatching directional alerts on whether the market is
    actually moving in the predicted direction.  89% of historical misses
    were correct-sentiment but wrong-market-context.

    D-120 / D-121: 7d regime gate with asymmetric thresholds.
    Bearish uses a tighter threshold (1.5%) because bearish signals in even
    mildly bullish regimes had 4% precision (1/25).
    Bullish threshold stays at 3.0%.

    Rules:
      - bearish + 7d change > +threshold_bearish → divergent (block)
      - bullish + 7d change < −threshold_bullish → divergent (block)
      - bullish + price rising (24h > 0)  → aligned
      - bearish + price falling (24h < 0) → aligned
      - otherwise                         → divergent (block)
    """
    sentiment_lower = sentiment.strip().lower()

    # D-120 / D-121: 7d regime override — asymmetric thresholds.
    if sentiment_lower == "bearish" and change_pct_7d > regime_threshold_7d_bearish:
        return False
    if sentiment_lower == "bullish" and change_pct_7d < -regime_threshold_7d_bullish:
        return False

    # D-118: 24h directional alignment.
    if sentiment_lower == "bullish":
        return change_pct_24h > 0.0
    if sentiment_lower == "bearish":
        return change_pct_24h < 0.0
    return True  # non-directional: always pass


@dataclass(frozen=True)
class DirectionalEligibilityDecision:
    """Eligibility decision for directional alert operations."""

    is_directional: bool
    directional_eligible: bool | None
    directional_block_reason: str | None = None
    eligible_assets: list[str] = field(default_factory=list)
    blocked_assets: list[str] = field(default_factory=list)


def evaluate_directional_eligibility(
    *,
    sentiment_label: str | None,
    affected_assets: list[str],
    sentiment_score: float | None = None,
    impact_score: float | None = None,
    title: str | None = None,
    directional_confidence: float | None = None,
    event_timing: str | None = None,
    actionable: bool | None = None,
    priority: int | None = None,
    source_name: str | None = None,
) -> DirectionalEligibilityDecision:
    """Return directional eligibility for operational metrics.

    Non-directional sentiments return ``directional_eligible=None``.
    Directional sentiments must pass score-strength gates, a reactive-narrative
    filter (bearish only), AND resolve to at least one supported tradeable
    crypto symbol; otherwise they are blocked.
    """
    sentiment = (sentiment_label or "").strip().lower()
    if sentiment not in _DIRECTIONAL_SENTIMENTS:
        return DirectionalEligibilityDecision(
            is_directional=False,
            directional_eligible=None,
        )

    # D-122: Non-actionable alerts are noise for directional tracking.
    # Empirical: actionable=false had 22% precision vs 52% for actionable=true.
    if actionable is False:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_NOT_ACTIONABLE,
        )

    # D-142: Bearish directional disabled — 4% precision (1/24) on eligible
    # resolved outcomes.  Bearish news is not price-predictive in current
    # market conditions.  Re-enable when market-context analysis is added.
    if BEARISH_DIRECTIONAL_DISABLED and sentiment == "bearish":
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_BEARISH_DISABLED,
        )

    # V-DB4c 2026-05-08: Soft-Confidence-Adjuster.
    # Sources auf monitor/source_watch.txt bekommen priority -= 1, BEVOR
    # der LOW_PRIORITY-Gate prueft. Damit kippen P8-Watchlist-Sources nach
    # P7 und werden geblockt; P9-Watchlist-Sources bleiben eligible mit
    # tieferer Position. Operator-kuratierte Liste.
    effective_priority = priority
    if (
        effective_priority is not None
        and source_name
        and source_name.lower() in _load_source_watchlist()
    ):
        effective_priority = effective_priority - 1

    # D-122: Low-priority alerts lack predictive value for directional tracking.
    # Empirical: P7 had 21% precision.  Minimum P8 required.
    if effective_priority is not None and effective_priority <= 7:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_LOW_PRIORITY,
        )

    # D-133: Source-level precision gate.
    # Sources with persistently low directional precision are blocked.
    if source_name and source_name.lower() in _LOW_PRECISION_SOURCES:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_LOW_PRECISION_SOURCE,
        )

    # V-DB4b 2026-05-08: Promotional/listicle gate.
    # Pre-sale spam, "Top 3 Cryptos to Buy", Multiplikator-Ziele ("100x before
    # listing"), spekulative Preis-Ziele. Diese Headlines sind keine
    # Marktnachrichten, sondern Pump-Narrative — geringe Hit-Rate, hohe Source-
    # Verzerrung. Filter wirkt VOR Score-Gates, weil Promo-Headlines oft hohen
    # Sentiment-Score und Impact haben (LLM-Analyse erkennt den "bullish ton",
    # aber nicht die fehlende Substanz).
    if title and _is_promotional(title):
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_PROMO_PATTERN,
        )

    # Score-strength gates (D-111): block weak directional signals early.
    if sentiment_score is not None and abs(sentiment_score) < MIN_SENTIMENT_MAGNITUDE:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_WEAK_SIGNAL,
        )
    # D-121: Asymmetric impact threshold — bearish needs higher impact.
    min_impact = MIN_IMPACT_SCORE_BEARISH if sentiment == "bearish" else MIN_IMPACT_SCORE_BULLISH
    if impact_score is not None and impact_score < min_impact:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_WEAK_SIGNAL,
        )

    # D-113/D-115: Reactive price narrative gate.
    # Titles describing past price moves ("drops", "surges") are lagging
    # indicators, not predictions.  Empirical 0% precision at P9/P10 bearish;
    # symmetric filter for bullish reactive titles (D-115).
    if sentiment == "bearish" and title and _is_reactive_bearish(title):
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_REACTIVE_NARRATIVE,
        )
    if sentiment == "bullish" and title and _is_reactive_bullish(title):
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_REACTIVE_NARRATIVE,
        )

    # D-116 / D-121: Asymmetric directional confidence gate.
    # Bearish requires ≥0.92 (only concrete adverse events); bullish ≥0.8.
    min_confidence = (
        MIN_DIRECTIONAL_CONFIDENCE_BEARISH
        if sentiment == "bearish"
        else MIN_DIRECTIONAL_CONFIDENCE_BULLISH
    )
    if directional_confidence is not None and directional_confidence < min_confidence:
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_LOW_DIRECTIONAL_CONFIDENCE,
        )

    # D-116: Backward-looking reports are not predictive signals.
    if event_timing in ("backward_report", "speculative"):
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=False,
            directional_block_reason=BLOCK_REASON_REACTIVE_NARRATIVE,
        )

    eligible_assets: list[str] = []
    blocked_assets: list[str] = []
    seen_eligible: set[str] = set()
    seen_blocked: set[str] = set()
    has_non_empty_asset = False

    for raw_asset in affected_assets:
        candidate = raw_asset.strip().upper()
        if not candidate:
            continue
        has_non_empty_asset = True

        # D-xxx: Filter Naked-Assets (e.g. BTC without /USDT) to improve precision
        if "/" not in candidate:
            if candidate not in seen_blocked:
                blocked_assets.append(candidate)
                seen_blocked.add(candidate)
            continue

        resolved = _resolve_symbol(candidate)
        if resolved is None:
            if candidate not in seen_blocked:
                blocked_assets.append(candidate)
                seen_blocked.add(candidate)
            continue
        normalized_symbol, _coin_id = resolved
        if normalized_symbol not in seen_eligible:
            eligible_assets.append(normalized_symbol)
            seen_eligible.add(normalized_symbol)

    if eligible_assets:
        # D-116: Majority non-crypto gate.
        # If more than half of the mentioned assets are non-crypto (equities,
        # ETFs, etc.), the article is primarily about traditional markets and
        # the crypto mention is incidental.  Empirical precision for these
        # is ~0% (COIN, MSTR, IBIT, HOOD, MARA always miss).
        total_assets = len(eligible_assets) + len(blocked_assets)
        if total_assets > 1 and len(blocked_assets) > len(eligible_assets):
            return DirectionalEligibilityDecision(
                is_directional=True,
                directional_eligible=False,
                directional_block_reason=BLOCK_REASON_MAJORITY_NON_CRYPTO,
                eligible_assets=eligible_assets,
                blocked_assets=blocked_assets,
            )
        return DirectionalEligibilityDecision(
            is_directional=True,
            directional_eligible=True,
            eligible_assets=eligible_assets,
            blocked_assets=blocked_assets,
        )

    if not has_non_empty_asset:
        reason = BLOCK_REASON_MISSING_ASSETS
    elif all(
        "/" not in raw_asset.strip().upper()
        for raw_asset in affected_assets
        if raw_asset.strip().upper()
    ):
        # V-DB5 Calibration 2026-05-08 (audit S-A1):
        # NAKED_ASSET nur wenn mindestens ein Asset crypto-mappable waere
        # (z.B. "BTC" → resolve_symbol("BTC/USDT") returns mapping).
        # "PredictIt" / "Sports-Bill" / "AAPL" sind NICHT crypto-mappable
        # und gehen als UNSUPPORTED_ASSETS, nicht als naked-crypto.
        any_crypto_mappable = any(
            _resolve_symbol(f"{raw_asset.strip().upper()}/USDT") is not None
            for raw_asset in affected_assets
            if raw_asset.strip()
        )
        reason = (
            BLOCK_REASON_NAKED_ASSET if any_crypto_mappable
            else BLOCK_REASON_UNSUPPORTED_ASSETS
        )
    else:
        reason = BLOCK_REASON_UNSUPPORTED_ASSETS

    return DirectionalEligibilityDecision(
        is_directional=True,
        directional_eligible=False,
        directional_block_reason=reason,
        eligible_assets=[],
        blocked_assets=blocked_assets,
    )
