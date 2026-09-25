# Runbook: LiteLLM-Transport auf der Pi (Smoke, Rollback, Transport-Wechsel)

Gilt für `kai-litellm.service` (127.0.0.1:4000, ADR 0019). Der Dev-Proxy :4001
hat sein eigenes Runbook (`dev_reserve_opencode.md`).

**Grundregeln**

- Der Proxy läuft gewollt auch dann, wenn ihn niemand aufruft (D-280). Leerlauf
  ist kein Befund.
- **Nie** das nackte `/health` aufrufen, weder auf :4000 noch auf :4001. Es löst
  echte, kostenpflichtige Modellaufrufe aus. Ein Wächtertest schützt die Skripte
  im Repo davor (#1062), aber nicht die Handbefehle.
- Dieses Runbook beschreibt keine Modellaufrufe. Jeder Aufruf mit Kosten braucht
  eine eigene Freigabe.
- Pfade: `/home/kai` ist ein Symlink auf `/home/ubuntu`. Die Unit ist
  release-gebunden (`runtime-exec --repo /home/kai/current`) und wird deshalb von
  `pi_release_deploy.sh` mit neu gestartet.

## 1. Smoke nach jedem Release und jedem Transport-Wechsel

```bash
cd /home/ubuntu/current
bash scripts/pi_litellm_smoke.sh            # erwartet: 3× PASS, Exit 0
pid=$(systemctl show kai-litellm -p MainPID --value)
readlink -f /proc/$pid/cwd                  # = /home/ubuntu/releases/<laufender SHA>
grep TRANSPORT_VERIFIED /home/ubuntu/ai_analyst_trading_bot/logs/litellm.err.log | tail -1
# Die Unit schreibt stdout/stderr per StandardOutput/StandardError=append: in
# logs/litellm.log und logs/litellm.err.log -- das Journal zeigt nur Start/Stop.
```

Soll:

| Probe | Soll | Bedeutung |
|---|---|---|
| `GET /health/liveliness` | 200 | Prozess antwortet, ohne Modellaufruf |
| `GET /v1/models` ohne Key | 401 | Auth-Callback `config/kai_litellm_auth_status.py` greift (#1000/#1046) |
| `GET /v1/models` mit falschem Key | 401 | dito |

Ohne den Callback antwortet LiteLLM 1.99.0 ohne Datenbank mit **500** (kein Key)
und **400 "No connected db."** (falscher Key). Stand 25.09.2026 unter Runtime
`d6f8058f`, gemessen vor dem Release mit #1046. Liefert der Smoke nach einem
Release mit #1046 noch 500/400, dann lädt der Proxy die alte Konfiguration
(cwd prüfen) oder der Callback lässt sich nicht importieren (`logs/litellm.err.log` prüfen).

## 2. Release-Rollback (ganzer KAI-Baum)

Nur, wenn ein Release scheitert. Es gibt dafür kein eigenes Skript. Rollback
heißt: das Vorgänger-Release erneut aktivieren und alle release-gebundenen Units
neu starten.

```bash
ls /home/ubuntu/releases/                   # --keep 3: der Vorgaenger liegt hier
ALT=/home/ubuntu/releases/<voller-alter-sha>
cd /home/ubuntu/ai_analyst_trading_bot
bash scripts/pi_activate_release.sh --release "$ALT" --current /home/ubuntu/current \
     --state /home/ubuntu/ai_analyst_trading_bot
source scripts/lib/pi_release_guard.sh
for u in $(pi_release_bound_units "$ALT/deploy/systemd"); do
    sudo -n /usr/local/sbin/kai-service-control restart "$u"
done
```

Danach prüfen:

- `curl -s 127.0.0.1:8000/health` meldet `runtime_commit` = alter SHA.
- Jede release-gebundene Unit ist `active`, und ihr `/proc/<pid>/cwd` liegt in `$ALT`.
- `systemctl --failed` ist leer.
- Smoke aus §1 ist grün. Liegt das Rollback-Ziel vor #1046, gilt dort 500/400 als Soll.

`drift_commits` ist nach einem Rollback **nicht 0**. Der Checkout steht dann auf
dem neueren SHA. Das ist erwartet und verschwindet mit dem nächsten
Vorwärts-Deploy. Den Checkout nicht zurücksetzen.

## 3. Transport-Wechsel und Transport-Rollback (nur LiteLLM)

Ein Transport-Wechsel erzwingt **kein** KAI-Release (ADR 0019 §7). Der Baum
entsteht aus `requirements-transport.lock` mit `--require-hashes`. Danach wird
der Freeze gegen den Lock geprüft (#1066). `pi_make_transport.sh` schaltet
nichts um und startet nichts.

`--transports` muss auf das Verzeichnis **mit** `litellm/` zeigen (das ist auch
der Standard ohne Flag: `$HOME/transport/litellm`). Der Builder schreibt die
venv-Pfade fest auf den Zielort. Ein am falschen Ort gebauter Baum lässt sich
deshalb nicht verschieben, sondern muss neu gebaut werden.

```bash
cd /home/ubuntu/current                     # Lock + pyproject des laufenden Release
NEU=$(bash scripts/pi_make_transport.sh --repo . --transports /home/ubuntu/transport/litellm)
echo "$NEU"                                 # /home/ubuntu/transport/litellm/<version>-<manifest8>
cat "$NEU/transport.json"                   # spec_sha256 = sha256 des Locks
ALT=$(readlink -f /home/ubuntu/transport/litellm/current)
ln -sfn "$NEU" /home/ubuntu/transport/litellm/current
sudo -n /usr/local/sbin/kai-service-control restart kai-litellm.service
```

Danach §1. Die letzte `TRANSPORT_VERIFIED`-Zeile in `logs/litellm.err.log` muss den neuen Baum nennen.

Belegt am 25.09.2026 09:46 CEST: Umschaltung `1.99.0-293669ce` -> `1.99.0-4e65cd31` (aus
`requirements-transport.lock`), bereit nach 13 s, `TRANSPORT_VERIFIED … manifest=4e65cd31…`,
0 fehlgeschlagene Units.

**Rollback:** `ln -sfn "$ALT" /home/ubuntu/transport/litellm/current`, dann den
Dienst neu starten und §1 ausführen.

Aufbewahrung: mindestens zwei Bäume. Eine Transport-Rotation gibt es nicht als
Skript. Ein alter Baum wird nur von Hand entfernt, und nur, wenn weder
`current` noch ein laufender Prozess auf ihn zeigt.

## 4. Routen-Ist (welche Route wird wirklich genutzt)

Aus der Pi-`.env` nur Namen und Modi lesen, nie Schlüsselwerte:

```bash
grep -E '^(KAI_INFERENCE_(ENABLED|MODE_CEILING|ROUTE_MODES|ROUTE_TIMEOUT_SECONDS)|KAI_LITELLM_[A-Z]+_MODEL)=' \
    /home/ubuntu/ai_analyst_trading_bot/.env
```

Die tatsächliche Nutzung steht in `artifacts/llm_telemetry.jsonl`. Aufrufe über
den Proxy tragen `transport=litellm`, der direkte Pfad `transport=direct`. Je
Route auswerten: Aufrufe, Fehler, letzter Erfolg. Eine konfigurierte Route ohne
Aufruf wird ausdrücklich als „ohne Nachweis seit <Datum>“ geführt.

Stand 25.09.2026 (7 Tage): 0 Aufrufe über `transport=litellm`. Die Research-Route
(`kai-kimi-research`, advisory) wurde zuletzt am 11.09. genutzt (11 Aufrufe),
Standard-Shadow zuletzt am 10.09. (2 Aufrufe). Der Produktionsverbrauch läuft
vollständig über `direct`.

## 5. Unerwartete Neustarts

`needrestart` startet nach einem `unattended-upgrade` Dienste neu, deren
Bibliotheken ersetzt wurden, darunter auch `kai-litellm` (belegt am 25.09.2026
04:34Z nach dem curl/libexpat-Update). Der Proxy liest `.env` und die
Konfiguration nur beim Start. Nach einem solchen Neustart gilt §1.
Seit #1072 ist das für `kai-*` abgeschaltet (`/etc/needrestart/conf.d/50-kai.conf`,
installiert am 25.09.2026).

## 6. Preise (Kostenmap)

`kai-litellm.service` und `dev_reserve.sh proxy` setzen
`LITELLM_LOCAL_MODEL_COST_MAP=True`. Die Preise kommen damit aus dem Transport-Baum,
sind an `requirements-transport.lock` gebunden, und der Start braucht kein Netz.
Ohne die Variable lädt LiteLLM die Preise bei jedem Start von GitHub `main`.
`environment_variables` in der Proxy-YAML wirkt dafür nicht, weil die Map beim
Import geladen wird. Neue Preise kommen mit dem nächsten Transport-Update (§3).

Die Unit-Änderung wirkt erst, wenn der Operator die Units mit
`sudo bash scripts/pi_apply_systemd_units.sh` angewendet und `kai-litellm` neu
gestartet hat. Prüfen:

```bash
pid=$(systemctl show kai-litellm -p MainPID --value)
tr '\0' '\n' < /proc/$pid/environ | grep -c '^LITELLM_LOCAL_MODEL_COST_MAP=True$'   # Soll: 1
```
