"""ADR 0003 Pflichtmetrik: Query-Latency p95 < 50ms.

Benchmarks die analytischen Read-Queries gegen 30 Tage Synthetic-Data.
Jede Query wird N=50 Mal ausgeführt, p95 errechnet, Pflichtmetrik geprüft.

Wenn die Pflichtmetrik nicht hält:
- Test failt sofort
- Implementation ist nicht ready für Phase-7-Cutover
- Re-Design oder DDL-Anpassung nötig (bessere Indexes, Partitioning,
  ggf. Materialized Views)

Wichtig: keine Skip-Marker bei "langsamer Hardware" — Pflichtmetrik gilt
auf Pi-5 (4-Core-ARM64). Lokale Test-Hardware ist x86_64 mit ähnlichem
oder besserem Profil; wenn lokal failt, fällt's auf Pi-5 garantiert auch.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

import duckdb

# Pflicht-Schwelle aus ADR 0003
P95_LATENCY_MS_TARGET = 50.0
WARMUP_ITERATIONS = 5
MEASURE_ITERATIONS = 50


def _measure_query_latency_ms(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    *,
    iterations: int = MEASURE_ITERATIONS,
    warmup: int = WARMUP_ITERATIONS,
) -> dict[str, float]:
    """Run query N times, return min/p50/p95/p99/max in milliseconds."""
    # Warmup — primes any caches/zone-maps
    for _ in range(warmup):
        con.execute(sql).fetchall()

    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        con.execute(sql).fetchall()
        end = time.perf_counter()
        samples.append((end - start) * 1000.0)

    samples.sort()
    return {
        "min": samples[0],
        "p50": samples[len(samples) // 2],
        "p95": samples[int(len(samples) * 0.95)],
        "p99": samples[int(len(samples) * 0.99)],
        "max": samples[-1],
        "mean": statistics.mean(samples),
    }


def _open_readonly(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open connection in read-only mode (analog production read-path)."""
    return duckdb.connect(str(db_path), read_only=True)


# ---------------------------------------------------------------------------
# Pflichtmetrik-Benchmarks: 5 Dashboard-Queries
# ---------------------------------------------------------------------------


def test_realized_pnl_query_p95_under_50ms(thirty_day_db: Path) -> None:
    """Aggregat: total realized PnL + closed-position-Count."""
    sql = """
        SELECT
            SUM(pnl_usd) FILTER (WHERE pnl_usd IS NOT NULL) AS total_pnl,
            COUNT(*) FILTER (
                WHERE event_type IN ('position_closed', 'position_partial_closed')
            ) AS closes
        FROM trades
    """
    with _open_readonly(thirty_day_db) as con:
        stats = _measure_query_latency_ms(con, sql)
    print(
        f"\n[realized_pnl] min={stats['min']:.2f} p50={stats['p50']:.2f} "
        f"p95={stats['p95']:.2f} p99={stats['p99']:.2f} max={stats['max']:.2f} ms"
    )
    assert stats["p95"] < P95_LATENCY_MS_TARGET, (
        f"realized_pnl p95={stats['p95']:.2f}ms exceeds {P95_LATENCY_MS_TARGET}ms target"
    )


def test_attribution_pnl_query_p95_under_50ms(thirty_day_db: Path) -> None:
    """Aggregat: PnL grouped by source_tag + win/loss-Counts."""
    sql = """
        SELECT
            COALESCE(NULLIF(TRIM(source_tag), ''), 'unknown') AS tag,
            SUM(pnl_usd) FILTER (WHERE pnl_usd IS NOT NULL) AS total_pnl,
            COUNT(CASE WHEN pnl_usd > 0 THEN 1 END) AS wins,
            COUNT(CASE WHEN pnl_usd < 0 THEN 1 END) AS losses
        FROM trades
        WHERE event_type IN ('position_closed', 'position_partial_closed')
        GROUP BY tag
    """
    with _open_readonly(thirty_day_db) as con:
        stats = _measure_query_latency_ms(con, sql)
    print(
        f"\n[attribution_pnl] min={stats['min']:.2f} p50={stats['p50']:.2f} "
        f"p95={stats['p95']:.2f} p99={stats['p99']:.2f} max={stats['max']:.2f} ms"
    )
    assert stats["p95"] < P95_LATENCY_MS_TARGET, (
        f"attribution_pnl p95={stats['p95']:.2f}ms exceeds {P95_LATENCY_MS_TARGET}ms target"
    )


