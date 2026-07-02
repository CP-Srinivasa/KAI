# ADR 0003 — DuckDB als Analytical-Read-Layer + JSONL-WAL als Source-of-Truth

- **Datum**: 2026-05-09
- **Status**: Accepted (retroaktiv für `5bff7c1` MVP, prospektiv für Neo-Migration v2)
- **Kontext**: Operator-Diagramm 2026-05-09 ~14:55 UTC + Architect-Review (ART-DUCKDB-001..012) + Neo-Implementations-Auftrag 2026-05-09 ~17:30 UTC
- **Entscheidung**: D-2026-05-09-DuckDB-Storage-Pivot

## Kontext

KAI hat heute zwei aktive Storage-Layer:

1. **PostgreSQL** über SQLAlchemy + Alembic für transaktionales Domänenmodell (`canonical_documents`, `analyses`, `signals`, `paper_fills`, `LLMAuditRecord`-WIP).
2. **JSONL-Append-Only-Files** in `artifacts/` als Audit-WAL: `paper_execution_audit.jsonl` (Source-of-Truth via `paper_engine.rehydrate_from_audit`), `trading_loop_audit.jsonl` (2.27 MB lokal, 16 MB `api_request_audit.jsonl` auf Pi-5), `alert_audit.jsonl`, `alert_outcomes.jsonl`, `decision_journal.jsonl`, plus pro-Subagent-`findings.jsonl`.

Memory-Schmerz `kai_market_data_provider_symmetry.md`: *"Audit-File-Wachstum ungebremst. trading_loop_audit.jsonl wird per Status-Call vollständig geparsed. Lösung wäre Snapshot-DB-Strategie, eigener Refactor-Sprint."*

Operator hat 2026-05-09 15:57 CEST den MVP `5bff7c1` committet (`app/storage/analytics_db.py` + `app/storage/compaction_worker.py` + `pyproject.toml duckdb>=1.1.0`), ohne ADR. Architecture-red-team-Review hat 12 Findings dokumentiert, davon 4 crit. Operator hat 2026-05-09 ~17:30 CEST Neo beauftragt, die Pflicht-Implementation zu liefern mit konkreten Pflichtmetriken.

## Entscheidung

**Drei-Schicht-Storage-Architektur:**

1. **PostgreSQL bleibt SoT** für transaktionales Domänenmodell (canonical_documents, signals, fills, LLMAuditRecord). Alembic-Migrations bleiben einziger Pfad für PG-Schema-Änderungen.
2. **JSONL bleibt immutable WAL** in `artifacts/`. Append-Only, portalocker LOCK_EX bei Writes (etabliert via V-DB5 B-K2 in `app/alerts/audit.py`). Keine Mutation, keine Inline-Compaction. Logrotate verboten für `paper_execution_audit.jsonl` (rehydrate-SoT). Andere JSONLs **dürfen** rotiert werden, sobald DuckDB inkrementelle Compaction mit Watermark hat.
3. **DuckDB ist read-only Analytical-Layer** in `artifacts/analytics.duckdb` (MVP) bzw. `/mnt/usbssd/analytics.duckdb` (Phase 4+, USB-SSD). Geschrieben **ausschließlich** vom dedizierten `kai-compaction-worker.service` (Single-Writer). Alle anderen Prozesse (kai-server, kai-agent-worker, kai-paper-trading.timer, Backtest-Skripte) nutzen `duckdb.connect(..., read_only=True)`.

**Kompatibilität:** der `5bff7c1`-MVP bleibt im Code als Phase-1-Anchor, wird aber durch Neo-v2 fundamental überarbeitet (siehe Pflichtmetriken-Block).

## Pflichtmetriken (Operator-Direktive 2026-05-09)

Akzeptanzkriterien für jede Phase-Implementation:

| Metrik | Target | Mess-Pfad |
|---|---|---|
| **RAM-Footprint nach 30 Tagen** | **< 2 GB** | `pytest --benchmark-only tests/benchmarks/test_duckdb_ram.py`; pi5-RSS-Snapshot via `systemctl status kai-compaction-worker` |
| **Query-Latenz (Dashboard-Reads)** | **< 50 ms** p95 | `pytest --benchmark-only tests/benchmarks/test_duckdb_query_latency.py` mit synthetischen 30d-Daten |
| **CPU-Idle-Impact** | **< 5 %** | `top -b -n10 -d6 \| grep kai-compaction-worker` während Idle-Phase |
| **Crash-Safe Recovery** | **kein Daten-Verlust nach unsauberem Shutdown** | `tests/integration/test_duckdb_crash_recovery.py` mit kill-9 + Re-Start + WAL-Replay-Diff |

