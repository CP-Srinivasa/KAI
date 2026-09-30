#!/usr/bin/env bash
# macrot_node.sh -- Neuaufbau ALLER lnd-Macaroons auf dem RaspiBlitz (Node-Seite).
#
# Warum so und nicht anders (lnd v0.19.3 im Quelltext geprueft, 29.09.2026):
#   - "lncli deletemacaroonid 0" ist in lnd VERBOTEN (macaroons/store.go, ErrDeletionForbidden).
#   - "lncli changepassword --new_mac_root_key" wuerde die wallet.db neu verschluesseln -- tabu.
#   - Also: macaroons.db bei gestopptem lnd BEISEITELEGEN (nicht loeschen). lnd legt beim
#     Entsperren eine neue an und erzeugt alle Standard-Macaroons neu (config_builder.go).
#     Bis zum "cleanup" legt "rollback" den alten Stand exakt zurueck.
#
# Operator-Bedingung (29.09.2026): kein Satoshi darf verloren gehen oder sich bewegen.
#   - Dieses Skript enthaelt KEINEN Sende-, Kanal- oder On-Chain-Befehl.
#   - Es beruehrt NICHT: wallet.db, channel.db, channel.backup, Seed, lnd.conf, tls.*.
#   - Vorher harte Abbruchbedingungen, vorher/nachher Bilanz (macrot_snap.py).
#
# Aufruf auf dem Node (macrot.ps1 macht das vom Laptop aus):
#   bash ~/kai-macrot/macrot_node.sh check      nur lesen: Abbruchbedingungen + Bestand
#   bash ~/kai-macrot/macrot_node.sh rotate     Neuaufbau (interaktiv, Passwort C noetig)
#   bash ~/kai-macrot/macrot_node.sh resume     nach Unterbrechung weitermachen
#   bash ~/kai-macrot/macrot_node.sh rollback   alten Schluesselstand zuruecklegen
#   bash ~/kai-macrot/macrot_node.sh cleanup    alten Stand endgueltig vernichten
set -uo pipefail
umask 077

W=/home/admin/kai-macrot
MD=/mnt/hdd/app-data/lnd/data/chain/bitcoin/mainnet
OLD=$W/old
OUT=$W/out
LOG=$W/macrot.log
STATE=$W/state
LNB_ENV=/mnt/hdd/app-data/LNBits/data/.env
DEFAULT_MACS="admin readonly invoice invoices router signer walletkit chainnotifier"
KAI_READ_OPS="address:read info:read invoices:read macaroon:read message:read offchain:read onchain:read peers:read signer:read"
KAI_INV_OPS="info:read invoices:read invoices:write offchain:read onchain:read"
# So zeigt mac_ops.py die Rechte an (Aktionen je Eintrag zusammengefasst):
KAI_READ_SHOW="$KAI_READ_OPS"
KAI_INV_SHOW="info:read invoices:read,write offchain:read onchain:read"
KAI_ACC_SHOW="info:read invoices:read,write offchain:read,write onchain:read peers:read"

say() { echo "== $(date -u +%H:%M:%SZ) $*" | tee -a "$LOG"; }
die() { echo "ABBRUCH: $*" | tee -a "$LOG" >&2; exit 1; }
confirm() {
    local w
    read -r -p ">> Zum Fortfahren '$1' eintippen: " w
    [ "$w" = "$1" ] || die "nicht bestaetigt -- nichts weiter getan"
}
retry() {
    local n=$1 i
    shift
    for ((i = 0; i < n; i++)); do "$@" && return 0; sleep 3; done
    return 1
}
set_state() { echo "$1" >"$STATE"; }
get_state() { cat "$STATE" 2>/dev/null || true; }

on_exit() {
    local rc=$? st
    st=$(get_state)
    [ "$rc" -ne 0 ] || return 0
    if [ "$st" = moved ] || [ "$st" = unlocked ]; then
        echo
        echo "HINWEIS: Lauf im Zustand '$st' unterbrochen. Guthaben und Kanaele sind davon nicht betroffen."
        echo "  weitermachen:  bash $W/macrot_node.sh resume"
        echo "  zuruecklegen:  bash $W/macrot_node.sh rollback"
    elif [ "$st" = rollingback ]; then
        echo
        echo "HINWEIS: Rollback unterbrochen (alte Schluessel liegen schon zurueck). Guthaben unberuehrt."
        echo "  fortsetzen:    bash $W/macrot_node.sh rollback"
    fi
}
trap on_exit EXIT

