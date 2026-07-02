# P0-Befund — Sentiment-Drift ist Dispatch-Filter-Artefakt (2026-05-24)

**Stichtag:** 2026-05-24, Sonntag, Sentiment-Drift-Forensik-Sprint per Spec [[kai-sentiment-drift-p0-forensik-20260523]].
**Sprint-Owner:** Claude.
**Auftrag:** Hypothesen (c) Markt-Phase + (a) Quellen-Drift vor 30.05.-Decision validieren.
**Befund:** Spec-Annahme grundsätzlich falsch fokussiert. Drift sitzt NICHT im Klassifikator, NICHT in Quellen-Drift, NICHT in Markt-Phase — sondern im DISPATCH-FILTER.

---

## 1. TL;DR

**Die ganze Sentiment-Drift-Story seit 14.05. ist eine Operator-Sichtbarkeits-Artefakt.**

- DB-Realität 16.-24.05.: **31.9% directional** (926 / 2901 alerts mit sentiment_label, Quelle `canonical_documents`)
- Operator-Sichtbarkeit (`alert_audit.jsonl` = telegram-dispatched) 16.-23.05.: **2.7% directional** (3 / 113)
- **Gap: 29.2 pp** — d.h. der LLM klassifiziert weiterhin gesund direktional, aber die Dispatch-Pipeline filtert systematisch.

Die Spec-Annahme "Sample-Kollaps 87.8% → 8.3%" basiert auf alert_audit als Datenquelle und ist deshalb diagnostisch wertlos. Klassifikator-Realität war stabil; der **Operator-View ist durch akkumulierte Filter-Schwellen verzerrt.**

---

## 2. Empirie

### 2.1 LLM-Klassifikator (DB-Layer, `canonical_documents`)

| Window | n | bullish | bearish | mixed | neutral | dir% |
|---|---|---|---|---|---|---|
| 16.-24.05. (8d) | 2901 | 426 | 500 | 148 | 1827 | **31.9%** |
| 30.04.-07.05. (8d, Baseline) | 2603 | 340 | 182 | 88 | 1993 | 20.0% |

→ **Direktional-Anteil ist STEIG­END** (+11.9pp), nicht fallend.

Score-Verteilung 16.-24.05. (canonical_documents):
| Label | avg_score | avg_\|score\| | \|score\|>=0.2 | \|score\|>=0.4 |
|---|---|---|---|---|
| bearish | -0.576 | 0.576 | 500 (100%) | 485 (97%) |
| bullish | +0.624 | 0.624 | 426 (100%) | 414 (97%) |
| mixed | +0.107 | 0.161 | 83 / 148 | 0 |
| neutral | +0.009 | 0.009 | 34 / 1827 | 0 |

→ LLM ist konsistent: directional labels haben hohe Magnitude-Scores; mixed/neutral haben echte Score-Nähe an 0.

### 2.2 Dispatch-Layer (`alert_audit.jsonl`)

| Window | n | bullish | bearish | mixed | neutral | dir% |
|---|---|---|---|---|---|---|
| 16.-23.05. (7d) | 113 | 3 | 0 | 61 | 49 | 2.7% |

→ **Massiver Volumen-Cut** (von 2901 → 113 = -96%) und Direktional-Anteil kollabiert auf 2.7%.

### 2.3 Block-Stream (`blocked_alerts.jsonl`)

485 blocked alerts in 8d, Top-Reasons:

| Block-Reason | n | %  | Filter-Definition |
|---|---|---|---|
| not_actionable | 283 | 58% | `actionable=false` (LLM-Output) |
| **low_directional_confidence** | 105 | 22% | bullish<0.8 oder bearish<0.95 |
| **bearish_directional_disabled** | 51 | 11% | D-142 — bearish kategorisch off |
| low_precision_source | 21 | 4% | Hardcoded LOW_PRECISION_SOURCES + Wilson |
| weak_directional_signal | 14 | 3% | \|sentiment_score\|<0.55 ODER impact<0.6/0.8 |
| reactive_price_narrative | 11 | 2% | Title matches regex "surges/rallies/drops" |

### 2.4 Konkrete Recovery-Liste 23.-24.05. (DB: bullish/bearish + priority=10, NICHT im Operator-Stream)

