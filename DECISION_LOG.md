# DECISION_LOG.md

## Current State (2026-03-25)

- phase: `PHASE 5`
- status: `HOLD`
- gate: `No new feature work until >=50 directional alerts are resolved (hit/miss)`
- policy: `Operate pipeline daily, annotate outcomes, validate quality with real data`

## Compact Decision Log

### D-97 (2026-03-24)
Strategic hold activated for companion-ML and feature expansion.
Hold is operator-controlled and cannot be lifted automatically.
Only operations, measurement, and reporting are allowed while hold is active.

### D-98 (2026-03-24)
Alert hit-rate became the primary unblocking metric.
A minimum dataset of 50 resolved directional alerts is required.
No feature work resumes before this metric is computable.

### D-99 (2026-03-24)
No new sprint-contract documentation is allowed.
New decisions must be short and operational.
Historical contract material remains archived under `docs/archive/`.

### D-100 (2026-03-24)
Alert outcome annotation infrastructure was delivered and accepted.
Operator can annotate `hit`, `miss`, `inconclusive` from CLI.
Infrastructure is complete; remaining blocker is real outcome collection.

### D-101 (2026-03-24)
Priority MAE=3.13 and LLM-Error-Proxy=27.5% are accepted current production limits.
These metrics are improved via real operations and data quality work.
No internal architecture sprint may be opened to "optimize" them in isolation.

### D-103 (2026-03-24)
Canonical CLI was reduced to core product commands.
Research-heavy orchestration was removed from default operator surface.
Backward compatibility paths are non-canonical.

### D-104 (2026-03-24)
I-13 remains permanent in Tier1/rule-only fallback.
Fallback stays conservative and non-actionable.
Signal quality focus stays on LLM-driven directional alerts.

### D-105 (2026-03-24)
30-day review date is fixed to 2026-04-23.
After a real 7-day run, weak alert volume or precision triggers data-quality-only focus.
No new architecture work is allowed when this gate fails.

### D-106 (2026-03-24)
Living architecture was slimmed to `CLAUDE.md` and `docs/contracts.md`.
All other architecture/governance documents are historical artifacts.
Historical artifacts are maintained under `docs/archive/` only.

### D-107 (2026-03-25)
Companion-ML stubs and research-governance bulk were pruned from active paths.
Dead module shims and obsolete CLI stubs were removed.
Repository focus shifted to ingestion -> analysis -> alerts -> outcome tracking.

### D-108 (2026-03-25)
Governance surface was compacted for day-to-day operation.
`README.md`, `RUNBOOK.md`, and `app/cli/AGENTS.md` now reflect only active PH5-hold workflows.
Full historical decision narrative moved to `docs/archive/decision_log_archive.md`.

### D-109 (2026-03-25)
Core-path target architecture was formalized in `docs/contracts.md` and aligned in code.
`enrichment`/`schemas`/`decisions` were consolidated into `normalization`/`core`/`orchestrator` with compatibility shims.
Pipeline run metrics now include fetched, persisted, analyzed, priority distribution, and alerts fired.

### D-111 (2026-03-30)
Directional eligibility now requires score-strength gates: `|sentiment_score| >= 0.55` and `impact_score >= 0.55`.
Weak signals are blocked with reason `weak_directional_signal` to reduce false-positive pollution in hit-rate tracking.
Precision was 7.53% with 92.47% false positives; gates filter ~26% of current directional alerts (the weakest signals).

### D-117 (2026-04-04)
Multi-Agent-Modell (Codex als Signal Validator, Antigravity als Watchdog) pausiert — keiner der beiden liefert aktuell Mehrwert.
Nur Claude Code ist operativ aktiv. Reaktivierung prüfen am 30-Day-Gate (2026-04-23) nach Precision-Evaluation.

### D-118 (2026-04-04)
Price Trend Divergence Gate: Directional alerts werden nur dispatcht wenn der 24h-Preistrend die Sentiment-Richtung bestätigt.
Bullish + Preis steigt = pass. Bearish + Preis fällt = pass. Gegenteilig = block (BLOCK_REASON_PRICE_TREND_DIVERGENCE).
Begründung: 89% der historischen Misses (49/55) hatten korrektes Sentiment aber gegenläufigen Markt. Fail-open bei API-Fehler.

### D-119 (2026-04-04)
Pipeline-to-Paper-Trade bridge: Nach erfolgreichem directional Alert-Dispatch wird automatisch ein Paper-Trade-Cycle getriggert.
Nutzt bestehenden `run_trading_loop_once()` mit `OPERATOR_SIGNAL_AUTO_RUN_MODE=paper`. Fail-open: Fehler werden geloggt, blockieren aber nie die Pipeline.
Schliesst den Feedback-Loop: Pipeline → Alert → Paper-Trade → PnL-Messung (Phase B Deliverable 1+2).
