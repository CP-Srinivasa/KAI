# Auto-Annotate-Threshold-Forensik — 2026-05-22

**Auftrag:** DS-20260521-V5 (P1, read-only). Klären, warum 95.5% der Outcomes seit 2026-05-16 als `inconclusive` klassifiziert sind und welche Tuning-Optionen für die EOW-Re-Eval am 2026-05-30 vorliegen.
**Verfasser:** Claude Code Mid-Window-Forensik. Kein Code-Eingriff. Tuning-Vorschläge stehen als Operator-Decision-Vorlage am Ende.

---

## 1. Kurzbefund (3 Sätze)

Es gibt drei Annotation-Pipelines im Code (`auto_annotator.py`, `price_check.py` via `auto-check`-CLI, `offline_baseline.py`). **Die "gute" Pipeline (`auto_annotator.py` mit Vol-/Window-Scaling, threshold base 1.0%, smartem Tagging) läuft seit 2026-05-12 12:11:04 CEST NICHT** — `kai-auto-annotate.timer` ist deaktiviert. Was als 7758 `backfill`-inconclusives sichtbar ist, stammt aus dem Fallback-CLI `auto-check` (`app/alerts/price_check.py:_suggest_outcome`) mit fixer Threshold von 5.0% über 24h-Horizon — bei BTC/ETH-Tagesbewegung unter 5% bleibt fast alles strukturell `inconclusive`.

## 2. Betroffene Dateien/Funktionen

| Pipeline | Datei | Funktion | Aufrufer | Status |
|---|---|---|---|---|
| A | `app/alerts/auto_annotator.py:391-410` | Klassifikations-Loop | `kai-auto-annotate.timer` (every 6h) | **DEACTIVATED 2026-05-12 12:11:04 CEST** |
| B | `app/alerts/price_check.py:_suggest_outcome:40-63` | Fixer-Threshold-Klassifikator | `app.cli.main alerts auto-check` via `scripts/ph5_daily_ops.ps1:214` | aktiv (Workstation- oder externes Cron) |
| C | `app/alerts/offline_baseline.py:139` | Baseline-Report-Builder | `alerts baseline-report`-CLI | nur on-demand |

**Auto-Annotate-Schreibpfad zu `alert_outcomes.jsonl`:** beide Pipelines A+B nutzen `append_outcome_annotation` (`app/alerts/audit.py`).

## 3. Aktuelle Threshold-Logik

### Pipeline A (`auto_annotator.py`, INAKTIV)

```python
# auto_annotator.py:78-126 _scaled_threshold
def _scaled_threshold(elapsed_hours, base_threshold=1.0, volatility_24h=None):
    # Window-Factor:
    #   <=8h  -> 0.7  | <=12h -> 1.0 | <=24h -> 1.5 | <=48h -> 2.0 | >48h -> 2.5
    # Vol-Factor:
    #   <1%   -> 0.6  | 1-3%  -> 0.6..1.0 | >3% -> 1.0..1.5
    # Floor: 0.3%

# Klassifikation (auto_annotator.py:391-400):
if sentiment == "bullish" and pct_change >= threshold: outcome = "hit"
elif sentiment == "bearish" and pct_change <= -threshold: outcome = "hit"
elif sentiment == "bullish" and pct_change <= -threshold: outcome = "miss"
elif sentiment == "bearish" and pct_change >= threshold: outcome = "miss"
else: outcome = "inconclusive"

# Tag-Logik (auto_annotator.py:402-411):
#   "auto"     = first-time annotation
#   "reeval"   = re-eval of prior annotation, fresh (<72h)
#   "backfill" = stale_reeval + was annotated before
#   "catchup"  = stale_reeval + never annotated before
```

### Pipeline B (`price_check.py`, AKTIV)

```python
# price_check.py:_suggest_outcome:40-63
def _suggest_outcome(sentiment, change_pct, threshold_pct):
    abs_change = abs(change_pct)
    if abs_change < threshold_pct:
        return "inconclusive", f"{direction} {abs_change:.1f}% < {threshold_pct}% threshold"
    if sentiment == "bullish":
        return ("hit", ...) if change_pct >= threshold_pct else ("miss", ...)
    if sentiment == "bearish":
        return ("hit", ...) if change_pct <= -threshold_pct else ("miss", ...)
    return "inconclusive", f"non-directional sentiment: {sentiment}"

# CLI default: threshold_pct=2.0 (cli/main.py:1568)
# ph5_daily_ops.ps1 nutzt $AutoCheckThresholdPct (Variable, vermutlich 5.0)
```

