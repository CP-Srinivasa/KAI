# Auto-Annotate-Threshold-Forensik 2026-05-21

> **⚠ RECONCILIATION-HEADER (Claude Code, 2026-05-21 abends):**
>
> **Dieses Memo ist KOMPLEMENTÄR zur Master-V5-Forensik vom 2026-05-22.**
> Es entstand parallel ohne Cross-Check zu meinem bereits-committeden Memo
> `auto_annotate_threshold_forensik_2026-05-22.md` (commit `70aec2bc`).
>
> **Drei Diskrepanzen, die der Operator vor 2026-05-30-Re-Eval kennen muss:**
>
> 1. **Hauptbefund fehlt:** Dieses Memo erwähnt NICHT, dass
>    `kai-auto-annotate.timer` seit 2026-05-12 12:11 CEST 9 Tage tot war.
>    Das war der **dominante Faktor** für 7758 backfill-inconclusives.
>    Master-Memo hat das aufgedeckt, Operator-Option-A wurde ratifiziert,
>    Timer ist seit 2026-05-21 13:21 CEST reaktiviert
>    (`[[kai-auto-annotate-reactivation-20260521]]`).
>
> 2. **Datenbasis-Drift:** Dieses Memo zählt 4307 Outcomes / 3884 inconclusive
>    (90.2%). Pi-Live zeigt 8555 / 8173 (95.4%). Differenz weil dieses Memo
>    Workstation-lokale Daten (KAI-mirror oder Vor-Sync-Stand) genutzt hat
>    statt Pi-Live. Für 30.05.-Re-Eval gilt Pi als Source-of-Truth.
>
> 3. **Empfehlungs-Reihenfolge:** Dieses Memo schlägt Option A = Reporting-
>    Trennung (fresh/backfill/reeval) zuerst vor. Master-Memo hat Option A =
>    Pipeline-A-Reaktivierung priorisiert (bereits done). Die hier
>    vorgeschlagene Reporting-Trennung ist als **Folge-Sprint nach 30.05.-
>    Re-Eval** sinnvoll, nicht als sofortige Maßnahme.
>
> **Komplementär-Wert dieses Memos (legitim, weiter nutzen):**
> - §5 Eligibility-Gate-Forensik (P7=37/45 = 82% des frischen Streams,
>   actionable=false bei 37/45, bearish-disabled, `_primary_symbol`-first-
>   asset-only). Das sind strukturelle Blocker im DIRECTIONAL-Pre-Filter,
>   die das Master-Memo nicht analysiert hat.
> - §6 Option C (Blocker-Forensik P7/actionable/source/mixed-neutral) ist
>   ein konkret-spec'ter Folge-Sprint-Kandidat.
>
> **Disziplin-Drift:** Codex hat dieses Memo eigeninitiativ erstellt
> (Sign-off-Skip-Vorfall #3 am 2026-05-21), ohne vorab das Pi-Memo zu lesen
> (Worktree-Cross-Check-Lücke). Siehe Memory-Pin
> `[[feedback-codex-v2-signoff-gap-20260521]]`.
>
> ---

Scope: read-only Forensik zu `app/alerts/*.py`, `app/analysis/*` und
`artifacts/alert_outcomes.jsonl`. Keine Code-Aenderung, kein Threshold-Tuning,
kein SHADOW_ONLY-Flip.

## 1. Kurzbefund

Die Lernschicht erzeugt viele `inconclusive` Outcomes, weil das aktuelle
Auto-Annotate-Verfahren streng und konservativ arbeitet:

- Nur directional-eligible Alerts werden annotiert.
- `inconclusive` wird als unresolved behandelt und nicht in die Hit-Rate
  gezaehlt.
- Der groesste Teil der Outcome-Schreiblast ist Backfill/Reeval-Material, kein
  frisches Forward-Signal.
- Die aktuellen dynamischen Schwellen sind fuer 7-Tage-Backfills haeufig hoeher
  als die beobachteten Bewegungen.

Aktueller Datenstand in `artifacts/alert_outcomes.jsonl`:

- Gesamt: 4.307 Outcome-Annotationen
- `inconclusive`: 3.884, also 90,2 Prozent
- seit 2026-05-16 nach `annotated_at`: 117 Annotationen
- davon `inconclusive`: 106, also 90,6 Prozent
- davon `miss`: 11
- davon `hit`: 0

Die im Audit genannte Groessenordnung von 95,5 Prozent ist damit inhaltlich
bestaetigt, aber im aktuellen lokalen Snapshot nicht exakt reproduziert. Die
aktuelle Rohquote seit 2026-05-16 liegt bei 90,6 Prozent.

## 2. Betroffene Dateien/Funktionen

- `app/alerts/auto_annotator.py`
  - `_DEFAULT_MIN_AGE_HOURS`, `_DEFAULT_MAX_AGE_HOURS`,
    `_DEFAULT_MOVE_THRESHOLD`
  - `_REEVAL_MIN_AGE_HOURS`, `_STALE_REEVAL_WINDOW_HOURS`,
    `_DEFAULT_BACKFILL_BATCH`
  - `_scaled_threshold(...)`
  - `_primary_symbol(...)`
  - `auto_annotate_pending(...)`
- `app/alerts/audit.py`
  - `AlertOutcomeAnnotation`
  - `append_outcome_annotation(...)`
  - `load_outcome_annotations(...)`
  - `AlertAuditRecord`
- `app/alerts/hit_rate.py`
  - `build_outcomes_from_records(...)`
  - `compute_hit_rate(...)`
- `app/alerts/eligibility.py`
  - `evaluate_directional_eligibility(...)`
  - priority, actionable, source, bearish, confidence, asset and naked-asset
    gates
- `app/analysis/scoring.py`
  - `compute_priority(...)`
  - `is_alert_worthy(...)`
- `app/analysis/AGENTS.md`
  - RuleAnalyzer fallback semantics and priority ceiling

## 3. Aktuelle Threshold-Logik

Basisparameter in `auto_annotator.py`:

- Mindestalter frischer Kandidaten: 4 Stunden
- Maximalalter frischer Kandidaten: 72 Stunden
- Basis-Move-Threshold: 1,0 Prozent
- Reeval-Mindestalter fuer `inconclusive`: 24 Stunden
- Stale-Reeval-Fenster: 168 Stunden, also 7 Tage
- Backfill-Batch: 30 Kandidaten

`_scaled_threshold(...)` skaliert die Basisschwelle nach Alter:

- bis 8h: 0,7x
- bis 12h: 1,0x
- bis 24h: 1,5x
- bis 48h: 2,0x
- ueber 48h: 2,5x

Danach wird mit BTC-24h-Volatilitaet skaliert:

- unter 1 Prozent BTC-Volatilitaet: 0,6x
- 1 bis 3 Prozent: linear 0,6x bis 1,0x
- ueber 3 Prozent: bis maximal 1,5x
- Floor: 0,3 Prozent

Outcome-Entscheidung:

- bullish und Bewegung >= Schwelle: `hit`
- bearish und Bewegung <= negative Schwelle: `hit`
- bullish und Bewegung <= negative Schwelle: `miss`
- bearish und Bewegung >= Schwelle: `miss`
- alles dazwischen: `inconclusive`

## 4. Warum so viele inconclusive entstehen

1. Backfill dominiert die Outcome-Datei.

Seit 2026-05-16 sind 115 von 117 neuen Annotationen `backfill:*`, nur 2 sind
`auto:*`. Gesamt sind 3.577 von 4.307 Annotationen Backfill. Damit misst die
Quote vor allem historische Nachannotation, nicht die Qualitaet frischer
Operator-Signale.

2. Die beobachteten Bewegungen liegen meist unter der Schwelle.

Seit 2026-05-16 haben alle 117 neuen Annotationen das aktuelle Note-Pattern mit
`thr=...`. Davon liegen 106 Bewegungen unter der jeweiligen Schwelle. Das
entspricht exakt den 106 `inconclusive` Outcomes in diesem Zeitraum.

3. 7-Tage-Backfills erzeugen harte Schwellen.

Die Stundenverteilung seit 2026-05-16 hat Median 168h. Bei >48h greift der
2,5x-Altersfaktor. Die beobachtete Schwelle liegt seit 2026-05-16 bei:

- Minimum: 0,90 Prozent
- Median: 1,50 Prozent
- p75: 2,52 Prozent
- Maximum: 2,58 Prozent

Die beobachtete absolute Bewegung liegt dagegen bei:

- Minimum: 0,24 Prozent
- Median: 0,87 Prozent
- p75: 1,36 Prozent
- Maximum: 6,42 Prozent

Der Median-Move liegt also deutlich unter dem Median-Threshold.

4. `inconclusive` bleibt fuer Hit-Rate bewusst unresolved.

`app/alerts/hit_rate.py` behandelt `inconclusive` als `None`, also unresolved.
Das ist konservativ korrekt, fuehrt aber dazu, dass die Lernschicht bei vielen
kleinen, richtungsnahen Bewegungen nicht atmet.

5. Die frische Forward-Datenbasis seit 2026-05-16 ist sehr klein.

Join ueber `alert_audit.jsonl` und `alert_outcomes.jsonl` zeigt seit
2026-05-16 nach Dispatch-Zeit nur einen frischen annotierten Alert:

- `2ff1b754-7695-45de-8620-2f2b70a6e69f`
- bullish BTC/USDT
- +0,24 Prozent ueber 12,2h
- Schwelle 0,90 Prozent
- Outcome `inconclusive`

Damit darf die aktuelle Quote nicht als belastbare Forward-Precision gelesen
werden.

6. Eligibility und Scoring sind stark fail-closed.

`evaluate_directional_eligibility(...)` blockt unter anderem:

- non-actionable Alerts
- bearish directional Alerts, solange bearish disabled ist
- Priority <= 7
- Low-Precision-Sources
- Promo-Pattern
- schwache Signale
- reactive narratives
- niedrige directional confidence
- unsupported oder naked assets

`app/analysis/AGENTS.md` dokumentiert ausserdem, dass Rule-only Analysis
konservativ neutral/actionable=false bleibt und priority <= ca. 5 produziert.
Solche Dokumente erreichen keine SignalCandidate-Schwelle.

## 5. Datenbelege

Outcome-Datei:

- Datei: `artifacts/alert_outcomes.jsonl`
- Rows: 4.307
- Parse-Fehler: 0
- Gesamtverteilung:
  - `inconclusive`: 3.884
  - `miss`: 278
  - `hit`: 145
- Tags gesamt:
  - `backfill`: 3.577
  - `reeval`: 207
  - `auto`: 144
  - `price_unavailable`: 179
  - `manual`: 14
  - `other`: 186
- Pattern gesamt:
  - Threshold-Notes: 3.921
  - Price-unavailable: 179
  - Historical-Notes: 74

Seit 2026-05-16 nach `annotated_at`:

- Rows: 117
- `inconclusive`: 106
- `miss`: 11
- `hit`: 0
- Tags:
  - `backfill`: 115
  - `auto`: 2
- Assets:
  - SOL/USDT: 63
  - BTC/USDT: 53
  - LINK/USDT: 1
- Movement-vs-threshold:
  - 106 unter Schwelle
  - 11 an oder ueber Gegenrichtungs-Schwelle

Audit-Datei seit 2026-05-16:

- Datei: `artifacts/alert_audit.jsonl`
- Rows seit 2026-05-16: 45
- Sentiment:
  - neutral: 24
  - mixed: 17
  - bullish: 2
  - missing: 2
- Priority:
  - P7: 37
  - P9: 2
  - P10: 4
  - missing: 2
- Actionable:
  - true: 6
  - false: 37
  - missing: 2

Interpretation: Der frische Audit-Stream ist ueberwiegend neutral/mixed oder
non-actionable. Die annotierbare directional Teilmenge ist klein.

## 6. Drei Tuning-Optionen

Option A: Reporting-Schnitt schaerfen, keine Threshold-Aenderung.

- Separat berichten:
  - Fresh Auto-Annotate
  - Backfill
  - Reeval
  - Latest-per-document
  - raw append-only rows
- `inconclusive` nicht als Fehlerquote lesen, sondern als unresolved pool.
- Ziel: Operator sieht, ob das Problem Datenbasis, Threshold oder Reporting ist.

Option B: Reeval/Backfill-Schwellen getrennt kalibrieren.

- Fresh-Threshold unveraendert lassen.
- Backfill/Reeval mit eigener Schwelle oder eigenem Fenster auswerten.
- Beispiel als Spezifikation, nicht Umsetzung:
  - 7-Tage-Backfill nicht automatisch mit 2,5x hart lesen
  - oder separate Outcome-Klasse/Note fuer `below_threshold_directional_move`
    in Reporting, ohne Hit/Miss-Definition zu verwässern

Option C: Mehr Material in die directional Lernschicht bringen.

- Kein Threshold-Tuning zuerst.
- Stattdessen Forensik auf die Blocker:
  - P7-Cluster
  - actionable=false
  - mixed/neutral Dominanz
  - source reliability modifiers
  - bearish disabled
  - first-asset-only Auswahl in `_primary_symbol(...)`
- Ziel: mehr echte, klare directional Kandidaten, bevor die Hit/Miss-Schwelle
  aufgeweicht wird.

## 7. Risiko je Option

Option A:

- Niedriges Risiko.
- Keine Modell- oder Trading-Semantik wird veraendert.
- Risiko: Es verbessert nur Sichtbarkeit, nicht die Datenmenge.

Option B:

- Mittleres Risiko.
- Zu niedrige Backfill-Schwellen koennen historische Rauschen als Hit/Miss
  klassifizieren.
- Wenn Fresh und Backfill vermischt bleiben, entsteht Scheingenauigkeit.

Option C:

- Mittleres Risiko.
- Eligibility-Lockerung kann alte False-Positive-Probleme wieder oeffnen,
  besonders bei P7, bearish und low-precision sources.
- Muss einzeln gemessen werden, nicht als pauschale Gate-Lockerung.

## 8. Erwartete Wirkung

Option A:

- Sofort bessere Operator-Lesbarkeit.
- Erwartet keine Veraenderung der Hit/Miss-Verteilung.
- Reduziert Fehlinterpretation der 90 Prozent inconclusive als Pipeline-Defekt.

Option B:

- Kann die Backfill-Inconclusive-Quote sichtbar senken.
- Wirkung wahrscheinlich hoch auf historische Rows, aber gering auf frische
  Forward-Daten, solange seit 2026-05-16 nur ein frischer annotierter Alert
  vorliegt.

Option C:

- Verbessert die Lernatmung nachhaltiger, wenn die Blocker sauber belegt sind.
- Erwartete Wirkung kommt langsamer, dafuer weniger Risiko, historische
  Threshold-Definitionen zu verfaelschen.

## 9. Empfohlene Option

Empfohlen: Option A sofort, Option C als naechste Forensik, Option B erst nach
Operator-Sign-off.

Begruendung:

- Die aktuelle Quote ist stark durch Backfill/Reeval verzerrt.
- Die frische Forward-Datenbasis ist zu klein fuer Threshold-Tuning.
- Die Threshold-Logik arbeitet technisch konsistent: 106/106 inconclusive Rows
  seit 2026-05-16 liegen tatsaechlich unter der jeweiligen Schwelle.
- Das groessere Problem ist nicht ein einzelner Code-Bug, sondern
  Datenmaterial/Reporting/Eligibility-Schnitt.

Nicht empfohlen ohne Freigabe:

- `move_threshold` senken
- Altersfaktor fuer >48h direkt senken
- P7-Gate pauschal lockern
- bearish disabled entfernen
- SHADOW_ONLY flippen
- Bayes-Sizing oder Live-nahe Parameter ableiten

## 10. Operator-Decision-Anker fuer 2026-05-30

Bis 2026-05-30 sollte entschieden werden:

1. Soll das Operator-Reporting `fresh_auto`, `backfill`, `reeval` und
   `latest_per_doc` getrennt ausweisen?
2. Soll Backfill weiterhin mit derselben Hit/Miss-Definition wie Fresh-Signale
   bewertet werden?
3. Soll eine separate Reeval-Policy fuer 7-Tage-Fenster spezifiziert werden?
4. Soll zuerst eine P7/actionable/source Blocker-Forensik laufen, bevor
   Thresholds geaendert werden?
5. Welche Metrik ist fuer den naechsten Gate-Check massgeblich:
   raw append-only Outcomes, latest-per-document oder fresh dispatch cohort?

Empfohlener Decision-Pfad:

- 2026-05-30: Option A freigeben.
- Danach 7 Tage sammeln.
- Danach nur bei genuegend frischer Forward-Kohorte ueber Option B entscheiden.
- Option C parallel als reine Forensik vorbereiten, aber keine Gates ohne
  explizite Operator-Freigabe lockern.