lnd_state() {
    lncli state 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))' 2>/dev/null
}
wait_state() { # $1 Regex, $2 Sekunden
    local i
    for ((i = 0; i < $2; i += 3)); do
        [[ "$(lnd_state)" =~ $1 ]] && return 0
        sleep 3
    done
    return 1
}

# ---------------------------------------------------------------- LNbits
# LNbits (LNBITS_ADMIN_UI=true) liest die lnd-Macaroons aus der DB-Tabelle
# system_settings. Die .env schreibt RaspiBlitz' Prestart (bonus.lnbits.sh) bei
# jedem Start selbst aus den lnd-Dateien -- deshalb hier nur die DB.
lnbits_backup() {
    sudo cp -p "$LNB_ENV" "$OLD/lnbits.env" || die "LNbits-.env nicht gesichert"
    (cd / && sudo -u postgres psql -d lnbits_db -At -F $'\t' -c \
        "select id, value from system_settings where id in ('lnd_rest_admin_macaroon','lnd_rest_invoice_macaroon')") \
        >"$OLD/lnbits_settings.tsv" || die "LNbits-DB-Werte nicht gesichert"
    [ "$(wc -l <"$OLD/lnbits_settings.tsv")" -eq 2 ] || die "LNbits-DB: erwartet 2 Macaroon-Zeilen"
}

lnbits_db_set() { # $1 = new | old
    sudo python3 - "$1" "$MD" "$OLD/lnbits_settings.tsv" <<'PY' || die "LNbits-DB-Umstellung fehlgeschlagen"
import re, subprocess, sys
mode, md, backup = sys.argv[1:4]
if mode == "new":
    vals = {
        "lnd_rest_admin_macaroon": open(f"{md}/admin.macaroon", "rb").read().hex().upper(),
        "lnd_rest_invoice_macaroon": open(f"{md}/invoice.macaroon", "rb").read().hex().upper(),
    }
else:
    vals = {}
    for line in open(backup, encoding="utf-8").read().splitlines():
        k, v = line.split("\t", 1)
        vals[k] = v.strip('"')
for k, v in vals.items():
    assert k in ("lnd_rest_admin_macaroon", "lnd_rest_invoice_macaroon"), k
    assert re.fullmatch(r"[0-9A-Fa-f]+", v), "kein Hex"
sql = "".join(f"UPDATE system_settings SET value = '\"{v}\"' WHERE id = '{k}';\n" for k, v in sorted(vals.items()))
res = subprocess.run(
    ["sudo", "-u", "postgres", "psql", "-d", "lnbits_db", "-v", "ON_ERROR_STOP=1"],
    input=sql, capture_output=True, text=True, cwd="/",
)
print("LNbits-DB:", " / ".join(res.stdout.split("\n")[:2]).strip())
sys.exit(0 if res.returncode == 0 and res.stdout.count("UPDATE 1") == 2 else 1)
PY
}

lnbits_matches_files() { # DB und .env muessen den aktuellen lnd-Dateien entsprechen
    sudo python3 - "$MD" "$LNB_ENV" <<'PY'
import subprocess, sys
md, env = sys.argv[1:3]
f = {k: open(f"{md}/{n}", "rb").read().hex().lower() for k, n in (("ADMIN", "admin.macaroon"), ("INVOICE", "invoice.macaroon"), ("READ", "readonly.macaroon"))}
out = subprocess.run(
    ["sudo", "-u", "postgres", "psql", "-d", "lnbits_db", "-At", "-F", "\t", "-c",
     "select id, value from system_settings where id in ('lnd_rest_admin_macaroon','lnd_rest_invoice_macaroon')"],
    capture_output=True, text=True, cwd="/",
).stdout
ok = True
for line in out.splitlines():
    k, v = line.split("\t", 1)
    same = v.strip('"').lower() == f[k.split("_")[2].upper()]
    ok &= same
    print(f"  DB   {k}: {'passt' if same else 'ALT/FALSCH'}")
for line in open(env, encoding="utf-8").read().splitlines():
    if line.startswith("LND_REST_") and "_MACAROON=" in line:
        k, v = line.split("=", 1)
        same = v.strip().strip("\"'").lower() == f[k.split("_")[2]]
        ok &= same
        print(f"  .env {k}: {'passt' if same else 'ALT/FALSCH'}")
sys.exit(0 if ok else 1)
PY
}

