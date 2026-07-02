-- KAI DuckDB analytics schema — initial migration
-- ADR 0003 (2026-05-09) drei-Schicht-Storage-Architektur
-- PG = transaktionales SoT · JSONL = immutable WAL · DuckDB = analytical read-layer
--
-- Compaction-v2 (Phase 3) füllt diese Tabellen inkrementell aus den JSONL-WALs
-- via _compaction_watermark + INSERT OR IGNORE (idempotent, crash-safe).
-- Single-Writer: nur kai-compaction-worker.service schreibt; alle anderen
-- Prozesse öffnen mit duckdb.connect(..., read_only=True).

-- ===================================================================
-- Migration-Tracking (Alembic-Pendant für DuckDB)
-- ===================================================================

CREATE TABLE IF NOT EXISTS _schema_versions (
    version       VARCHAR PRIMARY KEY,
    applied_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description   VARCHAR NOT NULL,
    applied_by    VARCHAR
);

-- ===================================================================
-- Compaction Watermark — pro JSONL-Source ein Eintrag
-- ===================================================================

CREATE TABLE IF NOT EXISTS _compaction_watermark (
    source            VARCHAR PRIMARY KEY,
    last_byte_offset  BIGINT NOT NULL DEFAULT 0,
    last_event_id     VARCHAR,
    last_run_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rows_processed    BIGINT NOT NULL DEFAULT 0,
    rows_skipped_dup  BIGINT NOT NULL DEFAULT 0,
    last_error        VARCHAR
);

-- ===================================================================
-- 1. ticks — high-frequency market data (Phase 5+, MVP nicht aktiv gefüllt)
-- ===================================================================

CREATE TABLE IF NOT EXISTS ticks (
    ts             TIMESTAMP NOT NULL,
    symbol         VARCHAR NOT NULL,
    price          DOUBLE NOT NULL,
    volume         DOUBLE,
    source         VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL DEFAULT 'v1',
    extras         JSON,
    PRIMARY KEY (ts, symbol, source)
);

-- ===================================================================
-- 2. signals — emitted SignalCandidates (PG ist SoT, DuckDB ist Mirror)
-- ===================================================================

CREATE TABLE IF NOT EXISTS signals (
    signal_id      VARCHAR PRIMARY KEY,
    document_id    VARCHAR,
    asset          VARCHAR NOT NULL,
    side           VARCHAR NOT NULL,         -- bullish | bearish
    confidence     DOUBLE NOT NULL,
    confidence_low DOUBLE,                   -- CI-Lower (KAI-No-Prediction §3)
    confidence_high DOUBLE,                  -- CI-Upper
    priority       INTEGER,
    sentiment      VARCHAR,
    actionable     BOOLEAN,
    created_at     TIMESTAMP NOT NULL,
    schema_version VARCHAR NOT NULL DEFAULT 'v1',
    extras         JSON
);

-- ===================================================================
-- 3. trades — paper-engine fills (compacted aus paper_execution_audit.jsonl)
-- ===================================================================

CREATE TABLE IF NOT EXISTS trades (
    fill_id        VARCHAR PRIMARY KEY,
    order_id       VARCHAR NOT NULL,
    asset          VARCHAR NOT NULL,
    side           VARCHAR NOT NULL,         -- long | short
    quantity       DOUBLE NOT NULL,
    price          DOUBLE NOT NULL,
    fee_usd        DOUBLE NOT NULL DEFAULT 0,
    pnl_usd        DOUBLE,                    -- NULL bei open-position
    source_tag     VARCHAR,
    event_type     VARCHAR NOT NULL,          -- order_filled | position_closed | position_partial_closed
    ts             TIMESTAMP NOT NULL,
    schema_version VARCHAR NOT NULL DEFAULT 'v2',
    extras         JSON
);

-- ===================================================================
-- 4. pnl_daily — aggregierte Daily-PnL (computed view, nicht persistiert)
-- ===================================================================

