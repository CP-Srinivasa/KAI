# Priority-Scoring Decision-Brief — EOW-Review 2026-05-23

**Auftrag:** DS-20260521-V1 — Operator-Decision-Pack für End-of-Window-Review.
**Quelle:** `artifacts/operator_memos/priority_scoring_inspection_2026-05-20.md` (Voll-Inspection, 9 Sektionen).
**Entscheidungs-Stichtag:** 2026-05-23 EOW-Review. Decision in <=5min möglich auf Basis dieses Briefs.

---

## Befund in 3 Zeilen

`compute_priority()` in `app/analysis/scoring.py:43-86` ignoriert `sentiment_label` vollständig (0 Gewicht, verifiziert via Repo-Grep). Mechanische Folge: das `EXECUTION_PAPER_MIN_PRIORITY=10`-Gate hat empirisch **43.5% direktionales Material** (p>=10), während das p=8/9-Band **87.8% direktional** ist (Cross-Tab DS-20260520-V1). Das Scoring wurde als Newsletter-/Research-Ranking gebaut und wird seit ADR-1-Reversion 19.05. als Trade-Trigger-Gate zweckentfremdet.

---

## 4 Optionen (Kurzform)

### Option A — Sentiment-Faktor in `compute_priority()` einbauen (Quickwin)

| Feld | Wert |
|---|---|
| Was ändert sich | 6. Faktor `sentiment_decisive` (bullish/bearish=1.0, mixed=0.4, neutral=0.0) mit Gewicht 0.10, andere Gewichte rebalancieren auf 0.27/0.27/0.18/0.13/0.05. |
| Erwarteter Effekt am Gate | p>=10 verschiebt sich Richtung höherer Sentiment-Klarheit; p=8/9-Band verliert direktionalen Überhang. Trade-Frequenz steigt erwartbar (heute ~0.62 Bayes/Tag). |
| Risiko | Tests in `tests/unit/test_scoring*.py` brechen (4-6 Anpassungen). Audit-Stream-Verteilung shiftet — Historie-Vergleiche schwieriger. Newsletter-Ranking bekommt eine Sentiment-Bias-Komponente, die es nicht braucht (Use-Case-Vermischung bleibt). |
| Aufwand | ~30min Patch + ~30min Test-Anpassung + ~15min PR-Doku. Mid-Window-machbar (3 Tage Restfenster). |
| Reversibilität | Hoch — Gewichte sind Konstanten, leicht zurückzudrehen. |

### Option B — Separater `trade_priority_score` (Architektur-Schritt)

| Feld | Wert |
|---|---|
| Was ändert sich | Neue Funktion `compute_trade_priority(result)` in `scoring.py` mit Sentiment-zentrierter Formel. `EXECUTION_PAPER_MIN_PRIORITY` operiert auf neuem Score. `recommended_priority` bleibt Newsletter-Ranking. Bridge-Sentiment-Filter wird redundant + entfernbar. |
| Erwarteter Effekt am Gate | Saubere Trennung Newsletter vs. Trade. Trade-eligibler Stream wird kleiner aber qualitätsmäßig konsistenter. Bayes-Stack bekommt direktionalen Input. |
| Risiko | Neuer Field in `AnalysisResult` + DB-Migration + neue Audit-Schema-Version. Im Mid-Window NICHT konservativ machbar (hektisch = Risiko). |
| Aufwand | Spec+Test-Plan 1d, Implementation 1-2d, Pi-Deploy+Audit-Validierung 1d = **5-7 Tage Sprint** = Phase-2 post-23.05. |
| Reversibilität | Mittel — Schema-Migration ist forward-compat, Rollback braucht eigene Migration. |

### Option C — Bridge erweitern, Scoring unverändert (Workaround)

| Feld | Wert |
|---|---|
| Was ändert sich | `_maybe_trigger_paper_trade` bekommt zweiten Pfad: priority>=7 + sentiment in {bullish,bearish} + sentiment_confidence>0.75 triggert ebenfalls. |
| Erwarteter Effekt am Gate | Trade-Frequenz steigt, aber Bewertung wird unklar (zwei Gates parallel). Direktionale p=8/9-News können jetzt triggern. |
| Risiko | Magic-Number-Stack (`sentiment_confidence>0.75` ist Bauchgefühl). Verlagert das Problem statt es zu lösen. Audit-Stream wird komplizierter zu interpretieren. Klasse von false-positive-Risiko, die der Re-Entry-Konservatismus explizit vermeiden will. |
| Aufwand | ~45min Patch + 60min Tests. |
| Reversibilität | Hoch — Branch-Logik in service.py, leicht entfernbar. |

