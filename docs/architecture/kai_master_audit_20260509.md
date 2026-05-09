# KAI — Architektur-Audit & Gap-Analyse vs. institutionelle Zielarchitektur

**Datum:** 2026-05-09
**Anlass:** Master-Direktive vom Operator: KAI als institutionelle Investment-, Risiko-, Portfolio- und Signal-Intelligence-Plattform (BlackRock-Aladdin-/Bloomberg-PORT-/State-Street-Alpha-Klasse, ohne proprietäre Systeme zu kopieren — abstrakte Architekturprinzipien)
**Cycle-Status:** V-DB5 P1-UI-Tranche heute deployed (commit `8712768`); F4-Verbose-Observer läuft auf Pi 5 bis morgen ~07:20 UTC; Provider-Symmetrie-Sweep heute fertig.

---

## 1. Executive Summary

KAI hat heute **keine institutionelle Architektur**, aber **substantielle Bausteine in 7 von 12 Direktiv-Domänen**. Der Sprung zur "Aladdin-Klasse" ist kein Sprint, sondern ein **mehrwöchiges, gestaffeltes Programm** in 7 Phasen. Die Bausteine, die heute funktionieren, sind solide (Pydantic-strikte Models, Audit-JSONL als Event-Sourcing, MCP-Subagent-Roster, paper-engine mit deterministischer rehydrate, portalocker-LOCK_EX bei Audit-Writes).

**Was fehlt am stärksten** (gemessen an der Master-Direktive):
- **Evidence Object Schema** (heute im Stash, *nicht* als Modul-1 produktiv — Codex baut es)
- **Risk Engine** ist Pre-Trade-Sizing, **kein** Portfolio-VaR/Expected-Shortfall/Stress-Test
- **Portfolio Engine** ist read-surface + paper-engine, **keine** Multi-Asset-Klassen-Rubriken (Core Reserve / Tactical / Event-Driven / Hedge-Layer)
- **Signal Intelligence** ist Single-Source mit Confluence-Filter, **kein** weighted Cross-Source-Score mit Manipulation-Penalty / Latency-Penalty
- **Decision Gates** existieren als 7-Schritt-TradingLoop, **fehlen** Risk-Limit-/Konzentrations-/Liquiditäts-/Slippage-Gates
- **Datenquellen-Breite**: News + RSS + NewsData + TradingView + Telegram-Channel + 5 Exchanges (Bybit/Binance/OKX/BitMEX/CoinGecko) sind real; **fehlen** On-Chain (Wallet-Flows, Stablecoin-Supply, Whale-Cluster), Macro (DXY, S&P, Treasury Yields), Reddit/X/YouTube-API

**Was *nicht* fehlt** und die Direktive bereits erfüllt:
- Audit-Trail (paper_execution_audit.jsonl, alert_audit.jsonl, alert_outcomes.jsonl, decision_journal, neo/dali/sentr/watchdog/architect/satoshi-Agent-JSONLs)
- Reproduzierbare paper-engine via `rehydrate_from_audit`
- Source-Klassifikation und Source-Lifecycle (active/planned/disabled/requires_api/manual_resolution/unresolved)
- Forward-Precision-Tracking + Per-Source-Active-Precision + Per-Source-Stability mit Wilson-95%-CIs
- Watchdog-Agent mit `listener_reactivity_check` + `position_monitor_no_market_data_stuck`
- Provider-Symmetrie über Bridge ↔ Position-Monitor ↔ Portfolio-Read (heute fertig)

**P0-Risiko entdeckt während dieses Audits**: V-DB5-Backend-Calibration-Code (16 modified + 5 untracked Files, 670+ Zeilen) lebt nur in `stash@{0}` lokal + uncommitted Pi-5-Working-Tree. **Nirgendwo committed**. Stash-Drop oder Pi-Ausfall → Verlust. Siehe Memory `v_db5_backend_uncommitted_risk.md`.

---

## 2. Bestandsaufnahme — was heute existiert

### 2.1 Domänen-Mapping (Master-Direktive § A-I) auf Repo-Module

