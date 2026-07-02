# Architektur-Crosscheck — externes „Institutional Investment Intelligence"-Papier

**Stand:** 2026-06-04 · **Typ:** Crosscheck / Red-Team-Bewertung (KAI Directive §6, §9, §12)
**Anlass:** Operator legte ein umfangreiches externes Strategiepapier vor ("Systemumbau von KAI zu einer
institutionellen Investment-Intelligence-Plattform"), erstellt von einem externen LLM auf Basis von
*Antigravity-Annahmen* über den Code-Stand — **ohne** den realen Code zu lesen.

Dieses Dokument hält fest, was davon **stimmt**, was **bereits gebaut** ist, was **genuin fehlt**
und was **Overengineering** für unseren Kontext (Single-Operator, Raspberry Pi 5, Paper-First,
`entry_mode=disabled`) wäre.

> **Kernbefund:** Das Papier ist ein solider *generischer* Aufsatz über institutionelle Finanzarchitektur,
> aber als KAI-Audit zu ~70–80 % **veraltet/faktisch falsch**: Es markiert als „fehlend / P0 / zuerst bauen",
> was längst im Repo liegt — oft anspruchsvoller als das, was es vorschlägt. Sein „Master-Prompt"
> („*zuerst die Kontrollarchitektur bauen*") würde uns wochenlang vorhandene Module neu bauen lassen
> (Verstoß gegen CLAUDE.md: *preserve existing architecture*, *extension over replacement*).

---

## 1. „Fehlt / P0" laut Papier — Realität im Code

| Papier behauptet „fehlt / P0" | Realität im Repo | Verdikt |
|---|---|---|
| DuckDB/SQLite statt JSONL-Reads | `app/storage/analytics_db.py`, `duckdb_migrate.py`, `compaction_worker.py`, `compaction_v2.py`, DuckDB-Migrations | **falsch** — Architektur existiert |
| Data-Quality-Gate (Freshness/Plausibility) | `app/market_data/` stale-gated snapshots + 40%-Implausibilitäts-Guard (`observability/edge_report.py`) | **teilweise** — Cross-Exchange-Median fehlt |
| CVaR / Expected Shortfall / VaR | `app/risk/portfolio_risk.py`: historical / parametric / Cornish-Fisher / Student-t / Monte-Carlo VaR+ES, Hill-Tail-Index, Cholesky, correlation_breakdown-Stress | **falsch** — reicher als das Papier |
| HRP / Black-Litterman ("P2-Differenzierung") | `app/risk/portfolio_optimizer.py`: HRP (Lopez de Prado), Risk-Parity (Spinu), Max-Sharpe, Max-Sortino, Min-Variance | **falsch** — HRP gebaut, nicht Zukunft |
| Strukturiertes Audit ohne Chain-of-Thought + Redaction | `app/audit/structured_reasoning.py` + `sanitization.py` + `decision_chain.py` (hash-chain) | **falsch** — genau so umgesetzt |
| ADX/Choppiness gegen Fehlsignale | `app/analysis/indicators/adx.py`, `realized_volatility.py`, `app/regime/` | **teilweise** — Multi-Regime-Erweiterung offen |
| SSE/WebSocket statt Polling | `app/api/event_hub.py` + `routers/events.py` | **falsch** — Event-Hub existiert |
| Calibration / Counterfactual / Walk-Forward | `app/learning/`: `calibration.py`, `active_calibrator.py`, `regime_calibration.py`, `counterfactual.py`, `walk_forward.py` | **falsch** — vorhanden |
| Manipulation-Detection-Regime | `app/risk/manipulation_detection.py` | **falsch** |
| KYT (Papier nennt es nicht) | `app/security/kyt/` (engine/providers/audit) angelegt | Papier unterschätzt Reifegrad |
| Truth-Layer / keine Frontend-Berechnung | PR #147 Dashboard-Truth-Layer + `observability/*_snapshot` | **teilweise** — formale Metric-Registry offen |

---

## 2. Was das Papier *richtig* sieht (behalten)

- **„Ein Signal darf nie direkt zur Order werden"** — deckt sich mit unserer Gate-Chain + `entry_mode=disabled` + Approval-Mode.
- **Control-before-Autonomy** — deckt sich mit `feedback_kai_no_prediction` und dem Memory-Leitsatz.
- **Truth-Layer / eine autoritative Berechnungsquelle pro Kennzahl** — berechtigte Schärfung; bei uns begonnen (#147), aber **noch nicht** als formale Metric-Registry mit `calculation_version`-Vertrag.
- **RACI/Approval-Matrix der Agenten** — sinnvoll; wir haben Rollen in `AGENTS.md`, aber keine explizite RACI.
- **Risk-adjusted Agent-Ranking** (Brier/Calibration/IC-by-horizon) — Bausteine in `app/learning/` da, aber nicht als Agent-Scoreboard zusammengeführt.

---

## 3. Genuin fehlend / interessant (die echten Lücken)

1. **Cross-Exchange Weighted-Median-Validierung** — Single-Exchange-Tick wird nicht gegen andere Börsen gekreuzt. Schutz gegen Flash-Crash-/Desync-Fehlsignal. **P1.**
2. **Formale Metric-Registry (Truth-Layer v2)** — `metric_id + calculation_version + owner + Toleranz`, Frontend nur Anzeige. **P1.** (begonnen via #147)
3. **Model-/Prompt-Registry als erzwingender Gate** — heute `learning/parameter_version.py` + `analysis/prompts.py`-Versionierung + `learning/approval.py`, aber kein erzwingender Registry-Gate. **P2.**
4. **Source-Reputation als dynamischer Score** — Quellen-Taxonomie + Exchange-Trust vorhanden, aber keine lernende Reputations-Engine mit Bot-Penalty. **P2.**
5. **Financial Knowledge Graph** — existiert nicht (nur `analysis/narratives/cluster.py` als Vorstufe). **P3 / ZIELBILD** — hoher Aufwand, für Pi-5-Single-Operator überdimensioniert.

---

## 4. Was wir *nicht* brauchen (Overengineering im aktuellen Kontext)

- **14-stufige Linear-Pipeline als „korrekte Zielarchitektur"** → **Nein.** Kein Ersatz für das A–H-Schichtenmodell in `KAI_IDENTITY.md`; es ist nur die H-Schicht (Execution-Pfad) explodiert und ignoriert die Reifegrad-Trennung (LIVE/VORBEREITET/ZIELBILD). SSOT bleibt.
- **DORA/MiFID-II-Compliance-Schwere** → Single-Operator, Paper-First, kein Finanzdienstleister. Als Prinzip (Resilienz/Audit) gelebt; als Regulatorik-Projekt irrelevant.
- **Hardware-Key / Challenge-Response / RBAC-ABAC-Vollausbau** → relevant erst für Live; Live ist `disabled`. Kein P0.
- **HMM-Challenger / DAG-Causal-Frameworks** → Lösung für ein Problem, das wir noch nicht haben: aktuell messen wir nicht einmal echten Generator-Edge (Canary-Probe-Artefakt, `real_resolved=0`). **Erst Edge messen, dann Edge-Maschinerie.**

---

## 5. Korrekte Zielarchitektur = `KAI_IDENTITY.md` (A–H), nicht die Pipeline-Grafik

Berechtigte Schärfungen, die in den SSOT übernommen wurden (Patch 2026-06-04):

- **Schicht H:** Execution-Pfad explizit als non-bypassable Gate-Kette dokumentiert:
  `Signal → DataQuality → Regime → Korrelation/Cluster → Risk(VaR/CVaR) → Liquidität/Slippage → Thesis-Invalidation → Approval(High-Impact) → Audit → Execution`.
- **Schicht D:** Hinweis auf Truth-Layer / formale Metric-Registry (`calculation_version`) ergänzt.

---

## 6. Abgeleitete Issues (ehrliche Reihenfolge gegen realen Stand)

| Prio | Maßnahme | Status heute |
|---|---|---|
| **P0** | Echten Generator-Edge messen (Canary-Artefakt raus, NEO-P-001/002) | offen, root-caused |
| **P1** | Truth-Layer → formale Metric-Registry mit `calculation_version` | begonnen (#147) |
| **P1** | Cross-Exchange-Median im Market-Data-Gate | fehlt |
| **P2** | Model-/Prompt-Registry als erzwingender Gate | teilweise |
| **P2** | Source-Reputation-Score + Bot-Penalty | teilweise |
| **P3/ZIELBILD** | Knowledge Graph, Causal-Framework | bewusst zurückgestellt |

**Empfehlung:** Papier nicht als Bauplan übernehmen, sondern als externen Crosscheck. Nur die echten Lücken
(1–4 in §3) als Issues verfolgen; Rest verwerfen, weil gebaut, SSOT-duplizierend oder Overengineering.
