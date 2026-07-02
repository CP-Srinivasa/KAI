# F2-V2 Bearish Re-Evaluation Spec (frühestens 2026-06-15)

**Status:** SPEC — vor Ausführung Operator-Sign-off auf Trigger-Verify + Methodik.
**Vorgänger:** `artifacts/operator_memos/f2_d142_bearish_reeval_2026-05-24.md` (Pi-lokal, gitignored). Befund: D-142 (`BEARISH_DIRECTIONAL_DISABLED = True`) bleibt — Bearish Q2 5.9% precision / Wilson Lower 1.0%.
**Sprint-Auftrag:** `kai-dispatch-filter-root-befund-20260524` §6 F2 → Re-Eval-Trigger als ausführbare Spec.

---

## Zweck

Die F2-V2 Re-Eval prüft, ob `BEARISH_DIRECTIONAL_DISABLED` (`app/alerts/eligibility.py:245`) auf `False` zurückgesetzt werden kann. Die 24.05.-V1-Analyse hat das verneint mit n=17 / Wilson Lower 1.0%; F2-V2 nimmt die Frage frühestens am 2026-06-15 wieder auf, wenn die Sample-Basis tragfähig ist und mehrere Filter-Confounders settled sind (F1 PR #60, F3 PR #61, Auto-Annotate-Reaktivierung 21.05.).

---

## 1. Trigger-Conditions (alle drei Pflicht)

| # | Condition | Quelle |
|---|---|---|
| 1 | Datum >= 2026-06-15 | Kalender |
| 2 | `alert_outcomes.jsonl` Coverage > 20% im 8w-rolling-Window | `artifacts/alert_outcomes.jsonl` vs. `canonical_documents` mit `actionable=true` |
| 3 | n_bearish_resolved >= 30 im 8w-rolling-Window | Bearish-Label + nicht-NULL outcome |

Sind weniger als 3 erfüllt: F2-V2 wird verschoben um +4 Wochen, neuer Trigger-Check am verschobenen Datum.

---

## 2. Datenbasis

**Window:** rolling 8 Wochen ab `max(2026-04-15, today-8w)`. Die Untergrenze 2026-04-15 entspricht F1-Deploy (`9a799cc8` 2026-05-24) **minus 3 Wochen Settling-Period** — soll künftige Filter-Drift-Re-Evals nicht von Pre-F1-Records dominiert werden lassen, wenn der Window länger zurückreicht.

**Source:**
- `data/dev.db` Table `canonical_documents` (DB-canonical)
- LEFT JOIN auf `artifacts/alert_outcomes.jsonl` via `external_id` ODER `id`

**Filter:**
- `sentiment_label = 'bearish'`
- `actionable = true`
- `effective_priority IS NOT NULL`

**F1-Confounder-Aware-Split:** zwei separate Buckets pro Auswertung:
- **Bucket A** — `substantive_pattern IS NULL` (Pre-F1 + Post-F1 ohne Whitelist-Treffer)
- **Bucket B** — `substantive_pattern IS NOT NULL` (Post-F1 mit Whitelist-Recovery)

Die 8 Whitelist-Pattern-Familien aus PR #60 können bearish-Recovery enthalten (z.B. "Bitcoin's hard-money thesis colliding with 5% Treasury yields" hätte ohne F1 als `bearish_directional_disabled` geblockt). Wenn Bucket B substanziell andere precision zeigt, ist das eine Empirie-Veränderung durch F1, kein D-142-Argument.

**F3-Confounder-Aware:** `directional_confidence` wird ab 2026-05-24 (PR #61 `cb731a41`) in `canonical_documents` persistiert. Pre-F3-Records haben NULL — Join via `COALESCE(directional_confidence, 0.0)` oder Bucket-Trennung "F3-aware vs F3-blind".

---

## 3. Berechnung

Wilson Lower Bound 95% pro Label×Priority-Bucket×F1-Bucket:

```
precision        = hits / (hits + misses)
wilson_lower_95  = ((p + z²/(2n)) − z · √((p·(1−p) + z²/(4n))/n)) / (1 + z²/n)
where z = 1.96, n = hits + misses, p = precision
```

**Output-Tabelle Format** (kompatibel zu V1):

| F1-Bucket | Priority-Bucket | hit | miss | resolved | precision | wilson_lower_95 |
|---|---|---|---|---|---|---|
| A (substantive_pattern=NULL) | p<8 | … | … | … | …% | …% |
| A | p=8/9 | … | … | … | …% | …% |
| A | p>=10 | … | … | … | …% | …% |
| B (substantive_pattern!=NULL) | p<8 | … | … | … | …% | …% |
| B | p=8/9 | … | … | … | …% | …% |
| B | p>=10 | … | … | … | …% | …% |

Plus Vergleichs-Spalten gegen Q2-V1-Baseline (5.9% / 1.0% / n=17).

---

## 4. Aktivierungs-Schwellen

### Stufe 1 — Shadow-Mode-Start

**Alle drei Pflicht:**
- precision >= 30% (auf zusammengefasste Bucket A+B)
- wilson_lower_95 >= 15%
- n_resolved >= 30 (Bucket A+B kombiniert)

Wenn erfüllt: Operator-Sign-off → Shadow-Mode-Code-Patch deployen — `BEARISH_DIRECTIONAL_DISABLED = True` bleibt, aber neuer Audit-Stream `bearish_shadow_decisions.jsonl` protokolliert was *passiert wäre*. **Kein Trade, kein Operator-Alert-Stream-Eintrag.**

Shadow-Phase: **14 Tage**.

### Stufe 2 — Live-Aktivierung (nach 14d Shadow)

**Alle drei Pflicht:**
- Shadow-Phase-precision >= 25% (auf realen Outcomes, nicht Pre-Shadow-Sample)
- Keine 3 consecutive p>=10-misses in Shadow-Phase
- Operator-Sign-off auf Shadow-Empirie + Auto-Rollback-Logic-Verify

Wenn erfüllt: PR mit `BEARISH_DIRECTIONAL_DISABLED = False` + Shadow-Empirie als Evidence im PR-Body + Test-Coverage für Bearish-Pfad.

### Stufe 3 — Auto-Rollback (nach Live-Switch)

**Trigger** (alle drei lösen Rollback aus):
- rolling 7d precision fällt > 10pp unter Aktivierungs-wilson_lower_95
- ODER >= 3 consecutive p>=10-misses
- ODER Operator-Manual-Disable via `--disable-bearish`-CLI

**Aktion:** `BEARISH_DIRECTIONAL_DISABLED = True` (ENV-Override `KAI_BEARISH_DIRECTIONAL_DISABLED=1` als no-deploy-Path), Memo `artifacts/operator_memos/f2_v3_rollback_<date>.md`, Telegram-Notify Operator. **Kein Sign-off nötig** — Safety-Default.

---

## 5. Stop-Conditions

| Befund | Folge-Aktion | Nächster Re-Eval-Termin |
|---|---|---|
| n < 30 (Trigger-3 verfehlt) | F2-V2 verschoben | +4 Wochen |
| wilson_lower_95 < 5% | D-142 bleibt verschärft, Memo + Folge-Sprint vertagen | +12 Wochen (frühestens 2026-09-07) |
| 5% <= wilson_lower_95 < 15% | Inconclusive, neue Probe abwarten | +8 Wochen (frühestens 2026-08-10) |
| 15% <= wilson_lower_95 < 30% threshold inconsistent | Stichprobe vergrößern, F2-V2 wiederholen | +4 Wochen |
| wilson_lower_95 >= 15% + n >= 30 | Shadow-Mode-Start (Stufe 1) | siehe §4 |

---

## 6. Operator-Sign-off-Punkte

| Punkt | Wann | Was wird unterschrieben |
|---|---|---|
| SO-1 Trigger-Verify | vor Skript-Run F2-V2 | Trigger-Conditions verifiziert + Methodik akzeptiert |
| SO-2 Shadow-Start | nach Skript-Run, vor Shadow-Patch | Empirie-Befund + Confounder-Bereinigung + Shadow-Patch-PR |
| SO-3 Live-Switch | nach 14d Shadow | Shadow-Empirie + Auto-Rollback-Code-Verify + Live-PR |
| SO-rollback | bei Auto-Rollback | **Keine** — Safety-Default, nur Notify |

---

## 7. Artefakte

| Pfad | Inhalt | Tracking |
|---|---|---|
| `scripts/f2_v2_bearish_reeval.py` | Analyse-Skript (~150 LOC, analog `/tmp/f2_bearish_precision.py` 24.05.) | git-tracked, vor 2026-06-15 zu schreiben |
| `tests/unit/test_f2_v2_bearish_reeval.py` | Wilson-Lower-Berechnung + Trigger-Logic-Tests (~12 Cases) | git-tracked, parallel zum Skript |
| `artifacts/f2_v2_bearish_precision_<date>.json` | Strukturierte Empirie-Daten | Pi-lokal (artifacts/ gitignored) |
| `artifacts/operator_memos/f2_v2_reeval_<date>.md` | Output-Memo analog 24.05.-Format | Pi-lokal |
| `app/alerts/bearish_shadow.py` | Shadow-Mode-Code (nach SO-2) | git-tracked, neuer Sprint |
| `artifacts/bearish_shadow_decisions.jsonl` | Shadow-Audit-Stream | Pi-lokal |

---

## 8. Folge-Sprints

- **Wenn Shadow-Start grün (SO-2 erteilt):** Sprint `F2-V3 Live-Activation` — Code-Patch + 14d Shadow-Validierungsperiode + Live-Switch-PR mit Auto-Rollback-Code
- **Wenn Re-Eval inconclusive (n<30 ODER 5% <= wilson < 15%):** Folge-Sprint **Outcomes-Annotation-Coverage** (Pipeline-A `kai-auto-annotate.timer` deckt nur stale_backfill ab, live-Pipeline hat 2% Coverage per V1-Befund — Lücke schließen, dann F2-V2 wiederholen)
- **Wenn wilson_lower_95 < 5%:** kein Sprint, D-142 ist quantitativ bestätigt. Nächste Re-Eval +12w.

---

## 9. Cross-Links

- `app/alerts/eligibility.py:243-246` — D-142 Konstante + Kommentar (write-target wenn Stufe 2 erreicht)
- F1 Whitelist-Pattern: PR #60 squash `9a799cc8`
- F3 Schema-Patch: PR #61 squash `cb731a41`
- F4 Dispatch-Observability: PR #59 squash `8f529148`
- Auto-Annotate-Reaktivierung 21.05. (`kai-auto-annotate.timer`) — Voraussetzung für Coverage-Verbesserung
- Wilson Lower Methodik: identisch zu V1-Memo §"Confidence-Berechnung"

---

## Methodik-Notiz

Die V1-Analyse hatte n=17 nach JOIN-Lücke (3994/4074 Q2-Records ohne Outcome-Annotation). Auto-Annotate-Reaktivierung schließt die Lücke ab 21.05. — die V2-Coverage am 15.06. testet, ob 25 Tage Auto-Annotate ausreichend Coverage erzeugt haben (Ziel: 20%+ statt 2% V1-Stand). Falls nein, wird F2-V2 zwangsläufig nach §5 Stop-Conditions verschoben — das ist erwartet, kein Spec-Versagen.

Die F1-Confounder-Trennung (Bucket A/B) ist nicht-trivial: bei kleinen B-Buckets (Whitelist-Treffer sind 8/d max, also ~448 in 8w aber davon vielleicht 20% bearish-Label) könnte die Bucket-Trennung statistisch unzulässig werden (n_B < 10). In dem Fall: Bucket A als primary, Bucket B als Sensitivitäts-Hinweis, kein Aktivierungs-Gate auf B alleine.
