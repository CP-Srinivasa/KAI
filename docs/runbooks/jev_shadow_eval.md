# Jev shadow evaluation

Dieses Werkzeug bewertet bereits aufgezeichnete TypeSafe-System-One-Antworten. Es startet
keinen Proxy, ruft kein Modell auf und ändert weder Routing noch Trading-Zustand. `app/ai`
bleibt die einzige Instanz, die Schwellen und Ergebniswirkung festlegt.

## Eingabe

Eine JSONL-Zeile beschreibt genau einen unabhängig gelabelten Fall:

```json
{"schema_version":"jev-shadow-case/v1","case_id":"news-001","expected_relevant":true,"baseline_relevant":true,"latency_ms":42.1,"cost_usd":0.000001,"response":{"model":"typesafe/jev-1.13.0","answers":{"relevant":{"type":"noul","noul":0.93}},"usage":{"input_tokens":120,"output_tokens":1}}}
```

- `expected_relevant` ist das vor Jev festgelegte Referenzlabel.
- `baseline_relevant` ist das Ergebnis des bestehenden KAI-Filters auf demselben Fall.
- `response` ist die unveränderte TypeSafe-Antwort. Alias oder angefragtes Modell dürfen
  nicht als tatsächliche Modellidentität eingesetzt werden.
- Unbekannte Kosten bleiben `null`; sie werden nicht als null Dollar verbucht.
- Personenbezogene Daten, Zugangsdaten und nicht freigegebene Inhalte gehören nicht in das
  Korpus. Der Korpus-Hash im Bericht macht spätere Änderungen sichtbar.

Leere Antworten, fehlende Fragen, unbekannte Antworttypen, ungültige Wahrscheinlichkeiten,
doppelte Fall-IDs oder wechselnde Modellidentität führen fail-closed zu ungültiger bzw. nicht
ausreichender Evidenz.

## Auswertung

Die Policy unter `config/jev_shadow_eval_policy.json` muss vor dem versiegelten Testsatz
feststehen. Sie definiert Mindeststichprobe, Review-Band, Mindestabdeckung, maximal zulässige
False-Negative-Rate, Brier-Score, Genauigkeitsgewinn gegenüber der Baseline sowie Kosten- und
Identitätsanforderungen.

```powershell
python -m scripts.jev_shadow_eval.cli `
  --input artifacts/jev/evidence.jsonl `
  --policy config/jev_shadow_eval_policy.json `
  --output artifacts/jev/report.json
```

Exit `0` bedeutet ausschließlich `READY_FOR_SHADOW_REVIEW`. Exit `2` bedeutet ungültige,
unzureichende oder nicht bestandene Evidenz. Jeder Bericht enthält `primary_ready=false`;
dieses Werkzeug besitzt keinen Aktivierungspfad.

Der Ausgabepfad muss neu sein. Bestehende Dateien (auch frühere Berichte, Eingabedaten
oder Policy-Dateien) werden niemals überschrieben; dafür endet der Aufruf mit Exit `2`.
Die Mindeststichprobe muss eine positive ganze Zahl sein.

Vor einem echten Lauf sind Korpusquelle, Labelverfahren, Datentrennung und zulässiger
externer Datentransfer durch den Integrator zu dokumentieren. Ein synthetischer Smoke prüft
nur Parser und Bericht, nicht die Modellqualität.
