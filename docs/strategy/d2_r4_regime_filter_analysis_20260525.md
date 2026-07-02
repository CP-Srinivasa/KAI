# D2-R4 Regime-Filter Analyse — Befund: NICHT ENTSCHEIDBAR (2026-05-25)

**Status:** Pre-Decision-Analyse zur 30.05.-Decision.
**Befund:** D2-R4 (R4 Regime-Filter-Aktivierung) kann am 30.05. **nicht entschieden** werden — Datenbasis ist mathematisch unter-versorgt.
**Skript:** `scripts/d2_r4_regime_filter_analysis.py` (committed in diesem PR, re-runnable).

---

## TL;DR

Re-Eval-Empfehlung: **D2-R4-Decision verschieben um 4-6 Wochen.** Gleichzeitig PR #74 (Multi-Window-Outcome) gibt Hoffnung dass die hohe Inconclusive-Rate (55/61) sich in der nächsten Annotation-Welle auflöst.

- 9148 alert_outcomes total, davon BTC/ETH (nach Asset-Normalisierung naked + USDT-suffix): 240 records
- Post-R1-Observer-Window (>= 2026-05-09): **61 records**
- Erfolgreich joined mit Regime-Stream: 61 (alle gefundenen)
- **Davon resolved (hit/miss): nur 6 — 55 inconclusive (90%)**
- Cross-Tab kann mathematisch keine Regime-Klasse als signifikant schlechter identifizieren

Decision-Pack-Memo `kai-decision-pack-2026-05-30-reframed` listete D2 als "intakte Datenbasis | 30.05. entscheidbar" — das ist mit dem Befund **falsifiziert**. Regime-Stream ist intakt, aber alert_outcomes-Resolution ist es nicht.

---

## Methodik

Skript joint drei Streams:

1. **`artifacts/alert_outcomes.jsonl`** (9148 records, 2024-2026 stale_backfill + live)
2. **`data/dev.db` `canonical_documents`** (4483 by_id + 4237 by_extid Lookup-Indizes, sentiment_label IN ('bullish','bearish'))
3. **`artifacts/regime_state/{btc,eth}_regime.jsonl`** (374 records BTC + 374 ETH, 2026-05-09 20:00 → 2026-05-25 08:00 UTC, hourly)

**Asset-Normalisierung** (Lehre aus `feedback-naked-asset-test-drift`): `BTC/USDT` / `BTC-USD` / `BTC` → `BTC` (gleiches Pattern für ETH).

**Regime-Lookup-Toleranz:** ±1.5h um document `fetched_at`-Timestamp. Bei hourly regime cron und 1.5h-Toleranz ist Miss-Rate ~0% (0/61 missing regime).

**Window:** `fetched_at >= 2026-05-09` (R1-Observer-Start). Pre-Window-Records werden nicht aggregiert, weil zu der Zeit kein Regime-Output existierte.

---

## Join-Statistik

| Stage | Records | Anteil |
|---|---|---|
| Total `alert_outcomes` | 9148 | 100% |
| Asset≠BTC/ETH (z.B. SOL, XRP, naked-NONE) | 2438 | 27% |
| Asset=BTC/ETH | 240 | 2.6% |
| BTC/ETH ohne `document_id`-Mapping in DB | 6351 | … (legacy) |
| BTC/ETH WITH mapping, pre-Window (<2026-05-09) | 179 | 75% von BTC/ETH |
| BTC/ETH WITH mapping, post-Window | 61 | 25% von BTC/ETH |
| Regime-Lookup erfolgreich | 61 | 100% |
| **Davon mit `outcome IN ('hit','miss')`** | **6** | **10%** |
| Davon `outcome='inconclusive'` | 55 | 90% |

**Lese-Befund:**
- **Asset-Naming-Drift ist groß:** 6351 outcomes ohne doc_id-Mapping wäre eine separate Sanity-Untersuchung wert. Vermutung: alte Records mit ext_id-Format-Drift oder gelöschte Dokumente.
- **90% Inconclusive-Rate** ist der kritischste Block. Cause unbekannt — Hypothesen: (a) Auto-Annotate-Schwelle zu eng, (b) Single-Window-Outcome (1h) verpasst längere Bewegungen, (c) `inconclusive`-Definition selbst zu konservativ.
- **PR #74 (DS-V-MW Multi-Window-Outcome 1h/4h/24h/72h/168h)** merged 25.05. — adressiert Hypothese (b) direkt. Re-Annotation der bestehenden 55 inconclusives kann die Sample-Größe erheblich vergrößern.

---

## Cross-Tab (n=6)

### Precision per (Asset, Regime)

| asset | regime | hit | miss | inc | n_res | precision | wilson_lower_95 |
|---|---|---|---|---|---|---|---|
| BTC | breakout_up | 2 | 3 | 55 | 5 | 40.0% | 11.8% |
| BTC | chop_quiet | 0 | 1 | 0 | 1 | 0.0% | 0.0% |
| ETH | (alle) | 0 | 0 | 0 | 0 | — | — |

### Precision per (Asset, Vol-Class)

| asset | vol_class | hit | miss | n_res | precision | wilson_lower_95 |
|---|---|---|---|---|---|---|
| BTC | vol_low | 2 | 4 | 6 | 33.3% | 9.7% |

### Sentiment × Regime

| regime | sentiment | hit | miss | n_res | precision |
|---|---|---|---|---|---|
| breakout_up | bullish | 2 | 3 | 5 | 40.0% |

