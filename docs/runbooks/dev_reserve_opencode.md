# Runbook — Entwicklerreserve: OpenCode → LiteLLM-Dev-Proxy → `kai-dev-*`

Gehört zu [ADR 0020](../adr/0020-developer-independence-reserve.md) und D-CORE-009.
Alles hier ist Operator-Handarbeit. Nichts davon läuft ungefragt, nichts davon
berührt `app/ai`, die Laufzeit-Fallback-Kette oder eine systemd-Unit.

## Laptop: KAI Developer Hub (empfohlener Einstieg)

Auf Windows bündelt der lokale Hub die bislang manuellen Schritte, ohne die
Architekturgrenze aufzuweichen:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_kai_dev_hub.ps1
python scripts/kai_dev_hub.py status
python scripts/kai_dev_hub.py ui
```

Der Installer kopiert Hub und Workflow in
`%USERPROFILE%\.kai\developer-hub\app\v<Version>` (aktuell 0.3.2), schreibt `install.json` mit
Quell-SHA und Datei-Hashes und erzeugt **KAI Developer Hub** auf dem Desktop.
Der Shortcut zeigt auf diese versionierte Kopie, nicht auf einen Donor-Branch.
Ein Worktree mit detached HEAD ist als Quelle zulässig. Die Herkunft belegt dann
`source_head` (voller SHA), `source_branch` ist `null` und `source_detached` ist `true`.
Der Installer löst erst alles auf und schreibt dann in einen Staging-Ordner, der
per Umbenennen zur Version wird. Scheitert ein Schritt, bleibt die vorhandene
Version unangetastet. Für Tests gibt es `-InstallRoot` und `-SkipShortcut`.
`python scripts/kai_dev_hub.py --version` zeigt die Version. Vor Installation
den geprüften Donor-Commit verwenden; ein lokaler Shortcut ersetzt weder
Claudes unabhängigen Review noch einen Mainline-Merge.

Jede schreibende Aufgabe beginnt mit **Neue Aufgabe**. Der Hub holt die
autoritative Remote-Base und erzeugt dafür einen eigenen `codex/dev-task-*`-
Worktree. Scheitert der Fetch, zeigt 0.3.2 den letzten verifizierten
Remote-Tracking-SHA samt Alter und verlangt eine ausdrückliche Bestätigung;
lokale Branch-Köpfe sind keine Offline-Basis. Offline-Sitzungen tragen in der
Liste die Markierung **OFFLINE-BASIS**. Verwaiste Sitzungen bleiben mit Grund
sichtbar und `prune-sessions` archiviert nur ihre JSON-Datei, ohne Git oder den
alten Pfad anzufassen. Ein Client-Start im gemeinsamen Checkout wird abgewiesen. Alle
Oberflächen bleiben auf ihr gewähltes Modell für die jeweilige Sitzung gepinnt:

- **OpenCode lokal (offline):** startet das vorhandene Ollama-Modell
  `kai-qwen3-coder:30b-64k`; kein Internet und kein Anbieter-Schlüssel nötig.
  Ab Hub 0.3.1 nicht mehr `30b-16k`: Bei der Abnahme am 23.09. war der
  OpenCode-Prompt nach einer gelesenen Datei 16 942 Token lang, und Ollama kürzte
  auf 16 384. Die Antwort ignorierte daraufhin das geforderte Format. Mit 64K war
  sie korrekt. Das Modell wird über ein hub-eigenes Konfigurationsfragment
  (`OPENCODE_CONFIG` → `developer-hub\opencode-local.json`, nur Loopback)
  bekanntgemacht; die globale OpenCode-Konfiguration bleibt unverändert.
  Das kompakte KAI-Kontextpaket wird im Startprompt mitgegeben, weil das
  OpenCode-Profil externe Verzeichnisse nicht lesen darf.
- **OpenCode Cloud-Reserve:** öffnet in einem neuen, auf `kai-dev-code`
  gepinnten Prozess. Der Hub holt ausschließlich den Dev-Proxy-Schlüssel über
  den bereits autorisierten SSH-Zugang in den Prozessspeicher, startet
  Dev-Proxy plus Loopback-Tunnel und prüft Katalog und eine echte kurze
  `kai-dev-code`-Antwort mit positiver Kostenmessung.
  Der Schlüssel wird weder in eine Laptop-Datei noch in ein Log geschrieben.
- **Hermes lokal:** startet Hermes TUI mit dem Aufgaben-Worktree als
  Arbeitsverzeichnis und gepinntem lokalem Provider. Hermes nutzt wegen seiner
  Mindestanforderung das vorhandene `kai-qwen3-coder:30b-64k`, lädt dadurch
  `AGENTS.md` und arbeitet nicht mehr als projektloser Chat. Der Hub setzt
  Reasoning auf `none` (andernfalls antwortet das lokale Modell mit HTTP 400)
  und legt den ersten Kontextprompt in die Zwischenablage: **einmal in Hermes
  einfügen und absenden**. Ab 0.3.1 setzt der Hub zusätzlich `TERMINAL_CWD`
  auf den Aufgaben-Worktree. Hermes löst Werkzeugpfade danach auf, nicht nach
  `--in`, und ohne `TERMINAL_CWD` fand `read_file` bei der Abnahme am 23.09.
  `scripts/dev_reserve.sh` nicht (Suche im Home-Verzeichnis). Die globale Hermes-
  Konfiguration wird nicht verändert. Der LiteLLM-Dev-Proxy bleibt OpenCodes
  Cloud-Reserve; Hermes' vorgeschaltete Key-/Kostenprüfung benötigt eine
  LiteLLM-Datenbank und ist mit dem bewusst datenbanklosen Dev-Proxy inkompatibel.
- **Kimi + Kontextpaket:** öffnet Kimi und markiert das vollständige
  `%USERPROFILE%\.kai\developer-hub\context\<session>\KAI_CONTEXT_*.md` zum
  Anhängen. Die Datei enthält tatsächliche Auszüge der versionierten Regeln,
  Architektur und Übergabe mit Quell-Hashes und ist ohne Windows-Dateizugriff
  verständlich. Kimi
  bleibt eine Beratungsoberfläche; garantierter lokaler Schreibzugriff besteht
  nur über OpenCode oder Hermes.

Der Hub schaltet niemals das Modell innerhalb einer schreibenden Sitzung um.
Jeder Button startet eine neue, explizit gepinnte Sitzung. Damit bleibt die
Handoff-Regel unten erhalten.

### Nachweisbare Übergaben

Die Hub-Oberfläche sichert zunächst einen Snapshot (binärer Patch für
getrackte Änderungen plus sichere ungetrackte Dateien und Hash-Manifest) und
schreibt dann strukturierte Übergaben nach
`%USERPROFILE%\.kai\developer-hub\handoffs\ledger.jsonl`. Jeder Beleg enthält
Agenten, Auftrag, Branch, exakten HEAD, Worktree-Status, Erledigtes, Offenes,
Annahmen, nächsten Schritt und Tests. SHA-256 und `previous_sha256` bilden eine
append-only Prüfkette. Das weist nachträgliche Veränderung und Reihenfolge nach,
nicht jedoch die reale Identität eines externen Modells. Der Empfänger muss
die individuelle `ACK:`-Challenge und die offene Aufgabe in seiner Antwort
bestätigen. Diese Antwort wird als verkettetes Ack-Ereignis erfasst. Ohne Ack
bleibt die Übergabe im Hub-Status sichtbar offen. Ein Ack ist ein dokumentierter
Empfangsbeleg, keine kryptographische Modell-Authentisierung.

Ab Hub 0.3.0 (Beleg-Schema 2) nennt das Kontextpaket die **Übergabe-ID**
getrennt von der Session-ID, dazu den Beleg-SHA-256, den Vorgänger-Beleg und den
Rückverweis auf `ledger.jsonl`. Das Ack muss die Übergabe-ID wörtlich enthalten.
Eine Session-ID an ihrer Stelle wird abgelehnt (Kimi-Befund vom 22.09.).

Aufgabenspezifischer Quelltext wird nur explizit mitgegeben: `--source <pfad>`
(wiederholbar, höchstens 12 Dateien). Zugelassen sind nur versionierte UTF-8-Dateien
im Aufgaben-Worktree. Secret-Namen (`.env`, `*.key`, `*secret*` …) und Inhalte,
die der gemeinsame Katalog `scripts/secret_guard.py` oder eine Schlüsselzuweisung
trifft, werden vor Snapshot und Ledger abgelehnt; die Meldung nennt Datei und
Zeile, nie den Wert. Je Datei stehen im Paket der Git-Blob bei HEAD, der
SHA-256 der Arbeitsdatei, ob sie von HEAD abweicht, die übernommenen Zeichen
und ein sichtbares `TRUNCATED`, wenn gekürzt wurde (Budget voll 90 000 Zeichen /
24 000 je Datei, kompakt 12 000 / 6 000).

```powershell
python scripts/kai_dev_hub.py handoff --from-agent OpenCode --to-agent Hermes `
  --task "..." --completed "..." --open-items "..." --next-action "..." --tests "..." `
  --source scripts/dev_reserve.sh --source config/litellm_dev.yaml `
  --source tests/unit/test_dev_reserve_contract.py
python scripts/kai_dev_hub.py verify-handoffs
python scripts/kai_dev_hub.py --repo <aufgaben-worktree> ack `
  --handoff-id <id> --agent Hermes --response-file <antwort.txt>
