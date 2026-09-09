# KAI COST CONTROL v0.1 — messen, zuordnen, begrenzen

**Stand:** 2026-09-08 · **Entscheid:** D-CORE-007 · **Ort:** `app/ai/` +
`app/observability/llm_telemetry.py` — keine neue Control Plane, kein zweiter
Artefaktstrom.

Vorher konnte KAI nicht sagen, was es kostet. `cost_usd` war auf **jeder** der
14.886 Telemetriezeilen `null`, weil das Feld nur vom LiteLLM-Antwort-Header
gefüllt wurde — und LiteLLM ist aus. Ein Budget existierte als Vertrag
(`app/ai/budget.py`), hatte aber **null Produktionsaufrufer**: kein Limit wurde
je gesetzt, kein Fensterzustand je gebaut.

Drei Sätze, auf denen alles steht:

1. **UNKNOWN ist nicht 0.** Eine Summe mit unsichtbaren Nullen sieht aus wie
   eine Abrechnung und ist keine.
2. **Schätzung ist keine Abrechnung.** Listenpreis und Rechnungsbetrag stehen
   nebeneinander, nie ineinander.
3. **Wo Geld abfliesst, muss die Bremse hinkommen** — auch auf dem Direktpfad.

---

## 1. Felder je Telemetriezeile

Alle additiv; jede bestehende Zeile und jeder bestehende Leser bleiben gültig.

| Feld | Werte | Bedeutung |
|---|---|---|
| `cost_usd` | `float \| null` | Kosten des Aufrufs. `null` = UNBEKANNT, **nie** 0. |
| `cost_known` | `bool` | `true` genau dann, wenn `cost_usd` gesetzt ist. |
| `cost_source` | `upstream` \| `list_price:<version>` \| `null` | **Abrechnung** vs. **Schätzung**. Die einzige Stelle, an der beides unterschieden wird. |
| `cost_status` | `OK` \| `COST_UNKNOWN` | |
| `cost_reason` | `no_tokens` \| `unknown_model` \| `no_model` \| `negative_tokens` \| `pricing_unavailable` \| `""` | Warum unbekannt. Leer bei `OK`. |
| `total_tokens` | `int \| null` | Nur wenn BEIDE Seiten bekannt sind. |
| `use_case` | siehe §2 | Wer die Arbeit beauftragt hat. |
| `escalation_reason` | `""` \| `route_reasoning` \| `route_critical` \| `critical_override` | Warum dieser Aufruf eine teurere Stufe verlangt. `standard`/`stt`/`bulk` sind **keine** Eskalation — ein Feld, das fast immer gesetzt ist, sagt nichts mehr. |
| `source` | `str \| null` | Feed-/Kanalname. **Getrennt** von `provider`. |
| `retry_count` | `int` | Physische Wiederholungen dieses Aufrufs (siehe §5). |

Berechnet wird an **einer** Stelle: `record_llm_call`
(`app/observability/llm_telemetry.py`). Sieben Aufrufer, eine Formel.

### `provider` ist der Anbieter — `source` ist die Quelle

Der Strom trug im Feld `provider` auch Feednamen (`CNBC`, `BeInCrypto`);
`app/ai/health.py` und `app/ai/spend.py` müssen sie deshalb wegfiltern, sonst
zählt jede Aggregation Feeds als Anbieter mit. `_telemetry_provider`
(`app/analysis/pipeline.py`) stellt die Zuordnung her, statt sie nur zu
filtern: fällt der aufgelöste Name mit dem Quellennamen zusammen, gewinnt der
Anbieter und die Quelle geht in `source`.

---

## 2. Zuordnungsregel (`use_case`)

Gesetzt am **Eintrittspunkt** über `use_case_scope` (`app/ai/audit.py`), nie im
Provider: der Provider weiss, WOMIT er fährt, aber nie, FÜR WEN.

