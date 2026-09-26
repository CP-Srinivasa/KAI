# Node-seitiges Budget für KAIs Sendeschlüssel (litd-Account)

> **Status: ENTWURF, nichts ausgeführt (26.09.2026).** Die Befehlsfolge für das Fenster steht in §9, der Ist-Stand am Node wurde am 26.09. abends gelesen (§3). Der Operator hat die Vorbereitung beauftragt. Jeder Schritt am Node und jeder Send braucht eine eigene Freigabe. Referenzen: D-288 (Lückenregister), D-289 (fremde Ausgaben werden gemeldet).

## 1. Warum

KAIs Sendeschutz (per_payment_max und daily_hard_cap je 1000 sat, genau ein Empfänger, HOTP ab 1 sat) steckt **nur in KAIs Software** (`PaymentService`). Der Node kennt weder den HOTP noch die Caps. Für einen Send prüft lnd ausschließlich, ob ein gültiger Macaroon mit Senderecht und das TLS-Zertifikat vorliegen.

- Auf der Pi liegt `~/kai-secrets/lnd/kai-payment.macaroon` (Rechte `600`, Benutzer `ubuntu`). Der Go-live-Preflight (GO) belegt ohne Geldbewegung, dass er **sendefähig** ist.
- **Alle** release-gebundenen Dienste und `cloudflared` laufen als `ubuntu`: kai-server, agent-worker, tg-listener, litellm, entry-watch, liquidation-stream, cloudflared.
- Folge: Eine Codeausführung in irgendeinem dieser Dienste (Abhängigkeit, Webhook, Tunnel) reicht, um **am KAI-Code vorbei** direkt am Node per Lightning zu zahlen, ohne HOTP und ohne Cap. Die Obergrenze wäre dann das **ausgehende Kanalguthaben** (am 26.09. lokal ≈ 370 000 sat).
- **On-chain ist über diesen Schlüssel nicht erreichbar.** `kai-payment.macaroon` trägt laut `docs/lightning_macaroon_matrix.md` nur `offchain:read offchain:write`, kein `onchain:write`. Der tatsächliche Scope ist am Node noch nicht nachgelesen (Matrix, offener Punkt), er gehört zu Schritt 4.4.
- D-289 sorgt dafür, dass so eine Ausgabe **auffällt**. Ein litd-Account sorgt dafür, dass sie **begrenzt** ist.

Einordnung: Ein gezielter Angriff ist unwahrscheinlich, der mögliche Schaden aber unbegrenzt. Priorität P2.

## 2. Was ein litd-Account leistet (belegt)

Quelle: Lightning Labs, *LND Accounts* (docs.lightning.engineering, gelesen am 26.09.2026).

- Ein Account ist ein **virtuelles Unterkonto** mit eigenem Macaroon. Das Limit setzt der **Node** über den RPC-Middleware-Interceptor durch, nicht der Client.
- Jede Zahlung wird **einschließlich Routing-Gebühren** vom virtuellen Guthaben abgezogen. Übersteigen die laufenden Zahlungen das Guthaben, wird abgelehnt.
- Einschränkung: litd prüft nicht, ob die Summe aller Accounts durch den Kanal gedeckt ist. Für uns ist das irrelevant, weil es genau einen Account mit kleinem Betrag gibt.
- Erzeugen: `litcli accounts create <sat> --save_to <pfad>`.

## 3. Ist-Stand am Node (gelesen am 26.09.)

- RaspiBlitz `codeVersion="1.12.1"`, lnd `0.19.3-beta`.
- litd ist **nicht installiert** (`systemctl is-active litd` ergibt `inactive`), aber über `config.scripts/bonus.lit.sh` verfügbar.

