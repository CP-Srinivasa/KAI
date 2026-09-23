#!/usr/bin/env bash
# kai_operator_arm_backup.sh — Backup scharf stellen und SOFORT beweisen.
#
# ANLASS (27.08.2026): Das verschluesselte Artefakt-Backup war nie gelaufen.
# Nicht weil der Timer fehlte — er lag seit dem 18.08. in /etc, war aber
# `disabled`, und KAI_BACKUP_PASSPHRASE fehlte. Ohne beides waeren der
# Praereg-Ledger, die Attestierungskette und jeder eingefrorene Datenschnitt
# nur auf einer SD-Karte.
#
# STAND 23.09.2026 (am Geraet geprueft): erledigt und laufend — beide Timer
# sind `enabled`, die Passphrase steht, es liegen 30 Tagesarchive
# (`artifacts/backups/<datum>/*.tar.gz.enc`, je ~9 MB, 236 MB gesamt) samt
# Manifest; der letzte Restore-Drill (01.09.) traegt archive_sha256 und
# Dateiliste, sein Timer laeuft monatlich.
#
# Das Skript bleibt trotzdem: es ist der dokumentierte Weg fuer ein NEU
# aufgesetztes Geraet, es ist idempotent (Schritt 1 entfaellt, wenn die
# Passphrase schon steht) — und es lag bis zum 23.09. NUR auf der Pi. Ein
# Skript, das die Wiederherstellbarkeit herstellt und selbst nicht gesichert
# ist, ist ein Widerspruch in sich.
#
# Dieses Skript nimmt die Passphrase ENTGEGEN (es erzeugt sie nicht und es
# schreibt sie nirgendwo hin ausser in die .env), stellt die Timer scharf und
# faehrt sofort einen echten Backup-Lauf plus Restore-Drill. Ohne Beweis kein
# "erledigt".
#
# AUFRUF (auf der Pi, im Repo-Verzeichnis):
#     bash scripts/kai_operator_arm_backup.sh
#
# Die Passphrase wird interaktiv abgefragt (kein Echo, keine Shell-History,
# kein Prozess-Argument). Mindestens 32 Zeichen.
#
# ⚠ SICHERE SIE AUSSERHALB DER PI. Stirbt die SD-Karte und die Passphrase lag
#   nur dort, ist jedes Backup unentschluesselbar — dann war die ganze Uebung
#   umsonst. Passwort-Manager, Papier im Safe, zweites Geraet: irgendetwas,
#   das nicht mit der Pi zusammen kaputtgeht.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
ENV_FILE="$ROOT/.env"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mABBRUCH: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || fail "$ENV_FILE fehlt — falsches Verzeichnis?"

# ── 1. Passphrase entgegennehmen ────────────────────────────────────────────
if grep -qE '^KAI_BACKUP_PASSPHRASE=.+' "$ENV_FILE"; then
    say "KAI_BACKUP_PASSPHRASE ist bereits gesetzt — Schritt 1 uebersprungen."
else
    say "Schritt 1/4 — Passphrase setzen"
    echo "Mindestens 32 Zeichen. Eingabe bleibt unsichtbar."
    printf 'Passphrase: '; read -rs PASS1; echo
    printf 'Wiederholen: '; read -rs PASS2; echo
    [ "$PASS1" = "$PASS2" ] || fail "Die beiden Eingaben stimmen nicht ueberein."
    [ "${#PASS1}" -ge 32 ] || fail "Zu kurz (${#PASS1} Zeichen, mindestens 32)."
    case "$PASS1" in *"'"*) fail "Einfache Anfuehrungszeichen bitte vermeiden.";; esac

    backup="$ENV_FILE.bak-$(date -u +%Y%m%dT%H%M%SZ)-prebackup-arm"
    cp -p "$ENV_FILE" "$backup" || fail "Sicherung der .env fehlgeschlagen."
    printf "KAI_BACKUP_PASSPHRASE='%s'\n" "$PASS1" >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    unset PASS1 PASS2
    echo "  .env ergaenzt (Sicherung: $(basename "$backup"))"
fi

# ── 2. Timer scharf stellen ─────────────────────────────────────────────────
say "Schritt 2/4 — Timer aktivieren (fragt nach dem sudo-Passwort)"
for unit in kai-backup-artifacts.timer kai-backup-restore-drill.timer; do
    if [ -f "/etc/systemd/system/$unit" ]; then
        sudo systemctl enable --now "$unit" && echo "  $unit: $(systemctl is-enabled "$unit")/$(systemctl is-active "$unit")"
    else
        echo "  ⚠ $unit fehlt in /etc — vorher anwenden:"
        echo "      sudo cp deploy/systemd/${unit%.timer}.service deploy/systemd/$unit /etc/systemd/system/"
        echo "      sudo systemctl daemon-reload"
    fi
done

# ── 3. Echter Backup-Lauf ───────────────────────────────────────────────────
say "Schritt 3/4 — Backup jetzt einmal fahren (kein Trockenlauf)"
sudo systemctl start kai-backup-artifacts.service
sleep 3
rc="$(systemctl show kai-backup-artifacts.service -p ExecMainStatus --value)"
echo "  ExecMainStatus=$rc  ($(systemctl show kai-backup-artifacts.service -p Result --value))"
case "$rc" in
    0) echo "  ✅ Backup erfolgreich";;
    2) fail "Exit 2 = Passphrase fehlt/leer. .env pruefen.";;
    3) fail "Exit 3 = keine Quelldateien gefunden. Falsches WorkingDirectory?";;
    *) echo "  ⚠ Exit $rc — Log: journalctl -u kai-backup-artifacts -n 30";;
esac
newest="$(find artifacts/backups -name '*.tar.gz.enc' -newermt '-10 minutes' 2>/dev/null | sort | tail -1)"
[ -n "$newest" ] && echo "  Archiv: $newest ($(du -h "$newest" | cut -f1))"

# ── 4. Restore-Drill: der eigentliche Beweis ────────────────────────────────
say "Schritt 4/4 — Restore-Drill (entschluesseln, entpacken, sha256 vergleichen)"
if [ -x scripts/kai_backup_restore_drill.sh ] || [ -f scripts/kai_backup_restore_drill.sh ]; then
    set -a; . "$ENV_FILE"; set +a
    bash scripts/kai_backup_restore_drill.sh; drc=$?
    proof="$(find artifacts/ops/backup_drill -name '*.json' -newermt '-10 minutes' 2>/dev/null | sort | tail -1)"
    echo "  drill_rc=$drc"
    if [ -n "$proof" ]; then
        echo "  Beweis: $proof"
        grep -oE '"(status|reason|files_restored|files_missing|sha256_mismatch)":[^,]*' "$proof" | head -5 | sed 's/^/    /'
    else
        echo "  ⚠ kein Beweis-Artefakt — das ist selbst ein Befund."
    fi
else
    echo "  ⚠ scripts/kai_backup_restore_drill.sh fehlt — Deploy nicht aktuell?"
fi

say "Fertig. Zwei Dinge bleiben bei Ihnen:"
echo "  1. Passphrase AUSSERHALB der Pi sichern (Passwort-Manager/Papier)."
echo "  2. In 24 h pruefen, dass der Timer von selbst gelaufen ist:"
echo "       systemctl list-timers kai-backup-artifacts.timer"
echo "       journalctl -u kai-backup-artifacts.service -n 20"
