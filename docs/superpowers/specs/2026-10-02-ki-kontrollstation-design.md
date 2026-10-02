# KI-Kontrollstation im KAI-Dashboard — Design

Stand 02.10.2026 · Sitzung bin-b6 · Freigabe der Abschnitte durch den Operator im Chat (02.10.)

## 1. Ziel

Eine Seite beantwortet aus Sicht des Operators drei Fragen, ohne dass jeder Anbieter einzeln geprüft werden muss:

1. **Läuft alles?** — Zustand jeder KI, jeder Route, jedes Anbieters und des LiteLLM-Proxys.
2. **Wer macht was, und was verbraucht es?** — Aufrufe, Token, Datenmenge, Kosten, Fehler je Aufgabe, Dienst, Weg und Modell; Guthaben je Anbieter.
3. **Wo muss ich eingreifen, und wie?** — Handlungsbedarf und Konflikte oben; Eingriffe direkt auf der Seite (Stufe 2).

Telegram meldet Schwellen sofort und einmal täglich im Operator-Digest.

## 2. Umfang

| Stufe | Inhalt | Wirkung auf KAI |
|---|---|---|
| **1 – sehen** | Zustände, Wer-macht-was, Konten und Guthaben, Konflikte, Protokoll, 14-Tage-Verlauf, Telegram-Schwellen und Digest-Block | nur lesend; ändert kein Verhalten |
| **2 – eingreifen** | Schaltflächen: Route auf aus oder Schatten, Anbieter sperren, Circuit zurücksetzen, Sparfenster schalten, LiteLLM neu starten; jeweils mit Aufheben | schreibend, nur in Richtung „vorsichtiger“ |

Stufe 1 geht zuerst live, Stufe 2 folgt als eigener PR.

**Nicht-Ziele:**
- Die Entwickler-KIs (Claude Code, Codex, Hermes, OpenCode/Dev-Proxy :4001) sind nicht Teil dieser Seite. Sie schreiben heute kein Kostenprotokoll. Der Developer Hub ist ein eigenes Vorhaben.
- Das Hochschalten auf `primary` gibt es im Dashboard nicht. Es bleibt ein geprüftes Operator-Skript mit Messbeleg (ADR 0017: keine implizite Aktivierung).
- Kein Budget-Erhöhen im Dashboard.

## 3. Zustandsmodell

Jedes Objekt (Aufgabe/Route, Anbieter, LiteLLM-Alias, Proxy) hat genau **einen** Zustand, dazu `reason` (Klartext) und `since`. Die Regeln werden in dieser Reihenfolge geprüft; die erste passende gilt:

| # | Zustand | Regel (Quelle) |
|---|---|---|
| 1 | **DEAKTIVIERT** ─ | Laut Konfiguration aus: Route nicht in `KAI_INFERENCE_ROUTE_MODES` bzw. `off`; Anbieter ohne Schlüssel; Anbieter mit Schlüssel, aber von keiner Route genutzt („ungenutzt“). |
| 2 | **AUSSER KRAFT** ⊘ | Aktiver Eingriff aus Stufe 2 weicht von der Konfiguration ab, mit Ablaufzeit (`ai_overrides.json`). In Stufe 1 kommt das nie vor. |
| 3 | **GESTÖRT** ✖ | Mindestens einer dieser Fälle: Proxy-Lebenszeichen ≠ 200 · Circuit eines Dienstes offen nach Fehlern · ≥ 5 aufeinanderfolgende Fehlversuche · Fehlerquote > 20 % in 1 h bei ≥ 10 Aufrufen · Guthaben-Fehler (402 / „Insufficient Balance“) · Kontoabfrage meldet Guthaben ≤ 0. |
| 4 | **PAUSIERT** ◐ | Von KAI bewusst angehalten, mit Wiederanlauf-Zeitpunkt: Budgettopf erschöpft (bis 00:00 UTC) · Sparfenster jetzt aktiv (bis Fensterende) · Circuit in Abkühlung (Restsekunden). |
| 5 | **AKTIV** ● | Mindestens ein erfolgreicher Aufruf in den letzten 15 min. |
| 6 | **BEREIT** ○ | Eingeschaltet und gesund, aber kein Verkehr in den letzten 15 min (mit Angabe „letzter Aufruf …“). |

Die Kopfzeile zählt die Zustände: „● n aktiv/bereit · ◐ n pausiert · ✖ n gestört · ⊘ n außer Kraft“.

## 4. Seitenaufbau