15 Schlagzeilen die der LLM klar direktional klassifiziert hat, alle p=10, alle geblockt:

- 2026-05-24 02:43 `cryptobriefing` p=10 bullish (s=+0.80): "Iran and US near memorandum of understanding as Bitcoin rallies past 82K on de-escalation hopes" → blocked als **reactive_price_narrative** (matches "rallies")
- 2026-05-24 02:28 `cryptobriefing` p=10 bullish (s=+0.70): "Iran and US move closer to finalizing MOU as Bitcoin surges past 82K" → blocked als **reactive_price_narrative** (matches "surges")
- 2026-05-24 02:43 `btc_echo` p=10 bullish (s=+0.70): "SEC genehmigt neue Bitcoin-Optionen für Nasdaq" → blocked als **low_directional_confidence**
- 2026-05-23 20:58 `coindesk` p=10 bullish: "Bitcoin heads higher as President Trump announces Iran peace agreement" → blocked, vermutl. reactive
- 2026-05-23 18:43 `cryptoslate` p=10 bearish (s=-0.60): "Bitcoin's hard-money thesis is colliding with 5% Treasury yields" → blocked als **bearish_directional_disabled**
- 2026-05-23 17:23 `cryptobriefing` p=10 bearish (s=-0.70): "US Embassy warns of potential large-scale attack on Ukraine within 24h" → blocked als **bearish_directional_disabled**
- 2026-05-23 14:13 `cointelegraph` p=10 bearish (s=-0.60): "Binance denies new WSJ report alleging $850M in Iran-linked transactions" → blocked als **bearish_directional_disabled**
- 2026-05-23 12:13 `cryptoslate` p=10 bullish (s=+0.80): "HYPE's path to $100 runs through Hyperliquid becoming on-chain Wall Street" → blocked als **reactive_price_narrative** (matches "path to")
- 2026-05-23 09:13 `cointelegraph` p=10 bullish (s=+0.70): "SEC approves Nasdaq to list Bitcoin index options" → blocked, ggf. confidence
- 2026-05-23 03:13 `cryptobriefing` p=10 bullish (s=+0.70): "Kevin Warsh appointed 17th Chair of Fed, bringing crypto-friendly stance"
- 2026-05-23 02:58 `cryptobriefing` p=10 bullish: "SEC approves options on Nasdaq Bitcoin Index for trading"
- 2026-05-23 02:28 `cryptobriefing` p=10 bullish (s=+0.60): "Trump administration launches token-backed mortgages to normalize bitcoin in home purchases"

**Substanzlogik:** Diese Schlagzeilen sind Operator-aus-Premium-Pipeline-Sicht **genau das gewünschte Material** — geopolitische Trigger, SEC-Genehmigungen, Fed-Chair-Wechsel. Aber jede einzelne wird durch unterschiedliche Filter blockiert.

---

## 3. Filter-Code-Map (`app/alerts/eligibility.py`)

```text
Filter-Reihenfolge in evaluate_directional_eligibility():

1. actionable=false      → BLOCK_REASON_NOT_ACTIONABLE       (D-122)
2. bearish_disabled      → BLOCK_REASON_BEARISH_DISABLED     (D-142, hardcoded BEARISH_DIRECTIONAL_DISABLED=True)
3. priority effective:
   - watchlist demote    (V-DB4c, monitor/source_watch.txt)
   - reliability mod     (Goal-pin 16.05., monitor/source_reliability.json, Wilson Lower 95)
4. effective_priority<=7 → BLOCK_REASON_LOW_PRIORITY         (D-122)
5. low_precision_source  → BLOCK_REASON_LOW_PRECISION_SOURCE (D-133)
6. promotional_pattern   → BLOCK_REASON_PROMO_PATTERN        (V-DB4b)
7. |sentiment_score|<0.55 → BLOCK_REASON_WEAK_SIGNAL         (D-111, MIN_SENTIMENT_MAGNITUDE=0.55)
8. impact<min            → BLOCK_REASON_WEAK_SIGNAL          (D-121, bull=0.60 / bear=0.80)
9. reactive_narrative    → BLOCK_REASON_REACTIVE_NARRATIVE   (D-113/D-115, regex regex)
10. confidence<min       → BLOCK_REASON_LOW_DIRECTIONAL_CONFIDENCE (D-116/D-121, bull>=0.8 / bear>=0.95)
11. event_timing reactive → BLOCK_REASON_REACTIVE_NARRATIVE  (D-116)
```