Diese Metriken sind **harte Gates** für Phase 4 (Cutover). Wenn nicht erfüllt: Phase wird gehalten, Optimierung oder Re-Design (Crosscheck `kai-no-prediction.md` §6 — keine Schönfärberei).

## 6 Tabellen im DuckDB (Operator-Mandat)

```sql
-- 1. ticks: high-frequency market data (later phase, MVP skipped)
CREATE TABLE ticks (
    ts             TIMESTAMP NOT NULL,
    symbol         VARCHAR(32) NOT NULL,
    price          DOUBLE NOT NULL,
    volume         DOUBLE,
    source         VARCHAR(32) NOT NULL,
    schema_version VARCHAR(8) NOT NULL DEFAULT 'v1',
    PRIMARY KEY (ts, symbol, source)
);

-- 2. signals: emitted SignalCandidates (currently in PG, mirrored via compaction)
CREATE TABLE signals (
    signal_id      VARCHAR(64) PRIMARY KEY,
    document_id    VARCHAR(64),
    asset          VARCHAR(32) NOT NULL,
    side           VARCHAR(8) NOT NULL,
    confidence     DOUBLE NOT NULL,
    priority       INTEGER,
    created_at     TIMESTAMP NOT NULL,
    schema_version VARCHAR(8) NOT NULL DEFAULT 'v1'
);

-- 3. trades: paper-engine fills (from paper_execution_audit.jsonl)
CREATE TABLE trades (
    fill_id        VARCHAR(64) PRIMARY KEY,
    order_id       VARCHAR(64) NOT NULL,
    asset          VARCHAR(32) NOT NULL,
    side           VARCHAR(8) NOT NULL,
    quantity       DOUBLE NOT NULL,
    price          DOUBLE NOT NULL,
    fee_usd        DOUBLE NOT NULL DEFAULT 0,
    pnl_usd        DOUBLE,
    source_tag     VARCHAR(64),
    event_type     VARCHAR(32) NOT NULL,
    ts             TIMESTAMP NOT NULL,
    schema_version VARCHAR(8) NOT NULL DEFAULT 'v2'
);

-- 4. pnl: aggregated daily/weekly PnL (computed views)
CREATE VIEW pnl_daily AS
    SELECT
        DATE_TRUNC('day', ts) AS day,
        source_tag,
        SUM(pnl_usd) FILTER (WHERE pnl_usd IS NOT NULL) AS realized_pnl_usd,
        COUNT(*) FILTER (WHERE event_type IN ('position_closed', 'position_partial_closed')) AS closes
    FROM trades
    GROUP BY day, source_tag;

-- 5. metrics: forward-precision, hit-rate, watchdog-findings (operational monitoring)
CREATE TABLE metrics (
    metric_id      VARCHAR(64) PRIMARY KEY,
    metric_type    VARCHAR(32) NOT NULL,  -- forward_precision, hit_rate, source_active_precision
    metric_value   DOUBLE NOT NULL,
    metric_window  VARCHAR(16),  -- 7d, 30d, 90d
    asset          VARCHAR(32),  -- nullable, NULL = portfolio-level
    source         VARCHAR(32),  -- nullable, NULL = aggregate
    computed_at    TIMESTAMP NOT NULL,
    schema_version VARCHAR(8) NOT NULL DEFAULT 'v1'
);

-- 6. audits: alerts dispatched + outcomes (from alert_audit + alert_outcomes JSONLs)
CREATE TABLE audits (
    audit_id       VARCHAR(64) PRIMARY KEY,
    document_id    VARCHAR(64) NOT NULL,
    channel        VARCHAR(16) NOT NULL,  -- telegram, email
    sentiment      VARCHAR(16),
    priority       INTEGER,
    actionable     BOOLEAN,
    outcome        VARCHAR(16),  -- hit, miss, inconclusive, NULL
    affected_assets JSON,  -- list[str]
    dispatched_at  TIMESTAMP NOT NULL,
    annotated_at   TIMESTAMP,
    schema_version VARCHAR(8) NOT NULL DEFAULT 'v1'
);

-- Indexes für <50ms-Queries
CREATE INDEX idx_trades_ts ON trades(ts);
CREATE INDEX idx_trades_asset ON trades(asset);
CREATE INDEX idx_audits_dispatched ON audits(dispatched_at DESC);
CREATE INDEX idx_audits_document ON audits(document_id);
CREATE INDEX idx_metrics_type_window ON metrics(metric_type, metric_window, computed_at DESC);
CREATE INDEX idx_signals_asset_created ON signals(asset, created_at DESC);
```