| Direktive | Repo-Module | Reife | Bemerkung |
|---|---|---|---|
| **A. Data Ingestion — News** | `app/ingestion/`, `app/integrations/{newsdata,rss}` | live | RSS-Feeds, NewsData-Free, regulatorische via RSS-Aggregat |
| **A. Data Ingestion — Social/Community** | `app/ingestion/telegram_channel_worker.py` | partial | Telegram-Channel: aktiv mit F1-F6 Resilience. Reddit/X/YouTube/TikTok = nicht integriert |
| **A. Data Ingestion — Market Data** | `app/market_data/`, `app/integrations/{coingecko,binance,bybit,okx,bitmex,kraken}` | live | OHLCV via Provider-Fallback-Chain (V25-D). Funding/OI/Liquidation = nicht erfasst |
| **A. Data Ingestion — On-Chain** | — | **0%** | keine Module |
| **A. Data Ingestion — Macro** | — | **0%** | DXY/S&P/Treasury fehlen vollständig |
| **A. Data Ingestion — Internal** | `artifacts/paper_execution_audit.jsonl`, `artifacts/trading_loop_audit.jsonl`, `artifacts/decision_journal.jsonl` | live | Audit-JSONL pro Domäne, Read durch `audit_replay.replay_paper_audit` etc. |
| **B. Data Quality & Normalization** | `app/normalization/`, `app/enrichment/`, `app/storage/repositories/document_repo.py` | partial | Asset-Symbol-Normalisierung, Sprache-Erkennung, Dedup via `content_hash`; **kein** zentrales Vertrauensscore-pro-Quelle, **kein** Manipulationsscore, **kein** Bot-/Spam-Wahrscheinlichkeit |
| **B. Evidence Object Schema** | `app/core/evidence.py` (untracked, im Stash) | **WIP** | Codex baut Modul-1: `EvidenceObject` mit deterministic ID, content_fingerprint, source-locator-required. 8 von 13 Pflichtfeldern abgedeckt |
| **C. Signal Intelligence** | `app/signals/{generator,bayesian_confidence,tradingview_consumer}` | partial | SignalGenerator hat 6 Filter + Confluence + SL/TP. **Kein** Cross-Source-weighted-Score mit Manipulation/Latency-Penalty |
| **D. Portfolio Engine** | `app/execution/{paper_engine,portfolio_read,portfolio_surface}` | partial | Spot-Paper-Trading mit Tier-Close. Multi-Asset-Klassen (Spot/Futures/Stablecoin/Hedge) und Rubriken (Core/Tactical/Event/On-Chain/Speculative/Cash/Hedge) existieren NICHT |
| **E. Risk Engine** | `app/risk/{engine,portfolio_risk,volatility}` | partial | Pre-Trade-Sizing (Risk-pro-Trade, Position-Sizing). **Kein** Portfolio-VaR, **kein** Expected Shortfall, **kein** Monte-Carlo, **kein** Stress-Test (BTC -10%/-20%/-35%, Exchange-Ausfall, Stablecoin-Depeg, Liquidation Cascade) |
| **F. Decision Gates** | `app/orchestrator/trading_loop.py`, `app/risk/engine.py` | partial | 7-Step-Pipeline (Cycle-Audit). Gates für (Datenqualität, 2-3-Source-Confirmation, Liquidität, Risk-Limit, Konzentration, Slippage, Watchdog/SENTR-Freigabe, Paper-Performance) sind **teilweise** implementiert; **viele** als implizite Pre-Trade-Checks ohne explizite Gate-Entity |
| **G. Dashboard** | `app/api/routers/{dashboard,operator,kai}.py`, `web/src/` | live | Forward-Precision, QualityBar, ReentryGate, ActivePrecision, Stability, AgentsStatus, KaiLiveWidget. **Es fehlt**: Stress-Test-Simulator-UI, On-Chain-Drilldown, Macro-Snapshot, Portfolio-Rubriken-Allokation, Rebalancing-Vorschläge |
| **H. Agentenrollen** | `app/agents/{worker,mcp_server,tools/}`, `artifacts/agents/{neo,sentr,watchdog,architect,dali,satoshi}/*.jsonl` | live | 6 Subagents als Auto-Routing-Pflicht (CLAUDE.md). Findings/Proposals/Implementations als JSONL. MCP-Server-Surface vorhanden |
| **I. Testing & Quality** | `tests/unit/`, `tests/integration/`, mypy strict, ruff | live | mypy strict + pytest grün als Quality-Bar. **Es fehlt**: Property-Based-Tests für Risk-Invariants (im Stash teilweise), Backtests gegen historische Krisen (Mai 2022 LUNA, FTX-Crash, ETF-Flush) |

