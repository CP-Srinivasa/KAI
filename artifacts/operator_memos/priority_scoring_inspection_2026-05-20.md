# Priority-Scoring-Code-Inspection — 2026-05-20

**Auftrag:** DS-20260520-NEW-1 (P0 vor 2026-05-23 End-of-Window). Klären, warum das `EXECUTION_PAPER_MIN_PRIORITY=10`-Gate empirisch nur 43.5% direktionales Material enthält, während das p=8/9-Band 87.8% direktional ist (Cross-Tab aus DS-20260520-V1).

**Verfasser:** Claude Code Mid-Window-Forensik. Read-only Inspection, kein Code-Eingriff. Schreib-Vorschläge stehen am Ende als Operator-Decision-Vorlage.

---

## 1. Pipeline-Pfad der `priority`-Berechnung

```
LLM-Provider (app/analysis/internal_model/provider.py)
  ↓ produces LLMAnalysisOutput inkl. recommended_priority (1-10) als Suggestion
  ↓
DocumentMutator.apply_to_document() (app/analysis/pipeline.py:402-415)
  ↓ ruft compute_priority(res, spam_probability=spam_prob)
  ↓ ÜBERSCHREIBT res.recommended_priority MIT formel-berechneter priority
  ↓ schreibt document.priority_score = priority.priority
  ↓
alert_audit.jsonl bekommt diesen Wert ins `priority`-Field
  ↓
EXECUTION_PAPER_MIN_PRIORITY-Gate (.env: =10) filtert hier
  ↓
_maybe_trigger_paper_trade() (app/pipeline/service.py:48-109)
  ↓ filtert NACHGELAGERT auf sentiment_label ∈ {bullish, bearish}
  ↓
trading_loop / paper_engine
```

**Schlüssel-Erkenntnis:** Der `priority`-Wert, den das Gate sieht, ist **immer** das Ergebnis aus `compute_priority()` — die LLM-Suggestion `recommended_priority` wird in `pipeline.py:414` durch den Formel-Wert überschrieben. Die LLM-Suggestion hat keinen Einfluss auf das Gate.

## 2. Die Scoring-Formel (`app/analysis/scoring.py:43-86`)

```python
def compute_priority(result, *, spam_probability=0.0) -> PriorityScore:
    actionable_value = 1.0 if result.actionable else 0.0
    quality = 1.0 - spam_probability

    raw = (
        result.relevance_score * 0.30   # is this even about our topics?
      + result.impact_score    * 0.30   # what's the potential market effect?
      + result.novelty_score   * 0.20   # is this new information?
      + actionable_value       * 0.15   # does it require a decision?
      + quality                * 0.05   # quality signal
    )
    priority = max(1, min(10, round(raw * 9) + 1))

    if result.actionable and priority < 10:
        priority = min(10, priority + 1)   # Actionability-Bonus +1
    if spam_probability > 0.70:
        priority = min(priority, 3)        # Spam-Cap
    return PriorityScore(...)
```

**Was IN der Formel steht:**
- `relevance_score` (LLM-Output, 0-1): topic match
- `impact_score` (LLM-Output, 0-1): market-wide effect potential
- `novelty_score` (LLM-Output, 0-1): newness of information
- `actionable` (LLM-Output, bool): "requires trading/position review"
- `spam_probability` (Pipeline-Aux, 0-1): quality penalty
- Bonus: `actionable=True` → +1 priority
- Cap: `spam > 0.7` → priority ≤ 3

**Was NICHT in der Formel steht (verifiziert via Grep über das gesamte Repo):**
- `sentiment_label` (bullish/bearish/neutral/mixed) — **0 Gewicht**
- `sentiment_score` (numerisch -1 bis +1) — **0 Gewicht**
- `confidence_score` (LLM-Self-Assessment) — **0 Gewicht**
- `directional_eligible` (Bridge-Vorbedingung) — **0 Gewicht**

## 3. Was das LLM zu `recommended_priority` instruktiert wird

`app/analysis/prompts.py:87-93`:

> recommended_priority:
>   1 = low priority, 10 = immediate review required.
>   Use 8-10 only for breaking news or major structural events.
>   **IMPORTANT: A high priority requires BOTH high impact AND forward_catalyst timing.**
>   Backward reports and speculation should never exceed priority 6.

**Kein Sentiment-Constraint im Prompt.** Das LLM wird angewiesen, Priority an "impact × forward_catalyst" zu binden. Das ist konsistent mit der Formel, aber ignoriert die Frage, ob das Event direktional klar ist.

## 4. Mechanik des Cross-Tab-Paradoxes

Mit dieser Formel + diesem Prompt landet im p≥10-Bucket alles, was die LLM als „high topic-match, high market-wide impact, novel, actionable" einstuft. Das sind empirisch (Pi-Daten 2026-05-20):

