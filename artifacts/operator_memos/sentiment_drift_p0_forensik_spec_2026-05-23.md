# P0 Folge-Sprint Spec — Sentiment-Drift-Forensik (2026-05-23)

**Stichtag-Trigger:** EOW-Validierung 2026-05-23, Sample-Kollaps in `alert_audit.jsonl` 7d-Cross-Tab.
**Deadline:** vor 2026-05-30 (Priority-Scoring-Decision-Termin) — sonst kollabiert auch die 30.05.-Decision auf zu dünner Datenbasis.
**Sprint-Owner:** Operator + Claude (parallele Codex-Session optional als Hypothese-(b)-Pfad).

---

## Befund (Empirie 23.05. EOW-Snapshot)

7d-Fenster 16.-23.05. zeigt **massiven Direktional-Kollaps** gegenüber Mid-Window-Forensik 20.05.:

| Bucket | 20.05. Baseline directional% | 23.05. 7d directional% | Δ |
|---|---|---|---|
| p>=10 | 43.5% (n=418) | 25.0% (n=8) | -18.5 pp |
| p=8/9 | 87.8% (n=475) | 8.3% (n=12) | **-79.5 pp** ⚠ |
| p<8 | 31.3% (n=917) | 0.0% (n=85) | -31.3 pp |

**Sample-Kollaps:** 7d 105 alerts mit `sentiment_label` vs Baseline 1810 (Faktor 17x kleiner). Über 7d nur **3 bullish, 0 bearish, 102 neutral/mixed** (2.9% directional). 4-Tage-Null-Direktional-Periode aus Pin 20.05. hat sich auf 7 Tage verlängert.

Audit-Stream-Konsequenz: Bayes-Schreibrate fällt von 0.62/Tag (21.05.) auf **0.29/Tag** (jüngstes 7d), n>=20-Schwelle erst ~2026-08-23. SHADOW_ONLY-Flip-Pfad am n-Kriterium mathematisch tot.

---

## 3 Konkurrierende Hypothesen

### Hypothese (a) — Quellen-Drift

**Annahme:** RSS-Quellen haben in den letzten 7d weniger direktional klassifizierbares Material geliefert (Markt-Quiet-Phase + Quellen-Mix-Verschiebung).

**Forensik-Schritte:**
1. `provenance`-Feld in `alert_audit.jsonl` 7d gruppieren: welche Quellen liefern Material, welche sind stumm?
2. Pre-14.05.-Baseline (z.B. 30.04.-07.05.) vs 7d-Window: gleiche Quellen-Verteilung?
3. Neue Quellen seit 14.05. integriert? Audit gegen `source_reliability.json` + Pipeline-Config.
4. Wenn Quellen-Mix gleich, aber Volume kollabiert → echte Markt-Phase (Hypothese (c)).

**Sign-off-Kriterium:** Quellen-Provenance-Verteilung 7d vs Baseline mit Top-10-Quellen-Tabelle + Delta-Spalte.

### Hypothese (b) — Klassifikator-Threshold

**Annahme:** PR #45/#46/#47-Cluster (2026-05-16) hat Sentiment-Klassifikator-Threshold zu konservativ geschraubt; mehr Material wird als neutral/mixed klassifiziert statt bullish/bearish.

