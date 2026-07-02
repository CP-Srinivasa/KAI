# Execution Gate Chain · Truth Layer v2 · RACI — Delta-Spec

**Stand:** 2026-06-05 · **Typ:** Delta-Ergänzung (KEINE neue SSOT) · **Modus:** Paper-First, `entry_mode=disabled`
**Autor:** Architect (`review`/`propose`)

> **Dieses Dokument ersetzt nichts.** Führend bleibt **[`docs/KAI_IDENTITY.md`](../KAI_IDENTITY.md)** mit dem
> A–H-Schichtenmodell und den Reifegraden LIVE/VORBEREITET/ZIELBILD. Hier werden ausschließlich die in
> [`docs/audit/architecture_crosscheck_20260604.md`](../audit/architecture_crosscheck_20260604.md) §5 als
> „berechtigte Schärfung" markierten Punkte **operationalisiert**: die non-bypassable Gate-Kette der **Schicht H**,
> der Truth-Layer **v2** der **Schicht D**, eine **RACI-/Approval-Matrix** und der **Overengineering-Schutz**.
>
> **Es wird KEINE 14-stufige Greenfield-Pipeline als Ersatz eingeführt.** Die Gate-Kette unten ist die
> *explodierte H-Schicht*, nicht eine neue Zielarchitektur. Reifegrade trennen LIVE von ZIELBILD; keine
> ZIELBILD-Komponente ist P0.

---

## 1. Schicht H — Non-bypassable Execution Gate Chain

**Invariante:** Ein Signal wird **niemals** direkt zur Order. Jedes Gate ist fail-closed (Ausnahme explizit
markiert). Jede Ablehnung wird mit `reason_code` (siehe `app/risk/reason_codes.py`) **auditpflichtig** in den
AuditStream geschrieben. Der globale Kill-Switch `EXECUTION_ENTRY_MODE` steht **vor** und **über** der Kette:
bei `disabled` wird jedes risikoerhöhende Intent mit `ENTRY_MODE_DISABLED` blockiert, bevor ein Gate überhaupt
prüft.

```
Signal
  → [G1] Data Quality (Freshness / Plausibility / Cross-Exchange)
  → [G2] Regime
  → [G3] Correlation / Cluster
  → [G4] Risk: VaR / CVaR / Drawdown / Exposure
  → [G5] Liquidity / Slippage
  → [G6] Thesis Invalidation
  → [G7] High-Impact Approval
  → [G8] Structured Audit
  → [G9] Execution (Paper · Live=disabled)
```

Reifegrad-Legende: **LIVE** = im Betrieb · **VORBEREITET** = Schnittstelle/Datenmodell da, gegated ·
**ZIELBILD** = geplant, noch nicht gebaut.

---

### G1 — Data Quality Gate  ·  Reifegrad: **LIVE (teilweise)**

| Feld | Inhalt |
|---|---|
| **Owner** | Watchdog (Health & Drift) · data-quality-inspector · Code: `app/market_data/`, `app/observability/edge_report.py` |
| **Input** | Market-Data-Snapshot (Preis/Volumen/Zeitstempel), Quellen-Metadaten, Signal-Referenzpreis |
| **Output** | `quality_ok: bool` + Snapshot oder Block; bei OK weitergereichter validierter Tick |
| **Failure mode** | Stale-Snapshot (Zeitstempel zu alt), implausibler Tick (>40 % Abweichung), fehlende Quelle |
| **Fail-Modus** | **fail-closed** — stale/implausibel ⇒ kein Trade; `app/market_data` stale-gated Snapshots |
| **Audit event** | `reason_code` u. a. `REJECT_UNCLASSIFIED` / Provenance-Log + Implausibilitäts-Guard-Eintrag |
| **Tests** | `tests/unit/test_market_data.py`, `test_market_data_coingecko.py`, `test_freshness_check.py`, `test_hold_metrics_freshness.py`, `test_edge_report.py` |
| **Lücke (P1)** | **Cross-Exchange Weighted-Median** fehlt — Single-Exchange-Tick wird nicht gegen andere Börsen gekreuzt (Crosscheck §3.1). |

### G2 — Regime Gate  ·  Reifegrad: **LIVE (Observer→Filter)**

