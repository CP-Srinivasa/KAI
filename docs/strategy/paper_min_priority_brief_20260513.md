# Decision-Brief — paper_min_priority (2026-05-13)

**Operator-Frage:** EXECUTION_PAPER_MIN_PRIORITY = 10 vs 7 vs Symbol-Erweiterung?
**Empfehlung:** Threshold bleibt 10. Echter Hebel ist Symbol-Set + Signal-Pfad-Auswahl, nicht die Zahl.

---

## Datenbasis (Pi 2026-05-13, 7d-Fenster)

### trading_loop_audit.jsonl (6081 cycles total, 2223 in 7d)

| Status | Count | % |
|---|---:|---:|
| priority_rejected | 2217 | 99.7% |
| risk_rejected | 4 | 0.2% |
| completed | 2 | 0.1% |

**Pro Symbol:**
- BTC/USDT: 1112 cycles, 1108 priority-rejected, 0 fills (2 completed in älterem Fenster)
- ETH/USDT: 1109 cycles, 1109 priority-rejected, 0 fills
- SOL/USDT, LINK/USDT: je 1 cycle, 0 priority-rejected, beide risk_rejected

**Priority-Verteilung der rejected cycles:** **100% (2217/2217) haben priority=1**.
Kein einziger cycle in 7d hatte priority≥2.

### alert_audit.jsonl (7d-Fenster, 120 dispatched)

| Priority | Total | Actionable |
|---:|---:|---:|
| 10 | 12 | 12 |
| 9 | 5 | 5 |
| 8 | 3 | 0 |
| 7 | 100 | 0 |

→ **alle 17 actionable alerts haben priority ≥ 9**. Senkung auf threshold=7 produziert **0** zusätzliche actionable cases.

**Symbol-Spread der actionable alerts:** BTC=8 (P=10), ETH=3 (P=10), XRP=2, SOL=2, LINK=1 — also auch außerhalb BTC/ETH gibt es regelmäßig actionable signals.

---

## Warum threshold=10 ≠ "zu wenige Trades"

Code-Lesung `app/orchestrator/trading_loop.py:1042` + `:782 build_loop_trigger_analysis`:

- Cron-Job läuft `analysis_profile="conservative"` (Default) → `recommended_priority=1`.
- Threshold=10 lehnt dieses cycle ab. **Threshold=2 würde es auch ablehnen.**
- Profile `bullish`/`bearish` würden priority=10 erzeugen, aber das wäre "trade everything" — unsicher ohne Triggering-Evidenz.
- Echte Trades laufen über `envelope_to_paper_bridge` (TradingView/Operator-pastes) — **dieser Pfad ist vom Priority-Gate ausgenommen** (D-149 Scope-Note: "envelope_to_paper_bridge bleibt unangetastet — Operator-pastes sind bereits Operator-kuratiert").

→ Der scheduled-cron-Pfad ist per Design ein **Health-Check, kein Trade-Treiber**. Threshold-Wert ist hier kosmetisch.

---

## Drei reale Hebel (in Empfehlungs-Reihenfolge)

### Hebel A — Symbol-Erweiterung (RECOMMENDED, P1)

Aktuell loopt der scheduled-cron nur BTC/ETH. alert_audit zeigt regelmäßig actionable alerts für XRP, SOL, LINK, AVAX, TON, ARB.

**Maßnahme:** TradingLoop-Symbol-Liste erweitern, ANALYSIS_PROFILE bleibt "conservative". Damit bekommen weitere Symbole ihren Health-Check.

**Aber:** Solange `recommended_priority=1` der Default-Output ist, ändert das nichts an Trade-Frequenz. → siehe Hebel B.

### Hebel B — Analyse-Provider an alert.priority koppeln (RECOMMENDED, P1)

Aktuell: scheduled-loop generiert seine Analysis via `build_loop_trigger_analysis(profile)` — synthetisches Konstrukt mit fixer Priority. Es zieht **keine** Info aus dem aktuellen alert_audit-Stream.