### Option D — Status quo bis nächstem Window-Ende 2026-05-30 (Do-Nothing)

| Feld | Wert |
|---|---|
| Was ändert sich | Nichts. Cross-Tab-Befund bleibt dokumentiert, `EXECUTION_PAPER_MIN_PRIORITY=10` bleibt aktiv. |
| Erwarteter Effekt am Gate | Unverändert. Bayes-Schreibrate bleibt ~0.62/Tag, Trade-Frequenz dünn. |
| Risiko | Trade-Datenbasis bis 30.05. bleibt zu dünn für statistisch tragfähige Bewertung. Weitere 7 Tage Indizien-Ebene. |
| Aufwand | 0min. |
| Reversibilität | trivial — kein Eingriff zu rollen. |

---

## Empfehlung Claude (für Operator-Sign-off)

**Variante 1 (konservativ):** Option D bis 30.05., dann nach 7d Daten Operator-Decision zwischen A und B. Begründung: Mid-Window-Reaktion auf einen fundamentalen Scoring-Mechanismus ist hektisch. 4 Bayes-Einträge sind Indizien, keine Inferenz. Lieber eine saubere 7d-Beobachtung mit klar abgegrenztem Vorher/Nachher als ein hastiger Patch.

**Variante 2 (Quickwin akzeptiert):** Option A heute (21.05.) oder morgen (22.05.) patchen, 7d Beobachtung bis 28.05., dann Operator-Decision für oder gegen Option B. Begründung: Wenn Sentiment-Klarheit wirklich der entscheidende Faktor ist, gibt Option A schnellstmöglich empirische Evidenz dafür.

**Welche Variante präferiert wird, ist Operator-Decision.** Claude tendiert zu Variante 1 (Konservativ), weil:
- 3 Tage Restfenster sind kurz für Test-Anpassungen + saubere Pi-Validierung.
- Option A vermischt Newsletter+Trade weiter, Option B löst es sauber. Wenn man eh in 5-7d zu B will, ist A ein verlorener Detour.
- Bayes-Pipeline atmet bereits (+1 Audit 21.05. 04:14 UTC). Die "Pipeline tot"-Hypothese der ADR-1-Reversion ist relativiert.

**Was NICHT empfohlen wird:** Option C (Magic-Number-Workaround widerspricht Re-Entry-Konservatismus).

---

## Decision-Anker für 23.05.

Operator entscheidet **eine** Antwort:

- [ ] **A** — Quickwin patchen, Spec heute starten.
- [ ] **B** — Phase-2-Sprint (5-7d) definieren, Start nach 23.05.
- [ ] **C** — Bridge-Workaround patchen (nicht empfohlen).
- [x] **D** — Status quo bis 2026-05-30, dann re-evaluieren. **← Operator-Sign-off 2026-05-21 (Pre-Review)**

Memory-Pin `[[kai-priority-sentiment-correlation-paradox]]` verhindert weitere Gate-ADRs auf `EXECUTION_PAPER_MIN_PRIORITY` bis zur Re-Eval am 30.05.

---

## Operator-Sign-off 2026-05-21 (Pre-Review-Ratifikation)

**Decision:** Variante 1 (Konservativ) = **Option D** ratifiziert.

**Begründung Operator:** Bayes-Datenbasis dünn (n=4, 2 organisch), 3 Tage Restfenster zu kurz für Test+Pi-Validierung von Option A. Option B sauberer Architektur-Schritt aber Phase-2-Pflicht, nicht Mid-Window. Option D bis 30.05. ist der defensivste Pfad, der gleichzeitig Datenakkumulation erlaubt.

**Folge:** EOW-Review 2026-05-23 wird zur Validierungs- + Snapshot-Sitzung (nicht Decision-Sitzung). Decision-Pack bleibt offen für Option A/B-Re-Eval am 2026-05-30.

**Gekoppelt an:** [[re-entry-end-of-window-2026-05-23]] §Phase-2-Decision = Option B (Shadow-Phase +7d bis 2026-05-30) — ebenfalls ratifiziert 2026-05-21.

---

## Querverweise

- Voll-Inspection: `artifacts/operator_memos/priority_scoring_inspection_2026-05-20.md`
- ADR-1-Reversion: `artifacts/operator_memos/re_entry_adr_cluster_2026-05-17.md` §ADR-1
- NEW-2-Sentiment-Klassifikator-Check: commit `61de61b` (Hypothese a bestätigt, b+c widerlegt)
- Daily 2026-05-20 Sektion §1 Architektur-Befund
- EOW-Skeleton: `artifacts/end_of_window_review_2026-05-23.md`