| Feld | Inhalt |
|---|---|
| **Owner** | Architect (Klassifikator-Design) · Neo (Code) · Code: `app/regime/`, RiskEngine Gate 9 |
| **Input** | BTC/ETH-Returns, Volatilitätsklasse, klassifiziertes Regime (deterministic threshold + 2-Bar-Hysterese) |
| **Output** | `regime_ok: bool` (Regime-Konflikt-Check), Regime-Label im Decision-Context |
| **Failure mode** | Signalrichtung steht im Konflikt zum aktuellen Regime; Choppiness/Anti-Fehlsignal |
| **Fail-Modus** | **fail-closed** im Gate (RiskEngine Gate 9 `REGIME_CONFLICT`); Regime-Schicht selbst ist read-only Observer |
| **Audit event** | `REJECT_REGIME_CONFLICT` |
| **Tests** | `tests/unit/test_market_regime_detection.py`, `test_regime_classifier.py`, `test_regime_service.py`, `test_regime_calibration.py`, `test_regime_lookup.py`, `test_regime_storage.py` |

### G3 — Correlation / Cluster Gate  ·  Reifegrad: **Berechnung LIVE · hartes Blocking-Gate VORBEREITET**

| Feld | Inhalt |
|---|---|
| **Owner** | Neo · Code: `app/risk/portfolio_risk.py` (`_correlation`, `correlation_breakdown`-Stress), `app/analysis/narratives/cluster.py` |
| **Input** | Bestehende Positionen, Korrelationsmatrix, Cluster-/Narrative-Zugehörigkeit des Kandidaten |
| **Output** | Korrelations-/Cluster-Exposure-Kennzahl; bei VORBEREITET: Block bei Cluster-Übergewicht |
| **Failure mode** | Verdeckte Klumpenrisiken (mehrere hoch-korrelierte Positionen = effektiv eine Wette) |
| **Fail-Modus** | **fail-closed** (sobald als hartes Gate aktiv); Korrelations-Stress (correlation→1) ist heute in VaR berücksichtigt |
| **Audit event** | (geplant) `REJECT_CLUSTER_CONCENTRATION` — Code zu vergeben bei Aktivierung |
| **Tests** | `tests/unit/test_portfolio_risk_engine.py`, `test_narrative_clustering.py` |
| **Hinweis** | Korrelations-*Berechnung* ist LIVE; ein eigenständiges **blockierendes** Cluster-Gate ist VORBEREITET (Diversification-Cap D-226/D-228 wirkt bereits portfolioseitig). |

### G4 — Risk Gate: VaR / CVaR / Drawdown / Exposure  ·  Reifegrad: **LIVE**

| Feld | Inhalt |
|---|---|
| **Owner** | Neo (Engine) · SENTR (non-bypassable-Invariante) · Code: `app/risk/engine.py` (Gates 1–10), `app/risk/portfolio_risk.py` |
| **Input** | Order-Intent (Entry/SL/TP/Leverage/Margin), Equity, offene Positionen, Tages-PnL, Drawdown-Stand |
| **Output** | `RiskDecision` mit `approved: bool` + `reason_codes: list[str]` |
| **Failure mode** | SL fehlt/invers, RR zu niedrig, Sub-Cost-Geometrie, Confidence/Confluence zu niedrig, Tagesverlust, Drawdown, Position zu groß, Martingale/Averaging-Down |
| **Fail-Modus** | **fail-closed** (Hard-Gate SL erzwungen; Martingale fail-safe). `RISK_GATES_MODE`: `off`/`audit`/`enforce` betrifft **nur** die optionalen Reward/Risk-Gates (Gate 10), nicht die Hard-Gates 1–9 |
| **Audit event** | `REJECT_KILL_SWITCH`, `REJECT_STOP_LOSS_MISSING`, `REJECT_SL_GEOMETRY`, `REJECT_SUB_COST_GEOMETRY`, `REJECT_RR_TOO_LOW`, `REJECT_DAILY_LOSS`, `REJECT_DRAWDOWN`, `REJECT_POSITION_TOO_LARGE`, … (vollständig in `reason_codes.py`) + `risk_gate_audit`-Spur |
| **Tests** | `tests/unit/test_risk_engine.py`, `test_risk_engine_properties.py` (property-based Invarianten), `test_risk_cost_geometry_gate.py`, `test_risk_gate_audit.py`, `test_kai_risk_guards.py`, `test_portfolio_risk_engine.py`, `test_position_risk.py` |

