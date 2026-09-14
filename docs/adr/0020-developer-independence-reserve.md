# ADR 0020 — KAI Developer Independence: eine herstellerneutrale Entwicklerreserve neben, nicht in der Laufzeit

- **Status:** **ACCEPTED — BINDEND** (Operator-Entscheid 2026-09-14, D-CORE-009).
  Freigegeben ist die **Architekturentscheidung** und ihre Repo-Seite (Konfiguration,
  Skript, Client-Profil, Tests, Runbook). Die **Aktivierung** — Entwickler-Schlüssel
  bei den Anbietern, Werte in der Pi-`.env`, erster Proxy-Start, die drei
  Kontrollaufgaben — sind Operator-Schritte, siehe „Status der Umsetzung".
- **Datum:** 2026-09-14
- **Betroffen:** `config/litellm_dev.yaml`, `scripts/dev_reserve.sh`, `opencode.json`,
  `.env.example`, `docs/AI_HANDOFF.md`, `docs/runbooks/dev_reserve_opencode.md`
- **Baut auf:** [ADR 0017](0017-ai-control-plane-and-litellm-transport.md) (Control-Plane
  vs. Transport), [ADR 0019](0019-litellm-transport-runtime.md) (attestierter
  Transport-Baum). Ersetzt nichts, hebt nichts auf.
- **Ändert nicht:** `app/ai`, `config/litellm.yaml`, die Laufzeit-Fallback-Kette
  (openai → gemini → regelbasiert), `KAI_INFERENCE_*`, das Tagesbudget der Laufzeit,
  `deploy/systemd/*`. Ein Test hält jede dieser Grenzen fest
  (`tests/unit/test_dev_reserve_contract.py`).

## Kontext: die Laufzeit ist abgesichert, die Entwicklung nicht

ADR 0017/0019 und D-270 regeln, womit KAI *läuft*: Research-Briefings und Analysen
gehen über `app/ai`, wahlweise durch den LiteLLM-Transport, mit Budget, Telemetrie
und Fail-closed-Gates. Sie regeln nicht, womit KAI *weiterentwickelt* wird. Das ist
heute Codex (OpenAI) als primärer Entwicklungsagent und Claude Code (Anthropic) als
Integrator und Prüfer — beides native Clients an ihrem jeweiligen Hersteller.

Werden OpenAI oder Anthropic zu teuer, limitiert oder vorübergehend nicht erreichbar,
steht die Entwicklung. Die Laufzeit hätte ihre Fallback-Kette; die Entwicklung hat
keine. Und die naheliegende Abkürzung — die Entwicklerwerkzeuge in `app/ai` oder
in die produktive Fallback-Kette zu hängen — verletzte D-270 (*Inferenz berät,
entwickelt nicht*) und vermischte zwei Budgets, zwei Schlüsselkreise und zwei
Verantwortungen.

## Entscheidung: fünf Ebenen, klar getrennt

| Ebene | Aufgabe | Lösung |
|---|---|---|
| KAI-Laufzeit | Research-Briefings, Analysen | `app/ai` → LiteLLM (`config/litellm.yaml`, 127.0.0.1:4000) → Laufzeitmodelle |
| Hauptentwicklung | komplexe Änderungen, Integration, Review | Codex nativ, Claude Code nativ — **unverändert** |
| **Unabhängige Entwicklerreserve** | Ersatz bei Kosten, Limits, Ausfall | **OpenCode → LiteLLM-Dev-Proxy (`config/litellm_dev.yaml`, 127.0.0.1:4001) → `kai-dev-*`** |
| Lokale Reserve | Datenschutz, einfache Arbeiten | später Ollama/Qwen-Coder — **nicht Teil dieses ADR** |
| Freigabe | Merge, Deployment, Produktion | **ausschließlich der Operator** |

Die Reserve wird **nicht** in `app/ai` und **nicht** in die produktive Fallback-Kette
eingebaut. Sie erhält einen eigenen Zugang zum vorhandenen, attestierten
LiteLLM-Transport (ADR 0019): derselbe Baum, ein **zweiter Prozess** mit eigener
Konfiguration, eigenem Schlüssel und eigenem Port.

### Die drei Routen

| Alias | Entwicklerrolle | vorgesehenes Modell (Stand 2026-09-14) | Listenpreis in/out je 1M |
|---|---|---|---|
| `kai-dev-economy` | Analyse, Tests, kleine isolierte Fixes | DeepSeek V4.1 Flash (`deepseek/deepseek-flash`, seit 2026-09-10) | 0,30 / 1,20 USD (Spitzenzeit), außerhalb die Hälfte |
| `kai-dev-code` | reguläre Coding-Reserve, **Standard** | Kimi K2.7 Code (`moonshot/kimi-k2.7-code`) | 0,95 / 4,00 USD |
| `kai-dev-frontier` | komplexe, lange Repo-Aufgaben, **nur nach Freigabe je Aufgabe** | Kimi K3 (`moonshot/kimi-k3`, 1M Kontext) | 3,00 / 15,00 USD |

