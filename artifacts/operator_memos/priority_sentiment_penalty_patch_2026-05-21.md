# Priority-Sentiment-Penalty Patch — Decision-Variante A' für EOW-Review 2026-05-23

**Status:** Patch im Branch `claude/priority-sentiment-penalty-20260521`, **nicht gemerged, nicht Pi-deployed**. Optional zur Operator-Decision am 23.05.-EOW-Review parallel zu Brief-Options A/B/C/D.

**Auftrag:** Operator wählte heute (21.05.) im Live-Chat "B" zwischen meinen Mid-Session-Optionen `A (Sentiment als 6. Faktor)` / `B (Multiplikator/Penalty auf neutral/mixed)`. Erst danach habe ich entdeckt, dass auf Pi bereits ein `priority_scoring_decision_brief_2026-05-23.md` vorlag, dessen Option-Letters andere Semantik haben. Dieser Patch entspricht **keiner** der Brief-Optionen 1:1 — er ist eine **Variante A'** (Single-Pfad, Sentiment-Eingriff, aber per Penalty statt per Faktor-Rebalancing).

Operator behält am 23.05. die volle Wahl zwischen Brief-A, Brief-B, Brief-C, Brief-D und diesem A' — der Patch existiert nur als reviewbare Code-Variante, nicht als Vorentscheidung.

---

## Was dieser Patch macht

`app/analysis/scoring.py`:

```python
_SENTIMENT_CLARITY_PENALTY: int = 2
_SENTIMENT_PENALTY_LABELS = frozenset({SentimentLabel.NEUTRAL, SentimentLabel.MIXED})

# nach actionable-bonus, vor spam-cap:
sentiment_penalized = result.sentiment_label in _SENTIMENT_PENALTY_LABELS
if sentiment_penalized:
    priority = max(1, priority - _SENTIMENT_CLARITY_PENALTY)
```

`PriorityScore`-Dataclass um Audit-Feld `is_sentiment_penalized: bool` erweitert. Reihenfolge: raw → priority-map → actionable-bonus → **sentiment-penalty** → spam-cap (spam-cap bleibt finaler Floor).

## Unterschied zu Brief-Option A

| Aspekt | Brief-A (6. Faktor) | A' (Penalty) |
|---|---|---|
| Mechanismus | `sentiment_decisive ∈ {1.0, 0.4, 0.0}` × 0.10, andere Gewichte rebalancieren auf 0.27/0.27/0.18/0.13/0.05 | Integer-Penalty `-2` auf neutral/mixed nach Bonus |
| Effekt auf p=10-neutral mit max scores | priority ≈ 8-9 (raw 0.9 → 9) | priority = 8 (10 - 2) |
| Effekt auf p=10-mixed mit max scores | priority ≈ 9 (raw 0.94 → 9) | priority = 8 (10 - 2) |
| Effekt auf direktionale News | priority leicht höher (Sentiment-Bias) | unverändert |
| Bestehende Gewichte | rebalanciert → Audit-Stream-Vergleichbarkeit bricht | unverändert → bestehende Audit-Historie bleibt vergleichbar |
| Audit-Feld | kein neues Feld nötig | `is_sentiment_penalized` zusätzlich |
| Test-Aufwand | 4-6 bestehende Tests umrechnen | 4 bestehende Tests `sentiment_label=BULLISH` setzen + 7 neue Penalty-Tests |
| Reversibilität | hoch (Gewichte-Konstanten) | sehr hoch (1 Flag in `_SENTIMENT_PENALTY_LABELS`) |

**Vorteil A':** Bestehende Gewichte unverändert → Audit-Stream-Vergleichbarkeit bleibt erhalten. Eindeutiger Audit-Trail via `is_sentiment_penalized`. Schärferer empirischer Effekt: p=10-neutral fällt deterministisch auf p=8, nicht erst auf p=9.

**Nachteil A':** Nicht-monoton in `sentiment_score`-Wert (Penalty ist Label-basiert, nicht Score-basiert). Eine konfidenz-schwache `bullish`-Lesart bekommt keinen Penalty, eine konfidenz-starke `neutral`-Lesart den vollen.