def test_recent_alerts_query_p95_under_50ms(thirty_day_db: Path) -> None:
    """ORDER-BY-DESC Query: 20 neueste alerts."""
    sql = """
        SELECT
            document_id,
            sentiment_label,
            priority,
            affected_assets,
            dispatched_at,
            COALESCE(outcome, '') AS outcome
        FROM audits
        WHERE is_digest = FALSE
        ORDER BY dispatched_at DESC
        LIMIT 20
    """
    with _open_readonly(thirty_day_db) as con:
        stats = _measure_query_latency_ms(con, sql)
    print(
        f"\n[recent_alerts] min={stats['min']:.2f} p50={stats['p50']:.2f} "
        f"p95={stats['p95']:.2f} p99={stats['p99']:.2f} max={stats['max']:.2f} ms"
    )
    assert stats["p95"] < P95_LATENCY_MS_TARGET, (
        f"recent_alerts p95={stats['p95']:.2f}ms exceeds {P95_LATENCY_MS_TARGET}ms target"
    )


def test_metrics_latest_per_type_p95_under_50ms(thirty_day_db: Path) -> None:
    """Window-function-Query: latest metric per (type, window, source)."""
    sql = """
        SELECT metric_type, metric_window, source, metric_value, ci_low, ci_high, sample_size
        FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY metric_type, metric_window, source
                    ORDER BY computed_at DESC
                ) AS rn
            FROM metrics
        )
        WHERE rn = 1
    """
    with _open_readonly(thirty_day_db) as con:
        stats = _measure_query_latency_ms(con, sql)
    print(
        f"\n[metrics_latest] min={stats['min']:.2f} p50={stats['p50']:.2f} "
        f"p95={stats['p95']:.2f} p99={stats['p99']:.2f} max={stats['max']:.2f} ms"
    )
    assert stats["p95"] < P95_LATENCY_MS_TARGET, (
        f"metrics_latest p95={stats['p95']:.2f}ms exceeds {P95_LATENCY_MS_TARGET}ms target"
    )


def test_pnl_daily_view_p95_under_50ms(thirty_day_db: Path) -> None:
    """View-Query: 30d daily PnL via pnl_daily-View."""
    sql = """
        SELECT day, source_tag, asset, realized_pnl_usd, closes, opens, total_fees_usd
        FROM pnl_daily
        ORDER BY day DESC, realized_pnl_usd DESC NULLS LAST
        LIMIT 100
    """
    with _open_readonly(thirty_day_db) as con:
        stats = _measure_query_latency_ms(con, sql)
    print(
        f"\n[pnl_daily_view] min={stats['min']:.2f} p50={stats['p50']:.2f} "
        f"p95={stats['p95']:.2f} p99={stats['p99']:.2f} max={stats['max']:.2f} ms"
    )
    assert stats["p95"] < P95_LATENCY_MS_TARGET, (
        f"pnl_daily_view p95={stats['p95']:.2f}ms exceeds {P95_LATENCY_MS_TARGET}ms target"
    )


def test_audits_join_with_outcome_filter_p95_under_50ms(thirty_day_db: Path) -> None:
    """Join-Query: audits mit Asset-Filter + Outcome-Group-By."""
    sql = """
        SELECT
            outcome,
            COUNT(*) AS n,
            COUNT(DISTINCT document_id) AS distinct_docs
        FROM audits
        WHERE dispatched_at > (NOW() - INTERVAL 7 DAY)
            AND is_digest = FALSE
        GROUP BY outcome
    """
    with _open_readonly(thirty_day_db) as con:
        stats = _measure_query_latency_ms(con, sql)
    print(
        f"\n[audits_outcome] min={stats['min']:.2f} p50={stats['p50']:.2f} "
        f"p95={stats['p95']:.2f} p99={stats['p99']:.2f} max={stats['max']:.2f} ms"
    )
    assert stats["p95"] < P95_LATENCY_MS_TARGET, (
        f"audits_outcome p95={stats['p95']:.2f}ms exceeds {P95_LATENCY_MS_TARGET}ms target"
    )


# ---------------------------------------------------------------------------
# Aggregat-Test: alle 6 Queries zusammen p95 < 50ms
# ---------------------------------------------------------------------------