## Compaction Strategy

**Inkrementell statt Full-Replace** (ART-DUCKDB-001 fix):

- **Watermark-Tabelle** `_compaction_watermark(source VARCHAR PRIMARY KEY, last_byte_offset BIGINT, last_event_id VARCHAR, last_run_at TIMESTAMP)`.
- **Compaction-Loop pro Source** (jedes JSONL):
  1. Read `last_byte_offset` aus Watermark.
  2. `seek(last_byte_offset)` im JSONL-File.
  3. Stream-parse neue Lines (tolerant gegen halb-geschriebene letzte Zeile via `app/storage/jsonl_io.py:read_jsonl_tolerant`-Pattern).
  4. `INSERT OR IGNORE` in DuckDB-Table (PRIMARY KEY-Konflikt = Idempotency).
  5. Update Watermark mit `current_byte_offset` + `last_event_id` + jetzt-TS.
  6. Commit transaction.
- **Crash-Recovery:** bei Compaction-Worker-Restart wird `last_byte_offset` gelesen. JSONL-Append-Only-Garantie (portalocker LOCK_EX) sichert: Bytes < `last_byte_offset` sind bereits in DuckDB. Schlimmstenfalls werden ein paar Zeilen doppelt gelesen → INSERT OR IGNORE schluckt das.
- **Schema-Drift-Behandlung:** explicit DDL pro Source (siehe Tabellen oben), `read_json_auto` nur als Fallback für unbekannte Felder in einer `extras: JSON`-Spalte.

## Retention + Parquet-Export

- **Hot-Tier (DuckDB):** 6 Monate rolling. Tagesweise CLEANUP-Job nach Compaction: `DELETE FROM trades WHERE ts < NOW() - INTERVAL 6 MONTH; CHECKPOINT;`.
- **Cold-Tier (Parquet):** vor DELETE wird `COPY (SELECT * FROM trades WHERE ts BETWEEN ...) TO 'archive/trades_YYYYMM.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);`.
- **Backup:** täglich `EXPORT DATABASE 'backup/analytics_YYYYMMDD/' (FORMAT PARQUET, COMPRESSION ZSTD)` als atomic-snapshot, dann `tar.zst → /mnt/backup/`.
- **JSONL-WAL-Logrotate**: `paper_execution_audit.jsonl` **nie rotieren** (rehydrate-SoT, gilt weiter). Andere JSONLs (alert_audit, trading_loop_audit, api_request_audit) dürfen rotiert werden, **wenn** Watermark-Compaction stable läuft (Phase 3+).

## Operator-Decisions, die diese ADR voraussetzt

Aus den 10 Architect-Review-Decisions (`artifacts/agents/architecture-red-team/findings.jsonl` IDs ART-DUCKDB-*) plus Operator-Direktive 2026-05-09 17:30 CEST:

1. ✅ **PG-Rolle**: bleibt SoT für transaktionales Domänenmodell.
2. ✅ **Compaction-Owner**: dedizierter `kai-compaction-worker.service` (NICHT mehr in kai-server-Lifespan wie `5bff7c1`).
3. ✅ **Compaction-Strategie**: inkrementell mit Watermark + INSERT OR IGNORE.
4. 🟡 **DuckDB-Storage**: SD-Karte für MVP, Phase-4-Plan auf USB-SSD migrieren. Pi-5 hat keinen USB-SSD heute — Operator-Decision-Pin.
5. ✅ **Schema-Governance**: explizite DDL + `app/storage/duckdb_migrations/`-Verzeichnis.
6. ✅ **JSONL-Logrotate**: paper_execution_audit nie, andere wenn stable.
7. 🟡 **TimescaleDB-Crosscheck**: 4-Wochen-Deadline auf Showstopper-Fixes — bei Verfehlung Migration-Prototyp.
8. ✅ **Retention**: 6 Monate hot, Parquet-Cold.
9. ✅ **Watchdog-Probe**: `compaction_mtime > 5min` warnt, > 15min crit. Reuse `forward_precision_watchdog.StreakState`.
10. ✅ **Schatten-Modus-Toleranz**: PnL DuckDB vs. JSONL-Direkt-Parse Δ < 0.01 USD pro Trade über 7 Tage.