## Erwartete Empirie-Verschiebung

Berechnet auf dem 1826-Einträge-Cross-Tab aus DS-20260520-V1:

| Bucket | Vorher | A' (geschätzt) |
|---|---|---|
| p ≥ 10 Anzahl | 418 | ≈ 182 |
| p ≥ 10 directional% | 43.5% | ≈ 93% |
| p = 8/9 Anzahl | 475 | ≈ 711 (gewachsen um ~236 ehem. p=10-Neutrals) |
| p = 8/9 directional% | 87.8% | ≈ 70% (verdünnt durch eingedrungene Neutrals) |

Empirischer Test: nach Deploy 7d Cross-Tab erneut ziehen.

## Was dieser Patch NICHT macht

- Keine Bridge-Änderung (`_maybe_trigger_paper_trade` bleibt 1:1).
- Keine LLM-Prompt-Änderung (`recommended_priority`-Instructions in `prompts.py:87` bleiben unverändert).
- Keine DB-Migration.
- Kein neues AnalysisResult-Field.
- Keine `EXECUTION_PAPER_MIN_PRIORITY`-Änderung.

## Tests

`tests/unit/test_scoring.py`:
- 4 bestehende Tests auf `sentiment_label=SentimentLabel.BULLISH`-Default in `_make_result()` umgestellt (sonst hätten sie den Penalty selbst getriggert und Erwartungen wären gebrochen).
- 7 neue Tests:
  - `test_sentiment_penalty_neutral_subtracts_two` — perfect_score-Setup + NEUTRAL → priority=8
  - `test_sentiment_penalty_mixed_subtracts_two` — analog für MIXED
  - `test_sentiment_penalty_does_not_fire_on_bullish` — Regression-Guard
  - `test_sentiment_penalty_does_not_fire_on_bearish` — Regression-Guard
  - `test_sentiment_penalty_floor_at_one` — Penalty kann nicht unter 1
  - `test_sentiment_penalty_spam_cap_still_binds` — Spam-Cap bleibt finaler Floor
  - `test_priority_paradox_regression` — DS-20260520-NEW-1 Regression-Guard

`tests/unit/test_analysis_rules.py` unverändert — die bestehenden Tests sind tolerant genug (`assert >= 8` statt `== 10`), Penalty-Effekt ändert nichts an deren Aussage.

**Test-Run:** `pytest tests/unit/test_scoring.py tests/unit/test_analysis_rules.py tests/unit/test_analysis_pipeline.py tests/unit/test_pipeline_service.py tests/unit/test_pipeline_fallback.py tests/unit/test_shadow_run.py tests/unit/test_research_signals.py tests/unit/test_research_briefs.py tests/unit/test_document_repository.py tests/unit/cli/test_analyze_pending_alerts.py tests/unit/test_api_research.py tests/unit/test_fetch_item.py` — **139 passed in 71s**.

## Decision-Anker (parallel zu Brief)

Operator wählt am 23.05. **eine** Antwort:

- [ ] **Brief-A** — Brief-Option A wie spezifiziert (6. Faktor, Gewichte-Rebalancing).
- [ ] **A'** — **Dieser Patch.** Penalty-Variante, bestehende Gewichte intakt, scharfes -2.
- [ ] **Brief-B** — Phase-2-Sprint separater trade_priority_score (5-7d).
- [ ] **Brief-C** — Bridge-Workaround (Brief empfiehlt explizit gegen).
- [ ] **Brief-D** — Status quo bis 2026-05-30.

## Querverweise

- Voll-Inspection: `artifacts/operator_memos/priority_scoring_inspection_2026-05-20.md`
- Decision-Brief: `artifacts/operator_memos/priority_scoring_decision_brief_2026-05-23.md`
- Memory-Pin: `[[kai-priority-sentiment-correlation-paradox]]`
- Branch: `claude/priority-sentiment-penalty-20260521`
- HEAD-Base: `1226353c` (origin/claude/p7/reentry-ia-codex-cycle Stand 21.05. 07:00 UTC)
