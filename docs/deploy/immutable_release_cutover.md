# Immutable-Release-Cutover — Runbook

**Gilt ab #848 (Mainline `d18d0b21`, 2026-09-03).** Beschreibt den Übergang vom
beweglichen Checkout auf unveränderliche Release-Bäume und den laufenden Betrieb
darin.

Dieses Dokument beschreibt, was die Skripte **tun**, nicht was sie tun sollten.
Wo Skript und Absicht auseinandergehen, steht es hier ausdrücklich. Wenn du eine
Abweichung findest, ist das Runbook falsch, nicht der Pi.

---

## Warum

Ein Prozess, der aus einem beweglichen Checkout startet, kann nicht beweisen,
welche Bytes er geladen hat. Python importiert Module erst zur Laufzeit, und der
Baum darf sich zwischen Attestierung und Import weiterbewegen:

```
Checkout OLD → attestiert OLD → Checkout wandert auf NEW → exec → importiert NEW
             → Marker behauptet OLD
```

Mehr Logik um den beweglichen Baum herum löst das nicht. Ein Baum, der sich
nicht bewegt, schon. Deshalb: `/home/ubuntu/releases/<REPO_SHA>/`,
schreibgeschützt, mit eigenem venv, und ein Symlink `current`, der atomar
umgeschaltet wird.

Das alte Modell — Prozess-Commit gegen Checkout-`HEAD`, `drift_commits` — ist
damit entwertet, nicht verbessert. Siehe [`runtime_identity.md`](runtime_identity.md),
das den Legacy-Stand beschreibt.

---

## Was heute auf der Pi steht (Messung 2026-09-03)

```
HOST                 kai-pi5  →  192.168.178.23        USER  ubuntu
/home/kai            Symlink auf /home/ubuntu
Checkout             /home/ubuntu/ai_analyst_trading_bot  @ 61b1b4d2 (#850)
/home/ubuntu/releases   EXISTIERT NICHT
/home/ubuntu/current    EXISTIERT NICHT
kai-server           MainPID läuft seit 2026-09-02 21:55 auf 61b1b4d2
/health              runtime_commit == checkout_commit, drift_commits = 0
```

Der erste Cutover ist damit eine **Erstinstallation**, kein Update. `drift = 0`
ist dabei ehrlich und kein Grund zur Beruhigung: Prozess und Checkout stehen
beide auf demselben alten Stand, weil kein `git pull` stattgefunden hat.

---

## Hybrid, und das ist Absicht

Die 63 Units unter `deploy/systemd/` zerfallen in **drei** Gruppen, nicht zwei:

```
63  TOTAL
=  5  RELEASE_BOUND    ExecStart aus /home/kai/current
+ 55  CHECKOUT_BOUND   ExecStart aus /home/ubuntu/ai_analyst_trading_bot
+  3  REPO_INDEPENDENT ExecStart-Binary ausserhalb des Repos
```

**RELEASE_BOUND (5)** — die langlaufenden Daemons, für die Provenienz zählt:

```
kai-server.service         kai-agent-worker.service     kai-tg-listener.service
kai-entry-watch.service    kai-liquidation-stream.service
```

**CHECKOUT_BOUND (55)** — überwiegend Timer-getriebene Einmalläufe.

**REPO_INDEPENDENT (3)** — sie laden keinen KAI-Code und wandern deshalb weder
mit `current` noch mit dem Checkout:

| Unit | ExecStart |
|---|---|
| `cloudflared.service` | `/usr/local/bin/cloudflared` |
| `kai-standby-data.service` | `/usr/local/bin/standby_to_usb.sh data` |
| `kai-standby-system.service` | `/usr/local/bin/standby_to_usb.sh system` |

`cloudflared` berührt den Checkout nur in einem `ExecStartPre`, das dessen
`logs/`-Verzeichnis anlegt — es lädt keinen KAI-Code.

> **Verifizierter Befund, der den Cutover überlebt und ihn nicht überleben darf.**
> `/usr/local/bin/standby_to_usb.sh` (root, ausserhalb des Repos, gemessen
> 2026-09-03) hat in Zeile 26 `REPO=/home/ubuntu/ai_analyst_trading_bot` fest
> verdrahtet. Der System-Tier sichert damit weiterhin den Checkout — und nach
> dem Cutover läuft der Code der fünf Daemons aus `/home/ubuntu/releases/<SHA>/`.
> Die Sicherung erfasst dann nicht mehr, was tatsächlich läuft, und meldet
> trotzdem Erfolg. Das Skript liegt ausserhalb des Repos; die Korrektur gehört
> zum Cutover, nicht in einen Repo-PR.