Eigener Menüpunkt **„KI-Kontrolle“**; der Seitenschlüssel `ai` ist schon von den Signal-Insights belegt. Gestaltet wird im vorhandenen Neon-System: `PageHeader tone="ai"`, Glow-Rahmen, Tokens aus `web/src/index.css` und `styles/kai.tokens.css`, Bewegung nur nach `docs/ui/dali_dashboard_v2_1_facelift_plan.md`. Die Seite aktualisiert sich alle 30 s.

1. **Kopfzeile:** Heute X / Tageslimit · Monat Y / Monatslimit · Prognose · Budgetende (heute schon um hh:mm, sonst Schätzung „reicht bis ~hh:mm UTC“).
2. **Handlungsbedarf:** offene Hinweise und Konflikte (Abschnitt 6), je mit Schweregrad, seit wann, Klartext und Aktion („Details“, „Aufladen ↗“ zur Konsole des Anbieters, in Stufe 2 der passende Eingriff).
3. **Verbindungen:**
   - Proxy: Zustand, Version, Baum = Lock, Lebenszeichen, seit wann.
   - Je LiteLLM-Alias: Alias → Upstream-Modell, Route, Modus, Zustand.
   - Je Anbieter, direkt und über LiteLLM: Zustand, letzter Erfolg, 24-h-Aufrufe und -Fehler, Circuit je Dienst.
4. **Wer macht was:** Tabelle je Aufgabe (Analyse, Chat, Freitext, Sprache, Research, Konsens) × Dienst × Weg (direkt/LiteLLM) × Modell, für die Zeiträume heute und 24 h. Spalten: Aufrufe, Token ein/aus, ≈ KB, Kosten (bekannt + Anzahl ohne Preis), Fehlerquote, Rückfälle, letzter Aufruf. Aufklappbar je Quelle.
5. **Konten & Guthaben:** je Anbieter Guthaben laut Anbieter (Alter der Abfrage), Reichweite in Tagen (aus dem 7-Tage-Verbrauch), Monatsverbrauch laut KAI, Link zur Konsole. Steht kein Konto-API bereit, heißt es „nur KAI-Messung“.
6. **Protokoll:** Konfigurationsänderungen der KI-Schalter (erkannt aus Fingerabdrücken, siehe 5.5), Eingriffe aus Stufe 2, Releases. Die neuesten oben.
7. **Verlauf 14 Tage:** Kosten je Tag gestapelt nach Anbieter; daneben Aufrufe, Token und Budgetende-Uhrzeit je Tag.

## 5. Daten und Backend

### 5.1 Endpunkte (nur lesend, Stufe 1)

- `GET /dashboard/api/ai/control` → `ai-control/v1`:
  `generated_at`, `summary`, `attention[]`, `connections{proxy, aliases[], providers[]}`, `workloads[]` (je Zeitraum), `accounts[]`, `protocol[]`, `null_reasons{}`.
  Jedes Feld, das `null` ist, trägt einen Eintrag in `null_reasons`. Das ist dieselbe Regel wie bei `ai-transport/v1`.
- `GET /dashboard/api/ai/control/history?days=14` → je Tag: Kosten je Anbieter, Aufrufe, Token, Budgetende.

Beide lesen nur Dateien; beim Seitenaufruf gibt es keinen Aufruf nach außen. Schutz wie die übrigen Dashboard-APIs.

### 5.2 Telemetrie (`artifacts/llm_telemetry.jsonl`)

- `app/ai/spend.py` führt `by_provider`, `by_model`, `by_use_case` und die Token schon intern. Neu ist eine Funktion, die die vollständige Aufschlüsselung je Aufgabe/Dienst/Weg/Modell/Quelle liefert. Sie nutzt dieselbe Kettenebenen-Regel (Innenzeile gewinnt), damit nichts doppelt gezählt wird.
- **Schema v10:** neues Feld `service` (`kai-server`, `kai-agent-worker` …). Gelesen wird es einmal je Prozess aus `/proc/self/cgroup` (systemd-Unit), also ohne Unit-Änderung; ohne systemd gilt `unbekannt`. Dazu gehören die Anpassungen an `SCHEMA_VERSION`, Routenbericht `KNOWN_TELEMETRY_SCHEMAS` und Vertragstests.
- Datenmenge: Token ein/aus; ≈ KB = Token × 4 Zeichen / 1024, als Näherung beschriftet.

### 5.3 Circuit je Dienst

Heute kennt jeder Prozess nur seinen eigenen Circuit (`app/ai/runtime._KREISE`). Neu: Bei jeder Zustandsänderung schreibt jeder Dienst `artifacts/runtime/circuit_<service>.json` atomar und entprellt. Das Dashboard liest alle Dateien; eine Datei älter als 24 h gilt als „unbekannt“.