**Lese-Befund:**
Wilson Lower 95% bei n=5 (BTC×breakout_up) ist **11.8%** — das ist mathematisch wertlos für Filter-Decision. Ein Regime, das vermeintlich 40% Precision zeigt, könnte real zwischen 12% und 78% liegen. Keine Regime-Klasse kann als signifikant schlechter klassifiziert werden, weil ETH komplett fehlt und alle BTC-Resolved-Outcomes in nur 2 Regime-Klassen fallen.

---

## Confounders

1. **Window-Länge 16 Tage** ist für Regime-Coverage knapp — `breakout_down`, `trend_down`, `vol_high`-Regimes hatten in diesem Window möglicherweise gar keine Aktivität. Das ist kein Skript-Bug, sondern Marktrealität in der Beobachtungszeit.

2. **Asset-Naming-Drift** in `alert_outcomes.jsonl`: gemischt `BTC`, `BTC/USDT`, `BTC-USD`. Normalisierung im Skript fängt es ab, aber gibt einen Sanity-Hinweis dass Producer-Schema fragmentiert ist.

3. **Doc_id-Mapping-Lücke (6351 records ohne join)** ist eine separate Pipeline-Frage. Mögliche Ursachen:
   - Pre-Schema-Migration Records mit anderem ext_id-Format
   - Documents in canonical_documents gelöscht (cleanup)
   - alert_outcomes für Documents anderer Pipeline (z.B. premium-signal-pipeline statt news-pipeline)

4. **Inconclusive-Rate 90%** ist der größte Hebel. PR #74 (Multi-Window) ist die hellste Hoffnung; ohne diese Maßnahme bleibt D2-R4 dauerhaft unter-versorgt.

---

## Empfehlung für 2026-05-30 Decision-Pack

### D2 (R4-Regime-Filter-Aktivierung): VERSCHIEBEN um 4-6 Wochen

Konkret: **frühester Re-Eval 2026-07-06** (6 Wochen post-PR-#74-Merge). Bis dahin:

1. **PR #74 wirken lassen:** Multi-Window-Outcome re-annotiert die bestehenden 55 inconclusives + alle neuen Records mit erweiterten Outcome-Fenstern. Erwartete Sample-Vergrößerung Faktor 5-15x bei resolved-rate.
2. **Doc_id-Mapping-Lücke untersuchen** (Folge-Sprint, nicht Decision-Blocker): warum 69% der alert_outcomes keinen canonical_documents-Match haben. Fix kann die historische Sample-Basis erschließen.
3. **Window-Länge wächst organisch:** +6w = 42 Tage zusätzlich, also 16d + 42d = 58d Regime-Coverage am Re-Eval-Termin.

### D2-V2 Re-Eval-Trigger (Mini-Spec)

| # | Condition | Schwelle |
|---|---|---|
| 1 | Datum | >= 2026-07-06 |
| 2 | n_resolved (joined) | >= 100 |
| 3 | n_resolved pro (asset×regime-class) für mind. 3 Regime-Klassen | >= 30 |

Bei Erfüllung: D2-Decision wieder aufnehmen mit gleicher Skript-Methodik (`scripts/d2_r4_regime_filter_analysis.py`).

---

## Folge-Sprints

- **DS-V-MW-Backfill** (auto-getriggert durch PR #74): Re-Annotation der 55 inconclusives. Erwarteter Settling-Zeitraum 1-2 Wochen.
- **Doc_id-Mapping-Forensik** (1d-Sprint): root-cause der 6351 fehlenden Mappings.
- **D2-V2 Re-Eval** (frühestens 2026-07-06): erneuter Skript-Run, dann Entscheidung.
- **R3-Shadow-Sprint** (parallel oder nach D2-V2): selbst wenn D2-V2 positiv, R4-Activation soll erst nach 2-wöchiger R3-Shadow-Phase erfolgen (analog F2-V2 / F3-V2 Pattern).

---

## Cross-Links

- `regime-r1-observer-status` — R1 Observer Setup + 6 Regime-Klassen + 3 Vol-Klassen
- `kai-decision-pack-2026-05-30-reframed` D2-Eintrag (mit diesem Befund zu korrigieren)
- PR #74 `fecebf93` DS-V-MW Multi-Window-Outcome (Inconclusive-Resolution-Pfad)
- `feedback-naked-asset-test-drift` (Asset-Normalisierungs-Lehre angewandt)
- `kai-auto-annotate-reactivation-20260521` (Pipeline-A-Reaktivierung)
- F2-V2 Spec (PR #75) + F3-V2 Spec (PR #76) — paralleler Trigger-Pattern, gleiche Stop-Condition "Outcomes-Coverage zu klein"

---

## Methodik-Hinweis

Wilson Lower 95% ist bei n=5 mathematisch valide, aber für Decision-Zwecke wertlos — die Konfidenz-Intervalle sind zu weit. Konsequenz: jeder Decision-Vorschlag auf Basis dieser 6 Records wäre Cargo-Cult-Statistik. Verschieben ist die einzige redliche Antwort.

Der gleiche Methodik-Stop ist im F2-V2 Spec (`docs/strategy/f2_v2_bearish_reeval_spec_20260525.md`) und F3-V2 Spec (`docs/strategy/f3_confidence_recalibration_spec_20260525.md`) eingebaut — Sample-Größe ist über alle drei Re-Eval-Tracks der primäre Engpass, gemeinsamer Hebel ist Auto-Annotate-Coverage + Multi-Window-Outcome.