### 2.2 Datenfluss heute (Ist-Zustand, post-V25-D, post-Provider-Symmetrie 2026-05-09)

```
RSS/NewsData/Telegram-Channel ─┐
                               │
TradingView Webhooks ──────────┼─► Ingestion ─► Normalization ─► document_repo (PostgreSQL)
                               │
Exchange-APIs (5 Provider) ────┘                                          │
                                                                          ▼
                                    SignalGenerator (6 Filter+Confluence) ─► alert_audit.jsonl
                                                                          │
                                                                          ▼
                                    Risk-Engine (Pre-Trade-Sizing) ─► trading_loop_audit.jsonl
                                                                          │
                                                                          ▼
                                    Operator-Approval (Telegram-Bot) ─► envelope_to_paper_bridge
                                                                          │
                                                                          ▼
                                    PaperEngine (rehydrate from audit) ─► paper_execution_audit.jsonl
                                                                          │
                                                                          ▼
                                    PositionMonitor (Provider-Fallback-Chain) ─► Tier-Close-Events
                                                                          │
                                                                          ▼
                                    Auto-Annotator (V-DB5) ─► alert_outcomes.jsonl
                                                                          │
                                                                          ▼
                                    Hold-Metrics + Forward-Precision + Per-Source-* ─► /dashboard/api/quality
```

---

## 3. Gap-Tabelle: Direktive → Realität → Aufwand

Skala: P0 = Verlust-Risiko/Sofort, P1 = nächste 1-2 Sprints, P2 = nächste 4-6 Sprints, P3 = Roadmap. Aufwand: S = ≤1 Tag, M = 2-5 Tage, L = 1-3 Wochen, XL = 1-3 Monate.