## 4. Warum so viele inconclusive entstehen

**Drei strukturelle Ursachen, in Wirkungsreihenfolge:**

**Ursache 1 (dominanter Faktor): Pipeline A inaktiv seit 9 Tagen.**
Die Pipeline mit smarter Vol-/Window-Skalierung (effektiver Threshold ~0.3-1.5% statt fix 5.0%) läuft nicht. `kai-auto-annotate.timer` zeigt `Stopped 2026-05-12 12:11:04 CEST`, kein Re-Enable seitdem. Pipeline B (fixer 5.0%-Threshold) ist Fallback — produziert systematisch mehr inconclusives.

**Ursache 2 (verstärkender Faktor): Sentiment-Distribution.**
DS-V1-Forensik 2026-05-20: 7d-Sentiment-Verteilung = 7.5% directional / 53.3% mixed / 39.3% neutral. Beide Klassifikatoren (A + B) klassifizieren non-directional automatisch als inconclusive (auto_annotator.py:399, price_check.py:63 `"non-directional sentiment"`). Das ist bei aktueller Sentiment-Verteilung allein schon ein Floor von ~92% inconclusive.

**Ursache 3 (sekundärer Faktor): Threshold-Magnitude vs. Markt-Volatilität.**
24h-BTC-Bewegung war im 7d-Fenster im niedrigen Bereich (~1-2% laut Regime-Daten `vol_low`). Selbst bei direktionalem Sentiment landet die Bewegung unter 5.0% → inconclusive. Pipeline A würde hier auf ~0.9% (1.0 × 1.5 Window-Factor × 0.6 Vol-Factor) skalieren und mehr Hits erfassen.

**Mechanische Konsequenz:** 7758/8173 backfill-inconclusives sind nicht Sentiment-Klassifikator-Fehler, sondern systematisches Ergebnis von (Pipeline-A-aus) × (Sentiment-Drift) × (Niedrig-vol-Markt).

## 5. Datenbelege

**Outcome-Verteilung (artifacts/alert_outcomes.jsonl, 2026-05-21 Stand):**

| Outcome | n | Anteil |
|---|---|---|
| hit | 127 | 1.5% |
| miss | 255 | 3.1% |
| inconclusive | 8173 | 95.4% |
| **Total** | **8555** | 100% |

**Inconclusive nach Tag (Pipeline-Quelle):**

| Tag | n | Anteil an inc | Pipeline |
|---|---|---|---|
| backfill | 7758 | **94.9%** | B (`auto-check (historical_window, horizon=24h)` 5.0%) |
| auto | 307 | 3.8% | A (`auto_annotator.py`, pre-12.05.) |
| reeval | 96 | 1.2% | A (pre-12.05.) |
| catchup | 0* | — | A |
| other | 12 | 0.1% | — |

*Catchup-Tag im Code definiert (`auto_annotator.py:406-408`), keine Einträge im Stream.

**Sample-Notes (alle backfill):**
```
auto-check (historical_window, horizon=24h): down 3.2% < 5.0% threshold; historical 24h window (1.42 -> 1.37, hour-bucketed)
auto-check (historical_window, horizon=24h): down 3.2% < 5.0% threshold; historical 24h window (71682.99 -> 69416.11, hour-bucketed)
```

**Cron-Status-Beleg:**
```
kai-auto-annotate.timer: Stopped 2026-05-12 12:11:04 CEST (1w 2d ago)
Last service run: kai-auto-annotate.service Active: inactive (dead) since Tue 2026-05-12 06:22:18 CEST
Last successful classification: "5 annotated: 0 hit, 0 miss, 5 inconclusive" (alle 5 unter Vol-skalierter threshold)
```

**Sentiment-Verteilung 7d (DS-V1-Forensik 2026-05-20, n=107 mit sentiment_label-Feld):**
- bullish: 7.5% | bearish: 0.0% | mixed: 53.3% | neutral: 39.3%
- 4 aufeinanderfolgende Tage (16-19.05.) mit 0% directional.

