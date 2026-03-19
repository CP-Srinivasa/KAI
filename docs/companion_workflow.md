# Companion Model Training & Evaluation Workflow

Dieses Dokument beschreibt den produktiven Workflow, um das lokale interne **Companion Model** (Tier 2 Analyst) auf Basis der externen **Teacher Models** (Tier 3) zu trainieren und gegen sie zu evaluieren. Der Prozess ist vollständig in die bestehende CLI und Architektur integriert.

---

## 1. Datensammlung (The Teacher Corpus)

Die Datensammlung erfolgt passiv im laufenden Betrieb. Jeder Feed-Ingest, der über einen externen LLM-Provider (z. B. `openai` oder `anthropic`) analysiert wird, schreibt folgende essenzielle Daten in das `CanonicalDocument`:
- Alle numerischen Scores (Sentiment, Relevance, Priority, etc.).
- Das deduktive Denken des Modells (`explanation_short` und `explanation_long`), welches in `document_metadata` gesichert wird.
- Den hart zugewiesenen `provider` String.

**Befehl im laufenden Betrieb:**
```bash
# Läuft regulär auf der Node als Cron-Job
APP_LLM_PROVIDER=openai trading-bot research analyze
```

---

## 2. Dataset Export (Fine-Tuning Preparation)

Um das Companion-Modell zu finetunen (z.B. mittels Llama-Factory oder Unsloth), exportieren wir die analysierten Dokumente als JSONL. 
Diese Daten enthalten die Chain-of-Thought (`co_thought`) Argumentation des Teachers.

**Workflow Command:**
```bash
# Extrahiere n-Dokumente vom Teacher für das Instruction-Tuning
trading-bot research dataset-export dataset.jsonl --provider openai --limit 10000
```

**Target Format (`assistant` Response):**
```json
{
  "co_thought": "Aufgrund positiver Zinsentscheide und einem starken operativen Quartal stufe ich das Sentiment als bullish ein.",
  "sentiment_label": "bullish",
  "sentiment_score": 0.8,
  "relevance_score": 0.9,
  "impact_score": 0.6,
  "novelty_score": 0.5,
  "priority_score": 8,
  "spam_probability": 0.0,
  "market_scope": "global"
}
```

---

## 3. Evaluation Gate (Model Promotion)

Nach dem Training darf das lokale Modell ("Internal Provider") niemals ungetestet in Produktion gehen. Wir validieren es gegen ein Set von Teacher-Dokumenten (Holdout-Set), indem die puren Texte lokal neu analysiert werden und die Score-Abstände (Mean Squared Error, Accuracy) verglichen werden.

**Workflow Command:**
```bash
# Starte Live-Evaluierung eines lokalen Modells
trading-bot research evaluate --limit 100 --teacher-provider openai --companion-provider internal
```

### Die vier harten Evaluations-Metriken:
| Metrik | Zielwert | Bedeutung |
|---|---|---|
| **Sentiment Accuracy** | `>= 85%` | Trefferquote der Bullish/Bearish/Neutral Labels |
| **Actionable Accuracy** | `>= 80%` | Korrekte Zuordnung, ob `priority_score >= 7` ist |
| **Priority MSE** | `<= 1.0` | Mittlere quadratische Abweichung im 1-10 Priority-Score |
| **Relevance MSE** | `<= 0.05` | Mittlere quadratische Abweichung im 0.0-1.0 Relevance-Score |

Nur wenn alle 4 Metriken in einer iterativen Stichprobe die Schwellwerte dauerhaft schlagen, ist das Modell bereit für Applikations-Logik und Generierung von Signal-Kandidaten.

---

## 4. Signal Candidate Generierung (Production)

Signal Kandidaten sind unabhängig vom zugrundeliegenden Provider: Sie aggregieren lediglich bestehende `AnalysisResult`-Punkte. 

**Workflow Command:**
```bash
# Extrahiere Signale basierend auf dem hochgestuften Companion Model
trading-bot research signals --provider internal
```

### Kompatibilitäts-Garantie:
1. **Fallback-Sicherheit:** Wenn das Internal-Model nicht verfügbar ist, erzeugt die CLI weiterhin Signale über den `fallback` (soweit die Rule-Relevance ausreicht).
2. **Provider-Agnostik:** Downstream-Applikationen können Signale blind konsumieren. Sie benötigen niemals Spezialbehandlung für `internal`, `openai`, oder `rule`.

---

## Zusammenfassung: Lifecycle Rule
1. **Scrape:** Tägliches Harvesting.
2. **Teach:** OpenAI/Anthropic annotieren die Daten (teuer, hohe Qualität).
3. **Export:** Extraktion von `dataset-export`.
4. **Train:** ML-Team verfeinert die Gewichte des GGUF-Modells.
5. **Evaluate:** Modell wird per `evaluate` lokal dem Härtetest unterzogen.
6. **Deploy:** Companion-API rückt als primärer Provider für Massen-Pipelines an (kostengünstig, schnell).