| Gap | Status heute | Direktive-Soll | Prio | Aufwand |
|---|---|---|---|---|
| **V-DB5 Backend Rescue-Commit** | uncommitted (Stash + Pi-5-Tree) | git-tracked auf Branch | **P0** | S |
| **Evidence Object Schema** in Produktion | Stash (untracked) | Modul-1 live, alle Adapters emittieren EvidenceObjects | **P1** | M |
| **Cognitive Audit Trail (LLMAuditRecord)** | Stash (Alembic-Migration `74fab3f5b5d5_add_llmauditrecord`) | DB-Tabelle live, jeder LLM-Call schreibt | **P1** | S |
| **Cross-Source-weighted Signal-Score** | nicht vorhanden | weighted_sum(source_credibility, novelty, cross_source_confirmation, market_reaction, volume_confirmation, onchain_confirmation, sentiment_delta, macro_alignment, liquidity_quality, manipulation_penalty, latency_penalty) | P1 | L |
| **Portfolio-VaR / Expected Shortfall** | nicht vorhanden | rolling 7/30/90d, methodisch dokumentiert (historisch + parametrisch) | P1 | M |
| **Stress-Test-Suite** | nicht vorhanden | BTC -10/-20/-35, Exchange-Ausfall, Stablecoin-Depeg, Liquidation-Cascade, ETF-Flow-Schock, Reg-Schock, Hack/Exploit, Zins-Schock, Makro-Risk-Off | P1 | L |
| **Portfolio-Rubriken-Engine** | nicht vorhanden | dynamische Zielgewichte für 7 Rubriken (Core/Tactical/Event/On-Chain/Speculative/Cash/Hedge) | P2 | L |
| **On-Chain-Ingestion** | nicht vorhanden | Blockchain-Explorer-API, Wallet-Flows, Exchange-Inflows/Outflows, Stablecoin-Supply, Whale-Transfer-Cluster | P2 | L |
| **Macro-Ingestion** | nicht vorhanden | DXY, S&P 500, Nasdaq, Gold, Öl, Treasury Yields, Fed/ECB Termine | P2 | M |
| **Reddit/X/YouTube-Social-Layer** | nicht vorhanden | API/legal-Scrape-Pfad mit Bot-/Spam-Wahrscheinlichkeit | P2 | L |
| **Manipulation-Score / Bot-Detection** | nicht vorhanden | als Feld im EvidenceObject + Penalty in Signal-Score | P2 | M |
| **Sticky-Highwater Re-Entry-Gate (V-DB5 B-H1)** | OR-Logik ohne Sticky | Highwater + Wilson-Floor (siehe v_db5_p2_proposals_20260509.md) | P2 | S |
| **Forward-Precision-Watchdog (V-DB5 B-I1)** | silent | Telegram-Alert wenn forward_precision_pct < 60% | P2 | S |
| **Tooltip-Primitive (V-DB5 K-1)** | native title=, kein Touch/A11y | Headless-Pattern Component | P3 | M |
| **3-Source-Panel-Konsolidierung (V-DB5 H-1)** | 3 Tabellen untereinander | 1 Tab-Container "Source-Performance" | P3 | M |
| **Hold-Snapshot Auto-Refresh (V-DB5 F-009)** | 16+d alt | Timer-getriggerte Refresh-Pipeline | P2 | S |
| **Tier-Close-Modell** | alle Tiers @ Markt-Preis | gestaffelt @ Tier-Preisen | P2 | S |
| **dynamic CoinGecko-Map** | hardcoded `_BASE_ASSET_TO_COINGECKO` | `/coins/list`-Lookup mit Persistent-Cache | P3 | S |
| **Audit-File-Wachstum** | unbegrenzt (paper_execution_audit, trading_loop_audit) | Snapshot-DB-Strategie | P2 | M |
| **DB-Schema-Server-Default `provider="coingecko"`** | inkonsistent | Migration auf `"fallback"` | P3 | S |

---

## 4. Roadmap-Phasen 1-7

> Disziplin: jede Phase **klein genug, dass sie in 1-2 Wochen abgeschlossen werden kann**. Keine Phase startet, bevor die vorherige Definition-of-Done erfüllt hat (siehe CLAUDE.md `What "Done" Means`).

### Phase 1 — Data Foundation (Woche 1-2)

**Ziel:** Evidence-Object-Schema produktiv, V-DB5-Backend gesichert, Cognitive-Audit-Trail live.

1. **V-DB5-Backend-Rescue-Commit** (P0, S): Stash auf Branch committen, Drift-Konflikte hand-sortieren, Pi-5 gegen Branch syncen. **Vor jeder weiteren Phase.**
2. **Evidence-Object-Schema** (P1, M): Codex' Stash-Modul live machen. Erweiterung um die 5 fehlenden Felder (`relevance`, `manipulation_risk`, `legal_access_status`, `downstream_usage_allowed`, `bot_spam_probability`). Tests + Docs + Migrations.
3. **EvidenceObject-Adapters** (P1, M): NewsData, RSS, TradingView, Telegram-Channel, Exchange-Status emittieren EvidenceObjects statt rohen `CanonicalDocument`.
4. **LLMAuditRecord live** (P1, S): Alembic-Migration `74fab3f5b5d5` aus Stash anwenden, jeder LLM-Provider-Call schreibt.

**DoD Phase 1:** alle Adapter emittieren EvidenceObjects; `/dashboard/api/quality.evidence_summary` zeigt # EvidenceObjects/h pro Source-Type; LLMAuditRecord-Tabelle hat ≥1 Row pro Worker-Run; V-DB5-Backend ist git-getrackt.

### Phase 2 — Signal Quality (Woche 3-4)

**Ziel:** Cross-Source-weighted Signal-Score, Manipulation-Detection, latency-aware Confluence.