### G5 — Liquidity / Slippage Gate  ·  Reifegrad: **Slippage-Modell LIVE · Liquiditäts-Pre-Trade-Gate VORBEREITET**

| Feld | Inhalt |
|---|---|
| **Owner** | Neo · Code: `app/execution/` (PaperExecutionEngine), `app/risk/engine.py` (`REJECT_NOTIONAL_TOO_LOW`, `REJECT_EXCHANGE_FILTER`, `REJECT_INVALID_TICK_SIZE`) |
| **Input** | Order-Notional, Tick-Size/Exchange-Filter, modellierte Slippage + Fees |
| **Output** | angepasster Fill-Preis (Paper) bzw. Block bei Filter-Verletzung |
| **Failure mode** | Notional zu klein, Tick-Size-Verletzung, Exchange-Filter; (ZIELBILD: Orderbuch-Tiefe unzureichend) |
| **Fail-Modus** | **fail-closed** für Exchange-Filter/Tick/Notional; Slippage+Fees werden im Paper-Fill **angewendet** (nicht geblockt) |
| **Audit event** | `REJECT_NOTIONAL_TOO_LOW`, `REJECT_INVALID_TICK_SIZE`, `REJECT_EXCHANGE_FILTER` |
| **Tests** | `tests/integration/test_bridge_gate_smoke.py`, Paper-Engine-Lifecycle-Tests |
| **Lücke** | Echte **Orderbuch-Tiefe-/Liquiditätsprüfung** pre-trade = VORBEREITET; heute nur Slippage-Modell + statische Filter. |

### G6 — Thesis Invalidation Gate  ·  Reifegrad: **Pflichtfeld LIVE · Laufzeit-Monitoring VORBEREITET**

| Feld | Inhalt |
|---|---|
| **Owner** | Architect (Vertrag) · Code: `app/orchestrator/decision_journal.py` (`invalidation_condition`), `app/execution/models.py` (`min_length=1`), `app/signals/generator.py` |
| **Input** | Signal-These + explizite Invalidierungsbedingung |
| **Output** | persistierte `invalidation_condition` (nicht-leer erzwungen); Reasoning-Step bei Gate-Reject |
| **Failure mode** | Trade ohne formulierte Ausstiegs-/Invalidierungslogik; These bereits durch Risk-Bounds widerlegt |
| **Fail-Modus** | **fail-closed** bei Erstellung — leere `invalidation_condition` ⇒ Reject (`_require_non_blank`) |
| **Audit event** | structured-reasoning Step „invalidation" in `app/audit/structured_reasoning.py` |
| **Tests** | `tests/unit/` decision-journal / signal-generator Pfade |
| **Lücke** | Automatisches **Laufzeit-Monitoring** der Invalidierungsbedingung mit Auto-Exit = VORBEREITET/ZIELBILD; heute Pflichtfeld + Reasoning-Step. |

### G7 — High-Impact Approval Gate  ·  Reifegrad: **LIVE (Approval-Mode)**

| Feld | Inhalt |
|---|---|
| **Owner** | **Operator (Sascha)** = Approver · SENTR = HMAC-/Auth-Enforcement · Code: `app/messaging/` (Telegram), `app/execution/` Approval-Bridge, Approval-Service |
| **Input** | Normalisiertes Signal mit `correlation_id`, High-Impact-Klassifikation |
| **Output** | Operator-Approval (Telegram-Klick / ADR-0004 Auto-Fill) ⇒ Freigabe; sonst kein Übergang |
| **Failure mode** | Order ohne menschliche Freigabe bei High-Impact; abgelaufene/duplizierte Approval |
| **Fail-Modus** | **fail-closed** — ohne gültige (HMAC-verifizierte) Approval kein Übergang in Execution |
| **Audit event** | Approval-Event mit `correlation_id` + HMAC-Verifikation; Latenz-Summary |
| **Tests** | `tests/unit/test_approval_hmac.py`, `test_approval_service.py`, `test_approval_latency_summary.py`, `test_telegram_channel_approval.py`, `test_bridge_entry_mode_guard.py` |

### G8 — Structured Audit Gate  ·  Reifegrad: **LIVE**