**Hardcoded Thresholds aktuell:**
```python
BEARISH_DIRECTIONAL_DISABLED = True        # D-142 — bearish ist kategorisch off
MIN_DIRECTIONAL_CONFIDENCE_BULLISH = 0.8   # D-116
MIN_DIRECTIONAL_CONFIDENCE_BEARISH = 0.95  # D-122 (raised 0.92→0.95)
MIN_SENTIMENT_MAGNITUDE = 0.55             # D-111
MIN_IMPACT_SCORE_BULLISH = 0.60            # D-119 (raised 0.55→0.60)
MIN_IMPACT_SCORE_BEARISH = 0.80            # D-122 (raised 0.75→0.80)
```

Empirische Begründung jeder Schwelle: in Code-Comments. Calibration-Quelle: Outcomes-Stream zu historischem Precision-Tracking (D-111 bis D-142, ~Q1 2026).

---

## 4. Spec-23.05.-Annahmen-Revision

| Spec-Annahme | Tatsächlich |
|---|---|
| 105 alerts/7d bedeutet Sample-Kollaps | 2901 alerts/8d in DB — Volumen normal, alert_audit ist Filter-Output |
| 87.8%→8.3% directional ist Klassifikator/Markt-Drift | Klassifikator: 31.9% directional (steigend gegen Baseline) |
| Hypothesen (a)/(b)/(c) sind die richtigen Achsen | Wahre Achse: kumulative Filter-Schwellen × News-Phase mit Price-Sprachverwendung |
| Bayes-Schreibrate 0.29/d ist News-getrieben | Bayes-Schreibrate ist Dispatch-getrieben — wenn Filter Material nicht durchlassen, kann Bayes nicht lernen |

→ **Hypothesen (a)+(b)+(c) sind alle drei in der Sprint-Definition obsolet.** Die richtige Hypothese:

