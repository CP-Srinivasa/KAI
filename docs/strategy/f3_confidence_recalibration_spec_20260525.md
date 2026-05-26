# F3-V2 Confidence-Threshold-Recalibration Spec (frühestens 2026-06-15)

**Status:** SPEC — vor Ausführung Operator-Sign-off auf Trigger-Verify + Methodik.
**Vorgänger:** F3 V-0 Schema-Patch (PR #61 squash `cb731a41`, 2026-05-24). `canonical_documents.directional_confidence` wird ab Pipeline-Run nach Merge persistiert.
**Sprint-Auftrag:** `kai-dispatch-filter-root-befund-20260524` §6 F3 → Confidence-Recalibration-Spec.

---

## Zweck

Die F3-V2 Re-Eval prüft, ob die heuristisch gesetzten Confidence-Thresholds
(`MIN_DIRECTIONAL_CONFIDENCE_BULLISH = 0.8`, `MIN_DIRECTIONAL_CONFIDENCE_BEARISH = 0.95`)
in `app/alerts/eligibility.py:253-256` empirisch optimal kalibriert sind.

Aktueller Stand: Schwellen wurden in D-116/D-121/D-122 auf Basis sehr kleiner Samples
(1/25, 18/24, 22% precision) gesetzt — heuristisch konservativ, möglicherweise zu strikt
oder asymmetrisch falsch kalibriert.

F3 V-0 hat die Voraussetzung geschaffen: `directional_confidence` ist ab 2026-05-24 persistiert
in `canonical_documents`, `AlertAuditRecord`, `BlockedAlertRecord`. Damit kann ROC/Precision-Recall
über die volle Confidence-Verteilung empirisch ausgewertet werden — statt nur "passed-the-gate"
vs "blocked".

---

## 1. Trigger-Conditions (alle drei Pflicht)

| # | Condition | Quelle |
|---|---|---|
| 1 | Datum >= 2026-06-15 | Kalender (3w post-F3-Deploy für Schema-Stabilität) |
| 2 | n_documents mit `directional_confidence IS NOT NULL` AND outcome_resolved >= 100 | `canonical_documents` JOIN `alert_outcomes.jsonl` |
| 3 | Pro Label (bullish/bearish) n_resolved >= 30 | wie #2, gruppiert |

Bei weniger als 3 erfüllt: F3-V2 verschoben um +4 Wochen.

---

## 2. Datenbasis

**Window:** rolling 4 Wochen ab `max(2026-05-24, today-4w)`. Untere Grenze ist F3-V-0-Deploy
(`cb731a41` 2026-05-24) — vor dem Datum gibt es kein persistiertes `directional_confidence`,
Pre-F3-Records sind NULL und werden nicht aggregiert.

**Source:**
- `data/dev.db` Table `canonical_documents` mit Filter `directional_confidence IS NOT NULL`
- LEFT JOIN auf `artifacts/alert_outcomes.jsonl` via `external_id` ODER `id`

**Filter:**
- `sentiment_label IN ('bullish', 'bearish')`
- `actionable = true`
- `directional_confidence IS NOT NULL`

**F1-Confounder-Aware-Split:** wie in F2-V2 — zwei Buckets (`substantive_pattern=NULL` / `!=NULL`).
F1-Whitelist verschiebt die Confidence-Verteilung an der unteren Rand-Population — separate
Auswertung verhindert, dass F1-Recovery die Threshold-Empfehlung verzerrt.

**Bearish-Confound:** `BEARISH_DIRECTIONAL_DISABLED = True` heißt bearish-Records werden vor Trade
geblockt — aber `canonical_documents` enthält trotzdem klassifizierte bearish-Records (mit
confidence). Auswertbar, aber Outcome-Coverage für bearish ist über `alert_outcomes.jsonl`
ggf. niedriger als für bullish (weil bearish-Alerts gar nicht im Operator-Stream landen, also
auch keine manuell annotierten Outcomes haben). Lösung: bearish-Auswertung nutzt
zusätzlich `BlockedAlertRecord`-Stream wenn vorhanden + dort verfügbares
`directional_confidence`-Feld + post-hoc-Annotation via Auto-Annotate-Pipeline.

---

## 3. Berechnung

### 3a. ROC/Precision-Recall Kurve

Pro Label (bullish/bearish) und F1-Bucket (A/B):

```
für confidence_bin in [0.50, 0.55, 0.60, ..., 0.95, 0.99]:
    selected = records WHERE directional_confidence >= confidence_bin
    precision_at_bin = hits(selected) / (hits + misses)(selected)
    recall_at_bin    = hits(selected) / hits_total
    n_at_bin         = |selected|
    wilson_lower_95  = Wilson-Lower(precision_at_bin, n_at_bin)
```

### 3b. Optimal-Threshold-Suche

Optimaler Threshold pro Label = **kleinster confidence-Bin**, für den gilt:
- `wilson_lower_95 >= target_floor` (Default-Empfehlung: 0.50 bullish, 0.70 bearish)
- `n_at_bin >= 30` (statistische Mindestgröße)
- Marginal precision gain bei +0.05 Schwellen-Anhebung < 5pp (Plateau-Kriterium)

Wenn kein bin alle drei erfüllt: F3-V2 inconclusive, Stop-Condition §5.

### 3c. Vergleich gegen aktuelle Schwelle

| Label | Aktuell | Empirisch optimal | Δ | wilson_lower_95 | n |
|---|---|---|---|---|---|
| bullish | 0.80 (D-116) | … | … | … | … |
| bearish | 0.95 (D-122) | … | … | … | … |

---

## 4. Aktivierungs-Schwellen

### Stufe 1 — Shadow-Re-Eval-Patch

**Wenn Δ_threshold (aktuell vs. empirisch) > 0.05 pro Label:**
Code-Patch ist gerechtfertigt. Vor Live-Edit: **Shadow-Mode** für 14 Tage:

- Neuer Audit-Stream `confidence_recalibration_shadow.jsonl`
- Pro Record loggt: original gate-decision, was-wäre mit neuer Schwelle, Δ-Outcome (wenn resolved)
- Kein Code-Patch auf MIN_DIRECTIONAL_CONFIDENCE_* — nur Shadow-Sim

### Stufe 2 — Live-Patch (nach 14d Shadow)

**Alle drei Pflicht:**
- Shadow-Phase-precision an neuer Schwelle >= 90% des prediction-Werts aus §3
- Throughput-Increase (more passing the gate) <= 50% (Trader-Stream-Floodgate-Schutz)
- Operator-Sign-off auf Shadow-Empirie

Code-Edit: `app/alerts/eligibility.py:253-256` mit Test-Update + ADR.

### Stufe 3 — Auto-Rollback

**Trigger:**
- Live-Phase rolling 7d precision an gepatchter Schwelle fällt > 5pp unter Shadow-Wert
- ODER Throughput in operator alert stream steigt > 100% (over-flood)

**Aktion:** ENV-Override `KAI_CONFIDENCE_THRESHOLD_BULLISH_OVERRIDE` / `..._BEARISH_OVERRIDE` 
auf alte Werte, Memo, Operator-Notify. Kein Sign-off — Safety-Default.

---

## 5. Stop-Conditions

| Befund | Folge-Aktion | Nächste Re-Eval |
|---|---|---|
| n_total < 100 | F3-V2 verschoben | +4w |
| n_label < 30 | gleicher Label-Skipped, anderer Label evaluiert | +4w für ausgelassenen Label |
| Plateau-Kriterium nirgends erfüllt | inconclusive, neue Probe abwarten | +8w |
| Δ_threshold <= 0.05 für beide Labels | aktuelle Schwellen bestätigt, ADR-Eintrag | keine Re-Eval (vermerken) |
| Δ_threshold > 0.05, aber wilson_lower_95 < target_floor | inconclusive | +8w |

---

## 6. Operator-Sign-off-Punkte

| Punkt | Wann | Was wird unterschrieben |
|---|---|---|
| SO-1 Trigger-Verify | vor Skript-Run | Trigger-Conditions + Methodik akzeptiert |
| SO-2 Shadow-Start | nach Skript-Run | Empirie-Befund + Confounder-Bereinigung + Shadow-Patch-PR |
| SO-3 Live-Patch | nach 14d Shadow | Shadow-Empirie + ADR + Live-PR |
| SO-rollback | bei Auto-Rollback | **Keine** — Safety-Default |

---

## 7. Artefakte

| Pfad | Inhalt | Tracking |
|---|---|---|
| `scripts/f3_v2_confidence_recalibration.py` | Analyse-Skript (~200 LOC) | git-tracked, vor 2026-06-15 zu schreiben |
| `tests/unit/test_f3_v2_confidence_recalibration.py` | Wilson-Lower + Threshold-Suche + Plateau-Kriterium (~15 Cases) | git-tracked |
| `artifacts/f3_v2_confidence_threshold_curve_<date>.json` | ROC/PR-Daten + optimal-Threshold-Output | Pi-lokal |
| `artifacts/operator_memos/f3_v2_recalibration_<date>.md` | Befund-Memo | Pi-lokal |
| `app/alerts/confidence_shadow.py` | Shadow-Mode-Code (nach SO-2) | git-tracked, neuer Sprint |
| `artifacts/confidence_recalibration_shadow.jsonl` | Shadow-Audit-Stream | Pi-lokal |

---

## 8. Folge-Sprints

- **Shadow-Start grün:** Sprint `F3-V3 Live-Patch` — Code-Edit auf MIN_DIRECTIONAL_CONFIDENCE_* + Tests + 14d-Shadow-Empirie als Evidence + ADR
- **Re-Eval inconclusive (n<100 ODER Plateau nicht erfüllt):** Folge-Sprint **Outcomes-Coverage** (analog zu F2-V2 — kai-auto-annotate.timer reaktiviert seit 21.05., aber Live-Pipeline-Coverage prüfen)
- **Δ_threshold <= 0.05:** kein Sprint, ADR-Eintrag "F3-V2 2026-06-15 bestätigt MIN_DIRECTIONAL_CONFIDENCE_* heuristisch korrekt"

---

## 9. Cross-Links

- `app/alerts/eligibility.py:247-256` — D-116/D-119/D-121/D-122 Constants + Begründungs-Kommentare (Threshold-Edit-Target)
- `app/alerts/eligibility.py:723-735` — Gate-Logic (Lese-Target für Threshold-Anwendung)
- F1 (PR #60 `9a799cc8`) — Substantive-Whitelist (Confounder)
- F3-V0 (PR #61 `cb731a41`) — Schema-Patch (Voraussetzung)
- F4 (PR #59 `8f529148`) — Dispatch-Observability (Audit-Stream-Quelle)
- Auto-Annotate-Reaktivierung 21.05. (Outcome-Coverage-Voraussetzung)
- F2-V2 Spec (PR #75) — Parallel-Spec mit gleichem Trigger-Pattern + Confounder-Behandlung

---

## Methodik-Notiz

ROC/PR-Kurve über confidence-Bins ist das richtige Werkzeug, weil die heuristisch gesetzten
Schwellen (0.8, 0.95) keine eingebauten statistischen Garantien haben — sie kommen aus
n=24 / n=25 Empirie. F3-V0 hat das Daten-Vakuum geschlossen: ab 24.05. ist die volle
Confidence-Verteilung pro Outcome auswertbar.

Das Plateau-Kriterium (marginaler Precision-Gain < 5pp bei +0.05 Schwellen-Anhebung) verhindert
Overfitting an Sample-Rauschen — sucht die kleinste Schwelle, die "gut genug" ist, statt die
absolute Maximum-Precision-Schwelle (die wäre meist 1.0 mit n=1).

Throughput-Cap (Stufe 2 Bedingung 2) ist Operator-UX-Schutz: ein zu großer Threshold-Drop
würde den Alert-Stream fluten und Operator-Aufmerksamkeit verdünnen. 50% Throughput-Increase
ist eine konservative Obergrenze; bei Bedarf in der Spec-Ratifizierung (30.05.) nachjustieren.

Bearish-Sample-Knappheit ist die wahrscheinlichste Stop-Bedingung — `BEARISH_DIRECTIONAL_DISABLED`
hat die bearish-Outcome-Coverage 2024-2025 strukturell unterversorgt. F2-V2 und F3-V2 teilen
das Problem; bei beiden ist die Auto-Annotate-Pipeline-Coverage der primäre Hebel zur
Stichprobenvergrößerung.