```

Bei Anbieterausfall: Arbeit im Aufgaben-Worktree stoppen, `git status --short`
prüfen, im Hub **Übergabe** samt Snapshot erzeugen, neuen Client auf derselben
Aufgabe starten, Kontext übernehmen und die Antwort mit Challenge als Ack
erfassen. Kein Modellwechsel innerhalb einer schreibenden Sitzung.

Cloud-Reserve beenden: **Cloud-Tunnel stoppen** bzw. `stop-cloud`. Das räumt auch nach einem
Absturz, Tunnelabbruch oder Neustart auf: Beendet wird nur eine noch lebende `ssh.exe` mit der
gespeicherten PID, der vom Hub gestartete Pi-Proxy wird über seine PID-Datei samt
Befehlszeilenprüfung beendet. Der Zustand bleibt stehen, solange die Pi nicht erreichbar ist,
sodass ein späterer Aufruf das Aufräumen abschließt. Ein Fehlstart räumt sich selbst auf.

Diagnose ohne Pi/Anbieter: `doctor --mode local-inference` prüft eine echte
Ollama-Antwort. `doctor --mode cloud` prüft Economy und Code mit echter kurzer
Antwort, Modellfeld und positivem Kostenkopf; Frontier wird nicht automatisch
aufgerufen. `automations` zeigt lokale KAI-Tasks mit Zustand, letztem Lauf,
Ergebnis und nächstem Termin; `last_success`/`last_failure` stammen aus Event 201
(Rückgabecode der Aktion), nicht aus Event 102, das auch Fehlläufe als „abgeschlossen“ meldet; Pi-systemd und Codex-Automationen sind dort
ausdrücklich *nicht geprüft*. Der Installer registriert einen stündlichen,
anbieterfreien `KAI-Developer-Reserve-Health`-Task: Nach einem Neustart startet
er Ollama lokal bei Bedarf und prüft Modelle, Übergaben und Automationen, ohne
eine Inferenz- oder Pi-Anfrage auszulösen. Ein
`last_result` ungleich 0 bleibt sichtbar, auch wenn `StartWhenAvailable` für
einen verpassten Lauf korrigiert wurde; Erfolg erst nach tatsächlich grünem Lauf.

## 0. Voraussetzungen

- Ein Release auf der Pi, das `config/litellm_dev.yaml` und `scripts/dev_reserve.sh`
  enthält (`ls /home/kai/current/config/litellm_dev.yaml`).
- Der attestierte LiteLLM-Transport (ADR 0019) liegt unter `/home/kai/transport/litellm/current`.
- **Eigene Entwickler-Schlüssel** bei Moonshot und DeepSeek — nicht die
  Laufzeit-Schlüssel. Dort das Ausgabenlimit am Schlüssel setzen; der Proxy ohne
  Datenbank kann keines durchsetzen.
- Sieben Werte in `/home/kai/ai_analyst_trading_bot/.env` (Vorlage: `.env.example`,
  Block „Entwicklerreserve"). Fehlt einer, startet der Proxy nicht.

```
LITELLM_DEV_MASTER_KEY=<neu erzeugt, z. B. openssl rand -hex 32>
KAI_DEV_LITELLM_ECONOMY_MODEL=deepseek/deepseek-flash
KAI_DEV_LITELLM_CODE_MODEL=moonshot/kimi-k2.7-code
KAI_DEV_LITELLM_FRONTIER_MODEL=moonshot/kimi-k3
KAI_DEV_LITELLM_ECONOMY_API_KEY=<DeepSeek-Dev-Key>
KAI_DEV_LITELLM_CODE_API_KEY=<Moonshot-Dev-Key>
KAI_DEV_LITELLM_FRONTIER_API_KEY=<Moonshot-Dev-Key>
```

Modell-IDs vor dem Setzen gegen die Anbieterliste prüfen (Stand 2026-09-14).

**Nach jeder verdeckten Eingabe die Länge prüfen** (Vorfall 14.09.: Einfügen im Windows-Terminal
lieferte jeden Schlüssel doppelt, beide Anbieter antworteten 401):
`grep -E "^KAI_DEV_LITELLM_.*_API_KEY=" .env | awk -F= '{print $1, length($2)}'` — DeepSeek 35,
Moonshot 51 Zeichen. Nach jeder Änderung an diesen Werten den Proxy neu starten; LiteLLM liest
die Umgebung nur beim Start.

## 1. Proxy starten (auf der Pi, Vordergrund)

```
ssh ubuntu@192.168.178.23
bash /home/kai/current/scripts/dev_reserve.sh proxy
```

Erwartet: `DEV_RESERVE_PROXY_START host=127.0.0.1 port=4001 …`, dann
`TRANSPORT_VERIFIED …` aus `pi_transport_exec.sh`, dann der LiteLLM-Start.
Der Proxy bindet nur 127.0.0.1. Ctrl-C beendet ihn. Fehlermarker:
`DEV_RESERVE_ENV_INCOMPLETE` (Variable fehlt), `DEV_RESERVE_PORT_BUSY` (läuft schon),
`DEV_RESERVE_NO_CONFIG` (Release ohne ADR 0020).

## 2. Tunnel (auf dem Laptop, zweites Terminal)

```
bash scripts/dev_reserve.sh tunnel      # gibt den Befehl aus
ssh -N -L 4001:127.0.0.1:4001 ubuntu@192.168.178.23
```

## 3. Smoke — Fail-closed (auf dem Laptop, drittes Terminal)

```
export KAI_DEV_LITELLM_KEY=<LITELLM_DEV_MASTER_KEY>     # nur diese Sitzung, nie in eine Datei
bash scripts/dev_reserve.sh smoke                     # economy + code
bash scripts/dev_reserve.sh smoke --include-frontier  # nur bewusst, K3 kostet
```

Je Route eine Zeile: `PASS route=… model=… cost_usd=…` oder `FAIL_CLOSED … <Grund>`.
Gründe: `http=<code>`, `modell_unbekannt`, `kosten_fehlen`, `kosten_null`.
**Eine Route mit `FAIL_CLOSED` wird nicht benutzt.** `kosten_null` heißt: LiteLLM
kennt den Preis des Modells nicht und setzt 0 — dann fehlt die Kostenmessung, nicht
das Geld. Bis das steht (Preis in der LiteLLM-Kostenkarte oder `model_info` mit
`input_cost_per_token`/`output_cost_per_token`), bleibt die Route zu.

## 4. OpenCode starten — nur im frischen Worktree

```
bash ~/KAI-mirror/scripts/kai_new_worktree.sh dev-reserve/<aufgabe>
cd /c/tmp/kai-dev-reserve-<aufgabe>-…
export KAI_DEV_LITELLM_KEY=<LITELLM_DEV_MASTER_KEY>
opencode
```

`opencode.json` im Repo-Wurzelverzeichnis wird automatisch gelesen. Es kennt genau
einen Provider (`kai-litellm-dev`, `http://127.0.0.1:4001/v1`), Standardmodell
`kai-dev-code`, Kleinmodell `kai-dev-economy`. `kai-dev-frontier` wird nur nach
ausdrücklicher Freigabe **je Aufgabe** gewählt (`/models` in OpenCode).