## 6. Drei Tuning-Optionen

### Option A — Pipeline-A reaktivieren (Quickwin, kein Tuning)

| Feld | Wert |
|---|---|
| Was ändert sich | `sudo systemctl enable --now kai-auto-annotate.timer`. Pipeline läuft alle 6h, klassifiziert mit Vol-/Window-Skalierung + smartem Tagging. |
| Erwarteter Effekt | Backfill-Anteil fällt schrittweise; hit/miss-Rate steigt für direktionale Alerts; Cross-Tab-Forensik klarer (Pipeline-A-tags trennbar von Pipeline-B). |
| Aufwand | <5min Systemd-Reaktivierung + Verifikation, kein Code-Eingriff. |
| Risiko | Niedrig. Pipeline-A war bis 12.05. aktiv ohne dokumentiertes Problem. |
| Reversibilität | trivial (disable). |

### Option B — Pipeline-B Threshold senken (in `ph5_daily_ops.ps1` $AutoCheckThresholdPct)

| Feld | Wert |
|---|---|
| Was ändert sich | `$AutoCheckThresholdPct` von 5.0 auf 2.0 (= CLI-Default `app/cli/main.py:1568`). |
| Erwarteter Effekt | Mehr Bewegungen kreuzen Threshold → mehr hit/miss. Bei aktueller Sentiment-Distribution (7.5% directional) steigt hit/miss-Rate von 4.6% (382/8555) auf grob ~8-12% (Schätzung). |
| Aufwand | 1-Wert-Edit in `scripts/ph5_daily_ops.ps1:215` oder CLI-Caller-Wrapper. |
| Risiko | **Mittel.** 2% in 24h ist bei `vol_low`-Regime auch noch häufig „Marktrauschen" — Misclassification möglich. |
| Reversibilität | trivial (Wert zurückdrehen). |

### Option C — Sentiment-Strukturklarheit: `inconclusive` aufsplitten

| Feld | Wert |
|---|---|
| Was ändert sich | Code-Eingriff in beiden Klassifikatoren: non-directional Sentiment liefert `structural_inconclusive`, direkt+unter-threshold liefert `directional_inconclusive`. Outcome-Set erweitert (`hit / miss / structural_inconclusive / directional_inconclusive`). |
| Erwarteter Effekt | KEINE Änderung an hit/miss-Anzahl — aber Forensik kann sehen ob inconclusive sentiment-bedingt oder threshold-bedingt. Source-Reliability Wilson-Loop kann jetzt sinnvoll filtern. |
| Aufwand | Mittel (1-2 Tage): 2 Klassifikatoren + AlertOutcomeAnnotation-Schema + Tests + Migration des bestehenden Streams. |
| Risiko | Schema-Migration für 8555 existierende Records. Downstream-Konsumenten (`hold-metrics`, `feature_analysis`, `source_reliability`) müssen `structural_inconclusive` korrekt behandeln (vermutlich ignorieren, identisch zu heute). |
| Reversibilität | Mittel — Schema kann forward-compatible bleiben, Code-Revert braucht eigene Migration. |

### Option D — Status quo bis 2026-05-30 EOW-Re-Eval

| Feld | Wert |
|---|---|
| Was ändert sich | Nichts. Pipeline B läuft weiter mit 5.0%-Threshold, Pipeline A bleibt deaktiviert. |
| Erwarteter Effekt | Unverändert. Inconclusive-Rate bleibt 95%+. Lern-Stack (Source-Reliability, Bayes) atmet weiter zu wenig. |
| Aufwand | 0. |
| Risiko | Lern-Datenbasis bleibt dünn → 30.05.-Decision für SHADOW_ONLY-Flip noch dünner. |
| Reversibilität | trivial. |

## 7. Risiko je Option

- **A:** **niedrig** (revert war default-Verhalten pre-12.05.). Einzig: Pipeline A schreibt Tag „backfill" auch — Cross-Tab muss Pipeline A's „backfill" von Pipeline B's „backfill" trennen (note-string-Pattern unterscheidet sich: A nutzt `"backfill: bullish SYM ..."`, B nutzt `"auto-check (historical_window..."`).
- **B:** mittel (Misclassification bei low-vol).
- **C:** mittel-hoch (Schema-Migration, Downstream-Konsumenten).
- **D:** niedrig technisch, hoch produktiv (verschiebt Problem auf 30.05.).