1. **Signal-Score-Engine v2** (P1, L): `SignalScore = weighted_sum(...)` mit 11 Komponenten gemäß Direktive § C. Konfigurierbare Gewichte aus `app/core/settings.py`. Property-Based-Tests für Score-Range, Monotonicity, Penalty-Effekt.
2. **Manipulation-Score** (P2, M): Bot-/Spam-Wahrscheinlichkeit als Field im EvidenceObject. Heuristiken: Account-Alter, Posting-Burst, Sentiment-Spike-Korrelation. Manipulation-Penalty in Signal-Score.
3. **Cross-Source-Confirmation** (P1, M): Signal nur wenn ≥2 unabhängige Sources (kanonisierte Source-Cluster) das gleiche Asset+Polarität tragen. SignalGenerator-Filter erweitern.
4. **Forward-Precision-Watchdog (V-DB5 B-I1)** (P2, S): Telegram-Alert wenn `forward_precision_pct < 60%` oder `forward_precision_ci_low_pct < 50%`.

**DoD Phase 2:** SignalScore zwischen 0-100 mit dokumentiertem Beitrag jeder Komponente; Backtest gegen Mai-2022-LUNA-Crash + FTX-Crash zeigt manipulation-penalty greift; Watchdog-Alert bei Forward-Precision-Drift.

### Phase 3 — Portfolio + Risk Engine (Woche 5-7)

**Ziel:** Multi-Asset-Klassen-Rubriken, Portfolio-VaR, Stress-Tests.

1. **Portfolio-Rubriken-Engine** (P2, L): 7 Kategorien (Core Reserve / Tactical Momentum / Event Driven / On-Chain Opportunity / High-Risk-Speculative / Cash/Stablecoin / Hedge Layer). Dynamische Zielgewichte aus Risk-Budget × Signalqualität × Liquidität × Volatilität × Korrelation × Marktregime. **Keine** Hard-Codings, alles über Settings + Tests.
2. **VaR / Expected Shortfall** (P1, M): historisch (rolling 30/90/365d) + parametrisch (varianz-basiert). Position-Sizing aus VaR-Budget statt Risk-pro-Trade-flat. Liquidity-adjusted Sizing.
3. **Stress-Test-Suite** (P1, L): 9 Szenarien gemäß § E. Backend-Job nightly + on-demand-API. Dashboard-UI Stress-Simulator.
4. **Korrelations-Matrix rolling 7/30/90d** (P2, M): Asset-Asset und Asset-Macro. Konzentrationsrisiko-Score.
5. **DB-Schema-Default-Migration** (P3, S): `provider="fallback"` als server-default.

**DoD Phase 3:** Rubriken-Allokation im Dashboard; VaR-Wert + Expected-Shortfall im Risk-Header; Stress-Test-Button löst Backend-Sim und zeigt Tabelle; Korrelations-Heatmap.

### Phase 4 — Decision Gates (Woche 8-9)

**Ziel:** Explizite, atomare Decision-Gate-Pipeline mit Audit-Spur pro Gate.

1. **Decision-Gate-Engine** (P1, M): 10 Gates aus § F als atomare `Gate` mit `evaluate(context) -> GateResult{passed, reason, recommendation}`. Pipeline mit kurzschluss-bei-Fail, Findings im decision_journal pro Gate.
2. **Liquidity-Gate** (P2, S): Mark-to-Market-Slippage-Estimate auf Order-Book-Top-N-Level pro Exchange.
3. **Concentration-Gate** (P2, S): Position-Größe vs. Rubriken-Cap.
4. **Slippage-Gate** (P2, S): Acceptable-Slippage-Threshold aus Settings.

**DoD Phase 4:** Jeder Trade-Versuch durchläuft 10 Gates; bei Fail klare Begründung im Telegram + Dashboard; Audit-Trail je Gate-Result.

### Phase 5 — Dashboard + Agent Control (Woche 10-11)

**Ziel:** Institutional-grade Dashboard-Surface mit Stress-Simulator, Rubriken-View, Agent-Steuerung.

