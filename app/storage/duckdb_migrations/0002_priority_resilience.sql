-- KAI DuckDB analytics schema — priority + resilience extension
-- Operator-Priority-Reorder 2026-05-09 (Memory feedback_kai_priority_reorder_20260509.md):
-- "Mehr KI auf schlechter Infrastruktur erzeugt nur intelligentere Fehler."
--
-- Schliesst die 4 fundamentalen Luecken die Antigravity-Crosscheck identifiziert hat:
--   A) Execution Engine — Marktimpact-Realismus statt slippage_pct=0.05 Fantasie
--   B) Latency-Hierarchie — 30s-altes ETF-Filing != 30min-altes ETF-Filing
--   C) Priority Engine — Queue-Tiers, Escalation-Levels, Backpressure
--   D) Meta-Analytics — Per-Agent-Per-Regime-Performance statt Per-Source-only
--
-- Strategie: ALTER TABLE ADD COLUMN auf existierenden Tabellen (alle nullable
-- by default fuer existing rows). Backfill von JSONL-WAL-rows ist
-- not-applicable (alte rows haben die Felder nicht — bleiben NULL, neue
-- compaction-runs setzen sie). Migration ist idempotent ueber
-- Information-Schema-Check pro Spalte.

-- ===================================================================
-- A) Execution Engine — trades-Extensions + liquidity_profiles
-- ===================================================================

-- Idempotent ALTER TABLE: DuckDB hat kein "ADD COLUMN IF NOT EXISTS"
-- vor 1.0; wir nutzen einen Pre-Check via duckdb_columns view.
-- Migration-Tool faengt duckdb.Error ab und schreibt Versionseintrag —
-- bei Re-Run ist 0002 in _schema_versions, also wird nicht erneut versucht.

ALTER TABLE trades ADD COLUMN IF NOT EXISTS intended_price DOUBLE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS slippage_bps DOUBLE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS orderbook_depth_top DOUBLE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS routing_role VARCHAR;
-- routing_role: maker | taker | aggressive | unknown

CREATE TABLE IF NOT EXISTS liquidity_profiles (
    asset            VARCHAR PRIMARY KEY,
    mean_spread_bps  DOUBLE NOT NULL,
    depth_top_usd    DOUBLE NOT NULL,
    depth_50bp_usd   DOUBLE,    -- Liquidity at 50 bps from mid (one side)
    depth_100bp_usd  DOUBLE,    -- Liquidity at 100 bps
    depth_200bp_usd  DOUBLE,    -- Liquidity at 200 bps
    sample_minutes   INTEGER NOT NULL,    -- calibration sample window
    last_calibrated  TIMESTAMP NOT NULL,
    schema_version   VARCHAR NOT NULL DEFAULT 'v1',
    extras           JSON
);

-- ===================================================================
-- B) Latency-Hierarchie — audits-Extensions
-- ===================================================================

ALTER TABLE audits ADD COLUMN IF NOT EXISTS event_age_sec DOUBLE;
ALTER TABLE audits ADD COLUMN IF NOT EXISTS ttl_class VARCHAR;
-- ttl_class: etf_filing | liquidation | whale | macro | rss | unknown
ALTER TABLE audits ADD COLUMN IF NOT EXISTS latency_decay_factor DOUBLE;
-- latency_decay_factor in [0.0, 1.0]: exp(-age/half_life) bei dispatch.
-- Wird bei Compaction-Time aus event_age_sec + ttl_class berechnet,
-- nicht zur Read-Time (KAI-No-Prediction: Wert ist Audit-Stempel,
-- nicht Live-Re-Computation).

-- ===================================================================
-- C) Priority Engine — audits-Extensions
-- ===================================================================