### 5.4 Konten (`kai-ai-accounts.timer`, stündlich)

Ein neuer Oneshot-Dienst plus Timer schreibt `artifacts/ai_accounts.json`. Je Konto: `provider`, `balance`, `currency`, `fetched_at`, `status` (ok/fehler/kein_api), `error` (z. B. „HTTP 401“), `topup_url`.

| Anbieter | Abfrage | Bemerkung |
|---|---|---|
| DeepSeek | `GET /user/balance` | am 01.10. erfolgreich genutzt |
| Kimi/Moonshot | `GET /v1/users/me/balance` | am 01.10. erfolgreich genutzt (bar + Gutschein) |
| OpenAI | `GET /v1/organization/costs` | **nur mit optionalem `OPENAI_ADMIN_KEY`**; sonst „nur KAI-Messung“ |
| Anthropic, xAI, Gemini | — | „kein Konto-API / von KAI ungenutzt“ |

- Scheitert eine Abfrage, bleibt der letzte gute Wert mit seinem Alter stehen.
- Schlüssel landen nie in der Datei, im Log oder in Meldungen (Secret-Katalog).

### 5.5 Konflikte und Protokoll

Der Timer prüft bei jedem Lauf Regeln gegen Konfiguration und Messwerte. Jeder Konflikt hat einen festen Schlüssel:
- Route `primary`/`shadow`, aber Schlüssel oder Modell fehlt
- Baum ≠ Lock
- Alias konfiguriert, aber seit 24 h nur Fehler
- Rückfallquote > 20 % bei `primary`
- Schema-Fehler eines Anbieters wiederholt (z. B. Kimi-Codeblock statt JSON)
- `ROUTE_MODES` nennt eine unbekannte Route

Für das Protokoll bildet der Timer einen Fingerabdruck der nicht geheimen KI-Schalter: `KAI_INFERENCE_*` außer Schlüssel, `KAI_LITELLM_*_MODEL`, `SOURCE_LLM_SPARFENSTER_*`, `APP_AI_BUDGET_*`. Ändert er sich, schreibt der Timer den Unterschied nach `artifacts/runtime/ai_control_protocol.jsonl`, z. B. „standard primary → off“.

## 6. Telegram

### 6.1 Schwellen (sofort, `app/alerts/notify.send_operator_notification`)

| Hinweis | Auslöser (Standard, per Env einstellbar) |
|---|---|
| Guthaben knapp | DeepSeek/Kimi < 5 $ oder Reichweite < 7 Tage |
| Monatsgrenze | KAI-Prognose > Monatslimit; mit Admin-Schlüssel OpenAI > 80 % Projektlimit |
| Budget früh leer | Tagesbudget vor 12:00 UTC erschöpft |
| Gestört | Proxy > 5 min nicht erreichbar · Circuit offen · Fehlerquote > 20 % in 1 h (≥ 10 Aufrufe) · Rückfälle > 20 % bei `primary` |
| Konflikt | neuer Konfliktschlüssel aus 5.5 |

- **Entprellung:** je Schlüssel einmal melden, nach 24 h einmal erinnern, bei Behebung einmal „behoben“. Der Zustand liegt in `artifacts/runtime/ai_control_alert_state.json`.
- Ruhezeit (Standard, einstellbar, im Chat nicht besprochen): 23:00–07:00 MESZ keine Meldung. Ausnahme ist „Gestört“ bei einer Route auf `primary`; was in der Ruhezeit auflaeuft, kommt um 07:00 gesammelt.

### 6.2 Tagesbericht als Block im bestehenden Operator-Digest

- Kein eigenes Telegram: `scripts/operator_digest.py` vereint die Reports bewusst in **einer** Nachricht. Neu ist dort ein Block „KI“: Kosten gestern je Anbieter · Monat/Limit/Prognose · Aufrufe · Token · Budgetende · Guthaben und Reichweite · offene Hinweise · Eingriffe.
- `scripts/digest_ops_block.py` nutzt `spend.py` schon und ist der Ansatzpunkt.

## 7. Stufe 2 — Eingriffe

- **Speicher:** `artifacts/runtime/ai_overrides.json` (atomar geschrieben, mit Versionszähler). `app.ai.runtime` liest die Datei je Aufruf, zwischengespeichert nach mtime. Fehlt die Datei oder ist sie kaputt, gilt „keine Eingriffe“ (fail-safe).
- **Semantik:** Gültig ist immer der vorsichtigere Wert aus Konfiguration und Eingriff (`primary > shadow > off`). Ein Eingriff kann nie hochschalten.
- **Aktionen** (`POST /dashboard/api/ai/control/actions`, geschützt mit `require_operator_api_token`, Bestätigungsdialog in der UI):

