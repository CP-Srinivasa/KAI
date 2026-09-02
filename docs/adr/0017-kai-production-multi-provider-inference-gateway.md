# ADR 0017 — KAI Production Multi-Provider Inference Gateway

**Status:** CODE_COMPLETE / deployment-gated (2026-09-01)  
**Scope:** operative KAI-Inferenz; kein Production-Cutover  
**Basis:** `3b0fa02751728a93e8c2c34a9bb6515a0dff2597`

## Problem und Status quo

Die operative 24/7-Inferenz läuft zuverlässig, ist aber im autoritativen Pfad an direkte
Provider-Clients und vor allem OpenAI gebunden. Die read-only Pi-Baseline vom 2026-09-01
zeigt 300 Calls/24 h, 0 % registrierte Fehler, p50 ca. 6,7 s und p95 ca. 20,9 s. Der Umbau
darf diese Zuverlässigkeit und keine Trading-, Approval- oder Risk-Grenze verschlechtern.

## Entscheidung

KAI erhält einen providerneutralen Vertrag unter `app/inference/`. Er spricht einen lokal
gebundenen LiteLLM-Proxy über die OpenAI-kompatible API an. Business-Code wählt nur eine
logische Route (`bulk`, `standard`, `reasoning`, `critical`, `stt`), keine Provider-ID.
Provider, Modell-IDs, Fallbackketten und optionale Preis-Metadaten bleiben Konfiguration.

Die Migration wird ausschließlich über den getrennten Namespace `KAI_INFERENCE_*`
gesteuert:

- `enabled=false`, `mode=off` ist Repository- und Rollback-Default; der direkte Altpfad
  bleibt unverändert autoritativ und LiteLLM ist keine Boot-Dependency.
- `shadow` führt geeignete Gateway-Calls parallel aus, speichert nur normalisierte sichere
  Vergleichsmerkmale und verändert nie das aktuelle Ergebnis.
- `primary` nutzt das Gateway primär und fällt bei Erschöpfung zentraler Gateway-Versuche
  auf den vorhandenen direkten Providerpfad zurück.

Der KAI-Router besitzt ein hartes Attempt-Limit, getrennte Retry- und Modell-Fallback-
Semantik, exponentielles Backoff mit Jitter, einen in-process Circuit Breaker je
Route/Modell, erneute strikt typisierte Pydantic-Validierung sowie optionale Budget-Gates.
LiteLLM-Retries sind deaktiviert, damit keine multiplikativen Retry-Stürme entstehen.

## Routing und Sonderpfade

`standard` bedient News-Analyse, Telegram Text Intent und KAI Smalltalk. `critical` wird nur
für den nicht-autoritativen Signal-Consensus-Vergleich verwendet. Signal Consensus selbst
bleibt direkt, unanim und fail-closed; das Gateway darf weder Agreement noch Confidence
ersetzen. STT läuft über einen eigenen `SpeechToTextProvider`; OpenAI Whisper bleibt als
direkter Rückfall erhalten. Der unabhängige direkte Anthropic-Shadow bleibt semantisch
sichtbar und wird nicht als Gateway-Consensus ausgegeben.

OpenAI wird nicht entfernt: `kai-openai-last-resort` und
`kai-openai-stt-last-resort` sind explizite konfigurierbare Rückfälle. xAI bleibt in Audit
und Kostenattribution Provider `xai`, auch wenn sein Client-Protokoll OpenAI-kompatibel ist.

## Telemetrie, Kosten und Budget

`artifacts/llm_telemetry.jsonl` bleibt die eine append-only Telemetrie. Schema v2 ergänzt
Route, Alias, tatsächlichen Provider/Modell, Token, Cache-Token, Kosten, Attempts, Fallback,
Circuit, Rollen, Request-ID und Schema-Status. Prompts, Dokumenttext und Secrets werden nie
geschrieben. Unbekannte Kosten sind `null`, nicht `$0`.

Preis-Metadaten sind optional und operatorgepflegt; es gibt keine volatile Preistabelle im
Business-Code. Soft-/Hard-Limits gelten kalenderbezogen täglich/monatlich, zusätzlich sind
Route-Maximum und Premium-Call-Limit konfigurierbar. Ohne gesetzte Limits ändert sich das
bisherige Verhalten nicht. Kritische Calls mit aktivem Hard-Limit und unbekanntem
Requestpreis werden fail-closed abgewiesen.

## Security und Betrieb

Der Proxy bindet ausschließlich `127.0.0.1:4000`. `/etc/kai/litellm.env` ist außerhalb des
Repositories, Besitzer `root:ubuntu`, Modus `0640`; systemd referenziert die Datei und
enthält keine Secrets. `kai-litellm.service` läuft als unprivilegierter Benutzer `ubuntu`,
ist gehärtet, restartbar und bewusst nicht mit `kai-server.service` verdrahtet oder
automatisch aktiviert. Der Status-Endpunkt ist read-only und zeigt nur Readiness-Bools,
nie Key-Metadaten.

## Trennung zu ADR 0015

`app/intelligence/*`, `KAI_LLM_*`, dessen `disabled|shadow`-Vertrag und
`influences_execution=false` bleiben unverändert. ADR 0015 ist weder der Production-Router
noch ein Gateway-Fallback. Beide Schichten haben getrennte Settings, Auditsemantik und
Authority-Grenzen.

## Alternativen

- Routing in jedem Business-Modul wurde verworfen: Fallback-, Retry-, Budget- und Audit-
  Drift wären unvermeidbar.
- Direkter Wechsel zu einem einzelnen günstigeren Provider wurde verworfen: Er löst die
  Anbieterabhängigkeit nicht.
- Big-Bang-Cutover wurde verworfen: Die gemessene stabile Baseline verlangt Shadow-Evidenz.
- Harte Server-Abhängigkeit zu LiteLLM wurde verworfen: `off` muss ohne Gateway booten.

## Graduation, Rollback und Konsequenzen

`primary` darf erst nach einem echten, kostenbehafteten und operatorautorisierten
Shadow-Fenster empfohlen werden. Mindestbericht: Schema-Erfolg, Failure-Rate, p50/p95,
Kosten/Call und /1000 Calls, Fallbackrate, Direction-/Critical-Field-Divergenz sowie
Consensus-Verhalten. Gegen die Baseline gelten zunächst: Schema-Erfolg ≥99,5 %,
registrierte Failure-Rate ≤0,5 %, p95 ≤20,9 s oder dokumentierte Qualitätsbegründung,
Direction-Disagreement ≤5 %, Critical-Field-Disagreement ≤2 % und keine Safety-Regression.
Diese Schwellen sind ein Review-Gate, keine automatische Umschaltung.

Rollback ist datenbankfrei: `KAI_INFERENCE_MODE=off` (oder
`KAI_INFERENCE_ENABLED=false`), `kai-server.service` neu starten, Status und Telemetrie
prüfen. Konsequenz: zusätzliche lokale Infrastruktur und Konfigurationspflege; im Gegenzug
werden Providerwahl, Schutzmechanismen und Kostenbeobachtung zentral und auditierbar.