| Feld | Inhalt |
|---|---|
| **Owner** | SENTR · Code: `app/audit/` (`structured_reasoning.py`, `sanitization.py`, `decision_chain.py` Hash-Chain) |
| **Input** | Entscheidungs-Kontext, Reasoning-Steps, PII/Secret-haltige Rohdaten |
| **Output** | append-only AuditStream-Eintrag (JSONL, `correlation_id`-Kette), redigiert, tamper-evident |
| **Failure mode** | Rohe Chain-of-Thought / PII / Secrets im Log; nicht-verkettbarer Eintrag; AuditStream nicht schreibbar |
| **Fail-Modus** | **fail-closed** — Audit ist Voraussetzung für Execution; **Recording ≠ Executing** bleibt invariant |
| **Audit event** | der Eintrag selbst (decision-hash-chain); Replay-fähig |
| **Tests** | `tests/unit/test_audit_sanitization.py`, `test_audit_stream_validation.py`, `test_audit_replay_*` (lifecycle/resilience/correction), `test_blocked_audit.py`, `test_kai_audit_service.py` |

### G9 — Execution (Paper · Live=disabled)  ·  Reifegrad: **Paper LIVE · Live VORBEREITET (disabled)**

| Feld | Inhalt |
|---|---|
| **Owner** | **Operator (Sascha)** (Live-Unlock) · Neo (Engine) · Code: `app/execution/` (PaperExecutionEngine, LiveExecutionEngine, `ExecutableOrderIntent`) |
| **Input** | freigegebener, voll-gegateter Order-Intent |
| **Output** | Paper-Fill (16-State-Lifecycle, Slippage+Fees) oder — bei Live — Exchange-Order (heute geblockt) |
| **Failure mode** | Live-Order trotz disabled-Mode; Doppel-Fill; State-Drift |
| **Fail-Modus** | **fail-closed** — `EXECUTION_ENTRY_MODE=disabled` blockiert global jedes risikoerhöhende Intent (`ENTRY_MODE_DISABLED`); Live-Engine-Pfad ungeöffnet |
| **Audit event** | `ENTRY_MODE_DISABLED` (ExecutionBlockerCode) + `FinalStatus` (EXECUTED/REJECTED_WITH_REASON/QUARANTINED/…) |
| **Tests** | `tests/unit/test_entry_mode.py`, `test_bridge_entry_mode_guard.py`, `test_live_audit.py`, Paper-Lifecycle-Tests |

> **Kein Bypass:** Die Premium-Telegram-Bridge wurde 2026-06-02 als Bypass geschlossen (Kill-Switch greift global,
> Invariant-Test grün). Es existiert **keine** zweite parallele State-Machine zu `LIFECYCLE_TRANSITIONS`.

---

## 2. Schicht D — Metric Registry / Truth Layer v2

