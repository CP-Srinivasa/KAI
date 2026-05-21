# Bayes-Audit-Trend & Decision-Basis — 2026-05-21

**Auftrag:** DS-20260521-V6 (P1) — Bewerten, ob die Bayes-Audit-Datenbasis am EOW-Review 2026-05-23 ausreicht für eine substanzielle `RISK_BAYES_CONFIDENCE_SHADOW_ONLY=false`-Flip-Decision.
**Methode:** Auswertung `artifacts/bayes_confidence_audit.jsonl` + Extrapolation aus Schreibrate.
**Verfasser:** Claude Code Mid-Window-Forensik. Read-only, kein Pi-Eingriff.

---

## 1. Datenbestand (Stand 2026-05-21T08:58 CEST)

| # | Timestamp UTC | Symbol | Direction | Posterior | Evidence-Weight | Dominanter Faktor |
|---|---|---|---|---|---|---|
| 1 | 2026-05-15 (Phase-2D-E2E-Test) | — | — | — | — | E2E-Validierung |
| 2 | 2026-05-15 (Phase-2D-E2E-Test) | — | — | — | — | E2E-Validierung |
| 3 | 2026-05-20T18:18:57 | BTC/USDT | long | 0.964 | 2.90 | news_relevance +2.0 (cryptoslate CME-VIX) |
| 4 | 2026-05-21T04:14:07 | BTC/USDT | long | 0.957 | 2.70 | news_relevance +1.8 |

**4 Einträge total.** Davon 2 aus E2E-Test (nicht aus echtem Pipeline-Flow) → effektiv **2 organische Einträge** in 5 Tagen seit ADR-1-Reversion 19.05. 21:34 CEST.

## 2. Schreibrate

- **Roh:** 4 Einträge / 6.5 Tage seit 2026-05-15 = **0.62/Tag**.
- **Organisch:** 2 Einträge / 5 Tage seit 19.05. 21:34 CEST = **0.40/Tag**.
- **Pi-`.env`** weiterhin `EXECUTION_PAPER_MIN_PRIORITY=10`, `RISK_BAYES_CONFIDENCE_SHADOW_ONLY=true`.

## 3. Extrapolation bis 2026-05-23 EOW-Review

Bei aktueller organischer Rate (0.40/Tag): bis 23.05. realistisch **+1 Eintrag** = 5 Einträge total = 3 organisch.

## 4. Wann ist die Datenbasis tragfähig?

Heuristik (konservativ, in Anlehnung an Wilson-Score-Intervall + Bayes-Update-Praxis bei dünnen Counts):

| n Einträge | Standardfehler Posterior-Schätzung | Eignung für SHADOW_ONLY-Flip |
|---|---|---|
| <5 | sehr hoch (>20%) | NICHT geeignet — Indizien |
| 5-10 | hoch (10-20%) | NICHT geeignet — schwache Inferenz |
| 10-20 | mittel (5-10%) | eingeschränkt geeignet, mit Sentinel-Period |
| 20-50 | niedrig-mittel (3-5%) | ausreichend für graduelle Aktivierung |
| >50 | niedrig (<3%) | volltragend |

**Mit 4-5 Einträgen am 23.05. liegen wir tief im "NICHT geeignet"-Bereich.**

Zudem: alle 4 Einträge sind BTC/USDT long mit hoher news_relevance — **keine Diversität** über Symbole, Directions oder dominante Faktoren. Eine SHADOW_ONLY-Flip-Decision auf dieser Basis würde implizit nur für genau diesen Eintrags-Typ generalisieren.

## 5. Empfehlung an Operator (EOW-Review 23.05.)

**SHADOW_ONLY bleibt `true`.** Bedingungen für Flip-Re-Evaluierung:

- **n >= 20 organische Audit-Einträge** (~50 Tage bei aktueller Rate, ~10 Tage falls Priority-Scoring-Decision A/B die Schreibrate verdoppelt+).
- **ODER >= 4 Wochen Beobachtungsfenster** (bis ca. 2026-06-16).
- **UND mindestens 3 verschiedene Symbole + beide Directions vertreten** (Diversitäts-Vorbedingung).

Zusätzlich vor Flip:
- Sentinel-Period definieren (z.B. 7 Tage Live mit Position-Cap `max_open_positions <= 2` für Bayes-getriggerte Trades).
- Rollback-Trigger: bei n>=3 Bayes-Trades mit Posterior >0.9 aber Outcome=miss → automatischer SHADOW_ONLY=true.

## 6. Was diese Bewertung NICHT tut

- Keine Aussage über Bayes-Code-Qualität — Code ist über Phase-2D-E2E-Test validiert.
- Keine Aussage über die *Richtigkeit* der bisherigen 4 Posteriors — beide BTC-Einträge haben hohe Posteriors (0.96/0.96) aber wir haben kein Outcome-Match (n=2 organisch, keine zugeordnete Trade-PnL).
- Keine Decision-Empfehlung zu Position-Sizing-Aktivierung (separater Backlog-Punkt aus [[session-2026-05-16-goal-sprint-pause-handover]]).

## 7. Kopplung zur Priority-Scoring-Decision

Falls Operator am 23.05. Option A (Sentiment-Faktor in compute_priority) wählt: erwartbare Trade-Frequenz steigt, dann auch Bayes-Audit-Schreibrate. Ziel n=20 in ~10-14 Tagen erreichbar.
Falls Option D (Status quo): Schreibrate bleibt 0.40/Tag, n=20 erst ca. 15.06. erreichbar.

Diese Kopplung ist ein zusätzliches Argument für Operator-Variante 1 aus dem Decision-Brief (Option D bis 30.05., dann re-evaluieren auf 7d-Daten) ist möglicherweise zu konservativ wenn parallele Bayes-Datenbasis-Akkumulation gewünscht ist — das ist Trade-Off, nicht Pflicht.

## 8. Datenquellen

- `artifacts/bayes_confidence_audit.jsonl` (4 Einträge, letzter 2026-05-21T04:14:07 UTC)
- Pi-`.env` `EXECUTION_PAPER_MIN_PRIORITY=10`, `RISK_BAYES_CONFIDENCE_SHADOW_ONLY=true`
- Decision-Brief: `priority_scoring_decision_brief_2026-05-23.md`
- Voll-Inspection: `priority_scoring_inspection_2026-05-20.md`