1. **Stress-Test-Simulator-UI** (P1, M): Form mit Szenario-Auswahl, Backend-Call, Tabellen-Output, PnL-Delta-Visualisierung.
2. **Rubriken-Panel** (P2, M): 7 Rubriken-Boxen mit Allokation/Ziel/Drift/Drawdown.
3. **3-Source-Panel-Konsolidierung (V-DB5 H-1)** (P3, M): Tab-Container "Source-Performance".
4. **Tooltip-Primitive (V-DB5 K-1)** (P3, M): Headless-Pattern.
5. **Agent-Control-Panel** (P2, S): Trigger-Buttons für SENTR/Watchdog/Architect/DALI/Neo/SATOSHI.

**DoD Phase 5:** Operator kann via Dashboard 9 Stress-Szenarien simulieren ohne Terminal; Rubriken-Allokation-Drift sichtbar; Tooltip-A11y vollständig.

### Phase 6 — Papertrading-Validation (Woche 12-13)

**Ziel:** Dokumentierte 30-Tage-Validierungs-Phase mit definierten Erfolgs-Kriterien, BEVOR live-Trading.

1. **30-Tage-Paper-Backtest gegen Live-Daten** (P0 für Live-Phase, M): Erfolgs-Kriterien aus Settings (Forward-Precision ≥60%, Drawdown ≤X%, Sharpe ≥Y, Max-Position-Hit-Rate ≥Z%).
2. **Backtest-Suite** (P1, L): Historische Krisen (Mai-2022-LUNA, Mar-2020-COVID-Crash, Nov-2022-FTX, Jan-2024-ETF-Approve, May-2024-TG-Halving): KAI-Decisions in den Daten, Ex-Post-PnL.
3. **Attribution-Report** (P2, M): Pro-Trade-Attribution: welcher Signal-Komponente verdankt sich Hit/Miss.

**DoD Phase 6:** 30 Tage live-paper, Erfolgs-Kriterien getroffen, Backtest-Charts gegen 5 historische Krisen, Attribution pro Trade.

### Phase 7 — Kontrolliertes Live-Trading (Woche 14+)

**Voraussetzung:** Phase 1-6 grün. **Niemals früher.** Keine Abkürzung.

1. **Live-Trading-Adapter** (P0, M): real-money-Adapter, ENV-Flag-Gating, Operator-Approval-Pflicht für jeden Trade in Phase 7.0.
2. **Safety-Tier**: max 0.5% Equity pro Trade, max 5% Total-Exposure, Hard-Cap pro Tag.
3. **Operator-Override** (P0, S): Telegram-Stop-Trade-Befehl + Dashboard-Kill-Switch.
4. **Live-Audit + Reconciliation**: jeden Live-Fill gegen Exchange-Receipt, Diff-Report bei Mismatch.

**DoD Phase 7.0:** 100 Trades live-real, alle Live-Audits matchen Exchange-Receipts ±0.01%, Operator-Override geprüft, Kill-Switch dokumentiert + getestet.

---

## 5. Risiken & Annahmen

### 5.1 Risiken

| Risiko | Wahrscheinlichkeit | Impact | Mitigation |
|---|---|---|---|
| V-DB5-Backend-Verlust durch Stash-Drop oder Pi-Ausfall | mittel | hoch | **P0**: Rescue-Commit innerhalb 24h |
| Codex / Hauptagent / KAI-Live-Phase-2-Drift erzeugen Merge-Konflikte | hoch | mittel | Disziplin: kleine Branches, häufige Rebase-Sync, Operator-Sign-off vor Cross-Track-Merge |
| Roadmap zu ehrgeizig — 14 Wochen sind 3.5 Monate, nicht Stunden | hoch | hoch | **Phase 0 schreiben mit hartem 2-Wochen-Cap, dann Re-Plan**. Bei Ist > 2 Wochen: Phase teilen, nicht stretchen |
| Property-Tests für Risk-Engine nicht vollständig | mittel | hoch | hypothesis-Tests im Stash bereits vorhanden, müssen aktiviert werden |
| Source-Provider-Lock-in (CoinGecko/Binance) | niedrig | mittel | Provider-Symmetrie heute fertig, Fallback-Chain greift |
| Live-Trading-Phase 7.0 ohne ausreichende Validierung | niedrig (wenn Disziplin gehalten wird) | sehr hoch | Phase 6 darf 30 Tage *nicht* abgekürzt werden |
| LLM-Provider-Cost explodiert durch tier-1 cognitive-audit-trail | niedrig | mittel | Token-Stats + cost-attribution in LLMAuditRecord, Alert bei Daily-Cost-Limit |

