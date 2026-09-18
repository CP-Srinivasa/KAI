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

Der Installer erzeugt **KAI Developer Hub** auf dem Desktop. Dort gibt es vier
bewusst getrennte Oberflächen:

- **OpenCode lokal (offline):** startet das vorhandene Ollama-Modell
  `kai-qwen3-coder:30b-16k`; kein Internet und kein Anbieter-Schlüssel nötig.
- **OpenCode Cloud-Reserve:** öffnet in einem neuen, auf `kai-dev-code`
  gepinnten Prozess. Der Hub holt ausschließlich den Dev-Proxy-Schlüssel über
  den bereits autorisierten SSH-Zugang in den Prozessspeicher, startet
  Dev-Proxy plus Loopback-Tunnel und prüft den authentifizierten Modellkatalog.
  Der Schlüssel wird weder in eine Laptop-Datei noch in ein Log geschrieben.
- **Hermes lokal:** startet Hermes TUI mit dem KAI-Checkout als
  Arbeitsverzeichnis und gepinntem lokalem Provider. Hermes nutzt wegen seiner
  Mindestanforderung das vorhandene `kai-qwen3-coder:30b-64k`, lädt dadurch
  `AGENTS.md` und arbeitet nicht mehr als projektloser Chat. Die globale Hermes-
  Konfiguration wird nicht verändert. Der LiteLLM-Dev-Proxy bleibt OpenCodes
  Cloud-Reserve; Hermes' vorgeschaltete Key-/Kostenprüfung benötigt eine
  LiteLLM-Datenbank und ist mit dem bewusst datenbanklosen Dev-Proxy inkompatibel.
- **Kimi + Kontextpaket:** öffnet Kimi und erzeugt/markiert
  `%LOCALAPPDATA%\KAI\DeveloperHub\KAI_CONTEXT_FOR_KIMI.md` zum Anhängen. Kimi
  bleibt eine Beratungsoberfläche; garantierter lokaler Schreibzugriff besteht
  nur über OpenCode oder Hermes.

Der Hub schaltet niemals das Modell innerhalb einer schreibenden Sitzung um.
Jeder Button startet eine neue, explizit gepinnte Sitzung. Damit bleibt die
Handoff-Regel unten erhalten.

### Nachweisbare Übergaben

Die Hub-Oberfläche schreibt strukturierte Übergaben nach
`%LOCALAPPDATA%\KAI\DeveloperHub\handoffs\ledger.jsonl`. Jeder Beleg enthält
Agenten, Auftrag, Branch, exakten HEAD, Worktree-Status, Erledigtes, Offenes,
Annahmen, nächsten Schritt und Tests. SHA-256 und `previous_sha256` bilden eine
append-only Prüfkette. Das weist nachträgliche Veränderung und Reihenfolge nach,
nicht jedoch die reale Identität eines externen Modells.

```powershell
python scripts/kai_dev_hub.py handoff --from-agent OpenCode --to-agent Hermes `
  --task "..." --completed "..." --open-items "..." --next-action "..." --tests "..."
python scripts/kai_dev_hub.py verify-handoffs
```

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