**Hypothese (d) — Dispatch-Filter-Über-Kalibration für News-Phase 2026-05:**
- Reactive-Narrative-Regex blockt Iran/Trump/Fed-Material weil Price-Sprache ("surges", "rallies", "heads higher") drin ist
- Bearish-Disabled (D-142) blockt jede Adverse-Event-Antwort (Treasury-Yields, WSJ-Iran-Reports)
- Confidence-Schwellen (D-122: 0.95 bearish) sind nicht-paramtrisch durch Operator wartbar
- Source-Reliability-Loop (PR #52) + Watchlist sind additiv und potenziell überlappend

---

## 5. Decision-Implikation für 30.05.

### 5.1 Priority-Scoring A/B/C/D-Decision

**Re-Eval der Option-D (Status quo) ist mathematisch tot:** Die "Status-quo-Wahrnehmung" stützt sich auf alert_audit, das nur ~4% des Klassifikator-Outputs zeigt. Option A' (PR #58 sentiment-penalty) operiert auf gleicher verzerrter Datenbasis.

**Neuer Vorschlag — Option E:**
- Decision auf 30.05. **nicht treffen**, sondern **erst Dispatch-Filter-Re-Calibration** durchführen
- Calibration-Sprint (5-7 Tage) mit drei Tasks:
  1. Reactive-Narrative-Regex auflockern: Whitelist "Trigger-Events mit Price-Anker" (z.B. "rallies past Y on X-trigger" mit X != reine Preis-Beschreibung)
  2. D-142 (bearish disabled) als Operator-Flag exposeen + n=24-Datenbasis auf 2026-Q2-Daten re-evaluieren
  3. Confidence-Schwellen (0.8/0.95) auf 90d-Outcome-Korrelation re-fitten

### 5.2 SHADOW_ONLY-Flip 30.05.

**Mathematisch unverändert tot am n-Pfad.** Aber: wenn Dispatch-Filter aufgelockert wird, könnte n>=20 in 1-2 Wochen erreichbar sein statt in 13 Wochen. Flip-Heuristik-Update [[kai-bayes-shadow-only-flip-heuristik]] auf n>=10@6w bleibt sinnvoll, aber **die Hauptbaustelle ist Dispatch-Filter, nicht Heuristik.**

### 5.3 Risiko bei Nicht-Handeln

Das System trifft die richtigen Klassifikationen, aber liefert sie nicht aus. Operator-Adaptive-Learning-Stack lernt auf einer Sub-Sample, die durch Hyper-Konservativismus stark verzerrt ist. **Jede Adaptive-Learning-Decision ab jetzt ist Bias-belastet, solange Dispatch-Filter unverändert.**

---

## 6. Folge-Sprint-Vorschläge (Operator-Decision)

### Sprint-Vorschlag F1 — Reactive-Narrative-Regex Whitelist (2-3 Tage)
Erweiterung von `_REACTIVE_BULLISH_PATTERNS`/`_REACTIVE_BEARISH_PATTERNS` um Trigger-Whitelist. Beispiel: Headlines mit "rally"/"surge" + benannter Trigger ("Trump announces", "SEC approves", "Iran") werden NICHT als reactive geblockt.

### Sprint-Vorschlag F2 — D-142 Re-Evaluation (1 Tag Inspection + 5-7 Tage Shadow)
Bearish-Outcomes seit 2026-04-01 auf Outcome-Stream samplen. Wenn Precision >= 30%, dann `BEARISH_DIRECTIONAL_DISABLED=False` mit Shadow-Mode-Re-Aktivierung.

### Sprint-Vorschlag F3 — Confidence-Threshold-Recalibration (2-3 Tage)
Outcome-zu-Confidence-Korrelation auf 90d. Wenn 0.7-Bullish vergleichbare Precision wie 0.8-Bullish hat → senken. Bei Bearish gleiches Verfahren ab 0.85.

### Sprint-Vorschlag F4 — Dispatch-Observability für Operator (1 Tag)
Daily-Strategy-Eintrag erweitern um "blocked_alerts_top_3_today" mit Reason-Breakdown. Operator sieht ab Tag-0 welches Material in welchem Filter hängt.

---

## 7. Was diese Analyse NICHT tut

- Kein Code-Patch.
- Keine Filter-Schwellen-Änderung ohne Sign-off.
- Keine Aussage ob D-142 falsch war (es war auf alter Datenbasis korrekt).
- Keine Pre-30.05.-Decision-Empfehlung ohne F1-F4.

---

## 8. Cross-Links

- [[kai-sentiment-drift-p0-forensik-20260523]] §Hypothesen — **wird durch dieses Memo abgelöst**
- [[kai-priority-sentiment-correlation-paradox]] §Update 2026-05-23 — Befund weiterhin valide, aber Wurzel anders
- [[adr1-paper-min-priority-reversion-20260519]] §Update 2026-05-20 — paper_min_priority-Setting ist downstream der gleichen Filter
- [[kai-bayes-shadow-only-flip-heuristik]] §Schreibrate-Klausel — bleibt sinnvoll
- Pi-Code: `app/alerts/eligibility.py` Zeile 240-630 (Filter-Reihenfolge + Schwellen)
- Pi-Daten: `data/dev.db` table `canonical_documents`, `artifacts/blocked_alerts.jsonl`

---

## 9. Datenquellen für diesen Sprint

- `data/dev.db` table `canonical_documents` (69.9 MB, n=13808 total, 2901 in 8d Window)
- `artifacts/alert_audit.jsonl` (113 in 7d Window, 7865 total)
- `artifacts/blocked_alerts.jsonl` (485 in 8d Window)
- `monitor/source_reliability.json` (Generated 2026-05-16, alle Sources `insufficient` Tier)
- `app/alerts/eligibility.py` Inspection
- Git-Log seit 2026-04-01 für source-/feed-Drift-Check

---

**Status:** Befund vollständig. Operator-Decision zwischen Sprint-Vorschlag F1-F4 offen. **Spätester Sign-off pre-30.05.: 2026-05-28** (damit F1+F4 vor Decision-Tag deployen können).