lnbits_check() { # $1 = Startzeit fuer journalctl
    local i log
    for ((i = 0; i < 40; i++)); do
        log=$(sudo journalctl -u lnbits --since "$1" --no-pager 2>/dev/null)
        if grep -q "with a balance of" <<<"$log"; then
            say "LNbits: Verbindung zu lnd steht ($(grep -o 'Backend [A-Za-z]* connected' <<<"$log" | tail -1))"
            return 0
        fi
        if grep -qE "isn't working properly|Retrying connection to backend" <<<"$log"; then
            say "WARNUNG LNbits: $(grep -E "isn't working properly|Retrying" <<<"$log" | tail -1 | cut -c1-200)"
            return 1
        fi
        sleep 3
    done
    say "WARNUNG: LNbits meldet nach 2 Minuten nichts -- sudo journalctl -u lnbits -n 50"
    return 1
}

# ---------------------------------------------------------------- lnd
stop_all() {
    say "Dienste stoppen: lnbits, boltzd, litd"
    sudo systemctl stop lnbits boltzd litd ||
        die "Dienste liessen sich nicht stoppen -- nichts verschoben; wieder starten: sudo systemctl start litd boltzd lnbits"
    say "lnd sauber stoppen (systemd)"
    sudo systemctl stop lnd || die "lnd-Stop fehlgeschlagen"
    systemctl is-active --quiet lnd && die "lnd laeuft noch"
    return 0
}

start_unlock() {
    if [ "$(lnd_state)" = SERVER_ACTIVE ]; then
        say "lnd laeuft bereits und ist entsperrt"
        return 0
    fi
    say "lnd starten"
    sudo systemctl start --no-block lnd || die "lnd-Start fehlgeschlagen"
    wait_state '^(LOCKED|NON_EXISTING|UNLOCKED|RPC_ACTIVE|SERVER_ACTIVE)$' 600 ||
        die "lnd meldet keinen Zustand (lncli state: '$(lnd_state)')"
    case "$(lnd_state)" in
        NON_EXISTING) die "lnd findet KEINE Wallet -- NICHTS weiter tun (nie 'create'!), Claude rufen" ;;
        LOCKED)
            local n
            for n in 1 2 3; do
                say "Wallet entsperren (Versuch $n/3) -- Passwort C eingeben"
                lncli unlock && break
                [ "$n" = 3 ] && die "Entsperren 3x fehlgeschlagen. Node bleibt gesperrt (Geld sicher). Manuell: lncli unlock, dann resume"
            done
            ;;
    esac
    say "Warten auf SERVER_ACTIVE"
    wait_state '^SERVER_ACTIVE$' 900 || die "lnd erreicht SERVER_ACTIVE nicht (Zustand: $(lnd_state))"
}

# Erst einlesen, dann suchen: "journalctl | grep -q" meldet unter pipefail bei einem
# TREFFER Fehler (grep schliesst die Leitung, journalctl stirbt an SIGPIPE).
boltz_on_lnd() { # $1 Startzeit
    local blog
    blog=$(sudo journalctl -u boltzd --since "$1" --no-pager 2>/dev/null)
    grep -q "Connected to lightning node" <<<"$blog"
}

proof_rejected() { # $1 Datei, $2 Beschreibung
    local err
    if err=$(lncli --macaroonpath "$1" getinfo 2>&1 >/dev/null); then
        die "BEFUND: $2 wird von lnd noch ANGENOMMEN"
    fi
    say "Beweis: $2 abgelehnt -> $(tr '\n' ' ' <<<"$err" | cut -c1-140)"
}