**Folge, die man nicht übersehen darf:** der Checkout bleibt nach dem Cutover
produktiv. Er darf nicht gelöscht, nicht eingefroren und nicht vergessen werden.
Wer ihn nach dem Cutover nicht mehr aktualisiert, friert 55 Units auf einem
alten Stand ein, während die fünf Daemons vorwärtsgehen — und keine der
Release-Prüfungen schlägt dabei an, weil sie nur `current` betrachten.

Der Zustand — `.env`, `artifacts/`, `data/`, `logs/` — lebt ebenfalls weiter im
Checkout-Verzeichnis. Das Release verlinkt ihn nur hinein.

---

## Die Reihenfolge

Sie ist nicht beliebig. Zwei Regeln tragen sie:

1. **Units vor dem `current`-Switch.** Der erste Start nach dem Deploy muss
   bereits unter dem neuen Attestierungsvertrag laufen.
2. **Deploy-Marker nach dem Switch.** Ein Marker, der `SHA X ist aktiv` sagt,
   während `current` noch auf `Y` zeigt, ist eine Lüge — und zwar eine, auf die
   sich anschließend jede Prüfung stützt.

```
 1  Checkout aktualisieren            git fetch/checkout im Arbeitsbaum
 2  Release bauen + versiegeln        scripts/pi_make_release.sh
 3  Units anwenden                    scripts/pi_apply_systemd_units.sh   (Operator, sudo)
 4  daemon-reload                     systemctl daemon-reload             (Operator, sudo)
 5  current atomar umschalten    ┐
 6  Deploy-Marker schreiben      ├─   scripts/pi_activate_release.sh
 7  alte Releases aufräumen      ┘
 8  Dienste neu starten               systemctl restart …                 (Operator)
 9  Prozess-Marker prüfen             /proc-Identität gegen release_path
10  Health + Truth-Verify             curl /health, kai truth verify
```

**Schritte 3, 4, 8, 9 und 10 sind nicht skriptiert.** Der Kopfkommentar von
`pi_activate_release.sh` nennt sie als Teil der Reihenfolge, aber das Skript
führt ausschließlich 5–7 aus. Wer sich auf den Kommentar verlässt und 3/4
überspringt, schaltet `current` um, während die Units noch auf den Checkout
zeigen — der Switch bleibt dann folgenlos und `/health` meldet trotzdem grün.

---

## Schritt 2 — `pi_make_release.sh`

```bash
bash scripts/pi_make_release.sh [--repo <checkout>] [--releases <dir>] [--state <dir>]
```

Ziel ist `<releases>/<REPO_SHA>`; existiert es schon, endet das Skript mit
Exit 0 und gibt den vorhandenen Pfad aus. Es ist damit idempotent.

Sechs Stufen, jede mit Abbruch:

1. **Code stagen** — nur `app/`, `config/`, `deploy/`, `scripts/`, dazu
   `requirements.lock` und `pyproject.toml`. `__pycache__` wird entfernt: Caches
   gehören nicht in eine Identität.
2. **Zustand verlinken, nicht kopieren** — `.env`, `artifacts`, `data`, `logs`
   werden als Symlinks auf `<state>/…` gelegt. Würden sie mitwandern, verlöre
   jeder Deploy den Zustand und jeder Rollback die seither entstandenen Daten.
3. **Eigener venv aus dem Lockfile** — neu gebaut, nicht kopiert. Ein
   hineinkopierter venv trüge vorhandenen Drift in einen angeblich
   unveränderlichen Stand.
4. **`pip check`** — schlägt er fehl, entsteht kein Release.
5. **`release.json`** (`kai_release/v1`) mit `repo_sha`, `release_path`,
   `release_tree_sha256`, `requirements_lock_sha256`, `python_version`,
   `venv_python_path`, `dependency_manifest_sha256`, `builder_version`.
   Der Baum-Hash wird von demselben Code berechnet, der ihn später prüft
   (`app/observability/release_identity.release_tree_sha256`) — zwei
   Implementierungen desselben Hashes wären zwei Wahrheiten.
