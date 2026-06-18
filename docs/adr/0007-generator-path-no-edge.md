# ADR 0007 — Current generator/screener/news path = NO_EDGE

**Status:** Accepted (2026-06-19)
**Stufe:** Measurement-Verdict (read-only) — keine Runtime-/Gate-/Execution-Folge
**Quelle:** Track-2-Edge-Audits (2.1–2.4) + Track 2.3 Source×Direction×Horizon (PR #356)

## Kontext

Mehrstufige read-only Edge-Analyse über `artifacts/shadow_candidate_resolved.jsonl`
(per-Horizont Forward-bps: 1m/5m/15m/1h) plus die source-attribuierte Brücke
(Track 2.4, DB-Join). Ziel: lokalisieren, *wo* Forward-Edge getragen oder zerstört
wird — als Instrument, nicht als Regel.

Zentrale methodische Korrektur (§12): **Mean-EV ist in Krypto durch einzelne
Tail-Winner extrem leicht zu fälschen.** Eine frühere Zwischen-These
(„LONG trägt +31bps / GO_CANDIDATE", „technical_screener×long×alt = CARRIER +98bps")
war jeweils ein Ausreißer-Artefakt (wenige Microcap-Zeilen dominierten den Mean).
Verdicts laufen deshalb ab jetzt auf **robustem trimmed-mean EV (`robEV`)**, nicht
auf Mean-EV. Mean bleibt nur ein Diagnosefeld; die Divergenz steht in der Tabelle
nebeneinander (`EVnet` vs `robEV`). Der Test
`test_outlier_inflated_mean_is_not_a_carrier` fixiert das dauerhaft.

## Entscheidung (Verdict)

**Der aktuell gemessene generator/screener/news-Pfad hat keinen robust handelbaren
Forward-Edge.**

- Kein robuster Long-Carrier. Einzige robust netto-positive Zelle:
  `technical_screener × short × major` (robEV +2.8…+6.9 bps, n=41 — **dünn**,
  SUPPORTING/Watchlist, **nicht** TRADEABLE).
- Bearish ist **nicht** richtungs-giftig (kein DIRECTION_POISON); Shorts liegen
  netto unter der ~20bps-Kostenhürde. Es ist eine *Kosten*-, keine *Richtungs*-Frage.
- **Kein** handelbarer invertierter EV — alle inverted robEV negativ, kein
  CONTRARIAN_CANDIDATE. Bearish-Invert bleibt tot (`ALERT_ALLOW_SHORT_NEWS=false`
  bleibt richtig).
- Beide Asset-Buckets tragen robust **negativ** (alt −14.3, major −9.9). Der
  scheinbare alt-„Träger" war reine Outlier-mean-Inflation.

## Lineage-Klärung: Track 2.3 vs Track 2.4 (kein Widerspruch)

Damit niemand später „thedefiant +3bps" (2.4) gegen „thedefiant bps_unavailable"
(2.3) ausspielt — beide sind korrekt, weil **unterschiedliche Datenlinie**:

| | Track 2.3 (PR #356) | Track 2.4 |
|---|---|---|
| Eingang | rohe `shadow_candidate_resolved.jsonl` | gleiche resolved-Rows **+ DB-Join** |
| `source`-Bedeutung | Resolver/Generator-Identität (technical_screener / autonomous_generator / none) | originärer News-`source_name` |
| Join | keiner | `candidate_id → shadow_candidate_ledger.document_id → canonical_documents.source_name` |
| News-Quellen | erscheinen als **`bps_unavailable`** (kein source_name im Rohstrom) | **gemessen**: thedefiant-long ≈ **+3 bps Median, sub-cost**, Hit 58% |

**Wahrheit:** Die Forward-bps der News-Events *existieren* — im Rohstrom sind sie
dem Generator zugeordnet, nicht dem News-`source_name`. „bps_unavailable" in 2.3 ist
eine Eigenschaft **dieses Rohpfads**, **keine** Aussage „nie gemessen". PR #355
backt den Join in künftige Rows ein (dann werden auch 2.3-Rows self-contained).
Hit/Miss (z.B. thedefiant Alert-95%) und Forward-bps sind **verschiedene
Wahrheiten** und werden **nicht** ineinander umgerechnet.

## Erlaubt

- read-only measurement / shadow observation
- künftige Attributions-Instrumentierung (PR #355-Linie)
- **Watchlist** `technical_screener × short × major` (weiter messen, nicht aktivieren)

## Nicht erlaubt (aus dieser Analyse)

- Source-Boost / Source-Downrank
- bearish-Invert / Short-Reenable
- Exit-/Horizon-/Sizing-/Cap-Tuning
- Runtime-/Gate-Änderung
- bps aus Hit/Miss schätzen oder backfillen

## Konsequenz

Downstream-Tuning (Exit, Horizon, Source-Gewicht, Invert) ist als Edge-Hebel
ausgeschlossen — die scheinbaren Gewinner waren Mean-/Outlier-/Hit-Miss-Artefakte.
Der nächste sinnvolle Schritt ist **nicht** den alten Generator zu ernten, sondern
**Upstream-Signal-Redesign** (Eventtyp × Magnitude × Timing × Novelty): welche
Eventtypen bewegen den Markt netto >20bps *und* früh genug. Das ist ein eigener,
separat zu autorisierender read-only Track — nicht Teil dieser Entscheidung.