| Inhalts-Typ | Typische Sentiment-Lesart | Typische impact | Typische priority |
|---|---|---|---|
| SEC/Regulatory action | meist `neutral` (unklar pro/contra Markt) | hoch (alle reagieren) | 8-10 |
| ETF-Listing / Exchange-Launch | meist `neutral` oder `mixed` | hoch | 9-10 |
| Tech-Update / Hardfork | meist `neutral` | mittel-hoch | 7-9 |
| Macro-Data Release (CPI, Fed) | meist `mixed` | hoch | 9-10 |
| Whale-Bewegung (specific) | oft `bullish`/`bearish` | mittel | 7-8 |
| Fund-Manager-Statement | oft `bullish`/`bearish` | niedrig-mittel | 6-8 |
| Analyst-Forecast | `bullish`/`bearish` | niedrig | 5-7 |
| Trading-Setup-Post | `bullish`/`bearish` | niedrig | 4-6 |

**Daraus folgt mechanisch:** Direktionale News landen statistisch im p=8/9-Band, neutral-aber-impact-stark im p=10-Band. Das Cross-Tab (p≥10: 50% neutral, p=8/9: 87.8% directional) ist nicht ein Bug, sondern eine **deterministische Konsequenz** der Scoring-Designentscheidung.

**Heute's Bayes-Eintrag bestätigt das:** Die cryptoslate-News "CME launching VIX-style fear trade to bitcoin" (priority=10, bullish) ist ein Grenzfall — strukturelles Event (CME = institutional), aber das LLM hat es als `bullish` klassifiziert (vermutlich wegen „fear trade to bitcoin" framing = direktionaler Hinweis). Die Mehrheit der p=10-News wäre als neutral klassifiziert worden.

## 5. Designkonflikt

Der File-Header von `scoring.py` ist explizit:

> Computes a single priority integer (1–10) from the scored fields of an AnalysisResult. **Used to rank documents for alerts and research packs.**

Das Scoring wurde als **Newsletter-/Research-Ranking** gebaut, nicht als Trade-Trigger-Score. Der Re-Use desselben Werts als Trade-Gate-Filter via `EXECUTION_PAPER_MIN_PRIORITY` ist ein nachträglicher Use-Case, der das ursprüngliche Design unterläuft:

- **Newsletter-Use-Case** will: was sollten wir den Operator zuerst sehen lassen? → Antwort: alles mit hohem impact, aktuelles, aktionsrelevantes. Sentiment-Klarheit ist hier sekundär (man kann ein neutrales aber wichtiges Event durchaus zuerst lesen wollen).
- **Trade-Trigger-Use-Case** will: bei welchen Signalen ist die direktionale Lesart so klar, dass ein Trade gerechtfertigt ist? → Antwort: nur klar bullish/bearish mit hoher Konfidenz. Sentiment-Klarheit ist hier zwingend.

Die Bridge `_maybe_trigger_paper_trade` versucht das nachträglich zu fixen (Filter auf `bullish/bearish`), kommt aber gar nicht zum Zug, weil das p≥10-Gate die direktionalen Signale ins p=8/9-Band gedrückt hat.

## 6. Lösungsoptionen (Operator-Decision vor 2026-05-23)

### Option A — Sentiment-Klarheit in `compute_priority()` einbauen (minimal-invasiv)

**Patch-Idee:** Sechster Faktor `sentiment_decisive` mit niedrigem Gewicht, andere Gewichte rebalancieren.

```python
# Beispiel-Werte, vor Commit zu validieren
sentiment_decisive = {
    "bullish": 1.0,
    "bearish": 1.0,
    "mixed":   0.4,
    "neutral": 0.0,
}.get(result.sentiment_label.value.lower(), 0.0)

raw = (
    result.relevance_score   * 0.27
  + result.impact_score      * 0.27
  + result.novelty_score     * 0.18
  + actionable_value         * 0.13
  + sentiment_decisive       * 0.10
  + quality                  * 0.05
)
```

**Pro:** ~30min Patch + 4-6 Test-Anpassungen. Single-priority-Pfad bleibt erhalten. Direkt messbar: erwartetes Cross-Tab-Shift = p≥10 mehr bullish/bearish, p=8/9 weniger Verzerrung.

**Kontra:** Vermischt zwei Use-Cases (newsletter-rank vs. trade-trigger) weiterhin in einer Formel. Newsletter-Use-Case bekommt jetzt eine Sentiment-Bias-Komponente, die er strenggenommen nicht braucht.

**Risiko:** Bestehende Tests in `tests/unit/test_scoring*.py` brechen wahrscheinlich (Test-Daten haben oft neutral-Sentiment + erwarten priority=10). Audit-Stream `alert_audit.jsonl` bekommt neue Verteilung — Historie-Vergleiche werden schwieriger.

**Reversibilität:** Hoch. Gewichte sind Konstanten in scoring.py, leicht zurückzudrehen.

### Option B — Separater `trade_priority_score` (sauberste Architektur)

**Patch-Idee:** Neue Funktion `compute_trade_priority(result)` in `scoring.py` mit Sentiment-zentrierter Formel. `EXECUTION_PAPER_MIN_PRIORITY` operiert auf `trade_priority_score`, `recommended_priority` bleibt newsletter-orientiert.