| `use_case` | gesetzt in |
|---|---|
| `news_intelligence` | `AnalysisPipeline.run` — deckt RSS, OKX, Messari, YouTube, Newsdata, Twitter ab |
| `research` | `app/intelligence/providers.py` (`_ok`/`_fail`) |
| `operator_manual` | abgeleitet aus `purpose ∈ {chat, intent, stt}` (nur Telegram/Web-Operator) |
| `trading_paper` | abgeleitet aus `purpose = consensus` |
| `premium_signals` | reserviert — heute kein LLM-Pfad |
| `monitoring` | reserviert — heute kein LLM-Pfad |
| `unknown` | alles ohne Scope und ohne bekannten Purpose |

**Rückfall, kein Rateversuch.** Ohne gesetzten Scope wird aus `purpose`
abgeleitet (`_PURPOSE_USE_CASE`). Was auch das nicht trifft, heisst `unknown`
und bleibt in der Auswertung auffindbar, statt einem beliebigen Topf
zugeschlagen zu werden.

**Shadow-Zeilen** (`role="shadow"`, `chain_position=-1`) tragen denselben
`use_case` wie der Primäraufruf — sie sind dieselbe Arbeit, zweimal bezahlt.

---

## 3. Kanonische Zählebene (die Regel, ohne die alles falsch ist)

Derselbe Aufruf erscheint bis zu zweimal im Strom: eine äussere Zeile
`chain_position == -1` (Pipeline, spannt die ganze Fallback-Kette) und je eine
Zeile `chain_position >= 0` pro Kettenversuch (`EnsembleProvider`).

> **Wo Versuchszeilen derselben `correlation_id` existieren, gewinnen sie; die
> äussere Zeile entfällt.** v1-Zeilen ohne `correlation_id` haben kein
> Gegenstück und bleiben erhalten.

Beide zu zählen verdoppelt jede Ensemble-Analyse — messbar an 1.442.260 gegen
1.499.219 Eingabetoken für denselben Verkehr (`KAI_COST_SURFACE.md:64-71`).
Die Regel wohnt in `app/ai/spend.py::dedupe_chain_levels`; `app/ai/health.py`
importiert sie, statt sie zweitzuschreiben.

---

## 4. Budget: Zustände und Verhalten

`app/ai/budget.py::evaluate_status` — rein, ohne Uhr und ohne I/O.

| Zustand | Auslöser | Wirkung Routine | Wirkung `critical` |
|---|---|---|---|
| `OK` | im Rahmen | läuft | läuft |
| `WARNING` | ≥ `warn_pct` % eines gesetzten Limits | **läuft** | läuft |
| `LIMIT_REACHED` | belegte Kosten ≥ Tages- oder Monatslimit | `BudgetExceeded` | läuft, `escalation_reason="critical_override"` |
| `COST_UNKNOWN` | unbelegte Aufrufe heute > `unknown_max_calls_per_day` | `BudgetExceeded` — **nur wenn ein Limit gesetzt ist**, sonst nur sichtbar | läuft, `critical_override` |

`WARNING` sperrt ausdrücklich nichts — es ist der letzte Zustand, in dem ein
Operator noch handeln kann, bevor die Pipeline stehen bleibt.

`COST_UNKNOWN` ist der **einzige fail-closed Zweig**: wer nicht weiss, was er
ausgibt, hat kein gedecktes Budget. Die Alternative wäre, unbegrenzt
weiterzulaufen, solange die Messung kaputt ist.

**Aber er sperrt nur, wenn überhaupt ein Limit gesetzt ist.** Ohne Budget gibt
es keine Deckung, die fehlen könnte. Ohne diese Einschränkung wäre ein Betrieb,
der nie ein Limit konfiguriert hat, ab dem 51. unbelegten Aufruf eines Tages
stehen geblieben — eine Stilllegung aus der Voreinstellung heraus, ausgelöst von
einem Messproblem statt von Kosten. Der Zustand wird trotzdem berechnet und in
`/health/ai` ausgewiesen: Sichtbarkeit braucht keine Erlaubnis, Sperren schon.

**`critical` (= `intent`) ist die einzige ausgenommene Route und bewusst nicht
konfigurierbar.** Wer sie sperrt, nimmt dem Operator die Fernbedienung für
genau den Zustand, den er gerade beheben muss.

