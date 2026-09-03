# ADR 0017 — `app/ai` ist die AI-Control-Plane; LiteLLM ist Transport darunter

- **Status:** **ACCEPTED — BINDEND** (Operator-Entscheid 2026-09-03).
- **Datum:** 2026-09-03
- **Betroffen:** jeder LLM-Aufruf in KAI, `app/ai/`, künftige `app/integrations/litellm/`, die direkten Provider, STT, Budget, Circuit-Breaker, Telemetrie, Deployment des Gateways
- **Verhältnis zu bestehenden ADRs:** ergänzt **D-CORE-001** (KAI CORE v1, ein AI-Gateway) um die Frage, was *unter* dem Gateway liegen darf. Lässt **ADR 0015** (Local Intelligence) unberührt — siehe „Abgrenzung zu ADR 0015". Ersetzt kein bestehendes ADR.
- **Präzisiert:** `docs/KAI_CORE_V1.md` § 3 (Addendum dort, keine Umschreibung)

## Kontext

D-CORE-001 hat vier parallele LLM-Pfade auf einen reduziert und `app/ai/` zur
einzigen Aufruf-Schicht gemacht. Im selben Zug wurde der Branch
`codex/llm-router-migration-20260901` (LiteLLM-Proxy) **nicht** gemergt, mit
Begründungen, die an der Gestalt jenes Branches hingen: fünfter Pfad, zweiter
Prozess, neue Dependency, God-File-Ratchet-Verstoß, eigene Deployment-Welt.

Diese Entscheidung ist wiederholt als „LiteLLM ist verworfen" gelesen worden.
Das steht so nirgends — verworfen wurde ein **Branch**, nicht eine
**Transport-Strategie**. Der Text sagt aber auch nicht das Gegenteil, und genau
diese Lücke hat die Fehllesart getragen. Dieses ADR schließt sie.

Die eigentliche Gefahr ist nicht LiteLLM, sondern die Form seiner ersten
Integration: sie brachte eine **zweite Control-Plane** (`app/inference/`) mit
eigener Telemetrie, eigenem Routing und eigenem Deployment neben `app/ai/`. Zwei
Wahrheiten über denselben Sachverhalt driften auseinander. KAI hat das am
2026-09-01 an der Runtime-Provenance teuer gelernt, als zwei Definitionen
desselben Prädikats nebeneinander standen und die schwächere entschied.

## Entscheidung

**Genau eine Architektur:**

```
KAI CALLERS
    ↓
app/ai/                    ← KAI AI CONTROL PLANE
    ↓
Provider-/Transport-Abstraktion
    ↓
LiteLLM  ODER  direkter Provider
    ↓
OpenAI / Anthropic / Gemini / Grok / weitere / lokal
```

Verbindlich:

| Zusicherung | Wert |
|---|---|
| `CONTROL_PLANE` | `app/ai` |
| `LITELLM_ROLE` | Provider-/Modell-**Transport** |
| `LITELLM_POSITION` | **unterhalb** `app/ai` |
| `KAI_POLICY_AUTHORITY` | `app/ai` |
| `KAI_AUDIT_AUTHORITY` | `app/ai` |
| `KAI_ROUTING_INTENT_AUTHORITY` | `app/ai` |
| `KAI_BUDGET_POLICY_AUTHORITY` | `app/ai` |
| `KAI_MODE_AUTHORITY` | `app/ai` |
| `TRADING_AUTHORITY` | **niemals** LiteLLM |
| `APP_INFERENCE_PARALLEL_CONTROL_PLANE` | **REJECTED** |

Der Transport darf langfristig variieren. **Die autoritative KAI-Policy darf
nicht variieren.** Kein Aufruf — auch kein Fallback, auch kein STT — darf Audit
oder Policy umgehen.

## Der Donor-Branch

`codex/llm-router-migration-20260901` @ `557a5e4cfd902e2e0ecb91120e865fcd2e00d61c`:

```
ARCHIVED / DO NOT MERGE AS A WHOLE     Branch-Entscheid aus D-CORE-001, unverändert
LiteLLM transport strategy             NOT REJECTED
Future LiteLLM integration             BELOW app/ai CONTROL PLANE
Parallel app/inference control plane   REJECTED
```

Er wird **nicht** rebased, **nicht** als Ganzes gemergt und **nicht** als zweite
Architektur wiederbelebt. Verwertbare Teile werden selektiv portiert und dabei
einzeln gegen die Mainline geprüft — kein blindes Cherry-Picking.

Portierungswürdig: Mode-Semantik (off/shadow/primary), Routing-Intent
(bulk/standard/reasoning/critical/stt), Fehlernormalisierung, bounded retry,
Circuit-Breaker, Budget-Governance, `AttemptTrace`/`InferenceResult`, echte
Provider-/Modell-Telemetrie, OpenAI-kompatibler HTTP-Transport, Gateway-Health,
Shadow-Vergleich, Evaluations- und Graduation-Grundlagen, Kosteninformationen
soweit beweisbar.

**Nicht** zu reproduzieren sind die Defekte des ersten Sprints: zweite
Control-Plane, zweite Telemetrie-Wahrheit, zweites Routing-SSOT, konkurrierende
Deployment-Welt, STT-Direktzugriff am Transportvertrag vorbei, alias-only
Circuit-Breaker trotz identifizierbarem Upstream, wirkungsloser Budget-Gate ohne
belastbare Schätzung, Router-Monolith, manuelle Graduation ohne
Mindestpopulation, unbewiesene Provider-/Modell-Erkennung, implizite
Produktions-Aktivierung.