### 5.2 Annahmen

- **Operator-Verfügbarkeit**: Sascha als alleiniger Operator. Phasen sequenziell, nicht parallel-besetzt.
- **Pi-5-Source-of-Truth bleibt**: kein erneuter Hardware-Cutover in den nächsten 14 Wochen.
- **Lizenzierte API-Verfügbarkeit**: NewsData-Free-Tier reicht nicht für Phase-2-Cross-Source-Confirmation; ggf. Paid-Tier oder Alt-Provider nötig.
- **Marktbedingungen**: Audit-Annahmen brechen bei extremen Events (Liquidity-Black-Hole). Stress-Tests behandeln das, aber Live-Phase-7 startet idealerweise nicht in Krisen-Volatilität.
- **CLAUDE.md-Auto-Routing-Pflicht**: alle Subagent-Aktivierungen erfolgen durch Hauptagent (nicht durch Hooks). Vergessene Trigger sind Pflichtverletzung, kein Tool-Bug.

---

## 6. Empfehlung für die nächsten 7 Tage

| Tag | Aufgabe | Phase | Verantwortlich |
|---|---|---|---|
| Heute (2026-05-09) | V-DB5-UI-Tranche done ✅; V-DB5-P2-Vorschläge schriftlich (siehe `v_db5_p2_proposals_20260509.md`); Architektur-Audit-Doc dieses File | — | Hauptagent |
| 2026-05-10 (Sa) | F4-Verbose-Observer auf Pi 5 deaktivieren ~07:20 UTC + 48h-Diagnose-Auswertung; **V-DB5-Backend-Rescue-Commit** | Phase 1 | Operator + Hauptagent |
| 2026-05-11 (So) | Pi-5 gegen Rescue-Branch syncen, kai-server restart, Smoke; Begin Evidence-Object-Schema-Erweiterung | Phase 1 | Hauptagent + Codex |
| 2026-05-12 (Mo) | EvidenceObject 5 fehlende Felder + Migration; LLMAuditRecord-Migration anwenden | Phase 1 | Hauptagent |
| 2026-05-13 (Di) | EvidenceObject-Adapter für NewsData + RSS | Phase 1 | Hauptagent |
| 2026-05-14 (Mi) | EvidenceObject-Adapter für TradingView + Telegram-Channel | Phase 1 | Hauptagent |
| 2026-05-15 (Do) | EvidenceObject-Dashboard-API `/dashboard/api/quality.evidence_summary` + Test-Suite + Memory-Update Phase-1-DoD | Phase 1 | Hauptagent |

---

## 7. Cross-Refs & Nächste Updates

- **V-DB5-UI-Commit**: `8712768` auf Branch `claude/v-db5-ui-tranche-20260509`, Pi-5-Deploy 2026-05-09 13:46 UTC
- **V-DB5-Backend-Risk**: Memory `v_db5_backend_uncommitted_risk.md` (P0)
- **V-DB5-P2-Proposals**: `artifacts/architecture/v_db5_p2_proposals_20260509.md` (heute)
- **F4-Status**: Memory `kai_listener_resilience_v25d_status.md` — Disable-Termin morgen ~07:20 UTC
- **Provider-Symmetrie**: Memory `kai_market_data_provider_symmetry.md` — heute fertig
- **Codex-Drift**: Memory-Eintrag implizit, Codex-Worktree `.codex/worktrees/6d04` mit 6 Frontend-WIPs ungestasht

**Nächstes Audit-Update:** nach Phase-1-DoD (~2026-05-16). Dann Re-Audit gegen die hier dokumentierten DoDs, identifiziere Drift zur Roadmap, korrigiere Phase-2-Planung.

---

*Dokument erstellt unter KAI Master Execution Directive § 7 (tägliche Maximalanalyse) + § 11 (Vorschlagsformat). Operator-Druck oder Schönreden vermieden — wenn etwas P0 ist, steht es als P0.*
