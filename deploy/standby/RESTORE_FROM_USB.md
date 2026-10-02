# KAI Cold-Standby — Wiederherstellung vom USB-Stick

**Quelle:** `deploy/standby/RESTORE_FROM_USB.md` im Repo. `standby_to_usb.sh` legt diese Datei
bei jedem erfolgreichen Lauf auf den Stick (`/mnt/kai-data/kai-standby/RESTORE_FROM_USB.md`).
Ändern nur im Repo.

**Zweck:** Die Pi 5 nach dem Tod der Boot-SD-Karte schnell vor Ort wiederherstellen, ohne
Laptop und ohne Netz. Totalverlust (Pi weg, Brand, Diebstahl) deckt der **KAI-Vault** auf der
Platte „KAI Backup“ ab, siehe unten.

## Was auf dem Stick liegt (seit 02.10.2026 nur verschlüsselt)

Erzeugt von `/usr/local/bin/standby_to_usb.sh {system|data}` als root, gesteuert über zwei Timer:

| Datei | Inhalt | Takt / Aufbewahrung |
|---|---|---|
| `system_<ts>.tar.gz.enc` | Quell-Checkout inkl. `.venv` und `.env` (ohne `data/`, `artifacts/`, `.git`) | wöchentlich Mo 04:00, 4 Sätze |
| `release_<ts>.tar.gz.enc` | das aktive immutable Release inkl. `.venv` | wöchentlich, 4 |
| `deploymarker_<ts>.tar.gz.enc` | `artifacts/runtime/deployment_marker.json` | wöchentlich, 4 |
| `etc_<ts>.tar.gz.enc` | `/etc/systemd/system` + `/etc/fstab` | wöchentlich, 4 |
| `data_<ts>.tar.gz.enc` | `data/` + `artifacts/` (Ledger, Journale, Telegram-Sitzung) | alle 6 h, 28 (~7 Tage) |
| `<satz>.sha256` | Prüfsumme des verschlüsselten Satzes | mit dem Satz |
| `REBUILD_HINTS_<ts>.txt` | Versionen, aktive Release-SHA, fstab-UUID, Unit-Namen (keine Geheimnisse) | 4 |

Alle Sätze nutzen dasselbe Format wie der Vault und die Pi-Tagesarchive: `openssl enc
-aes-256-cbc -salt -pbkdf2 -iter 200000` mit **`KAI_BACKUP_PASSPHRASE`**. Die Passphrase
liegt in KeePass, auf dem Escrow-Stick und laut Notfallblatt. Ohne sie ist der Stick
wertlos, genau so ist es gewollt.

**Nicht auf dem Stick** (bewusst, Schlüsseltrennung: Zugangsdaten nie mit der
Artefakt-Passphrase):
- `~/kai-secrets` (HOTP-Seed, Lightning-Macaroons): im Vault, Gruppe `ln`, mit `LN_SECRET_BACKUP_KEY`.
- `~/.cloudflared` (Tunnel-Zugangsdaten): im Vault, Gruppe `ln` (`ln_pi_tunnel`), mit
  `LN_SECRET_BACKUP_KEY`, seit 02.10.2026; nach `/home/ubuntu` entpacken. Ohne Vault: den Tunnel
  im Cloudflare-Dashboard neu verbinden (`cloudflared tunnel login`, Zugangsdatei neu erzeugen),
  die DNS-Routen bleiben bestehen.

## Wiederherstellung nach SD-Tod (Reihenfolge einhalten)

1. **Betriebssystem:** Ubuntu Server 24.04 für Pi 5 auf eine neue microSD flashen (Raspberry Pi
   Imager), Benutzer `ubuntu`. Booten, per SSH anmelden.

2. **Stick einhängen** (er überlebt den SD-Tod):
   ```bash
   sudo apt install -y exfatprogs
   sudo mkdir -p /mnt/kai-data && sudo mount /dev/sda1 /mnt/kai-data
   U=/mnt/kai-data/kai-standby
   neu() { ls -1t "$U"/"$1"_*.tar.gz.enc | head -1; }
   SYS=$(neu system); REL=$(neu release); MRK=$(neu deploymarker); ETC=$(neu etc); DAT=$(neu data)
   ```

3. **Zuerst die Prüfsummen.** Jede Zeile muss `OK` melden. Bei einer Abweichung den
   nächstälteren Satz derselben Art nehmen (`ls -1t "$U"/data_*.tar.gz.enc`):
   ```bash
   cd "$U" && for f in "$SYS" "$REL" "$MRK" "$ETC" "$DAT"; do
       sha256sum -c "$(basename "$f").sha256"
   done; cd ~
   ```