### Der bewusste Verhaltenswechsel

`app/ai/gateway.py` sagte bis 2026-09-08 ausdrücklich: *„Das Budget regiert die
LiteLLM-AUSGABE, nicht den Altpfad."* Weil LiteLLM aus ist, hätte ein
erschöpftes Budget damit **nichts** angehalten. Jetzt greift die Bremse an
beiden Stellen:

* `app/ai/gateway.py::execute_async` — für den Gateway-Pfad,
* `app/ai/runtime.py::invoke`, OFF-Zweig — für den **heutigen Normalfall**
  (`KAI_INFERENCE_ENABLED=false`), der zurückkehrt, ohne das Gateway je zu
  betreten.

Ein Budget nur im Gateway wäre in genau dem Modus wirkungslos, in dem KAI
läuft.

### Was Aufrufer tun

`BudgetExceeded` ist typisiert, damit ein Budgetende nicht wie ein
Anbieterausfall aussieht.

* **Pipeline** (`app/analysis/pipeline.py`): Dokument bleibt unanalysiert mit
  `fallback_reason="ai_budget_exceeded: <state> (<reason>)"`, Log
  `analysis_skipped_ai_budget`. Kein Absturz, kein Verlust.
* **Telegram-Chat** (`app/messaging/kai_chat_engine.py`): „AI-Budget erreicht.
  Steuerbefehle gehen weiter, Smalltalk pausiert."

**Fail-soft beim Lesen:** ein unlesbarer Strom oder eine unlesbare
Konfiguration ergibt den unbegrenzten Zustand — nie eine Sperre aus einem
Defekt heraus.

---

## 5. Retry-Zählung

Die vier Direktprovider tragen `@retry(stop=stop_after_attempt(3))`; die
Messung liegt **ausserhalb** dieses Dekorators. Bis zu drei bezahlte Requests
ergaben genau eine Telemetriezeile mit `retry_count=0` — die einzige
strukturelle Quelle, die eine Rechnung über die Telemetrie treiben kann.

Jetzt: `before_sleep=note_retry_attempt` in allen vier Providern. Der Hook
feuert **zwischen** Versuchen, drei Versuche ergeben also `retry_count=2` —
dieselbe Zählweise wie `record_attempt_trace`.

**Nur zählen, nicht entscheiden.** Die Versuchszahl bleibt in
`app/ai/retry.py`, das Prädikat in `app/ai/audit.py::is_retryable_error`. Es
gibt keine zweite Entscheidungsstelle.

---

## 6. Verschwendung, die entfernt wurde

| # | Was | Wo |
|---|---|---|
| W2 | Schatten lief auch dann, wenn ein **Relevanz-Gate** den Primäraufruf gespart hatte — jedes aussortierte Dokument kostete weiter einen `claude-sonnet-4-6`-Aufruf für einen Vergleich ohne Gegenstück. Log: `shadow_skipped_relevance_gate`. **Abgegrenzt:** bei *kein Primärprovider konfiguriert*, *LLM abgeschaltet* oder *Stub-Dokument* läuft der Schatten unverändert weiter — dort ist er die einzige Analyse, die es gibt, und ihn zu unterdrücken hätte eine gültige Konfiguration still stillgelegt | `app/analysis/pipeline.py` |
| — | `APP_ANALYSIS_SHADOW_ENABLED` (Default `true`) — Factory **und** `/health/ai` lesen aus einer Quelle | `app/analysis/factory.py::describe_shadow_chain` |
| — | `KAI_TWITTER_INGEST_ENABLED` (Default `true`) | `scripts/paper_trading_cron.sh` |

Nur beobachtet, **nicht** geändert (Verhaltensänderungen mit Qualitätsfolge):
`gpt-4o` für Smalltalk mit `max_tokens=150`, `claude-sonnet-4-6` vs.
`claude-sonnet-5`, unbegrenzter Kontext in jedem Telegram-Freitext — und der
**Stub-Gate**, der dieselbe Klasse Ersparnis böte wie W2, aber ausserhalb des
Auftrags lag.

---