CREATE OR REPLACE VIEW pnl_daily AS
    SELECT
        DATE_TRUNC('day', ts) AS day,
        COALESCE(NULLIF(TRIM(source_tag), ''), 'unknown') AS source_tag,
        asset,
        SUM(pnl_usd) FILTER (WHERE pnl_usd IS NOT NULL) AS realized_pnl_usd,
        COUNT(*) FILTER (WHERE event_type IN ('position_closed', 'position_partial_closed')) AS closes,
        COUNT(*) FILTER (WHERE event_type = 'order_filled') AS opens,
        SUM(fee_usd) AS total_fees_usd
    FROM trades
    GROUP BY day, source_tag, asset;

-- ===================================================================
-- 5. metrics — operational monitoring (forward_precision, hit-rate, watchdog-findings)
--    KAI-No-Prediction-konform: jede Metrik trägt Unsicherheits-Werte mit.
-- ===================================================================

CREATE TABLE IF NOT EXISTS metrics (
    metric_id        VARCHAR PRIMARY KEY,
    metric_type      VARCHAR NOT NULL,        -- forward_precision | hit_rate | source_active_precision | per_source_stability | iqe_drop_rate | ...
    metric_value     DOUBLE NOT NULL,         -- Punkt-Schätzung
    uncertainty      DOUBLE,                  -- CI-Width oder Bootstrap-Stddev (KAI §3)
    ci_low           DOUBLE,                  -- Wilson-95%-CI Lower
    ci_high          DOUBLE,                  -- Wilson-95%-CI Upper
    sample_size      INTEGER,                 -- n für Wilson-CI
    metric_window    VARCHAR,                 -- 7d | 30d | 90d | rolling
    asset            VARCHAR,                 -- NULL = portfolio-level
    source           VARCHAR,                 -- NULL = aggregate
    computed_at      TIMESTAMP NOT NULL,
    schema_version   VARCHAR NOT NULL DEFAULT 'v1',
    extras           JSON
);

-- ===================================================================
-- 6. audits — alerts dispatched + outcomes (compacted aus alert_audit + alert_outcomes)
-- ===================================================================

CREATE TABLE IF NOT EXISTS audits (
    audit_id          VARCHAR PRIMARY KEY,    -- z.B. f"{document_id}:{dispatched_at}"
    document_id       VARCHAR NOT NULL,
    channel           VARCHAR NOT NULL,        -- telegram | email
    sentiment_label   VARCHAR,
    priority          INTEGER,
    actionable        BOOLEAN,
    directional_eligible BOOLEAN,
    outcome           VARCHAR,                 -- hit | miss | inconclusive | NULL (unresolved)
    affected_assets   JSON,                    -- list[str]
    source_name       VARCHAR,
    is_digest         BOOLEAN NOT NULL DEFAULT FALSE,
    dispatched_at     TIMESTAMP NOT NULL,
    annotated_at      TIMESTAMP,
    expected_signal_p DOUBLE,                  -- IQE/LLM-Vorhersage (Calibration-Loop)
    schema_version    VARCHAR NOT NULL DEFAULT 'v1',
    extras            JSON
);

-- ===================================================================
-- Indexes für <50ms-Pflichtmetrik (ADR 0003 § Pflichtmetriken)
-- DuckDB nutzt Min-Max-Zonenmaps automatisch; Indexes ergänzen für
-- Point-Lookups + ORDER-BY-DESC.
-- ===================================================================

CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);
CREATE INDEX IF NOT EXISTS idx_trades_asset_ts ON trades(asset, ts);
CREATE INDEX IF NOT EXISTS idx_trades_event_type ON trades(event_type);

CREATE INDEX IF NOT EXISTS idx_audits_dispatched ON audits(dispatched_at);
CREATE INDEX IF NOT EXISTS idx_audits_document ON audits(document_id);
CREATE INDEX IF NOT EXISTS idx_audits_outcome ON audits(outcome);

CREATE INDEX IF NOT EXISTS idx_metrics_type_window ON metrics(metric_type, metric_window, computed_at);
CREATE INDEX IF NOT EXISTS idx_metrics_source ON metrics(source);

CREATE INDEX IF NOT EXISTS idx_signals_asset_created ON signals(asset, created_at);

CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, ts);

-- ===================================================================
-- Migration als applied markieren
-- ===================================================================

INSERT OR IGNORE INTO _schema_versions (version, description, applied_by)
VALUES ('0001', 'Initial schema: ticks, signals, trades, pnl_daily, metrics, audits + watermark', 'neo-2026-05-09');