4. **Passphrase einmal eingeben.** Sie bleibt nur in dieser Shell und taucht nicht in der
   Befehlshistorie auf:
   ```bash
   read -rsp 'KAI_BACKUP_PASSPHRASE: ' KAI_BACKUP_PASSPHRASE; echo; export KAI_BACKUP_PASSPHRASE
   dec() { openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:KAI_BACKUP_PASSPHRASE -in "$1"; }
   dec "$DAT" | tar tzf - | head -3     # Probe: Dateinamen statt "bad decrypt"
   ```

5. **Checkout zurück:**
   ```bash
   R=/home/ubuntu/ai_analyst_trading_bot
   sudo mkdir -p "$R"
   dec "$SYS" | sudo tar xzf - -C "$R"
   sudo chown -R ubuntu:ubuntu "$R"
   ```

6. **Aktives Release zurück**, mit der SHA aus den Rebuild-Hinweisen:
   ```bash
   SHA=$(grep -A1 '^## active release repo_sha' "$(ls -1t "$U"/REBUILD_HINTS_*.txt | head -1)" | tail -1)
   sudo -u ubuntu mkdir -p "/home/ubuntu/releases/$SHA"
   dec "$REL" | sudo -u ubuntu tar xzf - -C "/home/ubuntu/releases/$SHA"
   sudo -u ubuntu ln -sfn "/home/ubuntu/releases/$SHA" /home/ubuntu/current
   sudo mkdir -p /home/kai && sudo ln -sfn "/home/ubuntu/releases/$SHA" /home/kai/current
   ```

7. **Daten (jüngster Stand) und Deployment-Marker darüber:**
   ```bash
   dec "$DAT" | sudo -u ubuntu tar xzf - -C "$R"
   dec "$MRK" | sudo -u ubuntu tar xzf - -C "$R/artifacts/runtime"
   ```

8. **Systemkonfiguration:**
   ```bash
   dec "$ETC" | sudo tar xzf - -C /
   grep kai-data /etc/fstab      # UUID mit REBUILD_HINTS vergleichen, ggf. anpassen
   sudo systemctl daemon-reload
   ```

9. **Löschregeln anwenden, BEVOR irgendein Dienst startet.** Die Sicherung kann
   personenbezogene Daten enthalten, deren Frist inzwischen abgelaufen ist (IP-Adressen und
   IP-Kennwerte: spätestens 7 Tage, Meldungen, Einladungen). Die Datenschutzseite sagt zu,
   dass nach einer Wiederherstellung die Löschregeln erneut gelten:
   ```bash
   cd "$R" && sudo -u ubuntu ./.venv/bin/python scripts/audit_rotate.py --apply; echo "rc=$?"
   ```
   **Nur bei `rc=0` weiter.** Das Skript endet nur dann mit 0, wenn jede Frist lief und
   danach nichts mehr überfällig ist (`[retention] ... offen={... alle 0}` in der Ausgabe).
   Bei `rc=1` die Ausgabe lesen und **nicht starten**.

10. **Passphrase aus der Shell entfernen:** `unset KAI_BACKUP_PASSPHRASE`

11. **Dienste starten, dann prüfen:**
    ```bash
    sudo systemctl enable --now kai-*.service cloudflared.service
    systemctl --failed
    curl -s localhost:8000/health
    ```
    `.venv` stammt aus dem Archiv und läuft auf derselben Architektur und demselben OS direkt.
    Bei einem OS-Versionswechsel neu bauen (Python-Version und Repo-HEAD stehen in REBUILD_HINTS).

12. **Lightning:** Der Node (`admin@192.168.178.51`) ist ein eigenes Gerät, diese
    Wiederherstellung fasst ihn nicht an. KAIs Zugangsschlüssel (`~/kai-secrets`) kommen aus
    dem Vault (Gruppe `ln`, `LN_SECRET_BACKUP_KEY`). Bis dahin startet der Zahlungspfad nicht,
    er ist fail-closed.

## Welche Sicherung für welchen Ausfall

| Ausfall | Quelle |
|---|---|
| SD-Karte tot, Pi lebt | **dieser Stick** (vor Ort, kein Netz nötig) |
| Pi tot, Diebstahl, Brand | **KAI-Vault** auf der Platte „KAI Backup“: `pi_state` (`data/`, `artifacts/`, `.env`, Datenbanken), `ln` (Zugangsdaten inkl. Tunnel), Generationen 7 Tage / 4 Wochen / 12 Monate, `README-RESTORE.md` in jeder Generation |
| zusätzlich | Laptop `KAI-mirror\runtime-backups` (DPAPI, nur dieser Windows-Benutzer) |

Den Stick nach Möglichkeit an der Pi lassen. Er ist verschlüsselt, aber er ist die schnelle
Kopie, nicht die getrennte.