def test_dashboard_query_set_total_under_50ms_per_query(thirty_day_db: Path) -> None:
    """Realistic dashboard-load: 6 queries sequenziell. Keine darf > 50ms p95."""
    queries = {
        "realized_pnl": "SELECT SUM(pnl_usd) FROM trades WHERE pnl_usd IS NOT NULL",
        "loop_status": "SELECT event_type, COUNT(*) FROM trades GROUP BY event_type",
        "fills_count": "SELECT COUNT(*) FROM trades WHERE event_type = 'order_filled'",
        "audit_total": "SELECT COUNT(*) FROM audits",
        "metrics_count": "SELECT metric_type, COUNT(*) FROM metrics GROUP BY metric_type",
        "signal_count": "SELECT asset, COUNT(*) FROM signals GROUP BY asset",
    }

    failures: list[str] = []
    with _open_readonly(thirty_day_db) as con:
        for name, sql in queries.items():
            stats = _measure_query_latency_ms(con, sql, iterations=20, warmup=3)
            print(f"\n[{name}] p95={stats['p95']:.2f}ms p99={stats['p99']:.2f}ms")
            if stats["p95"] >= P95_LATENCY_MS_TARGET:
                failures.append(f"{name}: p95={stats['p95']:.2f}ms")

    assert not failures, f"Pflichtmetrik <50ms verfehlt für: {', '.join(failures)}"


# ---------------------------------------------------------------------------
# Sanity-Check: Synthetic-Data ist tatsächlich da
# ---------------------------------------------------------------------------


def test_synthetic_30d_db_has_expected_volumes(thirty_day_db: Path) -> None:
    """Verify the fixture actually populated the expected scale (env-driven)."""
    from tests.benchmarks.conftest import _ACTIVE_SCALE, DUCKDB_BENCH_SCALE

    with _open_readonly(thirty_day_db) as con:
        audits = con.execute("SELECT COUNT(*) FROM audits").fetchone()[0]
        trades = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        metrics = con.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        signals = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]

    print(
        f"\n[scale={DUCKDB_BENCH_SCALE}] audits={audits} trades={trades} "
        f"metrics={metrics} signals={signals}"
    )
    assert audits == _ACTIVE_SCALE["audits"], (
        f"Expected {_ACTIVE_SCALE['audits']} audits, got {audits}"
    )
    assert trades == _ACTIVE_SCALE["trades"], (
        f"Expected {_ACTIVE_SCALE['trades']} trades, got {trades}"
    )
    assert metrics == _ACTIVE_SCALE["metrics"], (
        f"Expected {_ACTIVE_SCALE['metrics']} metrics, got {metrics}"
    )
    assert signals == _ACTIVE_SCALE["signals"], (
        f"Expected {_ACTIVE_SCALE['signals']} signals, got {signals}"
    )


# ---------------------------------------------------------------------------
# ADR 0003 Pflicht: Memory-Constraint <2 GB RAM
# ---------------------------------------------------------------------------

P95_MEMORY_MB_TARGET = 2048.0  # 2 GB pro ADR 0003


def test_dashboard_query_set_memory_under_2gb(thirty_day_db: Path) -> None:
    """ADR 0003 Pflichtmetrik: Read-Pfad bleibt unter 2 GB RAM.

    Misst den Peak-RSS-Wachstum waehrend ein realistic dashboard query-set
    sequenziell laeuft. RSS=Resident Set Size, das ist die OS-Sicht auf
    den realen RAM-Verbrauch — kein Python-internes heap-tracking.
    """
    import os

    import psutil

    proc = psutil.Process(os.getpid())
    baseline_mb = proc.memory_info().rss / 1024 / 1024

    queries = [
        "SELECT SUM(pnl_usd) FROM trades WHERE pnl_usd IS NOT NULL",
        "SELECT event_type, COUNT(*) FROM trades GROUP BY event_type",
        "SELECT COUNT(*) FROM trades WHERE event_type = 'order_filled'",
        "SELECT COUNT(*) FROM audits",
        "SELECT metric_type, COUNT(*) FROM metrics GROUP BY metric_type",
        "SELECT asset, COUNT(*) FROM signals GROUP BY asset",
        "SELECT day, source_tag FROM pnl_daily ORDER BY day DESC LIMIT 100",
    ]

    peak_mb = baseline_mb
    with _open_readonly(thirty_day_db) as con:
        for _ in range(5):  # 5 wiederholungen = realistische dashboard-poll
            for sql in queries:
                con.execute(sql).fetchall()
                current_mb = proc.memory_info().rss / 1024 / 1024
                peak_mb = max(peak_mb, current_mb)

    delta_mb = peak_mb - baseline_mb
    print(
        f"\n[memory] baseline={baseline_mb:.1f}MB peak={peak_mb:.1f}MB "
        f"delta={delta_mb:.1f}MB target<{P95_MEMORY_MB_TARGET:.0f}MB"
    )
    assert peak_mb < P95_MEMORY_MB_TARGET, (
        f"peak RSS {peak_mb:.1f}MB exceeds ADR 0003 target {P95_MEMORY_MB_TARGET:.0f}MB"
    )
