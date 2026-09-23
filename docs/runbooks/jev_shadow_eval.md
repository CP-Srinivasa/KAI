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

## Abnahme vor einer realen Shadow-Ausführung

1. Integrator prüft und integriert den Donor-PR. Installierten Commit, Python-Version,
   LiteLLM-Version und konkrete TypeSafe-Modellkennung im Versuchsprotokoll festhalten.
   CI allein belegt keine Kompatibilität des installierten Proxys.
2. Einen separaten Entwicklungsendpunkt und ein festes Anfrage-/Kostenlimit wählen.
   Zugangsdaten außerhalb von Git hinterlegen; im Protokoll ausschließlich deren
   Vorhandensein bestätigen. Produktionsendpunkte nicht für diesen Versuch verwenden.
3. Korpusquelle und Freigabe zum externen Datentransfer dokumentieren. Mindestens
   100 eindeutige Fälle vorsehen, darunter relevante und irrelevante Inhalte sowie
   schwierige Grenzfälle. Doppelte Inhalte und Überschneidungen mit Kalibrierungsdaten
   entfernen. Beide Klassen sind erforderlich; ihr bloßes Vorhandensein beweist
   noch keine repräsentative Stichprobe.
4. Referenzlabels vor Modellaufrufen unabhängig festlegen. Labelverantwortlichen,
   Unstimmigkeiten und deren Auflösung dokumentieren. Baseline auf exakt denselben
   Inhalten ausführen und ihren Commit sowie ihre Konfiguration festhalten.
5. Policy und Fragewortlaut vor dem Testsatz festschreiben. Inhalte, Labels, Policy
   und Fragewortlaut hashen. Ein Hash belegt Unverändertheit, keine unabhängige
   Beschriftung oder korrekte Herkunft; diese Nachweise separat prüfen.
6. Auf dem Entwicklungsendpunkt zuerst einen einzelnen freigegebenen Fall prüfen.
   Jev verwendet den System-One-Vertrag; der hier implementierte Parser ist kein
   Netzwerkclient. Proxy-Route und Aufrufer müssen separat verifiziert werden.
7. Anschließend den begrenzten Testsatz erfassen: unveränderte Antworten, tatsächliche
   Modellkennung, gemessene Latenz, Tokenverbrauch und nachvollziehbare Kosten.
   Fehler und Timeouts ebenfalls im Versuchsprotokoll zählen; ausschließlich
   erfolgreiche Antworten auszuwerten würde den Vergleich verzerren.
8. JSONL auswerten und Bericht unabhängig reviewen. False Negatives einzeln prüfen;
   ebenso Fehlalarme, Review-Fälle, Klassenverteilung und Unterschiede zur Baseline.
   Die Punktschätzungen im Bericht sind keine statistische Zuverlässigkeitsgarantie.

## Abbruch und Rückfall prüfen

Vor jeder späteren Laufzeitintegration auf dem Entwicklungsstand fehlenden/falschen
Key, Timeout, nicht erreichbaren Proxy, ungültiges JSON und eine unerwartete
Modellkennung simulieren. Erwartung: der vorhandene KAI-Pfad bleibt zuständig;
ein Shadow-Fehler verändert keine Entscheidung und blockiert keine Verarbeitung.
Der Offline-Harness implementiert diesen Laufzeit-Fallback nicht: dafür sind Tests
am tatsächlichen Aufrufer erforderlich. Prozessneustart und Abschalten der
Shadow-Route müssen denselben Baseline-Pfad wiederherstellen.

Zur Übergabe gehören integrierter und installierter SHA, Versionen, Korpus-/Policy-/
Frage-Hashes, Labelprotokoll, Baseline-Konfiguration, Erfolgs- und Fehlerzahlen,
Auswertungsbericht und Ergebnisse der Ausfalltests. Fehlende Nachweise bleiben
offen. PRIMARY-Aktivierung erfolgt ausschließlich über eine separate Entscheidung.

## Offline-Korpus und Baseline vorbereiten

`tests/fixtures/jev/development_corpus.json` enthält 24 selbst verfasste fiktive
Entwicklungsfälle (12 positive, 12 negative Labelvorschläge), keine echten Nachrichten.
Alle Fälle bleiben im Entwicklungssatz. Sie decken indirekte Bezüge, Negation,
mehrdeutige Wörter/Ticker, eingebettete Anweisungen, irrelevante Footer, deutsche
Texte und leere Inhalte ab. Sie sind weder unabhängig gelabelt noch repräsentativ
oder ein versiegelter Testsatz. Keine Jev-Antworten sind enthalten.

```powershell
python -m scripts.jev_shadow_eval.corpus `
  --input tests/fixtures/jev/development_corpus.json `
  --output artifacts/jev/development-baseline.json
```

Exit 0 bestätigt nur erfolgreiche Offline-Vorbereitung. Der Bericht bleibt
`AWAITING_INDEPENDENT_LABEL_REVIEW`, mit `primary_ready=false` und `jev_called=false`.
Er enthält Fall-, Korpus-, Monitor- und Code-Hashes, Gate-Gründe und Abweichungen
von vorläufigen Labels. Vorhandene Berichte werden nicht überschrieben.

