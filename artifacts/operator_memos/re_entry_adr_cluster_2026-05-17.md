# ADR-Cluster: Re-Entry-Phase-Decisions — 2026-05-17

**Kontext:** Operator hat am 2026-05-17 in der Daily-Strategy die 5 Goal-Sprint-Decisions + paper_min_priority-Decision an Claude delegiert ("Claude-only (alle berechtigungen sind vorhanden)"). Re-Entry-Stichtag 2026-05-16 war D-1, beide formalen Gates erfüllt (siehe `re_entry_decision_2026-05-16.md`).

**Grundsatz:** Konservativer Re-Entry. Lieber 7d Schatten-Daten weiter sammeln als auf dünner Label-Basis (4.7% Hard-Resolution) Active-Switches drehen.

---

## ADR-1: paper_min_priority (V6)

**Aktueller Zustand:** `EXECUTION_PAPER_MIN_PRIORITY=10` (Pin-Stand seit 2026-05-15, D-182-Design).

**Beobachtung:** Bayes-Audit-Stream sammelt nur 2 Einträge in 2 Tagen seit Phase 2D E2E-Validation (2026-05-15). Lernschleife V4 (Bayes-Posterior) bekommt zu wenig Samples für brauchbare Posterior-Updates.

**Decision:** **JA, Lockerung 10 → 8 für 48h-Fenster** (Start 2026-05-17 23:30 CEST, Reversion 2026-05-19 23:30 CEST).

**Begründung:**
- 48h sind kurz genug, um Audit-Akkumulation zu beschleunigen, ohne dass es bei einer Drift-Erkennung lange dauert, zurück zu Default zu kommen.
- Priorität 8 ist immer noch in der oberen Hälfte (Skala 1–10), keine Lockerung auf "alles".
- SHADOW_ONLY bleibt aktiv → keine echte Geld-Exposure-Veränderung.

**Reversion-Plan:**
- Pi `.env` `EXECUTION_PAPER_MIN_PRIORITY=8` setzen, `systemctl restart kai-server kai-paper-trading.timer`.
- Reminder-Task `DS-20260519-REVERSION-V6` mit Stichtag 2026-05-19 23:30 anlegen.
- Bei Hard-Drift (≥3 anomale Paper-Outcomes/24h): sofortige Reversion vor Stichtag.

**Risiko:** Mehr Paper-Trades = mehr Audit-Stream-Volumen = potenziell mehr Pi-Disk-Verbrauch. Aktuell 3.7MB alert_outcomes.jsonl, 2.7KB bayes_confidence_audit.jsonl — Disk-Risiko vernachlässigbar.

---

## ADR-2: Tier-Verteilung

**Kontext:** Aus Goal-Sprint Day 1 Pause-Handover offen. "Tier-Verteilung" referenziert vermutlich Source-Reliability-Tiering (V1 Wilson-Loop) oder Signal-Priorität-Tier.

**Aktueller Zustand:** Alle 8 erfassten Sources stehen auf `insufficient` (V1 Wilson-Loop hat noch keinen Source mit ausreichend Samples für brauchbare Reliability-Schätzung).

**Decision:** **NEIN, kein Tier-Switch jetzt.** Tier-Mapping bleibt Default.

**Begründung:**
- Ohne sufficient-Klassifizierung mindestens einer Source ist eine Tier-Verteilung-Decision spekulativ.
- V7 (Source-Reliability Sufficient-Threshold, P2) muss erst Threshold-Definition liefern.

**Trigger für Wieder-Evaluation:** Sobald ≥3 Sources `sufficient` erreichen oder spätestens End-of-Window-Review 2026-05-23.

---

## ADR-3: R4-Filter-Aktivierung