## 7. Konfiguration (Pi)

`app/core/ai_cost_settings.py`, Präfix `APP_AI_`. Eigene Datei, weil
`app/core/settings.py` mit 1.883 Zeilen exakt auf seiner
God-File-Ratchet-Baseline steht.

```bash
# /home/kai/current/.env  — alles optional, Default = heutiges Verhalten
APP_AI_BUDGET_DAILY_USD=3.00          # leer/ungesetzt = kein Tageslimit
APP_AI_BUDGET_MONTHLY_USD=60.00       # leer/ungesetzt = kein Monatslimit
APP_AI_BUDGET_WARN_PCT=80             # Default 80
APP_AI_BUDGET_UNKNOWN_MAX_CALLS_PER_DAY=50   # Default 50, fail-closed
APP_AI_BUDGET_USECASE_NEWS_INTELLIGENCE_USD=2.00   # optional, je Auftraggeber
APP_ANALYSIS_SHADOW_ENABLED=true      # false spart die Zweitmeinung komplett
KAI_TWITTER_INGEST_ENABLED=true       # false überspringt den Twitter-Schritt
```

**Empfohlene Einführung:** zuerst ohne Limits fahren und `/health/ai` → `cost`
beobachten (Zustand wird auch ohne Limit berechnet). Erst wenn
`unknown_cost_calls_today` klein und `today_usd_known` plausibel ist, ein
Tageslimit setzen. Ein zu niedriges Limit stoppt die Doc-Pipeline.

Nach jeder Env-Änderung: `systemctl restart kai-server` **und** die Smoke-Regel
aus `feedback_maintenance_restart_protocol.md` (agent-worker, tg-listener,
cloudflared).

---

## 8. Sichtbarkeit: `/health/ai` → `cost`

Additiver Schlüssel, kein neues Dashboard, kein Probe-Call. Zwei Fenster
(**heute UTC**, **laufender Monat UTC**) statt eines Rollfensters: ein
Tagesbudget wird um Mitternacht zurückgesetzt, nicht 24 Stunden nach dem
letzten Aufruf.

```json
{"cost": {
  "today_usd_known": 1.83, "month_usd_known": 41.20,
  "unknown_cost_calls_today": 4, "unknown_cost_calls_month": 61,
  "calls_today": 212, "calls_month": 3480,
  "daily_limit_usd": 3.0, "monthly_limit_usd": 60.0,
  "warn_pct": 80.0, "unknown_max_calls_per_day": 50,
  "status": "OK", "reason": "", "blocks_routine": false,
  "top_provider": "anthropic", "top_use_case": "news_intelligence",
  "fully_accounted_today": false, "price_table_version": "2026-09-08",
  "note": "estimates from list prices; billing amounts are separate"
}}
```

`*_known` ist wörtlich gemeint: die Summe ist eine **Untergrenze**, solange
`unknown_cost_calls_*` > 0. Wer nur die Summe liest, liest sie falsch.

---

## 9. Preistabelle

`app/ai/pricing.py`, `PRICE_TABLE_VERSION = "2026-09-08"`,
`source = "list_price"`. USD je 1 Mio. Token.

| Modell | Input | Output | Status |
|---|---|---|---|
| `gpt-4o` | 2.50 | 10.00 | Liste — **Operator-Bestätigung offen** |
| `gpt-4o-mini` | 0.15 | 0.60 | Liste — Bestätigung offen |
| `claude-sonnet-4-6` | 3.00 | 15.00 | **gegen Rechnung 2026-08 geprüft** (+11 % auf 5,6-Tage-Stichprobe) |
| `claude-sonnet-5` | 3.00 | 15.00 | **unbestätigt**, siehe unten |
| `gemini-2.5-flash` | 0.30 | 2.50 | Liste — Bestätigung offen |
| `gemini-2.5-pro` | 1.25 | 10.00 | Liste — Bestätigung offen |

Alles andere → `COST_UNKNOWN`. Kein Rückfallpreis, kein Durchschnitt.
Modellnamen werden normalisiert (Kleinschreibung, Anbieter-Präfix entfernt) und
dann **exakt** nachgeschlagen: `gpt-4o-2024-08-06` fällt bewusst auf
`COST_UNKNOWN`, statt auf `gpt-4o` gebogen zu werden.