**Reifegrad: VORBEREITET (begonnen via #147 Dashboard-Truth-Layer).** Ziel: jede kritische Kennzahl hat genau
**eine** autoritative Backend-Berechnung; das Frontend **zeigt nur an, berechnet nichts**.

### 2.1 Registry-Vertrag (pro Metrik)

| Feld | Bedeutung | Pflicht |
|---|---|---|
| `metric_id` | stabiler, eindeutiger Schlüssel (z. B. `portfolio.realized_pnl_usd`) | ja |
| `calculation_version` | semver der Berechnungslogik; Änderung ⇒ neue Version + Reconciliation | ja |
| `owner` | verantwortliches Modul/Agent (Code-Pfad + Agent) | ja |
| `tolerance` | erlaubte Abweichung Backend↔Anzeige bzw. Re-Calc (absolut/relativ) | ja |
| `calculation` | **backend-only** — Pfad zur autoritativen Funktion; Frontend referenziert nur | ja |
| `display` | **dashboard display-only** — Frontend liest `metric_id`, rechnet nicht nach | ja |
| `reconciliation_status` | `reconciled` / `pending` / `divergent` / `not_started` | ja |

### 2.2 Initiales Register (Delta — wird beim Bau in Code/JSON gegossen)

| `metric_id` | `calc_version` | Owner (Modul) | `tolerance` | Backend-Calc | Display-only | `reconciliation_status` |
|---|---|---|---|---|---|---|
| `portfolio.realized_pnl_usd` | v1 | Neo · `app/execution/` + `app/observability/*_snapshot` | ±0.01 USD | ja | ja | `pending` (#147 begonnen) |
| `portfolio.unrealized_pnl_usd` | v1 | Neo · `app/execution/` | ±0.01 USD | ja | ja | `pending` |
| `portfolio.exposure_pct` | v1 | Neo · `app/risk/` | ±0.1 % | ja | ja | `pending` |
| `portfolio.drawdown_pct` | v1 | Neo · `app/risk/engine.py` | ±0.1 % | ja | ja | `pending` |
| `risk.var_95` / `risk.cvar_95` | v1 | Neo · `app/risk/portfolio_risk.py` | methodenabhängig | ja | ja | `not_started` |
| `risk.sharpe` / `risk.sortino` | v1 | Neo · `app/risk/portfolio_optimizer.py` | ±0.05 | ja | ja | `not_started` |
| `attribution.by_source` | v1 | Watchdog · `app/observability/edge_report.py` | ±0.01 USD | ja | ja | `pending` (Canary-Attribution gefixt #137) |

> **Regel:** Eine Kennzahl darf **nie** im Frontend neu berechnet werden. Divergenz Backend↔Anzeige außerhalb
> `tolerance` ⇒ `reconciliation_status = divergent` ⇒ Anzeige blockt/markiert (kein stiller Fehlwert).
> DuckDB-Vorlage: `metric_id VARCHAR(64) PRIMARY KEY` (ADR-0003) ist bereits vorgesehen.

---

## 3. RACI- / Approval-Matrix

**R** = Responsible (führt aus) · **A** = Accountable (genehmigt, eine Person) · **C** = Consulted · **I** = Informed.
Operator = **Sascha**. Agenten = Claude-Code-only Roster (`AGENTS.md`).

| Aktion | Responsible | Accountable | Consulted | Informed | Auditpflicht |
|---|---|---|---|---|---|
| **Data Quality Gate** (Schwellen/Quellen) | Watchdog / data-quality-inspector | Operator | Architect, SENTR | Neo | ja — Provenance + reason_code |
| **Risk Gate** (Limits/Schwellen) | Neo | Operator | SENTR, Architect | Watchdog | ja — `risk_gate_audit` |
| **Signal Approval** (High-Impact) | Operator | **Operator** | SENTR (HMAC) | Watchdog | ja — Approval-Event + HMAC |
| **Cancel Signal** | Operator | **Operator** | Neo | SENTR | ja — Cancel-Event + reason |
| **Model Release** (Param-Version) | Neo | Operator | Architect, SENTR | Watchdog | ja — `parameter_version` |
| **Prompt Release** (Prompt-Version) | Architect | Operator | SENTR | Neo | ja — Prompt-Version-Log |
| **Live Unlock** (`entry_mode` flip) | — (gesperrt) | **Operator** | SENTR, SATOSHI, Architect, Neo | alle | ja — Phase-0-Gates + Sign-off |
| **Kill Switch** (trigger/reset) | Operator / RiskEngine | **Operator** | SENTR | alle | ja — Kill-Switch-Event |
| **Dashboard Metric Change** (`calc_version`) | Neo / DALI | Operator | Architect | Watchdog | ja — Registry-Diff + Reconciliation |

> **Approval-Härte:** *Live Unlock* und *Kill Switch* sind ausschließlich beim **Operator** accountable — kein Agent
> darf `entry_mode` stillschweigend flippen (vgl. KAI_IDENTITY §H, Non-Negotiable Rules). *Model/Prompt Release* und
> *Dashboard Metric Change* sind heute teil-erzwingend (Versionierung vorhanden, **erzwingender** Registry-Gate = P2).

---

## 4. Reifegrade je Modul (Delta-Snapshot)

| Modul / Komponente | Reifegrad |
|---|---|
| Data-Quality stale-gate + 40 %-Implausibilität (`market_data`, `edge_report`) | **LIVE** |
| Cross-Exchange Weighted-Median (G1-Erweiterung) | **ZIELBILD (P1)** |
| Regime-Klassifikator + Regime-Gate (`app/regime`, RiskEngine Gate 9) | **LIVE** |
| Korrelations-/Cluster-Berechnung (`portfolio_risk`) | **LIVE** |
| Hartes blockierendes Cluster-Concentration-Gate | **VORBEREITET** |
| RiskEngine Gates 1–9 (VaR/CVaR/DD/Exposure/SL/RR) | **LIVE** |
| Reward/Risk-Gates 10 (`RISK_GATES_MODE`) | **LIVE (default `audit`)** |
| Slippage+Fees-Modell (Paper) | **LIVE** |
| Orderbuch-Tiefe-/Liquiditäts-Pre-Trade-Gate | **VORBEREITET** |
| Thesis-Invalidation als Pflichtfeld | **LIVE** |
| Thesis-Invalidation Laufzeit-Monitoring + Auto-Exit | **VORBEREITET** |
| High-Impact Approval (Telegram + HMAC) | **LIVE (Approval-Mode)** |
| Structured Audit + Hash-Chain + Redaction | **LIVE** |
| PaperExecutionEngine (16-State-Lifecycle) | **LIVE** |
| LiveExecutionEngine + `ExecutableOrderIntent` | **VORBEREITET (disabled)** |
| `EXECUTION_ENTRY_MODE` Kill-Switch | **LIVE (=disabled)** |
| Metric Registry / Truth-Layer v2 (formal) | **VORBEREITET (begonnen #147)** |
| Model-/Prompt-Registry als **erzwingender** Gate | **VORBEREITET (P2)** |
| Source-Reputation-Score + Bot-Penalty | **VORBEREITET (P2)** |
| Financial Knowledge Graph | **ZIELBILD (P3)** |
| Causal-DAG-Framework | **ZIELBILD (P3)** |
| Hardware-Key / Challenge-Response / RBAC-ABAC | **ZIELBILD (nur bei Live-Reife)** |

---

## 5. Overengineering-Schutz (verbindlich)

Übernommen aus Crosscheck §4 — diese Komponenten sind **bewusst zurückgestellt**, **keine** ist P0:

- **Knowledge Graph** → **P3 / ZIELBILD.** Hoher Aufwand, für Single-Operator/Pi-5 überdimensioniert. Vorstufe
  `analysis/narratives/cluster.py` genügt heute. **Nicht** als Voraussetzung für Execution behandeln.
- **Causal-DAG-Framework** → **P3 / ZIELBILD.** „Erst Edge messen, dann Edge-Maschinerie" — solange echter
  Generator-Edge nicht gemessen ist (`real_resolved=0`, Canary-Artefakt), löst ein DAG-Framework ein Problem,
  das wir noch nicht haben.
- **Hardware-Key / Challenge-Response / RBAC-ABAC-Vollausbau** → relevant **erst bei Live-Reife**. Live ist
  `disabled`. **Kein P0.**
- **DORA / MiFID-II** → als **Prinzip** (Resilienz, Audit-Trail, Nachvollziehbarkeit) berücksichtigt — **kein**
  Voll-Regulatorik-Projekt im Single-Operator-Paper-First-Modus. Kein Finanzdienstleister-Status.
- **14-stufige Linear-Pipeline als Zielarchitektur** → **abgelehnt.** Sie ist nur die explodierte H-Schicht; die
  SSOT bleibt das A–H-Modell in `KAI_IDENTITY.md`.

---

## 6. Akzeptanz-Check (dieses Delta)

- [x] Bestehende Architektur (A–H, `KAI_IDENTITY.md`) **nicht überschrieben** — dieses Dokument ist subordiniert.
- [x] Ergänzungen sind **delta-basiert** (Gate-Kette, Truth-Layer v2, RACI, Reifegrade, Overengineering-Schutz).
- [x] **Jedes Gate** hat Owner + Auditpflicht (`reason_code`/Audit-Event).
- [x] **Keine ZIELBILD-Komponente als P0** markiert (Knowledge Graph/Causal-DAG/Hardware-Key/Cross-Exchange = P1–P3).
- [x] **Keine** neue 14-stufige Greenfield-Pipeline als Ersatz.

---

## Verweise

- **SSOT:** [`docs/KAI_IDENTITY.md`](../KAI_IDENTITY.md) (A–H-Schichtenmodell, Reifegrade)
- **Crosscheck-Herkunft:** [`docs/audit/architecture_crosscheck_20260604.md`](../audit/architecture_crosscheck_20260604.md)
- **Code:** `app/risk/engine.py`, `app/risk/reason_codes.py`, `app/risk/portfolio_risk.py`, `app/regime/`,
  `app/market_data/`, `app/execution/`, `app/audit/`, `app/messaging/`
- **Agenten/RACI-Basis:** `AGENTS.md`, `CLAUDE.md` § Agent Roster
- **Storage-Vorlage Metric-Registry:** `docs/adr/0003-duckdb-storage-pivot.md` (`metric_id` PK)
