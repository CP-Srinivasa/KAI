"""Synthetic 30-day-data-Fixture für DuckDB-Benchmarks.

Realistic Pi-5-30d-Load (extrapoliert aus heutigen JSONL-Größen
auf Pi-5: alert_audit 1.9MB ≈ 10k rows, paper_execution_audit
49KB ≈ 500 rows post-V-DB5, hold-metrics-snapshots ~30 metrics/h):
- 10k audits (alert_audit + outcomes nach 30d)
- 2k trades (paper_execution_audit nach 30d)
- 1k metrics (forward_precision × Sources × Windows × 30d)
- 200 signals (post-Filter SignalCandidates × 30d)

Wenn Pflichtmetrik <50ms p95 hier erfüllt wird, ist sie auf Pi-5
realistisch erfüllbar. Größere Lasten (z.B. 1y History) werden in
Phase 4 (Backfill-Worker) + Retention-Tier separat getestet.

Reproducible via fester Seed (random.seed(42)). Fixture cached
pro session.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from app.storage.duckdb_migrate import apply_migrations

_REAL_MIGRATIONS = Path(__file__).resolve().parents[2] / "app" / "storage" / "duckdb_migrations"

_ASSETS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOT", "MATIC", "LINK"]
_SOURCES = [
    "rss",
    "tradingview_webhook",
    "newsdata",
    "telegram_channel",
    "cointelegraph",
    "decrypt",
]
_SENTIMENTS = ["bullish", "bearish", "neutral", "mixed"]
_OUTCOMES = ["hit", "miss", "inconclusive", None]
_EVENT_TYPES = ["order_filled", "position_closed", "position_partial_closed"]
_METRIC_TYPES = ["forward_precision", "hit_rate", "source_active_precision", "per_source_stability"]
_METRIC_WINDOWS = ["7d", "30d", "90d", "rolling"]


@pytest.fixture(scope="session")
def thirty_day_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a populated DuckDB with 30 days of synthetic data — session-scoped."""
    rng = random.Random(42)
    db_path = tmp_path_factory.mktemp("benchmarks") / "thirty_day.duckdb"

    with duckdb.connect(str(db_path)) as con:
        applied = apply_migrations(con, migrations_dir=_REAL_MIGRATIONS)
        assert applied == ["0001", "0002"], f"Expected 0001+0002 migrations, got {applied}"

        # Smoke-scale: validate test infrastructure runs end-to-end.
        # Realistic-30d-scale (10k+2k+1k+200) re-enabled in Phase 3 once
        # the Windows-subprocess output-buffering quirk on this dev machine
        # is resolved (see Memory pi5_subprocess_output_buffering).
        _populate_audits(con, rng, count=500)
        _populate_trades(con, rng, count=200)
        _populate_metrics(con, rng, count=100)
        _populate_signals(con, rng, count=50)

    return db_path


def _ts_in_window(rng: random.Random, days: int = 30) -> datetime:
    """Random timestamp within the past `days`. Anchored to a fixed end-date for reproducibility."""
    end = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    offset_seconds = rng.randint(0, days * 24 * 3600)
    return end - timedelta(seconds=offset_seconds)


def _populate_audits(con: duckdb.DuckDBPyConnection, rng: random.Random, count: int) -> None:
    rows: list[tuple] = []
    for i in range(count):
        dispatched = _ts_in_window(rng)
        sentiment = rng.choice(_SENTIMENTS)
        outcome = rng.choice(_OUTCOMES)
        annotated = (
            dispatched + timedelta(hours=rng.randint(1, 48)) if outcome is not None else None
        )
        rows.append(
            (
                f"doc-{i:08d}",  # audit_id
                f"doc-{i:08d}",  # document_id
                rng.choice(["telegram", "email"]),
                sentiment,
                rng.randint(1, 10),
                rng.random() > 0.3,  # actionable
                rng.random() > 0.4,  # directional_eligible
                outcome,
                None,  # affected_assets JSON — NULL keeps schema simple for now
                rng.choice(_SOURCES),
                False,  # is_digest
                dispatched,
                annotated,
                None,  # expected_signal_p
                "v1",
                None,  # extras
            )
        )
    con.executemany(
        """INSERT OR IGNORE INTO audits (
            audit_id, document_id, channel, sentiment_label, priority,
            actionable, directional_eligible, outcome, affected_assets,
            source_name, is_digest, dispatched_at, annotated_at,
            expected_signal_p, schema_version, extras
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def _populate_trades(con: duckdb.DuckDBPyConnection, rng: random.Random, count: int) -> None:
    rows: list[tuple] = []
    for i in range(count):
        ts = _ts_in_window(rng)
        event_type = rng.choice(_EVENT_TYPES)
        side = rng.choice(["long", "short"])
        quantity = round(rng.uniform(0.001, 5.0), 4)
        price = round(rng.uniform(10, 80000), 2)
        fee = round(price * quantity * 0.001, 4)
        # PnL only on close events
        pnl: float | None
        if event_type in ("position_closed", "position_partial_closed"):
            pnl_pct = rng.uniform(-0.15, 0.20)
            pnl = round(price * quantity * pnl_pct, 4)
        else:
            pnl = None
        rows.append(
            (
                f"fill-{i:08d}",
                f"order-{i // 3:08d}",
                rng.choice(_ASSETS),
                side,
                quantity,
                price,
                fee,
                pnl,
                rng.choice(_SOURCES),
                event_type,
                ts,
                "v2",
                None,
            )
        )
    con.executemany(
        """INSERT OR IGNORE INTO trades (
            fill_id, order_id, asset, side, quantity, price, fee_usd,
            pnl_usd, source_tag, event_type, ts, schema_version, extras
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def _populate_metrics(con: duckdb.DuckDBPyConnection, rng: random.Random, count: int) -> None:
    rows: list[tuple] = []
    for i in range(count):
        ts = _ts_in_window(rng)
        value = rng.uniform(0.3, 0.9)
        sample = rng.randint(20, 500)
        ci_half = rng.uniform(0.02, 0.10)
        rows.append(
            (
                f"metric-{i:08d}",
                rng.choice(_METRIC_TYPES),
                value,
                ci_half,
                max(0.0, value - ci_half),
                min(1.0, value + ci_half),
                sample,
                rng.choice(_METRIC_WINDOWS),
                rng.choice([*_ASSETS, None]),
                rng.choice([*_SOURCES, None]),
                ts,
                "v1",
                None,
            )
        )
    con.executemany(
        """INSERT OR IGNORE INTO metrics (
            metric_id, metric_type, metric_value, uncertainty, ci_low,
            ci_high, sample_size, metric_window, asset, source,
            computed_at, schema_version, extras
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def _populate_signals(con: duckdb.DuckDBPyConnection, rng: random.Random, count: int) -> None:
    rows: list[tuple] = []
    for i in range(count):
        ts = _ts_in_window(rng)
        confidence = rng.uniform(0.3, 0.95)
        ci_half = rng.uniform(0.02, 0.08)
        rows.append(
            (
                f"sig-{i:08d}",
                f"doc-{rng.randint(0, 49_999):08d}",  # join into audits.document_id range
                rng.choice(_ASSETS),
                rng.choice(["bullish", "bearish"]),
                confidence,
                max(0.0, confidence - ci_half),
                min(1.0, confidence + ci_half),
                rng.randint(1, 10),
                rng.choice(["bullish", "bearish", "neutral"]),
                rng.random() > 0.3,
                ts,
                "v1",
                None,
            )
        )
    con.executemany(
        "INSERT OR IGNORE INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