Rechenbeispiel je Sitzung (100.000 Eingabe-, 20.000 Ausgabetoken, ohne Cache):
economy ≈ 0,054 USD, code ≈ 0,175 USD, frontier ≈ 0,60 USD. K3 ist deshalb
Eskalationsstufe, kein Alltagsmodell.

Die Modell-IDs stehen **nicht** im Repository. Wie bei jeder Laufzeit-Route
(D-270/#956) sind Alias und Rolle fest, das Modell dahinter ist Konfiguration
(`KAI_DEV_LITELLM_<ROLLE>_MODEL`). Ein Anbieterwechsel ist ein `.env`-Eintrag,
kein Commit.

## Was bewusst NICHT gebaut wurde — und warum

**Kein LiteLLM-Virtual-Key mit Budget.** Virtual Keys, Modell-Whitelists je
Schlüssel und `litellm_settings.max_budget` werden gegen Verbrauch in der
LiteLLM-**Datenbank** durchgesetzt. Der Transport auf der Pi läuft ohne Datenbank
(ADR 0019); dort ist `max_budget` **fail-open** — der Proxy warnt einmal beim Start
und bedient danach Anfragen über das Limit hinaus. Eine Budgetzeile in der
Konfiguration wäre eine Bremse, die nicht bremst. Statt dessen:

1. **Modell-Whitelist durch Trennung:** der Dev-Proxy *kennt* nur die drei
   `kai-dev-*`-Routen. Es gibt keinen Weg von diesem Port zu `kai-standard`,
   `kai-kimi-research` oder einer anderen Laufzeit-Route.
2. **Schlüsseltrennung durch Prozess:** `LITELLM_DEV_MASTER_KEY` öffnet nur den
   Dev-Proxy; `LITELLM_MASTER_KEY` der Laufzeit taucht in seiner Umgebung nicht
   auf und verlässt die Pi nie.
3. **Budgettrennung beim Anbieter:** jede Route trägt einen **eigenen
   Entwickler-Schlüssel** (`KAI_DEV_LITELLM_<ROLLE>_API_KEY`), nicht
   `MOONSHOT_API_KEY`/`DEEPSEEK_API_KEY` der Laufzeit. Der Verbrauch ist beim
   Anbieter getrennt sichtbar, und Limits werden dort am Schlüssel gesetzt. Das
   KAI-Laufzeitbudget (1,25 USD/Tag, D-CORE-007/008) wird davon nicht berührt —
   die Reserve schreibt nicht in `artifacts/llm_telemetry.jsonl`.
4. **Fail-closed-Probe:** `scripts/dev_reserve.sh smoke` stellt je Route eine
   Mini-Anfrage und verlangt HTTP 200, eine gemeldete Modellidentität und einen
   Kostenkopf `x-litellm-response-cost` größer 0. Der Transport setzt unbekannte
   Kosten still auf 0 (Befund 2026-09-08); eine Route, die 0 meldet, wird nicht
   benutzt, bis die Kostenmessung steht.

Eine datenbankgestützte Virtual-Key-Verwaltung bleibt ein möglicher späterer
Schritt. Sie braucht Postgres auf der Pi und ist damit eine eigene Entscheidung.

**Keine systemd-Unit, kein Timer.** Die Reserve läuft nur, wenn ein Mensch sie
startet, im Vordergrund, und endet mit Ctrl-C. Nichts läuft ungefragt mit,
nichts landet in der Watchdog-Schleife (`DEFERRED_UNITS`), nichts kostet über
Nacht.

**Kein Umbau von Claude Code oder Codex.** Claude Code bleibt nativ; keine
globale `ANTHROPIC_BASE_URL`, keine Konfigurationsdatei, die zwischen Anthropic und
Kimi hin- und hergeschrieben wird. Codex *kann* über ein eigenes Profil
(`codex --profile kai-dev`) denselben Dev-Proxy nutzen — das ist bequem, macht
aber nur das Modell, nicht den Client herstellerunabhängig. Deshalb ist OpenCode die
Reserve, nicht ein Codex-Profil.

## Stabilitätsregeln (verbindlich)

- **Kein automatischer Anbieterwechsel in einer schreibenden Sitzung.** Ein
  Modellwechsel mitten in Tool-Aufrufen, Dateiänderungen oder Tests verliert
  Kontext, Tool-IDs und Annahmen. Bei Codeänderungen wird das Modell für die
  gesamte Sitzung gepinnt.
- **Bei Ausfall:** Sitzung stoppen, Diff und Teststand sichern, standardisierten
  Handoff erzeugen (Runbook §5), neue Sitzung mit dem Ersatzmodell beginnen.
- **Automatisches Fallback nur bei rein lesenden Analysen.**
- **Jeder Entwickler in eigenem Worktree** (`kai_new_worktree.sh`). Ein frischer
  Worktree trägt keine `.env` — das ist die erste Schutzschicht, das
  OpenCode-Profil (`permission.read` verweigert `*.env`, `*.macaroon`, `*.pem`,
  `*.key`, `*wallet*`, `*secrets*`) die zweite.
- **Kein Agent mergt, deployt oder verändert Produktion.** Das OpenCode-Profil
  verweigert `git push`, `git merge`, `git rebase`, `git reset --hard`,
  `gh pr merge`, `gh pr create`, `ssh`, `scp`, `rsync`, `sudo`, `systemctl`; alles
  andere außer Lesen/Testen fragt. Die Verbote stehen **nach** der `*`-Regel,
  weil bei OpenCode die letzte passende Regel gewinnt.
- **Unbekannte Modellidentität oder fehlende Kostenmessung → FAIL_CLOSED**
  (Smoke, siehe oben).

## Sicherheit

- Eigene Entwickler-Schlüssel beim Anbieter; Provider-Schlüssel nur im Gateway
  (Pi), nie auf dem Client.
- Der Dev-Proxy bindet fest `127.0.0.1:4001`. Vom Laptop ausschließlich per
  SSH-Tunnel (`scripts/dev_reserve.sh tunnel`). Kein Port wird geöffnet.
- `scripts/dev_reserve.sh proxy` exportiert **nur** `LITELLM_DEV_MASTER_KEY` und
  `KAI_DEV_LITELLM_*` aus der `.env` — nicht die Datei als Ganzes. Fehlt eine der
  sieben Variablen: kein Start.
- Der einzige Wert, der den Laptop erreicht, ist `LITELLM_DEV_MASTER_KEY`
  (dort `KAI_DEV_LITELLM_KEY`). Er öffnet ausschließlich den Dev-Proxy.
- Feste Aliasse, gepinnte Transport-Version (ADR 0019, attestiert bei jedem Start).
- Was der Proxy protokolliert, sieht der Operator im Vordergrund-Terminal; was die
  Sitzung kostet, zeigt OpenCode je Sitzung und der Anbieter je Schlüssel.

## Einführung (Reihenfolge)

1. Kimi-Laufzeitabschluss (#957, #953 × #954, #961, #962) — **erledigt** vor diesem ADR.
2. Diese Entscheidung in `docs/AI_HANDOFF.md` §3b und `DECISION_LOG.md` D-CORE-009 — **dieser PR**.
3. `kai-dev-*`-Routen, eigener Dev-Key, Skript, Tests — **dieser PR**.
4. OpenCode ausschließlich mit dem Dev-Key verbinden (`opencode.json`) — **dieser PR**;
   erster Start ist Operator-Schritt (Runbook §1–§3).
5. Drei kontrollierte Aufgaben (Runbook §4): Repository nur analysieren · kleinen
   isolierten Fehler beheben und Tests ausführen · bestehenden Diff unabhängig prüfen.
   Kosten, Zeit, Testresultate und unerlaubte Änderungen vergleichen.
6. **Erst danach** Kimi K2.7 Code als freigegebene Entwicklerreserve einstufen
   (Operator-Entscheid, `docs/AI_HANDOFF.md` §6).

## Status der Umsetzung

| | |
|---|---|
| Architekturentscheidung | **ACCEPTED** |
| Repo-Seite (Konfig, Skript, Client-Profil, Tests, Runbook) | **in diesem PR** |
| Entwickler-Schlüssel bei Moonshot/DeepSeek anlegen | **erledigt** 2026-09-14 |
| Sieben `.env`-Werte auf der Pi | **erledigt** 2026-09-14 |
| Release mit diesem PR auf der Pi | **erledigt**, Release `5972835e` (2026-09-14 12:21Z) |
| Erster Proxy-Start + Smoke | **erledigt**, economy + code PASS (2026-09-14) |
| Drei Kontrollaufgaben | **erledigt**, `artifacts/operator_memos/dev_reserve_trial_2026-09-14.md` |
| Einstufung K2.7 Code als freigegebene Reserve | **erteilt** 2026-09-14 (Operator); economy als Analyse-Reserve, frontier ungeprüft |
| Lokale Reserve (Ollama/Qwen) | **bewusst noch nicht** |
| DB-gestützte Virtual Keys | **bewusst noch nicht** |