6. **Versiegeln** — `mv` aus dem Staging, dann `chmod -R a-w` auf `app/`,
   `config/`, `deploy/`, `scripts/` und die drei Dateien. Die Zustands-Symlinks
   zeigen nach draußen und bleiben schreibbar. Abschließend prüft der versiegelte
   Baum mit seinem **eigenen** venv, ob er seinen Anspruch trägt
   (`verify_release`).

Erfolg: `RELEASE_READY=<pfad>` auf stderr, der Pfad auf stdout.

Das Skript schaltet `current` **nicht** um und schreibt **keinen** Deploy-Marker.

---

## Schritt 3/4 — Units

```bash
bash scripts/pi_apply_systemd_units.sh --dry-run     # erst messen
bash scripts/pi_apply_systemd_units.sh               # dann anwenden, mit Passwort
sudo systemctl daemon-reload
```

Unit-Dateien sind bewusst operator-privilegiert. Der Deploy-Pfad misst den
Unit-Drift nur (`pi_deploy_step.sh`, read-only) und meldet `DEPLOY_HOLD`; der
Broker `kai-service-control` startet Dienste, kopiert aber nichts nach `/etc`.
Der Grund steht im Skriptkopf und ist gut: ein kompromittierter
`ubuntu`-Prozess könnte sonst eine Unit im Arbeitsbaum ändern und als root
installieren lassen — `ExecStart=/bin/bash /home/ubuntu/evil.sh` wäre wieder ein
passwortfreier Root-Codepfad. **Der Dateiinhalt ist das Privileg.**

Das Skript sichert jede überschriebene Datei, bevor es die erste schreibt, und
beweist danach Byte-Gleichheit je Datei sowie `active` **und einen endlichen
nächsten Termin** je Timer. Letzteres stammt aus dem Vorfall vom 19.08.:
`kai-tv-auto-promote.timer` stand fünf Wochen auf enabled+active mit
`NextElapseUSecMonotonic=infinity` — ein Timer, der läuft und trotzdem keinen
Termin hat, sieht in jeder anderen Prüfung gesund aus.

**Ehrliche Grenze:** kein transaktionaler Vorgang. Zwischen erster Kopie und
letztem Beweis existiert ein Zustand, in dem manche Units neu und manche alt
sind. Der Rollback ist ein Rückweg, keine Atomarität.

---

## Schritt 5–7 — `pi_activate_release.sh`

```bash
bash scripts/pi_activate_release.sh --release <pfad> [--current <link>] \
                                    [--state <dir>] [--keep 3]
```

Defaults: `current` = `<releases>/../current`, `state` =
`<releases>/../ai_analyst_trading_bot`.

- **Prüfen** — `verify_release` mit dem venv des Release. Trägt es seinen
  Anspruch nicht, wird nichts umgeschaltet (Exit 1).
- **Umschalten** — `ln -sfn` ist **nicht** atomar; es entfernt und legt neu an.
  Stattdessen: Symlink daneben plus `mv -T`, ein einziger `rename(2)`. Es gibt
  keinen Moment, in dem `current` ins Leere zeigt. Danach wird gegengelesen.
- **Deploy-Marker** — `<state>/artifacts/runtime/deployment_marker.json`
  (`deployment_marker/v1`), aus dem gelesenen `release.json`, atomar per
  `tmp.replace(target)`.
- **Aufräumen** — nie stumpf „älteste nach mtime". Gelöscht wird nur, was
  nachweislich niemand mehr braucht: nicht `current`, von keinem lebenden
  Prozessmarker unter `artifacts/runtime/processes/*.json` referenziert, und
  außerhalb der `--keep`-Aufbewahrung.

---

## Schritt 8–9 — Neustart und Beweis

Die fünf umgestellten Units starten nicht direkt das Ziel, sondern:

```
ExecStart=…/current/.venv/bin/python -m app.cli.main trading runtime-exec \
          --unit %n --repo /home/kai/current -- <eigentlicher Befehl>
```

`runtime-exec` schreibt den Prozess-Marker **unter der eigenen PID** und ersetzt
sich danach per `os.execv` durch den Dienst. Deshalb `ExecStart` und nicht
`ExecStartPost`: ein zweiter Prozess würde eine andere Identität bezeugen als
die, die anschließend läuft.

Nach dem Restart prüfen:

