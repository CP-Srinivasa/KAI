---
name: research-crosscheck
description: Shadow-Analyse + Crosscheck über mehrere KI-Systeme (OpenAI/Gemini/Anthropic). Konsens/Dissens-Report, Confidence-Abgleich, Gegenhypothesen. Operationalisiert KAI Directive §6.
trigger: User sagt "Crosscheck", "Shadow", "Second Opinion", "Red Team", "Validierung"; oder vor P0-Entscheidung / vor Production-Gate / bei widersprüchlichen Signal-Hypothesen.
---

# Research Crosscheck (KAI)

Operationalisiert §6 (Maximale Nutzung KI-Systeme) der Master Execution Directive. Zwingt zu Mehr-Modell-Validierung, Dissens-Erkennung und expliziter Gegenhypothesen-Arbeit.

## Zweck

Ein einzelnes Modell kann confident-falsch sein. Crosscheck:
- verhindert blinde Annahme einer LLM-Meinung
- macht Confidence ehrlich (Konsens = höher, Dissens = Risiko-Flag)
- erzwingt Red-Team-Perspektive
- identifiziert Lücken/Widersprüche

**Anwendung nur wenn sinnvoll** — nicht für jede triviale Anfrage (Token-Kosten). Gate-Kriterien siehe §Trigger.

## Trigger (verbindlich anwenden wenn)

- Vor einer **P0-Entscheidung** (Production-Gate, Architektur-Pivot, Strategie-Änderung)
- Bei **widersprüchlichen Signal-Hypothesen** (z.B. 2 verschiedene Alerts mit gegensätzlichem Directional-Call zum selben Asset)
- Vor **Release neuer Signal-Logik** (Validierung der Scoring-/Gate-Logik)
- Bei **unsicheren Source-Klassifikationen** (AI labelt Source als „bearish" — zweites Modell zur Gegenprobe)
- User-Befehl: "Crosscheck", "Red Team", "Second Opinion"

**NICHT anwenden:**
- Bei trivialen Code-Fixes
- Bei klar deterministischen Fragen (Ruff-Error, SQL-Syntax, etc.)
- Wenn einzelnes Modell ausreichend präzise + Kosten höher als Nutzen

## Verfügbare Systeme

| System | Role | Stärke | Schwäche |
|--------|------|--------|----------|
| Claude (Anthropic) | Primary | Nuanciert, ehrlich über Unsicherheit | Kann zu vorsichtig sein |
| OpenAI (GPT-4/o-series) | Shadow-1 | Breites Weltwissen, Code-Logik | Tendenz zu confident-falsch |
| Gemini (Google) | Shadow-2 | Long-context, strukturiert | Inkonsistent bei subtilen Distinktionen |
| Lokale Modelle | Reserve | Privat, offline | Qualität variabel |

**Im KAI-Projekt integriert:** OpenAI (bereits via provider abstraction), Gemini (via GEMINI_API_KEY siehe `app/cli/main.py`). Claude ist nativ (dieser Agent). Ensemble-Provider existiert: `Ensemble (OpenAI -> Gemini)`.

## Verfahren (5 Modi)

### Mode 1: Parallel-Consensus
**Zweck:** Gleiche Frage an 2-3 Modelle, Antworten vergleichen.
**Output:**
- **Konsens-Punkte** (alle stimmen zu) → höheres Vertrauen
- **Dissens-Punkte** (Modelle widersprechen sich) → Flag + Ursachenanalyse
- **Blind Spots** (kein Modell erwähnt) → Operator-Prüfung

### Mode 2: Red-Team
**Zweck:** Einem Modell die These geben, einem anderen Modell den Auftrag, die These **zu zerlegen**.
**Prompt-Template für Red-Team-Modell:**
```
Du bist Red Team. Deine Aufgabe ist NICHT die These zu bestätigen.
Deine Aufgabe ist, sie zu zerlegen. Finde:
- fehlerhafte Annahmen
- unbelegte Schritte
- Daten die dagegen sprechen könnten
- alternative Erklärungen
- Edge Cases
Liefere Top-5 stärkste Gegenpunkte mit Evidenz-Vorschlag.
```

### Mode 3: Confidence-Calibration
**Zweck:** Modell A sagt „85% confident". Modell B bekommt gleiche Daten + A's Antwort + soll unabhängig bewerten.
**Output:** Adjustiertes Confidence-Intervall + Begründung der Abweichung

### Mode 4: Blind-Spot-Check
**Zweck:** Nach erster Analyse: Modell fragen „was habe ICH übersehen?" — mit anderer Rolle („als Skeptiker", „als Risk-Officer", „als Data-Engineer").