Der frische Worktree hat keine `.env`; das Profil verweigert zusätzlich das Lesen
von `*.env*`, `*.macaroon`, `*.pem`, `*.key`, `*wallet*`, `*secrets*` und jeden
`git push`/`merge`/`rebase`/`reset --hard`, `gh pr merge`/`create`, `ssh`, `scp`,
`rsync`, `sudo`, `systemctl`. Alles außer Lesen und Testen fragt.

Kimi K2.7 Code denkt sichtbar und lang („Thought“-Blöcke, auch über seine eigenen Werkzeugregeln).
Das ist Text, keine Schleife. Ein Durchgang ist zu Ende, wenn die Eingabezeile zurückkommt; Strg-C
bricht die laufende Antwort ab. Sitzungen lassen sich nachträglich aus
`~/.local/share/opencode/opencode.db` (Tabellen `session`, `message`, `part`) lesen, inklusive
Token je Schritt; die Kostenanzeige in OpenCode bleibt für diesen Provider 0,00.

### Die drei Kontrollaufgaben (Einführung, ADR 0020 §Einführung)

| # | Aufgabe | Modell | Erlaubt | Messen |
|---|---|---|---|---|
| 1 | Repository nur analysieren (z. B. „Wie fließt ein Alert von `app/analysis` bis Telegram?") | `kai-dev-economy` | nur lesen | Kosten, Dauer, Sachfehler |
| 2 | Kleinen isolierten Fehler beheben, Tests ausführen (ein `tests/unit/`-Fall, eine Datei) | `kai-dev-code` | Edit nach Rückfrage, `pytest`/`ruff` | Kosten, Dauer, Testresultat, **unerlaubte Änderungen** (`git status` gegen den Auftrag) |
| 3 | Bestehenden Diff unabhängig prüfen (`git diff <mainline>...HEAD` eines offenen PR) | `kai-dev-code` | nur lesen | Kosten, Dauer, Treffer gegen Claude-Code-Review |

Ergebnis je Aufgabe in `artifacts/operator_memos/dev_reserve_trial_<datum>.md`
festhalten (Kosten aus OpenCode-Sitzung **und** Anbieterkonsole, Zeit, Testzahlen,
Abweichungen). Erst mit drei Einträgen fällt die Einstufung „freigegebene Reserve"
(Operator, `docs/AI_HANDOFF.md` §6).

## 5. Ausfall mitten in der Sitzung — Handoff statt Wechsel

Kein Modellwechsel in einer schreibenden Sitzung. Stattdessen:

1. Sitzung stoppen. `git diff > /c/tmp/handoff_<aufgabe>.diff`, `git status --short`.
2. Teststand: `python -m pytest <betroffene tests> -q | tail -3`.
3. Handoff-Datei mit: Auftrag (wörtlich) · erledigt · offen · Annahmen ·
   nächster Schritt · Diff-Pfad · Teststand.
4. Neue Sitzung mit dem Ersatzmodell, Handoff als erste Nachricht.

Bei rein lesenden Analysen darf direkt mit einem anderen `kai-dev-*` weitergemacht werden.

## 6. Optional: Codex-Profil auf denselben Proxy

Bequem, aber nicht herstellerunabhängig (Client bleibt OpenAI). Nur in
`~/.codex/config.toml`, nie im Repo:

```toml
[model_providers.kai-dev]
name = "KAI LiteLLM Dev-Proxy"
base_url = "http://127.0.0.1:4001/v1"
env_key = "KAI_DEV_LITELLM_KEY"

[profiles.kai-dev]
model_provider = "kai-dev"
model = "kai-dev-code"
```

Aufruf `codex --profile kai-dev`. `codex` ohne Profil bleibt OpenAI.

## 7. Was hier nie passiert

- Kein Eintrag in `config/litellm.yaml`, kein `kai-dev-*` in `app/ai`.
- Keine Unit, kein Timer, kein Autostart.
- Kein Produktionsschlüssel im Dev-Proxy, kein Master-Key auf dem Laptop.
- Kein Merge, kein Deploy, kein Pi-Zugriff durch den Reserve-Client.
- Claude Code wird nicht umgestellt; keine globale `ANTHROPIC_BASE_URL`.