# ---------------------------------------------------------------- Phasen
check() {
    mkdir -p "$W"
    python3 "$W/macrot_snap.py" guards "$W/snap-check.json"
}

prepare() {
    [ -e "$OLD" ] && die "$OLD existiert schon (frueherer Lauf) -- erst 'rollback' oder 'cleanup'"
    sudo test -f "$MD/wallet.db" || die "wallet.db nicht am erwarteten Ort -- falscher Pfad?"
    sudo test -f "$MD/macaroons.db" || die "macaroons.db nicht am erwarteten Ort"
    say "Abbruchbedingungen pruefen + Bestand vorher sichern"
    python3 "$W/macrot_snap.py" guards "$W/snap-before.json" | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" = 0 ] || die "Abbruchbedingung erfuellt -- NICHTS veraendert"
    echo
    echo "Ab jetzt ca. 10 Minuten Unterbrechung: lnd, litd, boltzd und LNbits werden gestoppt."
    echo "Passwort C bereithalten. Rueckweg bis zum Aufraeumen: bash $W/macrot_node.sh rollback"
    confirm ROTIEREN
    mkdir -p "$OLD/files" || die "Arbeitsordner"
    lnbits_backup
    stop_all
    say "macaroons.db und alle Standard-Macaroons BEISEITELEGEN (verschieben, nicht loeschen)"
    set_state moved
    sudo mv "$MD/macaroons.db" "$OLD/files/" || die "macaroons.db nicht verschoben"
    if sudo test -e "$MD/macaroons.db.last-compacted"; then
        sudo mv "$MD/macaroons.db.last-compacted" "$OLD/files/" || die "last-compacted nicht verschoben"
    fi
    sudo find "$MD" -maxdepth 1 -name '*.macaroon' -exec mv -t "$OLD/files/" {} + || die "Macaroon-Dateien nicht verschoben"
    if [ -n "$(sudo find "$MD" -maxdepth 1 -name '*macaroon*')" ]; then
        die "Reste in $MD"
    fi
    sudo test -f "$MD/wallet.db" || die "wallet.db fehlt -- NICHTS weiter tun, Claude rufen"
    say "Beiseitegelegt: $(sudo ls "$OLD/files" | tr '\n' ' ')"
}