**Kontext:** R3-Shadow läuft seit 2026-05-16 (PR #51, Goal-Sprint Day 1). R4 wäre der nächste Schritt: Regime-Filter als Active-Gate für TradingLoop.

**Decision:** **NEIN, R4 bleibt gesperrt.**

**Begründung:**
- R3-Shadow läuft erst 1 Tag. Mindestens 7d Beobachtung erforderlich, bevor R4 sinnvoll diskutiert wird.
- Memory-Pin `[[regime-r1-observer-status]]`: 14d Operator-Validation läuft. R3 ist Folge davon, R4 noch weiter.
- Aktivierung ohne Datenbasis verletzt KAI-Master §10 (überprüfbar > vage).

**Trigger:** End-of-Window-Review 2026-05-23 + saubere R3-Shadow-Statistik.

---

## ADR-4: V3-Window (Source-Confluence)

**Kontext:** V3 Source-Confluence shadow audit ist via PR #53 live, läuft seit 2026-05-16. Window-Parameter steuert das Zeitfenster, in dem mehrere Sources als "confluent" gewertet werden.

**Decision:** **NEIN, V3-Window-Default beibehalten.**

**Begründung:**
- Window-Tuning ohne Audit-Daten ist Ratepass.
- Phase-2D Bayes-Audit-Stream hat 2 Einträge — auch V3 hat noch zu wenig konfluente Cases für eine Window-Statistik.

**Trigger:** Mid-Window-Check 2026-05-20 + V3-Audit-Stream ≥10 Cases.

---

## ADR-5: Bayes-Sizing-Aktivierung

**Kontext:** Bayes-Posterior (V4, PR #54) berechnet Confidence pro Signal. Bayes-Sizing wäre, diese Confidence direkt in `position_size_usd` einfließen zu lassen statt in der Decision-Chain stehenzubleiben.

**Decision:** **NEIN, Bayes-Sizing bleibt OFF.**

**Begründung:**
- `[[kai-live-trading-security-phase0]]`: Live-Mode bleibt disabled bis Sprint 39/40/41 grün.
- Bayes-Sizing in Paper hätte zwar keine Geld-Exposure, aber würde Paper-Performance-Signale verschmieren — Paper als Lern-Datengrundlage wäre danach nicht mehr vergleichbar mit Pre-Re-Entry.
- 2 Audit-Einträge sind keine Basis für Posterior-Vertrauen.

**Trigger:** End-of-Window-Review 2026-05-23 + Posterior-Stabilität messbar.

---

## ADR-6: SHADOW_ONLY-Flip

**Kontext:** `SHADOW_ONLY=true` bedeutet: Bayes-Confidence wird berechnet und logged, aber nicht in Decision-Chain eingespeist. Flip auf `false` würde Bayes-Aktiv-Mode bedeuten.

**Decision:** **NEIN, SHADOW_ONLY=true bleibt fix für mindestens 7 Tage** (bis 2026-05-23).

**Begründung:**
- Hard-Resolution-Rate 4.7% (V3-Forensik 2026-05-17) ist zu dünn für Active-Bayes.
- Memory-Pin `[[feedback-kai-no-prediction]]`: KAI darf nicht "predicten" — Bayes-Active in einer dünnen Label-Phase wäre genau das.
- Architektur-Gegencheck (KAI-Master §12): Re-Entry-Window hat noch keine Posterior-Stabilität gemessen.

**Trigger:** End-of-Window-Review 2026-05-23 + Posterior-Stabilität + Hard-Resolution-Rate ≥15% (siehe `re_entry_decision_2026-05-16.md` §3.5).

---

## Zusammenfassung

| ADR | Decision | Stichtag |
|---|---|---|
| ADR-1 paper_min_priority 10→8 | ✅ JA, 48h | Reversion 2026-05-19 23:30 CEST |
| ADR-2 Tier-Verteilung | ❌ Defer | 2026-05-23 End-of-Window |
| ADR-3 R4-Filter | ❌ Defer | 2026-05-23 End-of-Window |
| ADR-4 V3-Window | ❌ Defer | 2026-05-20 Mid-Window |
| ADR-5 Bayes-Sizing | ❌ Defer | 2026-05-23 End-of-Window |
| ADR-6 SHADOW_ONLY-Flip | ❌ Defer | 2026-05-23 End-of-Window |

**Implementierungsschritte ADR-1 (heute):**

1. SSH Pi: `.env` editieren — `EXECUTION_PAPER_MIN_PRIORITY=8`.
2. `systemctl restart kai-server kai-paper-trading.timer`.
3. Reminder anlegen für 2026-05-19 23:30 CEST (Reversion).
4. Memo `paper_min_priority_decision_2026-05-17.md` Carry-over zu `paper_min_priority_decision_2026-05-14.md` schreiben.
5. Daily-Strategy-Progress-Tabelle aktualisieren: DS-20260517-4 → in_progress mit Reversion-Stichtag.

**Operator-Veto:** Wenn Operator eine der Defer-Decisions ratifizieren oder eine andere Decision treffen möchte → einfaches Override-Memo `re_entry_adr_cluster_2026-05-17_override.md` anlegen. Bestehende ADRs bleiben als Audit-Anchor unverändert.