```python
def compute_trade_priority(result) -> int:
    if result.sentiment_label not in (BULLISH, BEARISH):
        return 0   # nicht trade-eligible
    score = (
        sentiment_confidence       * 0.30
      + result.relevance_score     * 0.20
      + result.impact_score        * 0.20
      + result.novelty_score       * 0.15
      + actionable_value           * 0.15
    )
    return max(1, min(10, round(score * 9) + 1))
```

**Pro:** Klare Trennung. Newsletter-Ranking bleibt unverändert. Trade-Trigger-Pfad hat eigenes Audit-Anchor. Bridge `_maybe_trigger_paper_trade` kann den Sentiment-Filter abschaffen, weil `trade_priority_score=0` für non-directional schon vor dem Gate filtert.

**Kontra:** Größerer Eingriff: neuer Field in `AnalysisResult` + DB-Migration für `document.trade_priority_score` + neue Audit-Schema-Version + Tests + Doku.

**Risiko:** Mid-Window-Zeitfenster eng (3 Tage bis EOW-Review). Realistisch ist Spec + Test-Plan in 1 Tag, Implementation 1-2 Tage, Pi-Deploy + Audit-Validierung 1 Tag.

**Reversibilität:** Mittel. Schema-Migration ist forward-compatible, aber Rollback braucht eigene Migration.

### Option C — Bridge erweitern, Scoring unverändert (Workaround)

**Patch-Idee:** `_maybe_trigger_paper_trade` operiert auf `sentiment_label ∈ {bullish, bearish}` UND niedrigerem Priority-Floor (z.B. priority ≥ 7), aber nur wenn `sentiment_confidence > X`.

```python
# pseudo
if sentiment in {BULLISH, BEARISH}:
    if priority >= EXECUTION_PAPER_MIN_PRIORITY:
        trigger()
    elif priority >= 7 and sentiment_confidence > 0.75:
        trigger()
```

**Pro:** Kleinster Code-Eingriff. Scoring-Formel bleibt unverändert. Reaktion auf Cross-Tab-Befund ohne Datenmodell-Änderung.

**Kontra:** Verlagert das Problem, statt es zu lösen. Zwei Gates statt einem. Wenn das Scoring eines Tages umgebaut wird, ist die Bridge-Logik widersprüchlich. Audit-Stream wird komplizierter zu interpretieren.

**Risiko:** Magic-Number-Stack. Falsch eingestelltes `sentiment_confidence`-Threshold kann false-positive-Trades produzieren — genau die Klasse von Risiko, die der Re-Entry-Konservatismus vermeiden will.

**Reversibilität:** Hoch. Branch-Logik in service.py, leicht entfernbar.

## 7. Empfehlung (zur Operator-Decision)

**Mid-Window-Zeitfenster (3 Tage bis 23.05.):**

- **Option A** ist die einzige in 1-2 Tagen umsetzbare Option, wenn vor EOW eine Messung gewünscht ist.
- **Option B** ist die richtige Langfrist-Architektur, sollte aber NICHT in Hau-Ruck-Patch geschehen — Phase-2-Sprint nach 2026-05-23.
- **Option C** löst das Problem nicht und sollte vermieden werden.

**Vorschlag (vor Operator-Sign-off):**

1. **Vor 2026-05-23:** Inspection-Memo (dieses File) als EOW-Pre-Fill-Beleg sichern. **Kein Patch** im Mid-Window — Reaktion in einem fundamentalen Scoring-Mechanismus ohne Window-Daten ist hektisch.
2. **2026-05-23 EOW-Review:** Operator entscheidet zwischen Option A (Quickwin) und Option B (Architekturschritt). Wenn Option A: Spec + PR im Re-Entry-Phase-2. Wenn Option B: Sprint-Definition für 5-7 Tage.
3. **Bis dahin:** keine weiteren Gate-ADRs auf `EXECUTION_PAPER_MIN_PRIORITY`. Memory-Pin `[[kai-priority-sentiment-correlation-paradox]]` verhindert das.

## 8. Datenquellen

- Pi `git rev-parse HEAD` = `f96f74fa` (2026-05-20 Inspection).
- `app/analysis/scoring.py` (118 LoC, vollständig inspiziert).
- `app/analysis/pipeline.py` Zeilen 402-420 (DocumentMutator.apply_to_document).
- `app/analysis/validation.py` (69 LoC, Sentiment-Konsistenz-Checks, kein Priority-Input).
- `app/analysis/prompts.py` Zeilen 87-93 (LLM-Instruktion).
- `app/analysis/internal_model/provider.py` (LLM-Output-Default).
- `app/alerts/service.py:405` (zweiter Call-Site für compute_priority).
- `app/pipeline/service.py:48-109` (Bridge-Filter).
- Empirisches Cross-Tab aus DS-20260520-V1.

## 9. Was diese Inspection NICHT tut

- Kein Patch, kein Branch, kein PR.
- Keine Code-Änderung.
- Keine ADR-Ratifizierung (Optionen A/B/C sind Decision-Vorlage, nicht Decision).
- Keine Annahme über Operator-Präferenz.

**Status:** ✅ Befund vollständig. Operator-Sign-off auf Option A/B/C steht aus.