## Implementation-Phasen (Neo-Mandat)

| Phase | Inhalt | Pflichtmetrik-Test | Aufwand |
|---|---|---|---|
| **0** | Diese ADR + Operator-GO | ADR-Sign-off | done |
| **1** | Schema-DDL als Migrations · `app/storage/duckdb_migrations/0001_initial_schema.sql` · `app/storage/duckdb_migrate.py` · Schema-Drift-Test | DDL läuft sauber durch, keine Type-Inferenz mehr | 1-2 Tage |
| **2** | Benchmark-Suite mit synthetic-30d-data · `tests/benchmarks/test_duckdb_ram.py`, `test_duckdb_query_latency.py` · CI-Schwellen | <2 GB RAM, <50ms p95 query | 1-2 Tage |
| **3** | Inkrementelle Compaction v2 · Watermark-Tabelle · `app/storage/compaction_v2.py` · `kai-compaction-worker.service` als single-writer | <5% CPU idle | 3-4 Tage |
| **4** | Backfill-Worker für historische JSONLs · `app/storage/backfill.py` · CLI `kai cli storage backfill` | volle Historie in DuckDB innerhalb 1h für 30d-JSONL | 2-3 Tage |
| **5** | Retention + Parquet-Export · `app/storage/retention.py` · Daily-Timer-Service | 6mo hot · Parquet pro Monat | 1-2 Tage |
| **6** | Crash-Recovery + Corrupt-WAL-Tests · `tests/integration/test_duckdb_crash_recovery.py` · `tests/unit/test_jsonl_corruption_handling.py` | crash-safe-Pflichtmetrik | 1-2 Tage |
| **7** | Pi-5-Deploy: pip install, systemd-Service, Schatten-Modus 7d, Cutover | Diff < 0.01 USD/trade | 2-3 Tage |

**Total realistic: 11-18 Tage.** Wenn unterschritten → Schönfärberei (verstößt §10).

## Ablauf-Konsequenzen für laufende Tracks

- **Phase-1 Master-Direktive (Evidence-Schema)**: läuft parallel, Coupling über `audits`-Tabelle (LLMAuditRecord wird PG-Master, DuckDB-Mirror via Compaction).
- **IQE Re-Design**: Operator-Decisions stehen aus. IQE braucht das `audits`-Schema. Sequenziell **nach** DuckDB-Phase-3.
- **V-DB5-Backend-Rescue**: P0 morgen früh, unabhängig von DuckDB.
- **F4-Verbose-Observer Disable**: morgen ~07:20 UTC, unabhängig.

## Status

- 2026-05-09: ADR retroaktiv für `5bff7c1`-MVP geschrieben + Operator-GO-Pflicht für Neo-Phase-1.
- 2026-06-09 (T+30d): Pflichtmetriken-Audit. Wenn nicht erfüllt → ADR-Re-Open + ggf. TimescaleDB-Pivot.

## Cross-Refs

- Architect-Review: `artifacts/agents/architecture-red-team/findings.jsonl` IDs ART-DUCKDB-001..012 (12 Findings, 4 crit + 4 warn + 2 info + 2 zusätzliche aus Pi-5-Diagnose)
- Memory: `duckdb_pivot_drift_20260509.md` (P0-Drift dokumentiert + Pi-5-OOS-Korrektur)
- Memory: `kai_market_data_provider_symmetry.md` (audit-file-Wachstum-Schmerz, paper_engine.rehydrate-Pattern)
- Memory: `feedback_kai_no_prediction.md` (Calibration-Pflicht für `metrics`-Tabelle)
- KAI Master-Direktive § H (Watchdog-Rolle, Pipeline-Health), § I (Testing-Pflicht)
- Sister-ADRs: 0001 (TradingView), 0002 (Signal-Consensus experimental)