**Forensik-Schritte:**
1. Code-Review `app/analysis/sentiment.py` (oder analoges Modul) — was hat sich seit 14.05. geändert?
2. Re-Klassifikation auf 50 Sample-Alerts aus 7d mit altem (pre-PR-#45) vs neuem Code-Pfad. Erwartung: wenn (b) korrekt, signifikanter Anteil Re-Klassifikationen von neutral → bullish/bearish.
3. Schwellwert-Sweep: Konfidenz-Threshold um 5/10/15 pp senken, neu klassifizieren, directional% messen.

**Sign-off-Kriterium:** Code-Diff-Bericht + Re-Klassifikations-Tabelle + Schwellwert-Sweep-Befund. Bei Bestätigung: Rollback-Patch oder Re-Tuning-Spec.

### Hypothese (c) — Echte Markt-Phase

**Annahme:** Krypto-Markt ist 7d niedrigvolatil, News-Material ist tatsächlich überwiegend neutral (Regulatorik-Updates, Konferenzen, BTC-VIX-Launch — Material ohne klare bullish/bearish-Implikation).

**Forensik-Schritte:**
1. Manuelle Stichprobe: 20 zufällige neutral/mixed-Alerts aus 7d lesen. Sind sie ECHT neutral?
2. BTC + ETH Spot-Volatilität 7d vs 30d-Baseline (vol_drop %).
3. Cross-Reference Regime-Audit (`regime_audit.jsonl`) — welcher Regime-Klasse dominiert 7d?
4. Wenn (c) korrekt: Keine Code-Änderung nötig, aber **Decision-Implikation**: bei niedrigvolatilen Markt-Phasen ist das Premium-Channel-Konzept ohnehin schwach — Operator soll Pause-Modus oder anderer Trigger-Mechanismus prüfen.

**Sign-off-Kriterium:** Stichproben-Bericht + Volatilitäts-Tabelle + Regime-Verteilung.

---

## Reihenfolge + Aufwand

| Hypothese | Aufwand | Reihenfolge | Begründung |
|---|---|---|---|
| (c) Markt-Phase | ~2h | **1. Schritt** | Cheapest, wenn bestätigt schließt es (a)+(b) als sekundär aus |
| (a) Quellen-Drift | ~3h | 2. Schritt | Data-only, kein Code-Read, schnell auswertbar |
| (b) Klassifikator-Threshold | ~6h | 3. Schritt | Code-Read + Re-Klassifikation, teuerster Pfad |

**Empfohlene Bearbeitung:** Sequenziell, frühestes Sign-off-Kriterium bricht den Sprint ab. Wenn (c) bestätigt → Decision-Memo, kein Patch. Wenn (a) → Quellen-Erweiterung. Wenn (b) → Klassifikator-Rollback oder Re-Tuning.

---

## Operative Deliverables

1. **Memo `sentiment_drift_forensik_befund_2026-05-26.md`** mit Hypothesen-Status (bestätigt / widerlegt / pending) und konkretem Folge-Patch-Vorschlag (falls (a) oder (b)).
2. **Cross-Tab Priority×Sentiment-Verteilung** auf gleichem 7d-Fenster, nach Quellen-Bucket geschnitten.
3. **Daily-Strategy 26.05.-Eintrag** mit DS-20260526-V1: Sentiment-Drift-Forensik-Sprint-Status.

**Spätester Sign-off:** 2026-05-28 (2 Tage Puffer vor 30.05.-Decision).

---

## Risiko bei Nicht-Bearbeitung

Wenn vor 30.05. **keine** Hypothese bestätigt ist:
- Priority-Scoring-Decision (Brief A/B/C/D + PR #58 A') hat keine empirisch valide Grundlage. Sample bleibt zu dünn.
- Option D (Status quo) ist dann zwingend, ohne Aussicht auf Anhebung der 30.05.-Decision auf eine echte Daten-Decision. **Wiederholung des 23.05.-Schwebezustands ist garantiert.**
- SHADOW_ONLY-Flip kann am 30.05. nicht entschieden werden (n-Pfad tot, Zeit-Pfad erst 13.06., kein Diversitäts-Signal).

---

## Datenquellen für diesen Sprint

- `artifacts/alert_audit.jsonl` (7865 Z, 1826 mit sentiment_label total; 105 in 7d)
- `artifacts/alert_outcomes.jsonl` (8870 Z, 913 in 7d, 99.6% incon)
- `source_reliability.json` (8 sources, alle insufficient)
- `regime_audit.jsonl` (hourly stamps)
- `app/analysis/sentiment.py` (Code-Inspection für (b))
- `git log v25.4.0..HEAD --grep sentiment` (PR-Tracking)

---

**Cross-Links:**
- [[kai-priority-sentiment-correlation-paradox]] (Befund 20.05.)
- [[adr1-paper-min-priority-reversion-20260519]] §Update 2026-05-20
- [[kai-bayes-shadow-only-flip-heuristik]] (Flip-Bedingungen)
- `priority_scoring_decision_brief_2026-05-23.md` §Option-D-Risiko
- `eow_validation_findings_2026-05-23_claude.md` §4 + §7