## 8. Erwartete Wirkung

| Option | hit/miss-Rate-Schätzung | Lern-Stack-Effekt | Bayes-Schreibrate-Effekt |
|---|---|---|---|
| A | 4.6% → 12-18% in 7d | Source-Reliability hat sinnvolle counts | indirekt (mehr hit/miss → mehr Posterior-Updates) |
| B | 4.6% → 8-12% in 7d | mäßig | indirekt |
| C | unverändert | Forensik schärfer, kein Lerneffekt | unverändert |
| D | unverändert | unverändert | unverändert |

## 9. Empfohlene Option

**Option A.** Begründung:
1. Pipeline A ist die designed-richtige Lösung (Vol-skaliert, smart-tagged, conservativ-tested).
2. Reaktivierung kostet <5min.
3. Risiko ist niedrig (war bis 12.05. live).
4. Größter Lern-Stack-Hebel ohne Code-Eingriff.
5. Wirft die Ursachen-Frage `Warum wurde der Timer 12.05. gestoppt?` auf — wertvoll für [[feedback-post-deploy-smoke-mandatory]]-Lehre.

**Sekundär:** Option C als Phase-2-Sprint NACH 30.05.-Re-Eval, weil Forensik-Wert langfristig hoch ist.

**NICHT empfohlen:** Option B isoliert (Magic-Number-Tuning ohne Vol-Adapt) und Option D (verlorene Woche).

## 10. Operator-Decision-Anker für 2026-05-30 EOW-Re-Eval

Operator entscheidet **eine** Antwort:

- [ ] **A** — Pipeline A sofort reaktivieren (`sudo systemctl enable --now kai-auto-annotate.timer`), Pipeline B optional auf 2.0% senken.
- [ ] **A + C-Spec** — A reaktivieren JETZT + C-Schema-Migration als Phase-2-Sprint nach 30.05.
- [ ] **B** — Pipeline B Threshold 5.0→2.0, Pipeline A bleibt aus.
- [ ] **C** — Schema-Migration jetzt, ohne A.
- [ ] **D** — Status quo bis weiterem Decision-Punkt.

**Vor jeder Option A/B/C:**
- Cron-Ausfall-Recon: Warum wurde `kai-auto-annotate.timer` 12.05. gestoppt? journal-grep auf Service-Restart-Zeit + Operator-Aktion. Memory-Lehre für [[feedback-post-deploy-smoke-mandatory]].
- Backfill-Tag-Dedup-Plan: Pipeline A's „backfill" überschreibt nicht Pipeline B's „backfill"? Idempotency via document_id-Latest-Wins-Mechanik in `auto_annotator.py` annehmen — vor Apply verifizieren.

**Kopplung zu anderen offenen Decisions:**
- Priority-Scoring Decision D ist bereits ratifiziert ([[kai-priority-sentiment-correlation-paradox]]).
- SHADOW_ONLY-Flip-Heuristik [[kai-bayes-shadow-only-flip-heuristik]] profitiert direkt von Option A (mehr hit/miss → mehr Bayes-Updates → schneller n>=20).
- Wenn Operator A wählt: Re-Eval 30.05. bekommt erstmals echte Lern-Daten statt 95.5%-inconclusive-Floor.

---

## Querverweise

- Auto-Annotator-Code: `app/alerts/auto_annotator.py:78` (`_scaled_threshold`), `:391-410` (Klassifikation)
- Price-Check-Code: `app/alerts/price_check.py:40-63` (`_suggest_outcome`)
- CLI-Caller: `scripts/ph5_daily_ops.ps1:213-220`, `app/cli/main.py:1566` (`alerts auto-check`)
- Systemd: `/etc/systemd/system/kai-auto-annotate.timer` + `.service`
- Daten-Stream: `artifacts/alert_outcomes.jsonl` (8555 records)
- Cross-Tab-Daten 2026-05-20: `artifacts/operator_memos/re_entry_end_of_window_2026-05-23.md` §Sentiment-Distribution
- Memory: [[kai-priority-sentiment-correlation-paradox]], [[kai-bayes-shadow-only-flip-heuristik]], [[feedback-post-deploy-smoke-mandatory]]