kai_keys() {
    mkdir -p "$OUT"
    say "KAI-Schluessel mit EIGENEN Wurzeln backen (101 = lesen, 102 = Rechnungen)"
    # shellcheck disable=SC2086 # Rechte absichtlich als Einzelwoerter
    lncli bakemacaroon --root_key_id 101 --save_to "$OUT/kai-readonly.macaroon" $KAI_READ_OPS >/dev/null ||
        die "bakemacaroon 101 fehlgeschlagen"
    # shellcheck disable=SC2086
    lncli bakemacaroon --root_key_id 102 --save_to "$OUT/kai-invoice.macaroon" $KAI_INV_OPS >/dev/null ||
        die "bakemacaroon 102 fehlgeschlagen"
    local bal got
    bal=$(python3 -c 'import json,sys; print([a["balance"] for a in json.load(open(sys.argv[1]))["accounts"] if a["label"] == "kai"][0])' "$W/snap-before.json") ||
        die "KAI-Budget aus dem Vorher-Bestand nicht lesbar"
    say "litd-Account 'kai' neu anlegen, gleiches Budget $bal sat (nur die Account-ID aendert sich)"
    if sudo -u lit litcli accounts info --label kai >/dev/null 2>&1; then
        sudo -u lit litcli accounts remove --label kai >/dev/null || die "alten Account nicht entfernt"
    fi
    sudo -u lit litcli accounts create "$bal" --label kai --save_to /home/lit/kai-account.macaroon >/dev/null ||
        die "Account-Anlage fehlgeschlagen -- Budget danach mit kai-ln-budget pruefen"
    sudo cat /home/lit/kai-account.macaroon >"$OUT/kai-account.macaroon" || die "Account-Macaroon nicht kopiert"
    sudo shred -u /home/lit/kai-account.macaroon
    python3 "$W/mac_ops.py" --expect 101 "$KAI_READ_SHOW" "$OUT/kai-readonly.macaroon" | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" = 0 ] || die "kai-readonly falsch gebacken"
    python3 "$W/mac_ops.py" --expect 102 "$KAI_INV_SHOW" "$OUT/kai-invoice.macaroon" | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" = 0 ] || die "kai-invoice falsch gebacken"
    python3 "$W/mac_ops.py" --expect account "$KAI_ACC_SHOW" "$OUT/kai-account.macaroon" | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" = 0 ] || die "kai-account unerwartet"
    got=$(lncli --macaroonpath "$OUT/kai-account.macaroon" channelbalance |
        python3 -c 'import json,sys; print(json.load(sys.stdin)["local_balance"]["sat"])') ||
        die "Account-Macaroon nicht nutzbar"
    [ "$got" = "$bal" ] || die "Account-Macaroon zeigt $got statt $bal sat"
    say "Account-Macaroon zeigt das virtuelle Budget $got sat (nicht das echte Kanalguthaben) -- litd-Bindung steht"
    (cd "$OUT" && sha256sum ./*.macaroon | sed 's#\./##') >"$OUT/SHA256SUMS"
}

finish() {
    say "Neue Standard-Macaroons abwarten"
    local i f missing=1
    for ((i = 0; i < 40; i++)); do
        missing=0
        for f in $DEFAULT_MACS; do sudo test -f "$MD/$f.macaroon" || missing=1; done
        [ "$missing" = 0 ] && break
        sleep 3
    done
    [ "$missing" = 0 ] || die "nicht alle Standard-Macaroons neu erzeugt"
    say "Rechte setzen (RaspiBlitz lnd.credentials.sh sync: nur chown/chmod/Gruppen)"
    /home/admin/config.scripts/lnd.credentials.sh sync mainnet >>"$LOG" 2>&1 || die "credentials sync fehlgeschlagen"
    local old_sha new_sha ids
    old_sha=$(sudo sha256sum "$OLD/files/admin.macaroon" | cut -d' ' -f1)
    new_sha=$(sudo sha256sum "$MD/admin.macaroon" | cut -d' ' -f1)
    [ "$old_sha" != "$new_sha" ] || die "admin.macaroon unveraendert"
    lncli getinfo >/dev/null || die "neuer admin.macaroon funktioniert nicht"
    proof_rejected "$OLD/files/readonly.macaroon" "ALTER readonly.macaroon (alte Wurzel 0)"
    proof_rejected "$OLD/files/admin.macaroon" "ALTER admin.macaroon (alte Wurzel 0)"
    ids=$(lncli listmacaroonids | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin).get("root_key_ids") or []))')
    say "Wurzelschluessel jetzt: $ids"
    lnbits_db_set new
    local t0
    t0=$(date '+%Y-%m-%d %H:%M:%S')
    # restart statt start: beim Fortsetzen ("resume") laufen die Dienste schon, die
    # Pruefungen unten brauchen aber einen Start NACH dem Umbau (journalctl --since).
    say "Dienste (neu) starten: litd, boltzd, lnbits"
    sudo systemctl restart litd boltzd lnbits || die "Dienststart fehlgeschlagen"
    retry 40 sudo -u lit litcli accounts list >/dev/null || die "litd antwortet nicht"
    say "litd antwortet"
    # boltzcli getinfo braucht AUCH den Boltz-Server im Internet (29.09. seit 11:11 NXDOMAIN).
    # Fuer den Umbau zaehlt nur: kommt boltzd mit dem neuen Schluessel an lnd heran?
    if retry 10 sudo -u bitcoin boltzcli getinfo >/dev/null 2>&1; then
        say "boltzd antwortet"
    elif boltz_on_lnd "$t0"; then
        say "boltzd: mit lnd verbunden (neuer Schluessel gilt); Boltz-Server extern nicht erreichbar -- unabhaengig vom Umbau"
    else
        die "boltzd kommt nicht an lnd heran"
    fi
    lnbits_check "$t0" || true
    say "LNbits-Schluessel gegen die neuen Dateien:"
    lnbits_matches_files | tee -a "$LOG" || say "WARNUNG: LNbits haelt noch alte Werte"
    kai_keys
    say "Bestand nachher + Bilanzvergleich"
    python3 "$W/macrot_snap.py" snapshot "$W/snap-after.json" | tee -a "$LOG"
    python3 "$W/macrot_snap.py" diff "$W/snap-before.json" "$W/snap-after.json" | tee -a "$LOG"
    local rc=${PIPESTATUS[0]}
    set_state done
    [ "$rc" = 0 ] || die "BILANZ-ABWEICHUNG -- nicht aufraeumen, Claude rufen"
    echo
    say "NODE FERTIG. Jetzt am Laptop: macrot.ps1 pi  (neue KAI-Schluessel auf die Pi)"
}

rotate() {
    mkdir -p "$W"
    prepare
    start_unlock
    set_state unlocked
    finish
}

resume() {
    case "$(get_state)" in
        moved | unlocked) ;;
        *) die "nichts fortzusetzen (Zustand: '$(get_state)')" ;;
    esac
    start_unlock
    set_state unlocked
    finish
}

rollback() {
    if [ "$(get_state)" != rollingback ]; then
        sudo test -f "$OLD/files/macaroons.db" || die "kein Sicherungsstand in $OLD/files"
        echo "Rollback legt den ALTEN Schluesselstand zurueck (alte Macaroons gelten dann wieder)."
        confirm ZURUECK
        sudo systemctl stop lnbits boltzd litd
        sudo systemctl stop lnd || die "lnd-Stop fehlgeschlagen"
        systemctl is-active --quiet lnd && die "lnd laeuft noch"
        say "neue Schluesseldateien entfernen, alte zuruecklegen"
        sudo find "$MD" -maxdepth 1 \( -name '*.macaroon' -o -name 'macaroons.db' -o -name 'macaroons.db.last-compacted' \) -delete
        sudo find "$OLD/files" -maxdepth 1 -type f -exec mv -t "$MD/" {} + || die "Zuruecklegen fehlgeschlagen"
        set_state rollingback
    fi
    start_unlock
    /home/admin/config.scripts/lnd.credentials.sh sync mainnet >>"$LOG" 2>&1 || die "credentials sync fehlgeschlagen"
    lnbits_db_set old
    local t0
    t0=$(date '+%Y-%m-%d %H:%M:%S')
    sudo systemctl start litd boltzd lnbits || die "Dienststart fehlgeschlagen"
    retry 40 sudo -u lit litcli accounts list >/dev/null || die "litd antwortet nicht"
    lnbits_check "$t0" || true
    python3 "$W/macrot_snap.py" snapshot "$W/snap-rollback.json" | tee -a "$LOG"
    python3 "$W/macrot_snap.py" diff "$W/snap-before.json" "$W/snap-rollback.json" | tee -a "$LOG"
    set_state rolledback
    sudo rm -rf "$OUT"
    mv "$OLD" "$W/old-zurueckgelegt-$(date -u +%Y%m%dT%H%M%SZ)"
    say "Rollback fertig. Wurde der KAI-Account schon ersetzt, braucht KAI einen neuen Account-Macaroon (Claude)."
}

cleanup() {
    [ "$(get_state)" = done ] || die "Rotation nicht abgeschlossen (Zustand: '$(get_state)')"
    echo "Aufraeumen vernichtet den ALTEN Schluesselstand endgueltig -- danach kein Rollback mehr."
    confirm AUFRAEUMEN
    sudo find "$OLD" -type f -exec shred -u {} + || die "shred fehlgeschlagen"
    sudo rm -rf "$OLD"
    [ -d "$OUT" ] && find "$OUT" -type f -exec shred -u {} + && rmdir "$OUT"
    set_state cleaned
    say "Aufgeraeumt. Es bleiben nur Bestand und Protokoll: $W/snap-*.json, $LOG"
}

case "${1:-}" in
    check) check ;;
    rotate) rotate ;;
    resume) resume ;;
    rollback) rollback ;;
    cleanup) cleanup ;;
    *) sed -n '2,24p' "$0"; exit 64 ;;
esac
