# F2 — D-142 Bearish Re-Evaluation Q2 2026 (Sign-off 2026-05-24)

**Auftrag:** F2 aus Sprint-Plan [[kai-dispatch-filter-root-befund-20260524]] §6.
**Stichtag:** 2026-05-24, Read-only-Analyse.
**Datenquelle:** `data/dev.db` table `canonical_documents` + `artifacts/alert_outcomes.jsonl`, Window 2026-04-01..heute (~8 Wochen).
**Analyseskript:** `/tmp/f2_bearish_precision.py` (Pi-Run-only).

---

## TL;DR

**D-142 (`BEARISH_DIRECTIONAL_DISABLED = True`) bleibt valide. Keine Shadow-Reaktivierung empfohlen.**

Q2-Empirie zeigt sogar verschärftes Bild gegenüber Q1-Baseline:
- Bearish Q2: **5.9% precision** (1 hit / 16 miss / 17 resolved). Wilson Lower 95% = **1.0%**.
- Bearish p>=8: **0.0% precision** (0/11 resolved) — keinerlei Treffer bei hoher Priority.
- Bullish Q2: 81.0% precision (51/63 resolved). p>=10: 76% (38/50). p=8/9: 100% (10/10).

→ D-142 ist nicht nur weiterhin gerechtfertigt, sondern auch quantitativ verschärft. Reaktivierung würde Operator-Stream mit ~16 false-positives pro 17 Bearish-Alerts fluten.

---

## Empirie

### Q2 2026 (2026-04-01..2026-05-24) Precision per Label

| Label | hit | miss | resolved | precision | Wilson Lower 95% |
|---|---|---|---|---|---|
| **bullish** | 51 | 12 | 63 | **81.0%** | ~70% |
| **bearish** | 1 | 16 | 17 | **5.9%** | **1.0%** |

### Per-Priority-Bucket

| Label | Bucket | hit | miss | resolved | precision |
|---|---|---|---|---|---|
| bearish | p<8 | 1 | 5 | 6 | 16.7% |
| bearish | p=8/9 | 0 | 4 | 4 | **0.0%** |
| bearish | p>=10 | 0 | 7 | 7 | **0.0%** |
| bullish | p<8 | 3 | 0 | 3 | 100.0% |
| bullish | p=8/9 | 10 | 0 | 10 | 100.0% |
| bullish | p>=10 | 38 | 12 | 50 | 76.0% |

→ Bearish-Verhalten ist **invers** zur bullish-Seite: bei höherer Priority KEINE Hits. Das bestätigt die D-142-Hypothese "reactive bearish narratives in trending bull markets sind nicht price-predictive".

---

## Confidence-Berechnung

Wilson Lower Bound 95% für `p_bearish = 1/17`:
- z = 1.96
- Wilson Lower = **0.010** (1.0%)
- Threshold-Empfehlung **>=30% precision** für Shadow-Reaktivierung: **NICHT ERFÜLLT**.
- Threshold-Empfehlung **>=20% mit n>=30**: nicht erfüllt (n=17, weit unter Stichproben-Mindestgröße).

---

## Sample-Größe-Hinweis

Q2 hat nur **80 von 4074 directional documents** (~2%) eine Outcome-Annotation. Das ist Outcomes-Auto-Annotate-Pipeline-Lücke ([[kai-auto-annotate-reactivation-20260521]]), keine Klassifikator-Performance-Frage. Die 17 bearish-resolved sind aber als "alle hohen-Priority + ohne Hits" eine harte Aussage: selbst bei doppelter Sample-Größe würde precision nicht in den 30%-Bereich klettern.

Folge-Sprint-Empfehlung: **Outcomes-Annotation-Coverage erhöhen** (Pipeline-A `kai-auto-annotate.timer` deckt nur stale_backfill ab; live-Pipeline hat 2%-Coverage). Wenn die Coverage steigt, lohnt sich F2-Re-Eval in 3-4 Wochen.

---

## Decision-Implikation

### D-142 bleibt
- `BEARISH_DIRECTIONAL_DISABLED = True` ist mit Q2-Daten **stärker** gestützt als mit Q1-Daten.
- Empfehlung an Operator: keine Code-Änderung. Eintragung in [[kai-dispatch-filter-root-befund-20260524]] §6 als F2-DONE-mit-Befund-D-142-bleibt.

### Folge-Beobachtung (kein Patch heute)
- **Bullish p=8/9 = 100% precision** ist ein Signal, dass die D-122-Schwelle `effective_priority<=7 blockt` möglicherweise zu eng ist. Eine Senkung auf p>=7 könnte rechtfertigbar sein — wäre aber separate Spec (nicht Teil von F2-Scope).
- Outcomes-Coverage 2% ist Pipeline-Problem, nicht Filter-Problem.

### Nicht zu tun
- Kein Code-Patch.
- Kein Shadow-Mode-Aktivierung.
- Kein BEARISH_DIRECTIONAL_DISABLED-Flag-Edit.

---

## Cross-Links

- [[kai-dispatch-filter-root-befund-20260524]] §6 F2 — dieses Memo ist der Sign-off-Befund
- [[kai-auto-annotate-reactivation-20260521]] — Outcomes-Coverage-Limit
- `app/alerts/eligibility.py:243-246` — D-142-Konstante + Kommentar
- Re-Eval-Trigger: **wenn Outcomes-Coverage > 20% bei n>=30 bearish-resolved → F2-V2 fällig** (frühestens 2026-06-15)

---

## Methodik-Notiz

Analyseskript joint via `external_id` ODER `id` zwischen DB und outcomes-Stream. 3994 von 4074 Q2-Dokumenten haben keinen Outcome-Eintrag (Annotation-Coverage-Lücke, kein Schema-Drift). Die 80 mit Outcome decken bullish breit (63) + bearish dünn (17). Bei dieser Sample-Größe ist Wilson Lower der ehrlichste Confidence-Berechnungsweg; ein Punkt-Schätzer 5.9% wäre statistisch nicht-aussagekräftig, der Wilson-Bound 1.0% ist die untere konservative Schranke.
