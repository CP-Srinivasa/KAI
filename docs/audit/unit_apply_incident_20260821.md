# Unit-Apply 2026-08-21: ein verworfener Lauf, ein bewiesener Lauf

**Zweck.** Wer den Zustand der systemd-Units auf `kai-pi5` zitiert oder die
Beweiskette fuer die T0-Epoche prueft, muss wissen, auf welchem Weg die 30
Unit-Aenderungen am 2026-08-21 nach `/etc/systemd/system` gelangt sind. Dieses
Dokument haelt die **gemessene** Abfolge fest — nicht die zuerst vermutete.

Stand: 2026-08-21. Alle Zeitstempel UTC, live erhoben aus
`journalctl _COMM=sudo`, `journalctl -u <unit>` und `/var/backups/kai-units/`.

---

## 1. Was gemessen wurde

| Zeit (UTC) | Ereignis | Beleg |
| --- | --- | --- |
| 08:11:07 | `pi_apply_systemd_units.sh` **Lauf 1** — Sicherung angelegt | `/var/backups/kai-units/20260821T081107Z/` (30 Dateien) |
| 08:11:16.848 | `kai-tv-auto-promote.timer` neu gestartet | `journalctl -u kai-tv-auto-promote.timer` |
| 08:11:16.859 | `kai-tv-auto-promote.service` startet — `Persistent=true` holt den seit 2026-07-12 verpassten Kalenderlauf nach, **11 ms** nach dem Restart | `journalctl -u kai-tv-auto-promote.service` |
| 08:11:20.03 | Beweis misst den Timer **mitten im Lauf**, liest "kein naechster Termin", meldet FEHLGESCHLAGEN | Skript-Ausgabe, Rollback-Block |
| 08:11:20.34 | Der Lauf faehrt regulaer zu Ende: `open_events=2093, promoted=0, rejected=2093` | `tv_auto_promote.run` im Journal |
| 08:11:20.9 | Rollback **aller 30** Units aus der Sicherung | Skript-Ausgabe |
| ~08:15–08:19 | Privilegien-Broker installiert: `install -m 0755 -o root -g root deploy/bin/kai-service-control /usr/local/sbin/kai-service-control`, danach `visudo -c` | `journalctl _COMM=sudo` |
| 08:20:01 | Broker-Negativtest: `kai-service-control start definitely-not-a-kai-unit` | `journalctl _COMM=sudo` |
| 08:20:57 | `pi_apply_systemd_units.sh` **Lauf 2** — Sicherung angelegt, 30 Units angewendet, Beweis bestanden, **kein** Rollback | `/var/backups/kai-units/20260821T082057Z/` (30 Dateien); 60 `cp`-Aufrufe im Fenster (30 Sicherung + 30 Anwendung) |
| 08:25:02 | `kai-tv-auto-promote` feuert | `LastTriggerUSec` |
| 08:30:05 | Fire unter der neuen Unit, exakt am Kalendertermin 08:30:00 | `LastTriggerUSec`, Journal |

## 2. Was NICHT passiert ist

Die zuerst geaeusserte Vermutung, die Units seien ueber
`scripts/pi_install_systemd.sh` nach `/etc` gelangt, ist **widerlegt**. Das
Skript wurde am 2026-08-21 nicht ausgefuehrt; die beiden Journal-Treffer auf
diesen Dateinamen sind der Hinweistext des `privilege_broker`-Health-Befunds
("Installieren via ..."), kein Lauf. Der Broker kam ueber den
`--broker-only`-aequivalenten Einzelbefehl, die Units ueber den kontrollierten
Apply.

Damit gilt fuer diese Transition:

* Ein Sicherungs-Artefakt existiert: `/var/backups/kai-units/20260821T082057Z`.
* Der Apply-Vertrag (Sicherung vor der ersten Mutation, Byte-Beweis,
  Timer-Nachbedingung, Rueckweg) wurde eingehalten.
* `Successful Controlled Unit Apply = YES`.

## 3. Ursache des verworfenen Laufs

Der Beweis-Schritt fragte "hat dieser Timer einen naechsten Termin?", ohne zu
beruecksichtigen, dass die Antwort waehrend eines laufenden Oneshots regulaer
"nein" lautet: `OnUnitActiveSec` hat nichts zum Ankern, und ein per
`Persistent=` nachgeholter Lauf haelt den Timer ebenso kurz terminlos.

Dieselbe Frage war am selben Tag im Waechter gehaertet worden (#748) — die
zweite, unabhaengige Implementierung im Shell-Beweis blieb ungehaertet und
kassierte damit die Reparatur, die die gehaertete moeglich gemacht hatte.

**Behoben durch #755:** der Beweis unterscheidet jetzt drei Faelle statt zwei —
Termin vorhanden / kein Termin, aber der ausgeloeste Lauf dauert an (warten bis
`KAI_UNIT_PROOF_WAIT_S`, Default 90 s; kein Rollback) / kein Termin ohne
laufenden Lauf (Fehlschlag wie bisher).

Zeitliche Einordnung: Lauf 2 lief **vor** dem Merge von #755 (08:26:17) noch mit
dem ungehaerteten Skript. Er bestand den Beweis, weil zufaellig kein Lauf aktiv
war, als gemessen wurde — nicht, weil die Race behoben war.

## 4. Nachverifizierter Endzustand

Unabhaengig nach dem Apply gemessen:

* Byte-Drift Repo ↔ `/etc`: **0**
* `systemctl --failed`: **0**
* `kai-tv-auto-promote.timer`: `OnCalendar=*:0/5` + `Persistent=true`,
  `LastTrigger=08:30:05Z`, `NextElapse=08:35:00Z` — erster regulaerer Takt seit
  dem 2026-07-12
* `timer_scheduleability`-Befunde: **0** (vorher 11 gemeldete, davon 0 echt)
* `privilege_broker`-Befunde: **0**; `sudo -n` bleibt verweigert
* Stop-Kaskade: `Wants=` statt `Requires=` auf allen sechs Oneshots,
  `TimeoutStartSec=25min` auf `kai-shadow-resolver`
* Deploy-Urteil: `DEPLOY_SUCCESS`

## 5. Offen

`scripts/pi_install_systemd.sh` kann weiterhin beim Broker-Einbau divergente
Units nach `/etc` kopieren — ohne Sicherung, Freeze-Guard, Beweis und Rueckweg.
Dieser Pfad wurde am 2026-08-21 **nicht** benutzt, existiert aber. Die
praeventive Kontrolle ist **PR #749** (`--broker-only` plus Divergenz-Gate) und
sollte vor T0 geschlossen sein.