## Mode-Vertrag

| Modus | Autoritativer Pfad | LiteLLM |
|---|---|---|
| `OFF` | bestehender direkter KAI-Pfad | nicht beteiligt |
| `SHADOW` | bestehender direkter KAI-Pfad | läuft parallel, **keine** Execution Authority |
| `PRIMARY` | LiteLLM für **explizit graduierte Routen** | direkter Provider bleibt kontrollierter Fallback |

`PRIMARY` wird **nie global** aktiviert. Graduation erfolgt **pro Route** und
bleibt Operator-Entscheidung; ein automatisches Umschalten gibt es nicht.

Die direkten Provider werden **nicht** entfernt. Sie sind Rollback, Notpfad,
Shadow-Komparator und Migrationssicherheit — aber sie liegen hinter derselben
Control-Plane.

## Was Beweis heißt

Unit-Tests genügen für `PRIMARY` **nicht**. Vorher real nachzuweisen sind:
LiteLLM startet auf dem Pi an einer kontrollierten Localhost-Grenze; je ein
echter Call gegen OpenAI, Gemini und — falls konfiguriert — Anthropic; der
tatsächliche Provider und das tatsächliche Modell; Request- bzw.
Correlation-ID; Input- und Output-Token; Latenz; Kosten **oder ausdrücklich
UNKNOWN**; Retry-, Fallback-, Circuit- und Rate-Limit-Verhalten; Auth-Fehler,
Timeout, Schemafehler, Gateway-down; der direkte Rollback; und dass sich an
Trading- und Execution-Gates nichts geändert hat.

Keine erfundenen Kosten. **`UNKNOWN` wird nie als 0 behandelt** — weder in der
Telemetrie noch im Budget. Eine harte Ablehnung pro Request setzt eine belegbare
Schätzung voraus; ohne sie wird nicht abgelehnt, sondern als unbekannt gebucht.

Graduation `SHADOW → PRIMARY` verlangt eine echte Population und weist
mindestens aus: Route, `sample_count`, Erfolgsrate, Schema-Valid-Rate,
Fallback-Rate, p50- und p95-Latenz, `cost_known_rate`, Kosten wo bekannt,
Qualitätsvergleich, Anteil bekannter Provider-Identität, Fehlerverteilung. Ohne
Mindeststichprobe gibt es kein „PROVEN".

## Circuit-Breaker

Der Circuit-Zustand darf **nicht** allein `Route:Alias` repräsentieren, wenn
LiteLLM den tatsächlichen Upstream meldet. Auseinanderzuhalten sind: logische
Route, angeforderter Alias, tatsächlicher Provider bzw. tatsächliches Modell.
Ein defekter Upstream darf nicht alle Alternativen desselben logischen Alias
sperren — sonst nimmt ein einzelner kaputter Anbieter genau die Ausweichwege
mit, die es in dem Moment braucht.

## STT

STT wird **kein** Sondertransport neben dem Gateway:
`KAI STT → app/ai-Vertrag → stt-Route → Transport`. Fallbacks, Audit, Mode,
Timeout und Fehlerklassifikation benutzen denselben Vertrag wie jede andere
Route.

## Abgrenzung zu ADR 0015

ADR 0015 (Local Intelligence) bleibt ein **eigener** Vertrag und wird mit dieser
Control-Plane **nicht** verschmolzen:

| | ADR 0015 | diese Control-Plane |
|---|---|---|
| Scope | lokal / Ollama | Multi-Provider |
| Modus | shadow-only | wirksame Analysepfade |
| Klassifikation | `untrusted_analysis` | reguläre Analyse |
| Execution | `influences_execution=false` | gemäß bestehenden Gates |
| Fallback | **kein** stiller Cloud-Fallback | Fallback erlaubt |

Diese Grenzen bleiben ausdrücklich bestehen.

## Deployment

Der LiteLLM-Dienst hält sich an die nach #848 geltende Deployment- und
Runtime-Provenance-Architektur: unveränderlicher Release-Baum, Zustand vom Code
getrennt, keine hartkodierten Pfade in einen beweglichen Checkout,
systemd-Änderungen **vor** dem ersten Restart, vollständig definierter Rollback.
Keine zweite Deployment-Welt.

## Konsequenzen

**Positiv:** eine Stelle entscheidet über Policy, Audit, Budget und
Routing-Absicht; der Transport wird austauschbar, ohne dass die Governance
mitwandert; die direkten Provider bleiben als Rückfallebene erhalten; LiteLLM
kann ohne Architekturbruch eingeführt und ohne Datenverlust wieder entfernt
werden.

**Negativ:** der Transportwechsel kostet eine zusätzliche Abstraktionsschicht,
und Teile des Donor-Branches müssen portiert statt übernommen werden — das ist
langsamer als ein Merge und der Preis dafür, dass am Ende **eine** Architektur
steht und nicht drei.

**Rückrollbarkeit:** `OFF` stellt den heutigen Zustand her; da die direkten
Provider bestehen bleiben, ist der Rückweg ein Moduswechsel und keine Migration.