### Mode 5: Counter-Hypothesis
**Zweck:** These + Anti-These generieren, dann Entscheidung treffen.
- These: „Setup zeigt Long-Bias"
- Anti-These: „Setup zeigt Bull-Trap"
- Welche Evidenz würde JEDE stützen? Welche haben wir aktuell?

## Input / Output-Format

### Input
- Hypothese/Frage klar formuliert
- Relevante Daten (Zeitreihe, Signal-Parameter, Source-Content)
- Gewünschte Modi (1-5, mind. 1)
- Entscheidungs-Kontext (warum Crosscheck nötig)

### Output-Format (verbindlich)

```
### Crosscheck-Thema
<1 Satz>

### Modelle eingesetzt
- <Modell A>: Rolle
- <Modell B>: Rolle
- <Modell C>: Rolle (optional)

### Kern-Hypothese
<knappe Darstellung>

### Konsens (alle Modelle)
- Punkt 1
- Punkt 2

### Dissens / Widersprüche
- <Modell A> sagt X, <Modell B> sagt Y → Ursache-Analyse: <Modelldaten/Weltbild/Prompt-Sensitivität?>

### Gegenargumente (Red Team)
- Top-1: <Schwäche + Evidenz-Vorschlag>
- Top-2: ...

### Blind Spots
- Was kein Modell gesehen hat:
- Operator-Check empfohlen für:

### Confidence-Urteil
- Einzel-Confidence pro Modell
- Aggregiertes Confidence-Urteil (niedriger als Einzel bei Dissens)

### Empfehlung
- Weiter wie geplant / überprüfen / verwerfen / Gate erhöhen / mehr Daten holen

### Token-Kosten (grob)
- ca. X $ / Y tokens
```

## Integration in bestehende Infrastruktur

Im KAI-Projekt:
- `app/analysis/llm/` — Provider-Abstraktion (OpenAI/Gemini Ensemble existiert)
- `app/integrations/` — Provider-Clients
- Aufruf-Beispiel:
```python
from app.analysis.llm import build_provider
primary = build_provider("openai")
shadow = build_provider("gemini")
primary_result = primary.analyze(prompt)
shadow_result = shadow.analyze(prompt + f"\n\nAndere Analyse sagt: {primary_result.summary}. Unabhängig bewerten.")
```

Ergebnisse in `artifacts/agents/crosscheck/YYYY-MM-DD_<thema>.json` ablegen.

## Anti-Pattern

- Crosscheck bei trivialen Anfragen (Kosten ohne Nutzen)
- Modelle mit identischem Prompt füttern → künstlicher Konsens
- Dissens wegerklären („Modell B hat's falsch verstanden") ohne tiefere Prüfung
- Red-Team-Modus mit Soft-Prompts („was könnte man besser machen") — muss scharf sein
- Nur Konsens dokumentieren, Dissens/Blind-Spots verschlucken
- Confidence einfach mitteln — bei Dissens nach UNTEN gewichten

## Post-Run Pflicht

1. Artifact `artifacts/agents/crosscheck/YYYY-MM-DD_<thema>.md` schreiben
2. Token-Kosten in Summary vermerken (Transparenz)
3. Wenn Dissens → in DECISION_LOG.md referenzieren
4. Wenn Blind-Spot gefunden → TaskCreate für Operator-Check

## Referenz
- KAI Master Execution Directive §6 (maximale Nutzung KI-Systeme)
- Verwandte Skills: `daily-strategy-review` (trigger bei P0-Entscheidungen), `source-expansion` (Kategorie D: konkurrierende AI als Kontrolle)
- Code: `app/analysis/llm/` (Provider-Abstraktion), `app/cli/main.py` (Gemini-Setup)