| Aktion | Wirkung |
|---|---|
| `route_mode` | Route → `shadow` oder `off` |
| `provider_block` | LiteLLM-Upstream oder Alias sperren → Aufrufe gehen direkt; direkter Anbieter sperren = Notbremse, die Aufgabe bekommt die Regelanalyse |
| `circuit_reset` | Zeitstempel in der Datei; jeder Dienst setzt beim nächsten Aufruf seinen Circuit zurück |
| `sparfenster` | `off`/`shadow`/`enforce`. Einzige Aktion in beide Richtungen: das Fenster kann Aufrufe nur sparen, nie Ausgaben oder Risiko erhoehen |
| `litellm_restart` | `sudo -n /usr/local/sbin/kai-service-control restart kai-litellm.service`, nur dieser Dienst (fest verdrahtet), höchstens 1× je 5 min |
| `clear` | Eingriff aufheben |

- Jeder Eingriff trägt `actor`, `reason` (Pflichtfeld), `expires_at` (Standard 24 h, „bis auf Widerruf“ wählbar) und landet im Protokoll. Auf der Seite erscheinen betroffene Objekte als **AUSSER KRAFT** mit Ablaufzeit.
- Die `.env` bleibt die Grundkonfiguration. Eingriffe sind sichtbar, zeitlich begrenzt und aufhebbar, also kein zweiter, versteckter Konfigurationsstand.

## 8. Fehlerfälle

- Fehlt eine Quelle, zeigt der Bereich „keine Daten“ mit Grund, nie eine erfundene 0 (No-Fake).
- Ist ein Artefakt veraltet, wird es mit seinem Alter angezeigt und bekommt ab festen Grenzen den Zustand „unbekannt“: Konten > 3 h, Circuit > 24 h, Routenbericht > 2 h.
- Kontoabfrage: Zeitlimit 10 s je Anbieter, kein Wiederholversuch im selben Lauf; ein Fehler bei einem Anbieter stoppt die übrigen nicht.
- Telegram-Fehler blockieren den Timer nicht. Der nächste Lauf versucht es erneut, die Entprellung bleibt korrekt.

## 9. Tests und Abnahme

**Tests:**
- Zustandsregeln: jede Regel einzeln plus Reihenfolge, synthetische Eingaben.
- Aggregation aus synthetischer Telemetrie (v9 und v10 gemischt, Kettenebenen, Legacy-Zeilen).
- Konto-Parser gegen aufgezeichnete Antworten (DeepSeek, Moonshot, OpenAI-Costs, Fehlerantworten 401/402/5xx); kein Live-Aufruf in Tests.
- Schwellen: Auslösen, Entprellung, Erinnerung, „behoben“, Ruhezeit.
- Vertragstest `ai-control/v1`: jedes `null` hat einen Grund.
- Digest-Block: Text-Snapshot.
- Frontend: Typprüfung + Build. Die Release-Nachkontrolle greift ausgelieferte Bundle-Texte.
- Stufe 2 zusätzlich: Eingriffe nur abwärts, Ablauf, fail-safe bei kaputter Datei, Rate-Limit beim Neustart, Auth-Pflicht.

**Abnahme Stufe 1 (live auf der Pi):**
- Die Seite zeigt alle Routen und Anbieter mit Zustand und Grund.
- Die Zahlen von „Wer macht was“ für heute stimmen mit `spend.current_spend` überein (±0 Aufrufe).
- DeepSeek- und Kimi-Guthaben stimmen mit der Anbieterkonsole überein.
- Der nächste Operator-Digest enthält den KI-Block.
- Ein provozierter Testhinweis (Schwelle per Env herabgesetzt) kommt genau einmal an.

## 10. Betrieb und Rollout

- **Stufe 1:**
  - PR mit Backend, Frontend, Telemetrie v10 und Timer-Units.
  - Release-Skript (Muster `kai_release_*.ps1`).
  - Units einspielen per Operator-Skript mit sudo, wie beim Routenbericht-Timer.
  - Optional `OPENAI_ADMIN_KEY` in die Pi-`.env`: Der Operator legt den Schlüssel in der OpenAI-Konsole an, Claude bekommt ihn nie zu sehen.
- **Stufe 2:** eigener PR, direkt nach der Abnahme von Stufe 1.

## 11. Offene Punkte

- Ob ein OpenAI-Admin-Schlüssel angelegt wird, entscheidet der Operator. Ohne ihn bleibt OpenAI bei „nur KAI-Messung“, die am 01.10. 1,7 % neben der OpenAI-Konsole lag.
