# DECISION_LOG.md

## Current State (2026-04-10)

- phase: `PHASE 5`
- status: `SPLIT-RELEASE` (D-124, formerly HOLD since D-98)
- unblocked: `non-signal-critical work (docs, source taxonomy, observability, tests, refactors, paper-engine tooling, precision-improvement work)`
- still blocked: `signal-critical work (new signal consumers, live exchange relay, companion-ML reactivation) until quality bar met`
- quality bar for full release: `precision >= 60% on >= 50 resolved alerts OR (precision >= 50% AND priority/hit corr >= 0.40 AND >= 10 real paper fills with PnL tracking)`
- policy: `Operate pipeline daily, annotate outcomes, prioritize precision-improving work`

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

### D-120 (2026-04-06)
7d-Regime-Gate: Directional Alerts werden geblockt wenn der 7d-Preistrend stark gegenläufig zum Sentiment ist (>3%).
Bearish in 7d-Bullmarkt = block. Bullish in 7d-Bearmarkt = block. CoinGecko-Adapter auf `/coins/markets` umgestellt (liefert 24h+7d in einem Call).
Begründung: Bearish-Precision 4% (1/25), Bullish-Precision 75% (18/24). Bearish-Misses waren systematisch Regime-Rauschen, kein Modell-Bug.

### D-121 (2026-04-08)
Asymmetrische Signal-Filter: Bearish-Thresholds verschärft basierend auf 92 resolved Outcomes (bearish 4% vs bullish 75%).
Confidence: bearish 0.92 (war 0.8), bullish bleibt 0.8. Impact: bearish 0.75 (war 0.60), bullish bleibt 0.60.
7d-Regime: bearish 1.5% (war 3.0%), bullish bleibt 3.0%. Nur hochkonviktive bearish Events (Hacks, Bans) passieren noch.

### D-122 (2026-04-08)
Full-Text-Fallback für RSS-Feeds mit leeren Bodies (trafilatura).
CoinDesk-Feed liefert 25 Artikel/Run mit content_len=0 — alle wurden als Stubs übersprungen.
Adapter holt jetzt bei leeren Entries den Volltext von der Artikel-URL.
PowerShell-Cron-Script (paper_trading_cron.ps1) Unicode-Parse-Bug gefixt (U+2500/U+2014 → ASCII).

### D-123 (2026-04-09)
Drei Precision-Filter für directional eligibility basierend auf 331 resolved Outcomes (40% Precision gesamt).
1. `actionable=false` → block (22% vs 52% Precision). 2. Bearish-Thresholds verschärft: Confidence 0.92→0.95, Impact 0.75→0.80.
3. `priority<=7` → block (21% Precision). Combo actionable+bullish erreicht 62% Precision.
Legacy-Aufrufer unberührt (neue Params optional mit Default None).

### D-124 (2026-04-10)
D-98 Hold → Split-Release: nicht-signalkritische Arbeit freigegeben (Docs, Source-Taxonomy, Observability, Tests, Refactors, Paper-Engine-Tooling, Precision-Improvement-Work). Signal-kritische Arbeit (neue Signal-Konsumenten, Live Exchange Relay einschalten, Companion-ML-Reaktivierung) bleibt blockiert.
Quality-Bar für Voll-Release: Precision ≥60% auf ≥50 resolved alerts ODER (Precision ≥50% UND Priority/Hit-Korrelation ≥0.40 UND ≥10 real paper fills mit PnL-Tracking).
Begründung: Formalgate erfüllt (93/50 resolved directional, 160 paper cycles, `hold_releasable`), aber Precision 41.94%, Priority-Hit-Corr 0.2556, nur 3 Paper-Fills mit realized PnL=0.0 — für signal-sensitive Freigaben unreif. Recall nicht berechenbar (kein negative ground truth).

### D-125 (2026-04-11)
theblock.co (source_id 68daff98) status: active → disabled. Feed liefert persistent HTTP 403 (Cloudflare/anti-bot), analog zu cryptoslate (D-124). Notes um Datum + Grund ergänzt, RSS-Header unverändert. Aktive RSS-Feeds: 11 → 10. Revisit, sobald UA-/Header-Workaround getestet werden kann (signal-critical, aktuell blockiert).

### D-126 (2026-04-11)
Neues read-only CLI `alerts analyze-resolved` (non-signal-critical) bricht resolved directional outcomes nach Asset / Sentiment / Priority / Priority-Group / Source auf. Pure Funktion `app/alerts/feature_analysis.py` + Rich-Table-Rendering + `--json-out`. 8 Unit-Tests. Erste Befunde an den 93 resolved (Stand 2026-04-11, Precision 41.94%): bearish 23.53% vs bullish 52.54%; priority p7=22.22% (36 resolved, größter+schlechtester Bucket); decrypt 20.00% (20 resolved) + bitcoin_magazine 21.43% (14 resolved) als schwächste signifikante Quellen; 161 von 616 directional doc_ids (26%) nicht mehr in canonical_documents (Retention-Artefakt, 12 davon im resolved Sample als "unknown"-Bucket, alle hits → Bias minimal aber erkennbar). Dient als Grundlage für Precision-Improvement-Arbeit, kein Fix.

### D-129 (2026-04-14)
D-119 Paper-Trade Bridge: Echte LLM-AnalysisResult wird jetzt an den Trading-Loop durchgereicht statt Fake-Conservative-Profile.
Vorher: Bridge rief `run_trading_loop_once()` auf, das intern `build_loop_trigger_analysis(profile="conservative")` baute (neutral, actionable=False, confidence=0.5) → Signal-Generator filterte IMMER → 0 Fills aus D-119.
Nachher: `run_trading_loop_once(analysis_result=...)` akzeptiert optional eine echte AnalysisResult. Bridge übergibt die Alert-Analyse direkt → Signal-Generator sieht echte Scores (confidence ~0.85, actionable=True, impact ~0.7) → Fills möglich.
Freshness-Threshold von 120s auf 300s für Bridge-Aufrufe erhöht (CoinGecko Free-Tier-Kompatibilität).

### D-128 (2026-04-14)
Markt-Kontext in LLM-Analyse-Prompt injiziert. BTC/ETH Preis, 24h/7d Change und Markt-Regime werden vor jedem Batch via CoinGecko geholt und dem LLM als Kontext übergeben.
System-Prompt erweitert um `already_priced_in`-Guidance: LLM soll bewerten ob News bereits eingepreist ist und `directional_confidence` entsprechend senken.
Fail-open: Bei CoinGecko-Fehler wird ohne Markt-Kontext analysiert. Einmal pro Batch gecacht (nicht pro Dokument).

### D-127 (2026-04-14)
Bearish directional eligibility komplett deaktiviert (`BEARISH_DIRECTIONAL_DISABLED=True`).
Datengrundlage: 50 eligible resolved outcomes — bearish 4% Precision (1 hit / 24 miss), bullish 76% (19/25). Bearish-Signale aus RSS-News sind in Aufwärtstrends nicht preis-prädiktiv; selbst actor-action-Titel (Hacks, Sells) und hohe Confidence (0.95+) verhindern Misses nicht. Bearish-Block allein hebt simulierte Forward-Precision auf ~76%. Re-enable geplant, sobald Markt-Kontext-Analyse (Regime-Detection, Echtzeit-Sentiment) bearish-Signale validieren kann. Alerts werden weiterhin dispatched (Telegram/Email), nur die directional-Eligibility für Precision-Tracking ist blockiert.