**Maßnahme:** `build_loop_trigger_analysis` so erweitern, dass es bei Vorhandensein einer frischen actionable Analyse (z. B. aus `alert_audit.jsonl` mit ts ≤ 60 min, matching symbol) deren `priority` + Sentiment übernimmt. Bei keinem Match → Conservative-Fallback wie bisher.

**Effekt mit aktueller Daten:** ~17 actionable trade-Kandidaten in 7d (2.4/Tag), realistisch ~10 davon nach risk-gate → 1-2 Trades/Tag (statt aktuell 0).

**Aufwand:** ~1 Tag (Module: alert_audit-Reader mit Freshness-Filter, Mapping affected_assets→symbol, Test-Coverage).

### Hebel C — Threshold senken (NICHT empfohlen)

Statistisch dünn: D-149-Evidenz ist priorisiert P≥10 vs P7-9 → 72.7% vs 29% hit-rate, disjunkte CIs. Senkung auf 7 erhöht erwartete loss-Frequency 2.5×. Mit aktuellen 17 actionable cases in 7d (5 davon P=9) wären das ~5 zusätzliche Trade-Versuche in 7d, davon ~3-4 erwartet als Loss.

**Wenn Hebel C trotzdem:** auf 9 senken, nicht auf 7 — die 5 P=9-alerts in 7d wurden empirisch noch nicht im disjoint-CI-Band gemessen, würden den Sample-Range natürlich erweitern.

---

## Empfehlung

1. **Threshold=10 bleibt.** Empirie + Architektur stützen das.
2. **Hebel B (Alert→Analysis-Coupling) umsetzen** — das ist die wirkliche Bottleneck-Auflösung. ~1 Tag Aufwand, P1.
3. Hebel A (Symbol-Erweiterung) **erst nach B**, sonst nur mehr "priority=1 rejected"-Spam in trading_loop_audit.

---

## Vorschlag (KAI Master Directive §11)

### Vorschlag
Alert→Analysis-Coupling für scheduled TradingLoop (`build_loop_trigger_analysis` lookups alert_audit)

### Warum jetzt?
Der scheduled-loop läuft 2200×/7d mit fixer priority=1, produziert 0 trades, während 17 actionable alerts in der gleichen Periode ungenutzt vorbeifließen. Threshold-Diskussion ist Symptom-Therapie.

### Erwarteter Nutzen
- 1-2 trades/Tag im paper-mode (vs aktuell 0) → reale Calibration-Daten für Step 5/4/2-Pipeline.
- Trading-Loop wird vom dummy-Health-Check zum echten Signal-Konsumenten.
- Threshold=10 bleibt valide, weil endlich echte priorities ankommen.

### Datenquellen / Systeme
- artifacts/alert_audit.jsonl (input, neu: Reader-Modul)
- app/orchestrator/trading_loop.py::build_loop_trigger_analysis (mod)
- Freshness-Gate: 60 min default, env-konfig

### Umsetzungsweg
1. `app/orchestrator/alert_lookup.py` (neu): Reader für letzte actionable alerts pro symbol mit ts ≤ freshness.
2. `build_loop_trigger_analysis` erweitern: prüft erst lookup, fällt sonst auf bestehende profile-Logik zurück.
3. Tests: 6-8 unit + 1 integration (cron-stub mit gefakten Alerts).
4. Roll-out: feature-flag `EXECUTION_LOOP_USE_ALERT_PRIORITY=false` (default), Operator schaltet manuell.

### Parallel möglich?
Ja. Step 4 (ThresholdOptimizer) und Tabletop-Drill blockieren nicht. Pre-condition: Step 5 gemerged (jetzt e755f89 bereit).

### Aufwand
realistisch 1 Tag (Reader + Test-Coverage + Flag).

### Risiken
- technisch: Race-condition alert vs loop-tick (lockfree, aber „heisses" alert nach Trade-Erstellung könnte als second-trigger reinkommen). Mitigation: idempotenter alert_id-Marker pro cycle.
- qualitativ: aktuelle 17 actionable/7d-sample sehr klein. Empirisch beobachten + Re-Calibration nach 30d-Window.
- operativ: alert_audit Append-only, keine Mutation — safe.

### Priorität
P1 (sofort nach Step 5/4 merge).