**Nachgelesen am 26.09. abends (nur lesend):**
- `bonus.lit.sh` installiert **LiT `0.14.1-alpha`**. Es lädt das Release, prüft Hash und GPG-Signatur und legt den Benutzer `lit` und `litd.service` an. Der Betrieb ist `lnd-mode=remote` gegen `127.0.0.1:10009` mit `admin.macaroon`. Der Benutzer `lit` existiert noch nicht.
- **`rpcmiddleware.enable=true` steht bereits** in `lnd.conf` (`/mnt/disk_storage/app-data/lnd/lnd.conf`). lnd läuft seit 04.07. mit dieser Datei. Das Skript ändert dann nichts an `lnd.conf`, **ein lnd-Neustart ist nicht nötig** (Punkt 4.3 ist erledigt, die Kanäle bleiben verbunden). Es startet nur RTL neu.
- **Angriffsfläche:** Das Skript öffnet `ufw allow 8443` (LiT-Weboberfläche mit Passwort B) und legt **einen Tor-Onion-Dienst** für diese Oberfläche an. Beides braucht KAI nicht, §9 Schritt 2 nimmt es wieder zurück.
- `admin` hat passwortloses `sudo` am Node.
- **KAIs Sende-Macaroon** (`~/kai-secrets/lnd/kai-payment.macaroon` auf der Pi, nur der Identifier gelesen): Ops `offchain:read,write`. On-chain ist also nicht erreichbar, damit ist der offene Punkt der Matrix bestätigt. Er hat **`root_key_id = 0`**, ebenso `kai-invoice.macaroon`.
- Die Tagesbackups der Pi enthalten `kai-secrets` **nicht** (22 Dateien, nur Journale, Ledger und Beweise).

## 4. Vor der Umsetzung zu klären (NICHT dokumentiert, muss am Node bewiesen werden)

1. **Fail-closed bei litd-Ausfall.** Wird eine Anfrage mit Account-Macaroon abgelehnt, wenn litd nicht läuft (keine Middleware registriert)? Die Doku sagt es nicht ausdrücklich. Wäre die Anfrage dann ohne Limit erlaubt, wäre der Account wertlos. Das ist **Abnahmetest A3** und ein K.-o.-Kriterium.
2. **Endpunkt.** Kann KAI mit dem Account-Macaroon weiter direkt `lnd`-REST (`:8080`) ansprechen, wobei lnd an die Middleware weiterreicht? Oder muss KAI über den litd-Port gehen? Davon hängt ab, ob sich an `APP_LN_*` mehr als der Macaroon-Pfad ändert.
3. **lnd-Neustart.** `rpcmiddleware.enable=true` in `lnd.conf` braucht einen lnd-Neustart. Dabei trennen sich die Kanäle kurz. Nur mit Drain-Check: kein Intent unterwegs (`scripts/payment_drain_check.py`).
4. **Alten Schlüssel wirklich entwerten.** Nur den Pfad zu tauschen genügt nicht, solange `kai-payment.macaroon` gültig bleibt. Dazu prüfen:
   - Mit welchem `root_key_id` wurde er gebacken (`lncli printmacaroon`)?
   - Ist es `0` (Standard), trifft `lncli deletemacaroonid 0` **auch** `admin.macaroon`, `readonly.macaroon` und `invoice.macaroon`. Dann ist eine gezielte Entwertung nicht möglich, und es braucht einen geplanten Neuaufbau aller Macaroons.
   - **Befund 26.09.: Es ist `0`.** Eine gezielte Entwertung geht nicht. Bis zu einem Neuaufbau aller Macaroons gilt: Die Datei wird nach der Umstellung **von der Pi gelöscht**. Weitere Kopien (Laptop, D:-Vault, Escrow) sind vorher zu suchen und zu löschen. Das senkt das Risiko, beweist aber keine Entwertung. Der Neuaufbau (neue Macaroons mit eigener `root_key_id` je Zweck, danach `deletemacaroonid 0`) wird ein eigenes Vorhaben, weil er RTL, LNbits, LiT und die Pi zugleich berührt.
5. **Receive-Pfad.** KAI empfängt mit `kai-invoice.macaroon`, das bleibt getrennt. Ein Account-Macaroon verbucht eingehende Zahlungen als Guthaben des Accounts. Ob der Self-Use-Receive künftig über den Account laufen soll, ist offen.
6. **Reconcile.** Das Lesen mit `readonly.macaroon` bleibt unverändert. Prüfen, dass `ListPayments` mit dem Readonly-Macaroon weiter **alle** Node-Zahlungen sieht, damit D-289 nichts verliert.

## 5. Ablauf (Entwurf)