**Offener Widerspruch bei `claude-sonnet-5`:** der Auftragswert lautet
3.00/15.00 und ist ausdrücklich als unbestätigt markiert; der unabhängige Audit
`KAI_COST_SURFACE.md:86-91` nennt 2.00/10.00. Beide sind unbelegt, und KAI
benutzt das Modell nirgends. In der Tabelle steht der Auftragswert, weil sie
EINE Herkunft haben muss; die Abweichung ist im Code benannt statt weggemittelt.
Auflösung nur über eine echte Rechnung.

---

## 10. Verweise

* `KAI_COST_SURFACE.md` (Repo-Root) — unabhängiger Audit einer anderen Session,
  gegen die Telemetrie geprüft. Dieses Dokument schreibt ihn **nicht** fort und
  ersetzt ihn nicht; es liefert die Mechanik zu seinen Zahlen. Die
  `claude-sonnet-4-6`-Validierung gegen die Rechnung 2026-08 stammt von dort.
* ADR 0017 — Budget lehnt nur mit Beleg ab.
* `docs/DECISION_LOG.md` — D-CORE-007.

## 11. Siegel 2026-09-09 — COST CONTROL v0.1 = SEALED (D-CORE-007 geschlossen)

Geprüft am Gerät (kai-pi5) nach Aktivierung von Release `9438e7c6` (PR #923 + Nachtrag #930), Kriterien des Operators:

| # | Kriterium | Befund |
|---|---|---|
| 1 | Neue OpenAI-Aufrufe tragen gpt-4o und werden bepreist | 27 neue Zeilen, `model`/`actual_model` = `gpt-4o`, 27/27 bepreist, Summe 0,21138 $ |
| 2 | Keine neuen COST_UNKNOWN über den Wrapper-Pfad | 0 in den neuen Zeilen |
| 3 | Altzeilen als unmetered getrennt | `unmetered_legacy_calls_today` eigenes Feld; die 22 unbekannten Zeilen des Tages sind die Pre-Fix-Zeilen von 00:15–07:42 UTC (echte Unbekannte zur Schreibzeit), keine Altzeilen |
| 4 | Unbekannt-Schwelle 50 | 50, Status OK, `blocks_routine=false` |
| 5 | Anthropic-Zweitmeinung 0 neue Aufrufe | 0 seit Restart 05:31 UTC; `chain.shadow=[]`; Default im Code `false` |
| 6 | Twitter bei jedem Cron-Tick übersprungen | 04:41, 05:42, 06:43, 07:42 CEST »twitter skipped«, 0 Fetches seit 2026-09-08 21:15 |
| 7 | Limits 1 $/Tag, 25 $/Monat, Warnung 80 % | aktiv; Tagesstand 08:40 CEST 0,553 $ bekannt, Top openai / news_intelligence |
| 8 | `analyze pending` mit echtem Use Case | alle 27 Zeilen `use_case=news_intelligence` |
| 9 | Diskrepanz Dokumente vs. Modellaufrufe erklärt | CLI weist aus: `50 success / 27 llm_call / 23 skipped`; Skips sind Stub-, Relevanz- und Krypto-Gate (`reason=no_crypto_signal` u. a.), die ein Dokument ohne Modellaufruf abschließen und als Erfolg zählen — kein Cache, keine Doppelzählung |

**Status:** SEALED. Keine weitere Cost-Control-Arbeit außer bei Regression oder echter neuer Kostenquelle. Bekannte Vereinfachung: Cached-Input-Preis (1,25 $/M) wird nicht modelliert, Schätzung liegt damit leicht über der Abrechnung. Hinweis für den Betrieb: mit 1 $/Tag wird die Tagesgrenze bei heutiger Analyse-Last voraussichtlich am Nachmittag erreicht; die Routine-Analyse pausiert dann bis 00:00 UTC — das ist die gewollte Kalibrierung, nicht ein Fehler.