```bash
systemctl restart kai-server kai-agent-worker kai-tg-listener \
                  kai-entry-watch kai-liquidation-stream
cat /home/ubuntu/ai_analyst_trading_bot/artifacts/runtime/processes/*.json
readlink -f /proc/<MainPID>/cwd          # muss im aktiven Release liegen
curl -s http://127.0.0.1:8000/health
```

`release_path` im Prozess-Marker, `release_path` im Deploy-Marker und das Ziel
von `current` müssen dreifach übereinstimmen. Weicht eines ab, ist der Deploy
nicht bewiesen — unabhängig davon, was `/health` sagt.

---

## Rollback

Ein früheres Release liegt noch unter `<releases>/<alter SHA>` (Aufbewahrung
`--keep`, Standard 3). Rückweg:

```bash
bash scripts/pi_activate_release.sh --release /home/ubuntu/releases/<alter SHA>
sudo systemctl restart <die fünf Units>
```

Der Zustand wandert nicht mit und bleibt daher erhalten. Wurden in Schritt 3
Units geändert, gehört der Unit-Rückweg aus `pi_apply_systemd_units.sh` dazu —
sonst zeigen neue Units auf ein altes Release.

---

## Gate vor dem ersten Cutover — der Backup-Vertrag

**Der Cutover ist blockiert, bis dieses Gate steht.** Es blockiert weder das
Runbook noch den Installer-Guard; es sitzt genau zwischen „Code und Doku fertig"
und „Pi erstmals umschalten".

Nach dem Cutover existieren **zwei** produktive Code-Welten gleichzeitig:

```
/home/ubuntu/ai_analyst_trading_bot      55 CHECKOUT_BOUND Units
/home/ubuntu/current -> releases/<SHA>    5 RELEASE_BOUND Daemons
```

Ein System-Tier-Backup, das weiterhin nur den Checkout sichert, ist danach kein
vollständiges „code + .venv"-Backup mehr — und das ist nicht der schlimme Teil.
Der schlimme Teil ist, dass es **trotzdem erfolgreich endet**. Ein Backup, das
grün meldet und die Hälfte des laufenden Codes nicht enthält, ist gefährlicher
als eines, das ausfällt: der Ausfall wird bemerkt.

**Nicht einfach `REPO` von Checkout auf `current` umbiegen.** Das wäre derselbe
Fehler mit umgekehrtem Vorzeichen — dann fielen die 55 Checkout-Units aus dem
System-Tier. Der richtige Vertrag nach #848 ist **Checkout UND aktives konkretes
Release**, nicht entweder/oder. „Konkret" heisst: der aufgelöste Pfad
`releases/<SHA>`, nicht der Symlink — ein Backup, das den Symlink sichert,
sichert einen Namen.

Abnahmekriterien:

```
STANDBY_SYSTEM_BACKUP
  CHECKOUT_ROOT                          COVERED
  ACTIVE_RELEASE_PATH  (aufgeloest)      COVERED
  ACTIVE_RELEASE_VENV                    COVERED
  release.json                           COVERED
  deployment_marker.json                 COVERED
  SYSTEMD_UNIT_STATE                     COVERED / bestehender Vertrag geprueft

FALSE_GREEN_ON_MISSING_ACTIVE_RELEASE    IMPOSSIBLE
RESTORE_PROOF                            PASS
```

Die vorletzte Zeile ist die eigentliche Anforderung und keine Formalie: fehlt das
aktive Release, muss der Lauf **fehlschlagen**, nicht überspringen. Ein
`[ -d "$X" ] || continue` erfüllt jede der COVERED-Zeilen und verletzt trotzdem
den Vertrag.

`/usr/local/bin/standby_to_usb.sh` liegt ausserhalb des Repos und gehört dem
Operator; diese Änderung ist deshalb kein Repo-PR.

---

## Nicht-Ziele

- **Kein Deploy ohne Operator.** Units anwenden, `daemon-reload` und Restarts
  bleiben passwortpflichtig.
- **Keine Zustandsmigration.** `.env`, `artifacts/`, `data/`, `logs/` bleiben, wo
  sie sind.
- **Keine Vollmigration der 55 Timer-Units** in diesem Schritt.
- **Keine Korrektur von `/usr/local/bin/standby_to_usb.sh`** durch einen
  Repo-PR — das Skript liegt ausserhalb des Repos und gehoert dem Operator.