| # | Schritt | Wer | Rückweg |
|---|---|---|---|
| 0 | Frische SCB-Sicherung außerhalb des Nodes, Drain-Check grün, kein Pilot und kein Release parallel | Claude (prüft) | – |
| 1 | litd über `bonus.lit.sh on` installieren. Prüfen, was das Skript an `lnd.conf` ändert (`rpcmiddleware.enable`) und ob es lnd neu startet | Operator (sudo) | `bonus.lit.sh off` |
| 2 | Punkte 4.1 und 4.2 beweisen, mit einem **Test-Account über 1 sat** und ohne echten Send: Probe über eine ungültige Rechnung, wie beim Armed-Probe im Preflight | Claude + Operator | Test-Account löschen |
| 3 | KAI-Account anlegen: Budget **5 000 sat** (Vorschlag), Macaroon nach `~/kai-secrets/lnd/kai-account.macaroon` (Rechte `600`) | Operator | Account löschen |
| 4 | `.env`: `APP_LN_PAYMENT_MACAROON_PATH` auf den Account-Macaroon umstellen (`scripts/env_backup.sh` vorher). kai-server neu starten | Operator + Claude | alter Pfad, Restart |
| 5 | Abnahme (Abschnitt 6) | Claude + Operator | Schritt 4 zurück |
| 6 | Alten `kai-payment.macaroon` entwerten (nur wenn 4.4 das gezielt erlaubt), sonst eigenes Vorhaben „Macaroon-Neuaufbau“ | Operator | – (irreversibel) |

## 6. Abnahme

- **A1:** Der Go-live-Preflight mit dem Account-Macaroon meldet GO.
- **A2:** `/pay` über 100 sat mit HOTP wird SETTLED. `fee_actual_msat` ist gleich lnd `fee_msat`. Das Account-Guthaben sinkt um Betrag plus Gebühr.
- **A3 (K.-o.):** Mit gestopptem litd wird ein Send mit dem Account-Macaroon **abgelehnt**, geprüft per Probe mit einer ungültigen Rechnung, ohne Geld.
- **A4:** Ein Send über dem Account-Guthaben wird **vom Node** abgelehnt, auch mit korrektem HOTP. Das ist der eigentliche Beweis, dass das Limit nicht mehr nur in KAIs Software sitzt.
- **A5:** Reconcile `ok` und `complete`. Eine Wallet-Zahlung am KAI-Account vorbei erscheint weiter als `unattributed` (D-289).

## 7. Offene Operator-Entscheide

- ~~Die Budgethöhe~~ **Entschieden am 26.09. (D-290): 5 000 sat, aufgefüllt von Hand.**

### 7a. Pflicht-Anforderung des Operators (26.09.): jederzeit einfach anpassbar

Der Operator stimmt dem Account nur zu, wenn er das Budget **jederzeit einfach** ändern kann. Daraus folgt:

- **Ändern NUR vom Laptop, nie von der Pi.** Ein Budget, das sich von der Pi aus erhöhen lässt (Telegram-Befehl, Dashboard-Knopf, Macaroon mit Account-Schreibrecht auf der Pi), schützt nicht: Wer auf der Pi Code ausführt, würde sich zuerst das Budget hochsetzen.
- **Ein Befehl am Laptop**, gebaut als Teil der Umsetzung: `kai-ln-budget <neuer_betrag_sat>` bzw. `kai-ln-budget show`. Er arbeitet über den vorhandenen Laptop-SSH-Weg (`admin@192.168.178.51`, Schlüssel liegt nur auf dem Laptop) und führt nacheinander aus:
  1. den Stand zeigen: Budget, Verbrauch, Restguthaben;
  2. den neuen Wert setzen (`litcli accounts update <id> --new_balance …`);
  3. den neuen Stand gegenlesen;
  4. die Änderung mit Zeit, altem und neuem Wert lokal protokollieren (`%USERPROFILE%\.kai\logs\ln_budget.log`).
- **Anzeigen darf die Pi:** Budget und Verbrauch erscheinen in Telegram (`/pay status`) und im Dashboard, aber nur lesend.
- **Abnahme A6:** Eine Budgetänderung per `kai-ln-budget` wirkt sofort. Ein Send über dem alten und unter dem neuen Budget geht danach durch, und ein Änderungsversuch mit den Mitteln der Pi scheitert.
- Ob die Alltags-Wallet ebenfalls einen eigenen Account bekommt.
- Ob der alte Macaroon gezielt entwertet werden kann (4.4) oder ein Neuaufbau aller Macaroons geplant werden muss.