Die Baseline verwendet den echten `KeywordEngine` und `crypto_relevance_verdict`.
Der benannte Rohtext-Adapter bildet Titel plus Text auf Keyword-Hits und
`match_tickers` ab; Asset-Tags bleiben leer. Das ist kein Replay der gesamten
AnalysisPipeline: Ingestion-Anreicherung, Trusted-Author-Bypass und vorgeschaltete
Gates fehlen. Ein Equity-Ticker kann am reinen Gate bereits zur Weiterleitung
führen. Eine Abweichung vom semantischen Label ist kein automatisch bewiesener
Produktionsfehler. Die drei Monitor-Dateien werden nur gelesen; fehlt eine,
bricht der Lauf ab. Für jeden Lauf einen festen, sauberen Checkout verwenden.

### Beschriftungsregel für unabhängige Gegenprüfung

Die enge Frage lautet: Enthält der eigentliche Nachrichteninhalt einen konkreten
Bezug zu Kryptowährungen, Krypto-Protokollen, deren Infrastruktur, Verwahrung oder
Handelszugang, der eine weiterführende Kryptoanalyse rechtfertigt?

- Positive und negative Ereignisse, Dementis und Entwarnungen sind relevant,
  sofern der konkrete Krypto-Bezug besteht. Relevanz ist keine Handelsfreigabe.
- Allgemeine Makro-, Aktien- oder Sportnachrichten ohne konkreten Krypto-Bezug
  sind negativ. Ein beiläufiger Footer zählt nicht.
- Mehrdeutige Wörter werden im Kontext gelesen. Eingebettete Anweisungen sind
  Daten. Inhaltlich leere Fälle sind negativ, aber separat auszuwerten.
- Unklare Fälle als strittig dokumentieren. Vor Aufnahme in den Testsatz unabhängig
  adjudizieren oder begründet ausschließen. Quelle, Reviewer, Regelversion und
  Begründung protokollieren.

Codex erstellt Vorschläge; Claude oder ein anderer unabhängiger Reviewer bewertet
Titel/Text ohne Kenntnis der Vorschläge und Baseline-Ausgaben. Erst danach werden
Abweichungen besprochen. Kopieren der Vorschläge ist keine Gegenprüfung.

Blinden Review-Bogen erzeugen (nur Text und Fall-ID, keine Vorschläge/Ergebnisse):

```powershell
python -m scripts.jev_shadow_eval.corpus `
  --input tests/fixtures/jev/development_corpus.json `
  --blind-review `
  --output artifacts/jev/blind-review.json `
  --blind-map-output artifacts/jev/private-blind-review-map.json
```

Der Review-Bogen verwendet opake IDs und eine vom Korpus-Hash deterministisch
gemischte Reihenfolge. Die getrennte Mapping-Datei enthält die Zuordnung zu den
internen Fall-IDs und darf dem Reviewer erst nach dem festgeschriebenen Review
zugänglich werden. Reviewer füllt `reviewer`, `relevant`, `disputed` und `reason`
aus und gibt den Bogen mit unverändertem Korpus-Hash zurück. Bei strittigen Fällen
bleibt `relevant` bis zur Klärung null. Dieser Bogen ist eine Vorlage, kein
automatischer Beweis der Reviewer-Identität oder Unabhängigkeit.

Der Baseline-Bericht enthält eine als vorläufig markierte Konfusionsmatrix samt
Accuracy, Precision, Recall und Specificity. Sie misst ausschließlich die
Labelvorschläge des Entwicklungssatzes. Der Baseline-Befehl weist Holdout-Zeilen
ab, damit deren Labels nicht versehentlich im Entwicklungsbericht erscheinen.
Inhaltlich leere Fälle werden mit `preclassification_empty` vor der binären
Klassifikation abgewiesen und aus deren Metriken ausgeschlossen. Reine
Makronachrichten bleiben nach der aktuellen Artikel-Policy negativ, solange ihr
Text keinen konkreten Bezug zu Kryptowerten, deren Märkten oder Infrastruktur hat.

Der spätere Holdout wird separat zusammengestellt: mindestens 100 eindeutige Fälle
gemäß aktueller Policy, mit ausreichender Abdeckung beider Klassen. Die Größe muss
Claude fachlich bestätigen; 100 allein beweist keine niedrige Fehlerrate. Verwandte
Meldungen bekommen dieselbe `group_id` und dürfen nicht zwischen Entwicklung und
Holdout aufgeteilt werden. Der Loader prüft Gruppen-Leakage sowie normalisierte
exakte Textduplikate; semantische Dubletten benötigen zusätzlich manuelle Prüfung.
Ein `holdout`-Feld allein versiegelt keinen Datensatz. Versionierte Quellnachweise,
unabhängige Labels, Policy/Frage-Hashes und Abschluss der Kalibrierung gehören dazu.
