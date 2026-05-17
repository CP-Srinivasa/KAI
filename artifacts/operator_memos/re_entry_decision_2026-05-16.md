# Re-Entry-Decision Memo — 2026-05-16

**Stichtag:** 2026-05-16 (TV-Pivot Re-Entry, festgelegt 2026-04-23)
**Memo erstellt:** 2026-05-17 23:15 CEST (Audit-Anchor, ein Tag verspätet)
**Modus aktuell:** `RE_ENTRY_MODE` live (Pin), `SHADOW_ONLY=true`, `EXECUTION_PAPER_MIN_PRIORITY=10`

---

## 1. Was wurde gemessen

**Pi-Live-Status 2026-05-17T06:00 UTC (kai-daily-strategy.timer):**

| Metrik | Wert | Gate-Schwelle | Status |
|---|---|---|---|
| Resolved directional alerts (hit+miss) | 382 (127 hit / 255 miss) | ≥200 | ✅ |
| Paper-Trading abgeschlossene Trades | 14 | ≥10 | ✅ |
| TV pending events (unpromoted) | 15 | — | informativ |
| Baseline-Precision (hit/(hit+miss)) | 33.2% | — | beobachtbar, kein hartes Gate |

**Beide Re-Entry-Gates formal erfüllt.**

---

## 2. Forensik-Befund (V3 Precision-Drop-Analyse)

**`artifacts/alert_outcomes.jsonl` Vollscan (Pi, 2026-05-17 23:10 CEST):**

- Total annotated outcomes: **8143**
- davon `hit`: 127 (1.6%)
- davon `miss`: 255 (3.1%)
- davon **`inconclusive`: 7761 (95.3%)**

**Pro-Tag-Verteilung der letzten 14 Tage zeigt:** Auto-Annotate produziert pro Tag 50–800 Outcomes, aber fast ausschließlich `inconclusive`. Hard-Labels (hit/miss) entstehen mit ~5% Rate.

**Konsequenz für die Re-Entry-Decision:**

Die formal als "Baseline-Precision = 33.2%" gemessene Größe basiert auf **4.7% der ursprünglichen Alerts**. Die übrigen 95.3% sind aus dem Lern-Signal effektiv ausgeschlossen. Die Lernschleife V1–V4.1 (Source-Reliability, R3-Shadow, Source-Confluence, Bayes-Posterior) hat damit eine **deutlich dünnere Label-Basis als die Gate-Zahl 382 suggeriert**.

**Das ist kein Bug. Das ist ein Designkonflikt zwischen Gate-Definition (n_resolved) und Lernschleifen-Voraussetzung (n_labeled).**

---

## 3. Decision

**Re-Entry-Phase wird wie folgt eröffnet:**

1. **SHADOW_ONLY=true bleibt aktiv für 7 Tage** (Stichtag 2026-05-23).
2. **EXECUTION_PAPER_MIN_PRIORITY=10 bleibt unverändert** für die ersten 48h. Re-Evaluation nach V6-Memo (separate Decision, siehe ADR-Cluster 2026-05-17).
3. **Mid-Window-Check 2026-05-20** (3 Tage in 7d-Beobachtungsfenster): Drift in V1/V2/V3/V4-Posteriors prüfen, gegebenenfalls Decisions früh ziehen.
4. **End-of-Window-Review 2026-05-23**: vollständiger Lernstack-Health-Check, Decision über Phase 2 (Active-Mode oder weitere Shadow-Phase).

**Gleichzeitig verbindlich aufgenommen in das Re-Entry-Tracking:**

5. **Inconclusive-Rate als kritisches Sekundär-Gate.** Bevor Active-Mode geprüft wird, muss die Hard-Resolution-Rate über 14d-Rolling-Window dokumentiert sein. Ziel-Threshold zur Diskussion (Vorschlag: ≥15% hard-resolved für Active-Mode-Eligibility). Ohne dieses Sekundär-Gate riskiert Active-Mode eine Decision-Basis von dünner Datenlage.

---

## 4. Was NICHT entschieden wurde

- **Kein Live-Trading-Switch.** `LIVE_MODE=false` bleibt unverändert. Re-Entry betrifft Paper/Shadow-Operation, nicht Live-Execution.
- **Kein paper_min_priority-Flip.** Siehe separates ADR-Cluster-Memo 2026-05-17.
- **Keine 5 Goal-Sprint-Decisions** (Tier-Verteilung, R4-Filter, V3-Window, Bayes-Sizing, SHADOW_ONLY-Flip). Siehe separates ADR-Cluster-Memo 2026-05-17.

---

## 5. Verantwortlichkeiten

- **Mid-Window-Check 2026-05-20:** Claude (Auto-Trigger via Daily-Strategy-Sprint).
- **End-of-Window-Review 2026-05-23:** Operator + Claude gemeinsam.
- **Inconclusive-Rate-Untersuchung:** P1-Backlog-Item, nicht Re-Entry-Blocker. Threshold-Diskussion vor 2026-05-23 abgeschlossen.

---

## 6. Provenance

- **Datenquelle Gate-Messung:** Pi `/home/kai/ai_analyst_trading_bot/artifacts/alert_outcomes.jsonl` (3.7M, last write 2026-05-17 23:26 CEST).
- **CLI:** `daily-strategy bootstrap` (`app/cli/commands/daily_strategy.py`).
- **Daily-Strategy-Review:** `artifacts/daily_strategy/2026-05-17.md`.
- **Related Memos:** `paper_min_priority_decision_2026-05-14.md`, `re_entry_adr_cluster_2026-05-17.md` (folgt).
- **Pi-HEAD:** `019d48f3` (== `origin/claude/p7/reentry-ia-codex-cycle`).