## 8. Alternative oder Ergänzung ohne litd

Die Dienste bekämen getrennte Unix-Benutzer, und nur der sendende Prozess (kai-server) dürfte `kai-payment.macaroon` lesen. Das verkleinert die Angriffsfläche (cloudflared, litellm und tg-listener könnten den Schlüssel nicht mehr lesen), **begrenzt aber keinen Betrag**, denn kai-server selbst ist über das Dashboard exponiert. Das ergänzt den Account, ersetzt ihn aber nicht.

## 9. Befehlsfolge für das Fenster (Stand 26.09. abends)

**Rahmen:** Rund 45 Minuten, Operator und Claude gemeinsam. Kein Release und keine Pi-Wartung parallel. Jeder Schritt hat ein **Stop-Kriterium**. Tritt es ein, geht es mit dem Rückweg zurück und das Fenster endet. Node-Befehle laufen am Laptop in einer Sitzung `ssh admin@192.168.178.51`, Pi-Befehle über `ssh ubuntu@192.168.178.23`. Wo nur Claude liest, steht „Claude“.

### Schritt 0: Vorbedingungen (Claude)
- Drain-Check auf der Pi: `DRAIN_OK`. Der letzte Reconcile ist `ok` und `complete`.
- Frische SCB-Kopie vom Node auf den Laptop: `C:\Users\sasch\KAI-mirror\sync-scb-from-node.ps1`. Die Baseline-sha ist notiert.
- Node: `lncli getinfo` mit `synced_to_chain=true`, der Kanal ist `active`.
- **Stop**, wenn ein Intent unterwegs ist oder der Kanal nicht aktiv ist.

### Schritt 1: LiT installieren (Operator)
```
sudo /home/admin/config.scripts/bonus.lit.sh on
```
- Erwartet: Download, `gpg --verify` ist gut, `OK - the Lightning lit service is now enabled`. Das Skript ändert `lnd.conf` nicht, weil `rpcmiddleware.enable=true` schon gesetzt ist. lnd läuft durch.
- Kontrolle: `systemctl is-active litd lnd` ergibt zweimal `active`. `sudo journalctl -u litd -n 30` zeigt keinen Fehler, der Account-Dienst ist registriert.
- **Stop**, wenn lnd neu startet, die GPG-Prüfung scheitert oder litd nicht `active` wird. Rückweg: `sudo /home/admin/config.scripts/bonus.lit.sh off`.

### Schritt 2: Angriffsfläche zurücknehmen (Operator)
```
sudo sed -i 's/^httpslisten=.*/httpslisten=127.0.0.1:8443/' /mnt/hdd/app-data/.lit/lit.conf
sudo ufw delete allow 8443
sudo /home/admin/config.scripts/tor.onion-service.sh off lit
sudo systemctl restart litd
```
- Kontrolle: `sudo ss -ltnp | grep 8443` zeigt nur `127.0.0.1:8443`. `sudo ufw status | grep 8443` ist leer.

### Schritt 3: A3 fail-closed beweisen, ohne Geld (Operator führt aus, Claude wertet aus)
Die `litcli`-Syntax ist vorher mit `sudo -u lit litcli accounts create --help` zu bestätigen.
```
sudo -u lit litcli accounts create 1 --label a3-test --save_to /home/lit/a3.macaroon
sudo cp /home/lit/a3.macaroon /home/admin/a3.macaroon && sudo chown admin: /home/admin/a3.macaroon
lncli --macaroonpath /home/admin/a3.macaroon channelbalance      # (a) litd läuft
sudo systemctl stop litd
lncli --macaroonpath /home/admin/a3.macaroon channelbalance      # (b) litd gestoppt
sudo systemctl start litd
```
- **(a)** muss das **virtuelle** Guthaben des Accounts zeigen, also 1 sat, und **nicht** das Kanalguthaben (≈ 370 000 sat). Das beweist, dass lnd die Anfrage an litd weiterreicht, auch über den normalen lnd-Port. Damit ist Punkt 4.2 beantwortet, und für KAI ändert sich nur der Macaroon-Pfad.
- **(b)** muss mit einem **Fehler** enden, der Middleware oder Caveat nennt.
- **K.-o.:** Zeigt (b) das echte Kanalguthaben, ist das Verhalten fail-open. Dann bricht das Vorhaben ab, der Rückweg ist `bonus.lit.sh off`. Ohne fail-closed ist der Account wertlos (§4.1).
- Aufräumen: `sudo -u lit litcli accounts list` zeigt die ID, dann `sudo -u lit litcli accounts remove <id>` und `rm /home/admin/a3.macaroon; sudo rm /home/lit/a3.macaroon`.