ALTER TABLE audits ADD COLUMN IF NOT EXISTS queue_tier VARCHAR;
-- queue_tier: P0 | P1 | P2 | P3 (P0 = sofort/critical, P3 = deferred/optional)
ALTER TABLE audits ADD COLUMN IF NOT EXISTS queue_status VARCHAR;
-- queue_status: pending | processing | done | dropped | escalated
ALTER TABLE audits ADD COLUMN IF NOT EXISTS escalation_level INTEGER;
-- escalation_level: 0 = normal, 1 = watchdog-warned, 2 = telegram-pushed,
-- 3 = decision-gate-skipped (Operator-Override). DEFAULT NULL bei
-- ALTER TABLE; Compaction setzt 0 fuer neue rows.

-- ===================================================================
-- D) Meta-Analytics — metrics-Extensions + regime_snapshots
-- ===================================================================

ALTER TABLE metrics ADD COLUMN IF NOT EXISTS agent_slug VARCHAR;
-- agent_slug: neo | satoshi | watchdog | architect | dali |
-- data-quality-inspector | architecture-red-team | source-scout |
-- general-purpose | NULL (system-level metric, no agent attribution)
ALTER TABLE metrics ADD COLUMN IF NOT EXISTS regime_tag VARCHAR;
-- regime_tag: bull_trend | bear_trend | high_vola | low_vola |
-- macro_risk_off | unknown (waehrend Regime-Detection nicht aktiv)

CREATE TABLE IF NOT EXISTS regime_snapshots (
    snapshot_id        VARCHAR PRIMARY KEY,
    regime_tag         VARCHAR NOT NULL,
    confidence         DOUBLE NOT NULL,
    confidence_low     DOUBLE,            -- Wilson-CI Lower (KAI-No-Prediction)
    confidence_high    DOUBLE,            -- Wilson-CI Upper
    detected_at        TIMESTAMP NOT NULL,
    -- evidence_features: JSON-blob of features the detector used
    -- (e.g. {"realized_vol_30d": 0.42, "btc_corr_eth": 0.82,
    --        "mac_reg_off_score": 0.31, "breadth_pct_above_ma50": 0.65})
    evidence_features  JSON NOT NULL,
    detector_version   VARCHAR NOT NULL,  -- regime detector code version (git short-sha)
    schema_version     VARCHAR NOT NULL DEFAULT 'v1',
    extras             JSON
);

-- ===================================================================
-- Indexes — neue Pfade adressieren neue Use-Cases
-- ===================================================================

-- Queue-Worker scannt nach (queue_tier, queue_status); P0 zuerst, dann FIFO.
CREATE INDEX IF NOT EXISTS idx_audits_queue ON audits(queue_tier, queue_status);

-- TTL-Queries: alle audits einer ttl_class innerhalb eines Zeitfensters.
CREATE INDEX IF NOT EXISTS idx_audits_ttl_dispatched ON audits(ttl_class, dispatched_at);

-- Meta-Analytics: per-Agent-per-Regime-Aggregationen ueber Zeit.
CREATE INDEX IF NOT EXISTS idx_metrics_agent_regime ON metrics(agent_slug, regime_tag, computed_at);

-- Regime-History fuer Backtest-Slicing.
CREATE INDEX IF NOT EXISTS idx_regime_detected ON regime_snapshots(detected_at);
CREATE INDEX IF NOT EXISTS idx_regime_tag ON regime_snapshots(regime_tag);

-- Routing-Analyse: Maker/Taker-Verteilung pro Asset+Zeitfenster.
CREATE INDEX IF NOT EXISTS idx_trades_routing ON trades(routing_role);

-- Liquidity-Profile-Refresh: zuletzt-kalibrierte Assets.
CREATE INDEX IF NOT EXISTS idx_liquidity_profiles_calibrated ON liquidity_profiles(last_calibrated);

-- ===================================================================
-- Migration als applied markieren
-- ===================================================================

INSERT OR IGNORE INTO _schema_versions (version, description, applied_by)
VALUES (
    '0002',
    'Priority-Resilience: Execution-Realismus + Latency-Hierarchie + Priority-Queue + Meta-Analytics',
    'neo-2026-05-09-priority-reorder'
);
