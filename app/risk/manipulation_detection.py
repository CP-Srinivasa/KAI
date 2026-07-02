"""Manipulation Detection Engine — institutional-grade surveillance.

Pure-Python implementation. No numpy / scipy / NLP dependency.

Detects 8 manipulation patterns across three domains:

Social
  • coordinated_shilling   — text-similarity clusters in narrow time windows
  • fake_engagement        — engagement Z-score outliers / impossible ratios
  • bot_network            — young accounts with low inter-arrival entropy

Market
  • wash_trading           — volume-to-price-impact ratio + matched pairs
  • spoofing               — high cancel ratio + outsized orders
  • pump_and_dump          — sequence-match on price/volume bars

On-chain
  • abnormal_wallet        — volume Z-score + post-dormancy bursts + funnels
  • insider_behavior       — wallet flow vs. forward-return lead-lag correlation

Per-source output:
  • trust_score            — 0..1, higher = more trustworthy
  • manipulation_probability — 0..1, combined across detected patterns
  • historical_reliability — Bayesian-smoothed accuracy from past calls

Outputs are immutable dataclasses, JSON-serializable, audit-friendly.
The engine never raises on bad data — it returns warnings instead.
"""

from __future__ import annotations

import bisect
import hashlib
import logging
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime

from app.risk.manipulation_detection_models import (
    ALL_PATTERNS,
    PATTERN_ABNORMAL_WALLET,
    PATTERN_BOT_NETWORK,
    PATTERN_COORDINATED_SHILLING,
    PATTERN_FAKE_ENGAGEMENT,
    PATTERN_INSIDER_BEHAVIOR,
    PATTERN_PUMP_AND_DUMP,
    PATTERN_SPOOFING,
    PATTERN_WASH_TRADING,
    SOURCE_MARKET_ACCOUNT,
    SOURCE_SOCIAL_ACCOUNT,
    SOURCE_WALLET,
    Account,
    HistoricalCall,
    ManipulationDetectionConfig,
    ManipulationReport,
    OrderEvent,
    Post,
    PriceBar,
    SourceTrustReport,
    Trade,
    WalletTx,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Pure helpers
# ============================================================================


def _parse_iso_to_epoch(ts: str) -> float:
    """Parse an ISO 8601 timestamp into seconds-since-epoch. Returns 0.0 on
    parse failure — callers treat that as 'unknown' and skip ordering."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _bigrams(text: str) -> set[str]:
    """Whitespace-aware character bigrams over alphanumeric tokens."""
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    out: set[str] = set()
    for token in cleaned.split():
        if len(token) < 2:
            out.add(token)
            continue
        for i in range(len(token) - 1):
            out.add(token[i : i + 2])
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def _zscore(x: float, sample: list[float]) -> float:
    """Z-score of x against `sample` (mean/stdev). Returns 0 on degenerate input."""
    if len(sample) < 2:
        return 0.0
    mu = statistics.fmean(sample)
    sd = statistics.pstdev(sample)
    if sd <= 0.0:
        return 0.0
    return (x - mu) / sd


def _pearson_correlation(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs2, ys2 = xs[-n:], ys[-n:]
    mx = statistics.fmean(xs2)
    my = statistics.fmean(ys2)
    num = sum((xs2[i] - mx) * (ys2[i] - my) for i in range(n))
    sxx = sum((x - mx) ** 2 for x in xs2)
    syy = sum((y - my) ** 2 for y in ys2)
    if sxx <= 0.0 or syy <= 0.0:
        return 0.0
    return num / math.sqrt(sxx * syy)


def _normalized_entropy(intervals: list[float], n_bins: int = 10) -> float:
    """Shannon entropy of log-binned interval lengths, normalized to [0, 1].

    1.0 = uniform across bins (human-like noisy posting). 0.0 = all in one
    bin (machine-like uniform spacing).
    """
    if len(intervals) < 5:
        return 1.0
    positive = [d for d in intervals if d > 0.0]
    if len(positive) < 5:
        return 1.0
    log_max = math.log(max(positive) + 1.0)
    if log_max <= 0.0:
        return 1.0
    counts = [0] * n_bins
    for d in positive:
        idx = min(int(math.log(d + 1.0) / log_max * n_bins), n_bins - 1)
        counts[idx] += 1
    total = sum(counts)
    if total == 0:
        return 1.0
    h = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            h -= p * math.log(p)
    max_h = math.log(n_bins)
    return h / max_h if max_h > 0 else 1.0


def _union_find_clusters(edges: list[tuple[int, int]], n: int) -> list[list[int]]:
    """Connected components via Union-Find. Returns clusters of size > 1."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        if 0 <= a < n and 0 <= b < n:
            union(a, b)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return [g for g in groups.values() if len(g) > 1]


def _logistic(x: float) -> float:
    """Logistic squashing — turns z-scores into probability-like 0..1 values."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _combine_independent(probs: list[float]) -> float:
    """Combine evidence as 1 − Π(1 − p_i). Treats detectors as independent."""
    not_p = 1.0
    for p in probs:
        not_p *= 1.0 - max(0.0, min(1.0, p))
    return 1.0 - not_p


# ============================================================================
# Engine
# ============================================================================


class ManipulationDetectionEngine:
    """Surveillance engine — runs all available detectors, aggregates per source."""

    def __init__(self, config: ManipulationDetectionConfig | None = None) -> None:
        self._config = config or ManipulationDetectionConfig()

    # -------------------------------------------------- coord shilling

    def _detect_coordinated_shilling(self, posts: list[Post]) -> tuple[int, dict[str, float]]:
        """Find clusters of similar posts in narrow time windows.

        Returns (cluster_count, per_source_evidence ∈ [0, 1]).
        """
        cfg = self._config
        if len(posts) < cfg.shilling_min_cluster_size:
            return 0, {}

        # Pre-compute bigrams + epoch
        n = len(posts)
        bigrams = [_bigrams(p.text) for p in posts]
        epochs = [_parse_iso_to_epoch(p.timestamp_utc) for p in posts]

        # Build similarity edges within time window
        edges: list[tuple[int, int]] = []
        for i in range(n):
            for j in range(i + 1, n):
                if abs(epochs[i] - epochs[j]) > cfg.shilling_time_window_seconds:
                    continue
                if _jaccard(bigrams[i], bigrams[j]) >= cfg.shilling_text_similarity_threshold:
                    edges.append((i, j))

        clusters = _union_find_clusters(edges, n)
        valid = [c for c in clusters if len(c) >= cfg.shilling_min_cluster_size]

        evidence: dict[str, float] = {}
        for cluster in valid:
            # Severity proportional to cluster size, capped at 1.0
            severity = min(1.0, len(cluster) / 10.0)
            for idx in cluster:
                src = posts[idx].source_id
                evidence[src] = max(evidence.get(src, 0.0), severity)

        return len(valid), evidence

    # ----------------------------------------------- fake engagement

    def _detect_fake_engagement(self, posts: list[Post]) -> tuple[int, dict[str, float]]:
        cfg = self._config
        if len(posts) < 5:
            return 0, {}

        # Z-score across the whole batch — look for extreme engagement
        engagement_values = [float(p.engagement_count) for p in posts]
        suspect_count = 0
        evidence: dict[str, float] = {}

        for p in posts:
            z = _zscore(float(p.engagement_count), engagement_values)
            ratio_anomaly = 0.0
            if p.follower_count_at_post > 0:
                ratio = p.engagement_count / p.follower_count_at_post
                if ratio > cfg.engagement_to_follower_ratio_threshold:
                    ratio_anomaly = min(1.0, ratio / cfg.engagement_to_follower_ratio_threshold)
            elif p.engagement_count > 100:
                # No followers but lots of engagement → very suspect
                ratio_anomaly = 1.0

            severity = 0.0
            if z > cfg.engagement_z_threshold:
                severity = max(severity, _logistic(z - cfg.engagement_z_threshold))
            severity = max(severity, ratio_anomaly)

            if severity > 0.5:
                suspect_count += 1
                evidence[p.source_id] = max(evidence.get(p.source_id, 0.0), severity)

        return suspect_count, evidence

    # --------------------------------------------------- bot network

    def _detect_bot_network(
        self, accounts: list[Account], posts: list[Post]
    ) -> tuple[int, dict[str, float]]:
        cfg = self._config
        if not accounts:
            return 0, {}

        # Group posts by account → compute interval entropy per account
        posts_by_account: dict[str, list[Post]] = defaultdict(list)
        for p in posts:
            posts_by_account[p.source_id].append(p)

        evidence: dict[str, float] = {}
        suspect_account_ids: set[str] = set()

        for acc in accounts:
            score_components: list[float] = []

            # 1) Account age
            if 0 < acc.account_age_days < cfg.bot_max_account_age_days:
                age_severity = 1.0 - (acc.account_age_days / cfg.bot_max_account_age_days)
                score_components.append(age_severity * 0.7)

            # 2) Default avatar
            if acc.has_default_avatar:
                score_components.append(cfg.bot_default_avatar_weight * 5)

            # 3) Follower / following ratio (low ratio = following many, follow back few)
            if acc.following_count > 0:
                ratio = acc.follower_count / acc.following_count
                if ratio < cfg.bot_min_follower_following_ratio:
                    score_components.append(min(1.0, 0.5 * (1.0 - ratio)))

            # 4) Bio length (default-empty profile)
            if acc.bio_length == 0 and not acc.verified:
                score_components.append(0.3)

            # 5) Posting interval entropy
            user_posts = posts_by_account.get(acc.account_id, [])
            if len(user_posts) >= cfg.bot_min_post_count_for_analysis:
                user_posts_sorted = sorted(
                    user_posts, key=lambda p: _parse_iso_to_epoch(p.timestamp_utc)
                )
                epochs = [_parse_iso_to_epoch(p.timestamp_utc) for p in user_posts_sorted]
                intervals = [epochs[i + 1] - epochs[i] for i in range(len(epochs) - 1)]
                ent = _normalized_entropy(intervals)
                if ent < cfg.bot_interval_entropy_threshold:
                    score_components.append(1.0 - ent)

            if not score_components:
                continue

            # Combined severity (weighted average, capped)
            severity = min(1.0, sum(score_components) / max(2.0, len(score_components)))
            if severity > 0.4:
                evidence[acc.account_id] = severity
                suspect_account_ids.add(acc.account_id)

        # "Bot network" requires more than 1 suspect account (a single bot is
        # noteworthy but not yet a network)
        n_networks = 1 if len(suspect_account_ids) >= 3 else 0
        return n_networks, evidence

    # -------------------------------------------------- wash trading

    def _detect_wash_trading(self, trades: list[Trade]) -> tuple[float | None, dict[str, float]]:
        cfg = self._config
        if len(trades) < cfg.wash_min_trades:
            return None, {}

        # Sort trades chronologically
        trades_sorted = sorted(trades, key=lambda t: _parse_iso_to_epoch(t.timestamp_utc))

        # 1) Volume-to-price-impact ratio
        prices = [t.price for t in trades_sorted if t.price > 0]
        sizes = [t.size for t in trades_sorted if t.size > 0]
        if len(prices) < 2 or not sizes:
            return None, {}
        total_volume = sum(sizes)
        price_first = prices[0]
        price_last = prices[-1]
        if price_first > 0:
            price_move_pct = abs(price_last - price_first) / price_first * 100.0
        else:
            price_move_pct = 0.0
        # Avoid zero division — synthetic floor for tiny moves
        ratio = total_volume / max(price_move_pct, 0.01)
        ratio_signature = 0.0
        if ratio > cfg.wash_volume_to_impact_threshold:
            ratio_signature = _logistic(math.log(ratio / cfg.wash_volume_to_impact_threshold))

        # 2) Self-pair detection: same buyer/seller participates in both sides
        evidence: dict[str, float] = {}
        if any(t.buyer_id and t.seller_id for t in trades_sorted):
            self_pair_count = sum(
                1 for t in trades_sorted if t.buyer_id and t.seller_id and t.buyer_id == t.seller_id
            )
            self_pair_share = self_pair_count / len(trades_sorted)
            if self_pair_share >= cfg.wash_self_trade_pair_threshold:
                # Attribute evidence to the colliding accounts
                for t in trades_sorted:
                    if t.buyer_id and t.seller_id and t.buyer_id == t.seller_id:
                        evidence[t.buyer_id] = max(evidence.get(t.buyer_id, 0.0), 0.9)

        signature = max(ratio_signature, max(evidence.values(), default=0.0))
        return signature, evidence

    # ----------------------------------------------------- spoofing

    def _detect_spoofing(
        self, order_events: list[OrderEvent]
    ) -> tuple[float | None, dict[str, float]]:
        cfg = self._config
        if len(order_events) < cfg.spoof_min_orders:
            return None, {}

        # Per-account counts
        canceled: dict[str, int] = defaultdict(int)
        filled: dict[str, int] = defaultdict(int)
        total: dict[str, int] = defaultdict(int)
        sum_size: dict[str, float] = defaultdict(float)
        n_size: dict[str, int] = defaultdict(int)
        for e in order_events:
            if not e.account_id:
                continue
            total[e.account_id] += 1
            if e.event_type == "canceled":
                canceled[e.account_id] += 1
            elif e.event_type in ("filled", "partially_filled"):
                filled[e.account_id] += 1
            sum_size[e.account_id] += abs(e.size)
            n_size[e.account_id] += 1

        # Per-account size reference excluding the focal account — otherwise a
        # high-volume spoofer drags the global mean up and hides its own outlier.
        sizes_by_account: dict[str, list[float]] = defaultdict(list)
        for e in order_events:
            if e.account_id and abs(e.size) > 0:
                sizes_by_account[e.account_id].append(abs(e.size))
        global_sum = sum(sum(v) for v in sizes_by_account.values())
        global_count = sum(len(v) for v in sizes_by_account.values())

        evidence: dict[str, float] = {}
        max_signature = 0.0
        for acc, n_total in total.items():
            if n_total < cfg.spoof_min_orders:
                continue
            cancel_count = canceled.get(acc, 0)
            fill_count = filled.get(acc, 0)
            if cancel_count + fill_count == 0:
                continue
            cancel_ratio = cancel_count / (cancel_count + fill_count)
            avg_size = sum_size[acc] / max(n_size[acc], 1)

            # Reference = mean size of all OTHER accounts
            self_sum = sum(sizes_by_account.get(acc, []))
            self_n = len(sizes_by_account.get(acc, []))
            other_sum = global_sum - self_sum
            other_n = global_count - self_n
            reference_size = other_sum / other_n if other_n > 0 else avg_size
            size_mult = avg_size / reference_size if reference_size > 0 else 0.0

            if (
                cancel_ratio >= cfg.spoof_cancel_ratio_threshold
                and size_mult >= cfg.spoof_size_multiplier_threshold
            ):
                severity = min(
                    1.0,
                    0.5 * (cancel_ratio - cfg.spoof_cancel_ratio_threshold) * 10.0
                    + 0.5 * min(size_mult / cfg.spoof_size_multiplier_threshold, 2.0) * 0.5,
                )
                severity = max(severity, 0.6)  # binary detection floor
                evidence[acc] = severity
                if severity > max_signature:
                    max_signature = severity

        signature: float | None = max_signature if evidence else 0.0
        return signature, evidence

    # ---------------------------------------------- pump and dump

    def _detect_pump_and_dump(self, bars: list[PriceBar]) -> tuple[float | None, dict[str, float]]:
        cfg = self._config
        n_required = cfg.pump_window_bars + cfg.dump_window_bars
        if len(bars) < n_required:
            return None, {}

        bars_sorted = sorted(bars, key=lambda b: _parse_iso_to_epoch(b.timestamp_utc))
        closes = [b.close for b in bars_sorted]
        volumes = [b.volume for b in bars_sorted]
        n = len(bars_sorted)

        max_signature = 0.0
        # Slide a (pump_window + dump_window) window through history
        for end in range(cfg.pump_window_bars + cfg.dump_window_bars, n + 1):
            pump_start = end - cfg.pump_window_bars - cfg.dump_window_bars
            pump_end = end - cfg.dump_window_bars
            dump_end = end

            pump_closes = closes[pump_start:pump_end]
            dump_closes = closes[pump_end:dump_end]
            if not pump_closes or not dump_closes or pump_closes[0] <= 0:
                continue

            pump_rise = (pump_closes[-1] - pump_closes[0]) / pump_closes[0]
            dump_fall = (dump_closes[-1] - dump_closes[0]) / dump_closes[0]

            if (
                pump_rise >= cfg.pump_price_increase_threshold
                and dump_fall <= -cfg.dump_price_decrease_threshold
            ):
                # Volume confirmation — pump-window volume Z-score vs. baseline
                baseline = volumes[max(0, pump_start - 100) : pump_start]
                pump_volumes = volumes[pump_start:pump_end]
                z = _zscore(statistics.fmean(pump_volumes), baseline) if baseline else 0.0
                vol_ok = z >= cfg.pump_volume_zscore_threshold
                severity = min(1.0, max(pump_rise, abs(dump_fall)))
                if vol_ok:
                    severity = min(1.0, severity * 1.2)
                if severity > max_signature:
                    max_signature = severity

        return max_signature, {}

    # ------------------------------------------------- abnormal wallet

    def _detect_abnormal_wallet(self, wallet_txs: list[WalletTx]) -> tuple[int, dict[str, float]]:
        cfg = self._config
        if not wallet_txs:
            return 0, {}

        # Group by wallet (using outgoing-side as the "actor" wallet)
        per_wallet: dict[str, list[WalletTx]] = defaultdict(list)
        for tx in wallet_txs:
            per_wallet[tx.from_wallet].append(tx)

        evidence: dict[str, float] = {}
        anomaly_count = 0

        # 1) Volume Z-score per wallet (recent vs. historical)
        for wallet, txs in per_wallet.items():
            if len(txs) < 5:
                continue
            txs_sorted = sorted(txs, key=lambda t: _parse_iso_to_epoch(t.timestamp_utc))
            usd_values = [t.usd_value for t in txs_sorted if t.usd_value > 0]
            if len(usd_values) < 5:
                continue
            recent = usd_values[-1]
            historical = usd_values[:-1]
            z = _zscore(recent, historical)
            severity = 0.0
            if z >= cfg.wallet_volume_z_threshold:
                severity = max(severity, _logistic(z - cfg.wallet_volume_z_threshold))

            # 2) Dormancy + sudden activity
            epochs = [_parse_iso_to_epoch(t.timestamp_utc) for t in txs_sorted]
            if len(epochs) >= 3:
                gap_days = (epochs[-1] - epochs[-2]) / 86400.0
                if gap_days >= cfg.wallet_dormancy_days:
                    severity = max(severity, 0.7)

            if severity > 0.5:
                evidence[wallet] = severity
                anomaly_count += 1

        # 3) Funnel detection: many distinct sources → single destination
        per_dest: dict[str, set[str]] = defaultdict(set)
        for tx in wallet_txs:
            per_dest[tx.to_wallet].add(tx.from_wallet)
        for dest, sources in per_dest.items():
            if len(sources) >= cfg.wallet_funnel_min_sources:
                evidence[dest] = max(evidence.get(dest, 0.0), 0.6)
                anomaly_count += 1

        return anomaly_count, evidence

    # ----------------------------------------------- insider behavior

    def _detect_insider_behavior(
        self,
        wallet_txs: list[WalletTx],
        bars: list[PriceBar],
        target_symbol: str | None,
    ) -> tuple[float | None, dict[str, float]]:
        cfg = self._config
        if not wallet_txs or not bars or target_symbol is None:
            return None, {}

        bars_sorted = sorted(bars, key=lambda b: _parse_iso_to_epoch(b.timestamp_utc))
        if len(bars_sorted) < cfg.insider_min_observations:
            return None, {}

        bar_epochs = [_parse_iso_to_epoch(b.timestamp_utc) for b in bars_sorted]
        bar_returns: list[float] = [0.0]
        for i in range(1, len(bars_sorted)):
            prev_close = bars_sorted[i - 1].close
            if prev_close > 0:
                bar_returns.append((bars_sorted[i].close - prev_close) / prev_close)
            else:
                bar_returns.append(0.0)

        # For each wallet: build a per-bar "net buy signal" (USD inflow) and
        # correlate against forward returns lagged by lead_window_bars.
        per_wallet: dict[str, list[WalletTx]] = defaultdict(list)
        for tx in wallet_txs:
            if target_symbol.lower() in (tx.asset.lower(), tx.asset.lower() + "usdt"):
                per_wallet[tx.from_wallet].append(tx)
                per_wallet[tx.to_wallet].append(tx)

        if not per_wallet:
            return 0.0, {}

        evidence: dict[str, float] = {}
        max_correlation = 0.0
        n_bars = len(bars_sorted)
        lead = cfg.insider_lead_window_bars

        for wallet, txs in per_wallet.items():
            if len(txs) < 5:
                continue
            wallet_signal = [0.0] * n_bars
            for tx in txs:
                ts = _parse_iso_to_epoch(tx.timestamp_utc)
                # bisect_right - 1 gives the bar that contains ts (left-closed,
                # right-open) and matches an exact bar-start to that same bar.
                bar_idx = max(0, min(n_bars - 1, bisect.bisect_right(bar_epochs, ts) - 1))
                # Net flow direction: receiving = +inflow, sending = −outflow
                signed = tx.usd_value if tx.to_wallet == wallet else -tx.usd_value
                wallet_signal[bar_idx] += signed

            # Forward return: r_{t+lead}
            if n_bars <= lead + 5:
                continue
            xs = wallet_signal[: n_bars - lead]
            ys = bar_returns[lead:]
            corr = _pearson_correlation(xs, ys)
            if corr >= cfg.insider_correlation_threshold:
                severity = min(1.0, corr)
                evidence[wallet] = severity
                if corr > max_correlation:
                    max_correlation = corr

        signature: float | None = max_correlation if max_correlation > 0 else 0.0
        return signature, evidence

    # --------------------------------------- historical reliability

    def _historical_reliability(self, calls: list[HistoricalCall]) -> dict[str, tuple[float, int]]:
        """Per-source (reliability, sample_size) from past calls.

        Reliability = correct_calls / total_calls, smoothed toward 0.5 by a
        Beta-style prior (alpha ≈ 1 / smoothing_alpha pseudo-observations).
        """
        cfg = self._config
        per_src: dict[str, list[HistoricalCall]] = defaultdict(list)
        for c in calls:
            per_src[c.source_id].append(c)

        out: dict[str, tuple[float, int]] = {}
        prior_n = max(1.0, 1.0 / max(cfg.history_smoothing_alpha, 1e-6))
        for src, lst in per_src.items():
            graded = [c for c in lst if c.realized_pnl_pct_30d is not None]
            if len(graded) < cfg.history_min_samples:
                out[src] = (cfg.history_neutral_score, len(graded))
                continue
            correct = 0
            for c in graded:
                pnl = c.realized_pnl_pct_30d or 0.0
                if c.direction == "bullish" and pnl > 0:
                    correct += 1
                elif c.direction == "bearish" and pnl < 0:
                    correct += 1
                elif c.direction == "neutral" and abs(pnl) < 5.0:
                    correct += 1
            n = len(graded)
            # Bayesian smoothing toward neutral
            reliability = (correct + cfg.history_neutral_score * prior_n) / (n + prior_n)
            out[src] = (max(0.0, min(1.0, reliability)), n)
        return out

    # --------------------------------------------- aggregation

    def _aggregate_source(
        self,
        source_id: str,
        source_type: str,
        evidence_per_pattern: dict[str, float],
        history: tuple[float, int],
        sample_size: int,
    ) -> SourceTrustReport:
        cfg = self._config
        weights = {
            PATTERN_COORDINATED_SHILLING: cfg.severity_weight_shilling,
            PATTERN_FAKE_ENGAGEMENT: cfg.severity_weight_fake_engagement,
            PATTERN_BOT_NETWORK: cfg.severity_weight_bot,
            PATTERN_WASH_TRADING: cfg.severity_weight_wash,
            PATTERN_SPOOFING: cfg.severity_weight_spoof,
            PATTERN_PUMP_AND_DUMP: cfg.severity_weight_pump,
            PATTERN_ABNORMAL_WALLET: cfg.severity_weight_wallet,
            PATTERN_INSIDER_BEHAVIOR: cfg.severity_weight_insider,
        }
        # Convert per-pattern evidence into pseudo-probabilities weighted by severity
        weighted_probs: list[float] = []
        detected: list[str] = []
        for pattern, raw in evidence_per_pattern.items():
            if raw <= 0.0:
                continue
            detected.append(pattern)
            w = weights.get(pattern, 0.10)
            weighted_probs.append(min(1.0, raw * (0.5 + w)))

        manipulation_p = _combine_independent(weighted_probs)
        history_score, history_n = history
        # Trust score: base on history, penalized by manipulation probability
        trust = max(0.0, history_score * (1.0 - manipulation_p))

        return SourceTrustReport(
            source_id=source_id,
            source_type=source_type,
            trust_score=trust,
            manipulation_probability=manipulation_p,
            historical_reliability=history_score,
            detected_patterns=sorted(detected),
            pattern_evidence=dict(evidence_per_pattern),
            sample_size=max(sample_size, history_n),
        )

    # --------------------------------------------------- public

    def _hash_inputs(
        self,
        posts: list[Post],
        accounts: list[Account],
        trades: list[Trade],
        order_events: list[OrderEvent],
        wallet_txs: list[WalletTx],
        target_symbol: str | None,
    ) -> str:
        payload = "|".join(
            [
                str(target_symbol or ""),
                f"posts={len(posts)}",
                f"accounts={len(accounts)}",
                f"trades={len(trades)}",
                f"orders={len(order_events)}",
                f"txs={len(wallet_txs)}",
            ]
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    def analyze(  # noqa: C901 — orchestrator, deliberately linear
        self,
        *,
        posts: list[Post] | None = None,
        accounts: list[Account] | None = None,
        trades: list[Trade] | None = None,
        order_events: list[OrderEvent] | None = None,
        wallet_txs: list[WalletTx] | None = None,
        bars: list[PriceBar] | None = None,
        historical_calls: list[HistoricalCall] | None = None,
        target_symbol: str | None = None,
    ) -> ManipulationReport:
        """Run all available detectors, aggregate per source.

        Each input list is optional. Detectors that lack their required inputs
        are skipped (their report fields stay None / 0). The engine never
        raises on bad data — warnings accumulate in the report.
        """
        posts = posts or []
        accounts = accounts or []
        trades = trades or []
        order_events = order_events or []
        wallet_txs = wallet_txs or []
        bars = bars or []
        historical_calls = historical_calls or []

        ts_now = datetime.now(UTC).isoformat()
        warnings: list[str] = []

        # Per-source evidence keyed by (source_type, source_id)
        evidence: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)

        # --- Social detectors ---
        coord_count, coord_ev = self._detect_coordinated_shilling(posts)
        for src, val in coord_ev.items():
            evidence[(SOURCE_SOCIAL_ACCOUNT, src)][PATTERN_COORDINATED_SHILLING] = val

        fake_count, fake_ev = self._detect_fake_engagement(posts)
        for src, val in fake_ev.items():
            evidence[(SOURCE_SOCIAL_ACCOUNT, src)][PATTERN_FAKE_ENGAGEMENT] = val

        bot_count, bot_ev = self._detect_bot_network(accounts, posts)
        for src, val in bot_ev.items():
            evidence[(SOURCE_SOCIAL_ACCOUNT, src)][PATTERN_BOT_NETWORK] = val

        # --- Market detectors ---
        wash_sig, wash_ev = self._detect_wash_trading(trades)
        for src, val in wash_ev.items():
            evidence[(SOURCE_MARKET_ACCOUNT, src)][PATTERN_WASH_TRADING] = val

        spoof_sig, spoof_ev = self._detect_spoofing(order_events)
        for src, val in spoof_ev.items():
            evidence[(SOURCE_MARKET_ACCOUNT, src)][PATTERN_SPOOFING] = val

        pump_sig, _pump_ev = self._detect_pump_and_dump(bars)

        # --- On-chain detectors ---
        wallet_count, wallet_ev = self._detect_abnormal_wallet(wallet_txs)
        for src, val in wallet_ev.items():
            evidence[(SOURCE_WALLET, src)][PATTERN_ABNORMAL_WALLET] = val

        insider_sig, insider_ev = self._detect_insider_behavior(wallet_txs, bars, target_symbol)
        for src, val in insider_ev.items():
            evidence[(SOURCE_WALLET, src)][PATTERN_INSIDER_BEHAVIOR] = val

        # --- Apply pump-and-dump signature to all flagged social sources for
        # the target symbol (they amplified a coordinated move) ---
        if pump_sig and pump_sig > 0.5 and target_symbol:
            for p in posts:
                if any(target_symbol.lower() in m.lower() for m in p.asset_mentions):
                    key = (SOURCE_SOCIAL_ACCOUNT, p.source_id)
                    cur = evidence[key].get(PATTERN_PUMP_AND_DUMP, 0.0)
                    evidence[key][PATTERN_PUMP_AND_DUMP] = max(cur, pump_sig * 0.5)

        # --- Historical reliability ---
        history = self._historical_reliability(historical_calls)

        # --- Build per-source reports ---
        # Posts/accounts/etc may name sources without any pattern flagged —
        # we still emit reports for sources with history or activity.
        all_sources: set[tuple[str, str]] = set(evidence.keys())
        for p in posts:
            all_sources.add((SOURCE_SOCIAL_ACCOUNT, p.source_id))
        for a in accounts:
            all_sources.add((SOURCE_SOCIAL_ACCOUNT, a.account_id))
        # Sources known only from historical calls still merit a report — the
        # caller asked us to track them.
        for c in historical_calls:
            all_sources.add((SOURCE_SOCIAL_ACCOUNT, c.source_id))

        sample_sizes_post: dict[str, int] = defaultdict(int)
        for p in posts:
            sample_sizes_post[p.source_id] += 1

        cfg = self._config
        source_reports: list[SourceTrustReport] = []
        for src_type, src_id in sorted(all_sources):
            ev = evidence.get((src_type, src_id), {})
            hist = history.get(src_id, (cfg.history_neutral_score, 0))
            n_samples = sample_sizes_post.get(src_id, 0)
            source_reports.append(self._aggregate_source(src_id, src_type, ev, hist, n_samples))

        if not posts and not trades and not order_events and not wallet_txs:
            warnings.append("no_input_data")

        return ManipulationReport(
            timestamp_utc=ts_now,
            target_symbol=target_symbol,
            coordinated_shilling_events=coord_count,
            fake_engagement_events=fake_count,
            bot_networks_detected=bot_count,
            wash_trading_signature=wash_sig,
            spoofing_signature=spoof_sig,
            pump_and_dump_signature=pump_sig,
            abnormal_wallet_flows=wallet_count,
            insider_behavior_signature=insider_sig,
            sources=source_reports,
            inputs_summary={
                "posts": len(posts),
                "accounts": len(accounts),
                "trades": len(trades),
                "order_events": len(order_events),
                "wallet_txs": len(wallet_txs),
                "bars": len(bars),
                "historical_calls": len(historical_calls),
                "patterns_known": len(ALL_PATTERNS),
            },
            warnings=warnings,
            inputs_hash=self._hash_inputs(
                posts, accounts, trades, order_events, wallet_txs, target_symbol
            ),
        )