### Schritt 4: KAI-Account anlegen (Operator)
```
sudo -u lit litcli accounts create 5000 --label kai --save_to /home/lit/kai-account.macaroon
sudo cp /home/lit/kai-account.macaroon /home/admin/kai-account.macaroon && sudo chown admin: /home/admin/kai-account.macaroon
```
Am Laptop (PowerShell). Die Datei geht über den Laptop, denn der Node hat keinen Schlüssel zur Pi:
```
scp admin@192.168.178.51:/home/admin/kai-account.macaroon $env:TEMP\kai-account.macaroon
scp $env:TEMP\kai-account.macaroon ubuntu@192.168.178.23:/home/ubuntu/kai-secrets/lnd/kai-account.macaroon
ssh ubuntu@192.168.178.23 "chmod 600 ~/kai-secrets/lnd/kai-account.macaroon"
Remove-Item $env:TEMP\kai-account.macaroon
ssh admin@192.168.178.51 "rm /home/admin/kai-account.macaroon; sudo rm /home/lit/kai-account.macaroon"
```

### Schritt 5: KAI umstellen (Operator, Claude prüft)
Auf der Pi:
```
cd ~/ai_analyst_trading_bot && bash scripts/env_backup.sh
sed -i 's#^APP_LN_PAYMENT_MACAROON_PATH=.*#APP_LN_PAYMENT_MACAROON_PATH=/home/ubuntu/kai-secrets/lnd/kai-account.macaroon#' .env
sudo systemctl restart kai-server
```
- Danach das Restart-Protokoll: `/health` 200, agent-worker, tg-listener und cloudflared laufen. `/health/payment` ist `ok`.
- Rückweg: den alten Pfad in `.env` zurücksetzen und `kai-server` neu starten.

### Schritt 6: Abnahme (§6) und der Laptop-Befehl `kai-ln-budget`
- **A1:** Go-live-Preflight mit GO. **A3** ist durch Schritt 3 erledigt.
- **A2:** `/pay` über 100 sat mit HOTP an den Pilot-Payee. Das Account-Guthaben sinkt um Betrag plus Gebühr (`sudo -u lit litcli accounts list`).
- **`kai-ln-budget` bauen (Claude):** Im Fenster, sobald die echte `litcli accounts update`-Syntax gelesen ist. Der Befehl läuft nur vom Laptop per `ssh admin@…`, die Pi bekommt kein Schreibrecht (§7a).
- **A4 + A6:** Mit `kai-ln-budget 50` wird das Budget gesenkt. Ein `/pay` über 100 sat wird dann **vom Node** abgelehnt, auch mit korrektem HOTP. `kai-ln-budget 5000` setzt es zurück, der nächste Send geht wieder durch.
- **A5:** Reconcile ist `ok` und `complete`. Der Digest zeigt keine „fremde Ausgabe“ für die eigenen Sends.

### Schritt 7: Alten Schlüssel stilllegen (Operator)
- Kopien von `kai-payment.macaroon` außerhalb der Pi suchen (Laptop, D:-Vault, Escrow-Archiv) und löschen.
- Danach auf der Pi `shred -u ~/kai-secrets/lnd/kai-payment.macaroon`, erst **nachdem** A1 bis A6 bestanden sind.
- Grenze: `root_key_id = 0`. Wer vorher eine Kopie gezogen hat, kann sie weiter nutzen, bis alle Macaroons neu aufgebaut sind (§4.4, eigenes Vorhaben).
