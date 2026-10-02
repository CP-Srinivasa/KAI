#!/usr/bin/env bash
# KAI cold-standby to the attached USB (SanDisk Extreme Pro, /mnt/kai-data, exfat).
# 2026-06-13, gehaertet 2026-09-04. Captures the LOCAL restore set so a dead
# boot-SD can be recovered fast WITHOUT depending on the Windows PC or the
# network -- and uniquely captures the SYSTEM/deps/config layer that the off-Pi
# backups (data only) leave out.
#
# QUELLE: Diese Datei ist die kanonische Fassung. `/usr/local/bin/standby_to_usb.sh`
# ist eine INSTALLATION davon, nicht die Wahrheit. Wer dort direkt editiert,
# erzeugt eine zweite, ungetestete Wahrheit ueber die Wiederherstellbarkeit --
# genau die Sorte Doppelung, an der KAI schon einmal eine Runtime-Provenance
# verloren hat. Installiert wird ueber deploy/bin/install_standby_backup.sh.
#
# Non-destructive: writes ONLY under /mnt/kai-data/kai-standby/. Leaves the
# existing eow_snapshots/ untouched.
#
# Tiers (two systemd timers):
#   system  (weekly): Quell-Checkout + AKTIVES immutable Release (inkl. dessen
#                     .venv) + Deployment-Marker + systemd units + rebuild hints.
#                     Excludes data/ + artifacts/ (captured by 'data') + caches/.git.
#   data    (6h):     data/ + artifacts/ -- the irreplaceable append-only `n`.
#
# WARUM DAS RELEASE MIT MUSS (#848 / ADR 0017):
# Seit dem Release-Modell laufen zwei produktive Code-Welten nebeneinander --
# der Quell-Checkout mit den checkout-gebundenen Units, und `current ->
# releases/<SHA>` mit den fuenf sich selbst bezeugenden Daemons. Ein System-Tier,
# das weiterhin nur den Checkout sichert, ist unvollstaendig. Der schlimme Teil
# ist nicht die Luecke, sondern dass so ein Lauf GRUEN meldet: ein Backup, das
# die Haelfte des laufenden Codes nicht enthaelt und Erfolg zurueckgibt, ist
# gefaehrlicher als eines, das ausfaellt -- der Ausfall wird bemerkt.
#
# Deshalb gilt hier: JEDE fehlende Zusicherung ist ein harter Fehlschlag.
# Kein `|| continue`, kein `[ -d "$X" ] ||`, kein "der Checkout wurde immerhin
# gesichert". FALSE_GREEN_ON_MISSING_ACTIVE_RELEASE = IMPOSSIBLE.
#
# Der Checkout wird NICHT ersetzt. Beides wird gesichert: nur zusammen laesst
# sich sowohl der Entwicklungsstand als auch die tatsaechlich laufende Revision
# wiederherstellen.
#
# Recovery (see RESTORE_FROM_USB.md): flash stock Ubuntu for Pi 5 -> untar newest
# system_ + release_ + etc_ -> untar newest data_ -> fix fstab UUID ->
# `current` auf das entpackte Release zeigen lassen -> systemctl enable --now.
#
# exfat note: no Unix perms/symlinks on the FS itself, but tar PRESERVES them
# inside the archive, so .venv symlinks + file modes survive the round-trip.
#
# VERSCHLUESSELT SEIT 2026-10-02 (Operator-Entscheid). Vorher lagen .env,
# Telegram-Sitzung und IP-haltige Zugriffsprotokolle im Klartext auf dem Stick --
# "gleiche physische Vertrauensgrenze" stimmte, solange der Stick steckt, aber er
# ist genau das Teil, das man abzieht, verleiht oder entsorgt, und die
# Datenschutzseite sagt "verschluesselte Sicherungen". Jetzt:
#   * jedes Archiv entsteht als tar | openssl direkt auf dem Stick, im selben
#     Format wie Vault und Pi-Tagesarchive (aes-256-cbc, PBKDF2, 200 000 Runden,
#     KAI_BACKUP_PASSPHRASE) -- Klartext beruehrt den Stick nie;
#   * jedes Archiv wird sofort entschluesselt und vollstaendig gelistet, erst dann
#     bekommt es seinen Namen und eine .sha256 daneben;
#   * fehlt die Passphrase, wird NICHTS geschrieben und der Lauf endet rot;
#   * die Passphrase wird aus der .env GELESEN, nie `source`d: dieses Skript laeuft
#     als root, die .env gehoert `ubuntu` -- sourcen hiesse, fremden Code als root
#     auszufuehren;
#   * nach einem erfolgreichen Lauf loescht das Skript die alten Klartext-Saetze
#     seiner Stufe selbst.
set -euo pipefail

MODE="${1:?usage: standby_to_usb.sh system|data}"

# Pfade sind ueberschreibbar, damit der Vertrag ohne Pi und ohne root pruefbar
# ist. Die Vorgaben sind die Produktionswerte; ein Test, der sie nicht setzt,
# testet die Produktion.
REPO="${KAI_STANDBY_REPO:-/home/ubuntu/ai_analyst_trading_bot}"
CURRENT_LINK="${KAI_STANDBY_CURRENT:-/home/kai/current}"
RELEASES_ROOT="${KAI_STANDBY_RELEASES_ROOT:-/home/ubuntu/releases}"
STATE_ROOT="${KAI_STANDBY_STATE_ROOT:-$REPO}"
USB="${KAI_STANDBY_USB:-/mnt/kai-data/kai-standby}"
# `-` statt `:-`: ein AUSDRUECKLICH leer gesetzter Wert schaltet den Guard ab,
# ein ungesetzter bekommt den Produktionspfad. Mit `:-` waere beides gleich
# gewesen — der Guard haette sich nicht abschalten lassen, und ein Test, der
# ihn abschalten will, liefe stattdessen gegen /mnt/kai-data.
MOUNT_GUARD="${KAI_STANDBY_MOUNT_GUARD-/mnt/kai-data}"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG=$USB/standby.log

DEPLOY_MARKER="$STATE_ROOT/artifacts/runtime/deployment_marker.json"
ENV_FILE="$REPO/.env"
RESTORE_DOC="$REPO/deploy/standby/RESTORE_FROM_USB.md"
PASS=""

log() { echo "$(date -u +%FT%TZ)  [$MODE] $*" | tee -a "$LOG" >&2; }

# Ein Vertragsbruch endet den Lauf. Der Grund steht im Log UND auf stderr, damit
# er im systemd-Journal auftaucht und nicht nur in einer Datei auf dem USB, die
# beim Restore vielleicht gerade nicht lesbar ist.
fail() {
    log "BACKUP_FAIL: $*"
    exit 1
}

# Passphrase LESEN, nicht sourcen (root liest eine ubuntu-Datei; `. .env` wuerde
# jede Kommandosubstitution darin als root ausfuehren). Gleiche Semantik wie das
# Vault fuer einfache Werte: KEY=wert, optional in "..." oder '...'.
read_passphrase() {
    local line
    [ -r "$ENV_FILE" ] || return 1
    line="$(grep -m1 -E '^[[:space:]]*(export[[:space:]]+)?KAI_BACKUP_PASSPHRASE=' "$ENV_FILE" || true)"
    line="${line#*KAI_BACKUP_PASSPHRASE=}"
    line="${line%$'\r'}"
    case "$line" in
        \"*\") line="${line#\"}"; line="${line%\"}" ;;
        \'*\') line="${line#\'}"; line="${line%\'}" ;;
    esac
    PASS="$line"
    [ "${#PASS}" -ge 32 ]
}

# Entschluesseln und VOLLSTAENDIG auflisten. `tar tzf -` liest bis zum Ende, prueft
# also auch die gzip-Pruefsumme. Kein `grep -q` in dieser Pipe (siehe archive_has).
list_sealed() {
    local archive=$1 cache=$2 st
    set +e
    KAI_STANDBY_PASS="$PASS" openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
        -pass env:KAI_STANDBY_PASS -in "$archive" 2>>"$LOG" \
        | tar tzf - > "$cache" 2>>"$LOG"
    st=("${PIPESTATUS[@]}")
    set -e
    [ "${st[0]}" -eq 0 ] && [ "${st[1]}" -eq 0 ] && [ -s "$cache" ]
}

# seal <name> <tar-argumente...>: verschluesselt schreiben, pruefen, erst dann fertig.
#
# tar | openssl landet als <name>.part auf dem Stick. tar ueber einen LAUFENDEN
# Baum endet mit 1 ("file changed as we read it") -- fuer append-only-Stroeme
# harmlos (schlimmstenfalls eine halbe letzte Zeile); 0 und 1 gelten, ab 2 und
# bei jedem openssl-Fehler ist der Satz verworfen. Danach die Probe
# (list_sealed); die Liste bleibt fuer archive_has im Cache. Erst nach bestandener
# Probe entstehen <name>.sha256 und der fertige Name. Gibt != 0 zurueck statt
# abzubrechen: der Aufrufer kennt den passenden BACKUP_FAIL-Grund.
seal() {
    local name=$1 st
    shift
    local part="$USB/$name.part" cache="$_LISTING_DIR/$name.list"
    rm -f "$part"
    set +e
    tar czf - "$@" 2>>"$LOG" \
        | KAI_STANDBY_PASS="$PASS" openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
            -pass env:KAI_STANDBY_PASS -out "$part" 2>>"$LOG"
    st=("${PIPESTATUS[@]}")
    set -e
    if [ "${st[0]}" -ge 2 ] || [ "${st[1]}" -ne 0 ]; then
        log "FAIL: tar rc=${st[0]} openssl rc=${st[1]} for $name"
        rm -f "$part"
        return 1
    fi
    if [ "${st[0]}" -eq 1 ]; then
        log "note: tar rc=1 (live file changed during read) -- accepted"
    fi
    if ! list_sealed "$part" "$cache"; then
        log "FAIL: Probe (entschluesseln + auflisten) fuer $name"
        rm -f "$part" "$cache"
        return 1
    fi
    (cd "$USB" && sha256sum "$name.part" | sed 's/\.part$//' > "$name.sha256")
    mv "$part" "$USB/$name"
    log "sealed: $name ($(wc -l < "$cache") Eintraege, $(du -h "$USB/$name" | cut -f1))"
}

# Aufbewahrung: die neuesten <n> verschluesselten Saetze einer Stufe, samt .sha256.
keep_newest() {
    local kind=$1 n=$2 f
    { ls -1t "$USB/${kind}"_*.tar.gz.enc 2>/dev/null || true; } | tail -n +$((n + 1)) \
        | while read -r f; do rm -f "$f" "$f.sha256"; done
}

# Alte Klartext-Saetze (vor 2026-10-02) und liegengebliebene .part einer Stufe
# loeschen -- nur, wenn es von ihr schon einen fertigen verschluesselten Satz gibt.
drop_plaintext() {
    local kind=$1
    { ls "$USB/${kind}"_*.tar.gz.enc >/dev/null 2>&1; } || return 0
    find "$USB" -maxdepth 1 -type f \
        \( -name "${kind}_*.tar.gz" -o -name "${kind}_*.tar.gz.part" \) -delete
}

# Ein JSON-Feld ohne Python: dieses Skript laeuft im Wiederherstellungspfad und
# darf nicht davon abhaengen, dass ein Interpreter mit passenden Paketen da ist.
#
# `|| true` ist hier kein Schlampern, sondern noetig: unter `set -o pipefail`
# laesst ein NICHT gefundenes Feld die ganze Pipeline mit 1 zurueckkommen, und
# `set -e` beendet das Skript dann SOFORT -- fail-closed zwar, aber ohne Grund im
# Log und im Journal. Ein fehlendes Feld ist hier schlicht ein leerer Wert; die
# Bewertung macht der Aufrufer, der den passenden BACKUP_FAIL-Grund kennt.
json_field() {
    local file=$1 key=$2
    grep -o "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$file" 2>/dev/null \
        | head -1 | sed 's/.*"\([^"]*\)"[[:space:]]*$/\1/' || true
}

# Enthaelt das Archiv wirklich, was es enthalten soll? Ein tar, das leise nichts
# eingepackt hat, ist die Kernvariante des falschen Gruens.
#
# WARUM HIER KEIN `grep -q` STEHT (gemessen 2026-09-07 auf kai-pi5)
#
# `tar tzf ... | grep -qE ...` liest das Archiv NICHT zu Ende: `grep -q` steigt
# beim ersten Treffer aus und schliesst die Pipe, `tar` bekommt SIGPIPE und
# endet mit 141. Unter `set -o pipefail` -- das dieses Skript in Zeile 49 setzt
# -- ist der Status der Pipeline damit der von `tar`, also ein Fehlschlag.
#
# Ergebnis: ein GEFUNDENER Eintrag las sich als fehlender. Der erste echte Lauf
# des Vertrags meldete
#
#     BACKUP_FAIL: ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (release.json)
#
# obwohl das Archiv `./release.json` und 24.817 `.venv`-Eintraege enthielt.
# Belegt in der Shell:
#
#     ( set -euo pipefail; tar tzf $A | grep -qE '(^|/)release\.json$' ) -> 141
#     ( set -eu;           tar tzf $A | grep -qE '(^|/)release\.json$' ) ->   0
#
# Je frueher der Treffer im Archiv liegt, desto sicherer schlaegt es fehl --
# eine fail-closed-Pruefung, die ausgerechnet den Erfolgsfall bestraft. Seit
# dem 2026-08-31 entstand deshalb kein System-Backup mehr.
#
# Die Liste wird einmal je Archiv erzeugt und dann mehrfach durchsucht: vier
# Pruefungen brauchen sonst vier vollstaendige Entpackvorgaenge von 137 MB.
# Einmal je Lauf, NICHT lazy in einer Kommandosubstitution: `$( )` laeuft in
# einer Subshell, deren EXIT-Trap sofort feuert -- das Verzeichnis waere weg,
# bevor `grep` es liest. Genau daran ist die erste Fassung gescheitert.
_LISTING_DIR="$(mktemp -d)"
trap 'rm -rf "$_LISTING_DIR"' EXIT

archive_has() {
    local archive=$1 pattern=$2 cache treffer
    cache="$_LISTING_DIR/$(basename "$archive").list"
    if [ ! -s "$cache" ]; then
        list_sealed "$archive" "$cache" || return 1
    fi
    # `grep -c` liest bis zum Ende -- kein SIGPIPE, kein falsches Negativ.
    treffer="$(grep -cE "$pattern" "$cache" || true)"
    [ "${treffer:-0}" -gt 0 ]
}

# Guard: target must be the real mounted USB, not a fallback dir on the SD.
if [ -n "$MOUNT_GUARD" ]; then
    mountpoint -q "$MOUNT_GUARD" || { echo "FAIL: $MOUNT_GUARD not mounted" >&2; exit 1; }
fi
case "$MODE" in
    system | data) ;;
    *) echo "unknown mode: $MODE (use system|data)" >&2; exit 2 ;;
esac
mkdir -p "$USB"
log "start ($TS)"
read_passphrase \
    || fail "PASSPHRASE_MISSING (KAI_BACKUP_PASSPHRASE in $ENV_FILE fehlt oder < 32 Zeichen) -- nichts geschrieben"

case "$MODE" in
  system)
    # ---- 1. Quell-Checkout, unveraendert wie bisher -------------------------
    [ -d "$REPO" ] || fail "CHECKOUT_MISSING ($REPO)"
    seal "system_$TS.tar.gz.enc" \
        --exclude=./data --exclude=./artifacts --exclude=./.git \
        --exclude='./.mypy_cache' --exclude='./.ruff_cache' \
        --exclude='./.pytest_cache' --exclude='./.hypothesis' \
        -C "$REPO" . \
        || fail "SYSTEM_TAR_FAILED ($REPO)"

    # ---- 2. Das AKTIVE Release -- Vertrag, kein Bonus ----------------------
    # Aufgeloest, nicht als Symlink: ein Backup des Symlinks sichert einen Namen.
    [ -L "$CURRENT_LINK" ] || [ -d "$CURRENT_LINK" ] \
        || fail "ACTIVE_RELEASE_MISSING (kein $CURRENT_LINK)"
    RELEASE_PATH="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
    [ -n "$RELEASE_PATH" ] && [ -d "$RELEASE_PATH" ] \
        || fail "ACTIVE_RELEASE_DANGLING ($CURRENT_LINK -> '${RELEASE_PATH:-?}')"

    # Der aufgeloeste Pfad muss unter dem erlaubten Release-Root liegen. Sonst
    # koennte `current` auf irgendetwas zeigen und das Backup wuerde es fuer den
    # laufenden Code halten.
    RELEASES_ROOT_REAL="$(readlink -f "$RELEASES_ROOT" 2>/dev/null || echo "$RELEASES_ROOT")"
    case "$RELEASE_PATH/" in
        "$RELEASES_ROOT_REAL"/*/) : ;;
        *) fail "ACTIVE_RELEASE_OUTSIDE_ROOT ($RELEASE_PATH nicht unter $RELEASES_ROOT_REAL)" ;;
    esac

    [ -f "$RELEASE_PATH/release.json" ] || fail "RELEASE_JSON_MISSING ($RELEASE_PATH)"
    [ -d "$RELEASE_PATH/.venv" ] || fail "VENV_MISSING ($RELEASE_PATH/.venv)"

    # ---- 3. Deployment-Marker aus dem STABILEN Zustandspfad ----------------
    [ -f "$DEPLOY_MARKER" ] || fail "DEPLOYMENT_MARKER_MISSING ($DEPLOY_MARKER)"
    MARKER_SHA="$(json_field "$DEPLOY_MARKER" repo_sha)"
    RELEASE_SHA="$(json_field "$RELEASE_PATH/release.json" repo_sha)"
    [ -n "$MARKER_SHA" ] || fail "DEPLOYMENT_MARKER_UNREADABLE (kein repo_sha)"
    [ -n "$RELEASE_SHA" ] || fail "RELEASE_JSON_UNREADABLE (kein repo_sha)"
    [ "$MARKER_SHA" = "$RELEASE_SHA" ] \
        || fail "MARKER_RELEASE_MISMATCH (marker=$MARKER_SHA release=$RELEASE_SHA)"

    # ---- 4. Release sichern, .venv ausdruecklich EINGESCHLOSSEN ------------
    # Kein --exclude=.venv: ohne sie ist der Baum kein lauffaehiger Stand,
    # sondern Quelltext, und der Restore braeuchte Netz und Paketquellen.
    seal "release_$TS.tar.gz.enc" -C "$RELEASE_PATH" . \
        || fail "RELEASE_TAR_FAILED ($RELEASE_PATH)"

    seal "deploymarker_$TS.tar.gz.enc" \
        -C "$(dirname "$DEPLOY_MARKER")" "$(basename "$DEPLOY_MARKER")" \
        || fail "DEPLOYMENT_MARKER_TAR_FAILED"

    # ---- 5. Inventar: enthaelt das Archiv wirklich, was es soll? -----------
    archive_has "$USB/release_$TS.tar.gz.enc" '(^|/)release\.json$' \
        || fail "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (release.json)"
    archive_has "$USB/release_$TS.tar.gz.enc" '(^|/)\.venv/' \
        || fail "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (.venv)"
    archive_has "$USB/release_$TS.tar.gz.enc" '(^|/)app/' \
        || fail "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (app/)"
    archive_has "$USB/deploymarker_$TS.tar.gz.enc" 'deployment_marker\.json$' \
        || fail "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (deployment_marker.json)"

    # Config bits outside the repo needed for a clean rebuild.
    archive_has "$USB/system_$TS.tar.gz.enc" '(^|/)app/' \
        || fail "ARCHIVE_MISSING_REQUIRED_CHECKOUT_CONTENT (app/)"
    seal "etc_$TS.tar.gz.enc" -C / etc/systemd/system etc/fstab \
        || log "WARN: etc-Abzug nicht erstellt (best effort, kein Vertragsbestandteil)"
    # Rebuild hints (versions + package state) for a faithful restore.
    {
        echo "# KAI standby rebuild hints  $TS"
        echo "## uname"; uname -a
        echo "## python"; python3 --version 2>&1
        echo "## repo HEAD"; git -C "$REPO" rev-parse HEAD 2>/dev/null
        echo "## active release"; echo "$RELEASE_PATH"
        echo "## active release repo_sha"; echo "$RELEASE_SHA"
        echo "## deployment marker repo_sha"; echo "$MARKER_SHA"
        echo "## fstab kai-data UUID"; grep kai-data /etc/fstab 2>/dev/null
        echo "## kai/cloudflared units"; ls /etc/systemd/system/ | grep -Ei 'kai|cloudflared' 2>/dev/null
    } > "$USB/REBUILD_HINTS_$TS.txt" 2>/dev/null || true
    # Retention: keep newest 4 weekly sets (verschluesselt, samt .sha256).
    for kind in system release deploymarker etc; do
        keep_newest "$kind" 4
        drop_plaintext "$kind"
    done
    { ls -1t "$USB"/REBUILD_HINTS_*.txt 2>/dev/null || true; } | tail -n +5 | xargs -r rm -f
    sz=$(du -h "$USB/system_$TS.tar.gz.enc" | cut -f1)
    rsz=$(du -h "$USB/release_$TS.tar.gz.enc" | cut -f1)
    log "done: system_$TS.tar.gz.enc ($sz) + release_$TS.tar.gz.enc ($rsz) [$RELEASE_SHA]"
    ;;
  data)
    seal "data_$TS.tar.gz.enc" -C "$REPO" data artifacts || fail "DATA_TAR_FAILED ($REPO)"
    archive_has "$USB/data_$TS.tar.gz.enc" '^(\./)?data(/|$)' \
        || fail "ARCHIVE_MISSING_REQUIRED_DATA_CONTENT (data/)"
    archive_has "$USB/data_$TS.tar.gz.enc" '^(\./)?artifacts(/|$)' \
        || fail "ARCHIVE_MISSING_REQUIRED_DATA_CONTENT (artifacts/)"
    # Retention: keep newest 28 sets (~7d @ 6h).
    keep_newest data 28
    drop_plaintext data
    sz=$(du -h "$USB/data_$TS.tar.gz.enc" | cut -f1)
    log "done: data_$TS.tar.gz.enc ($sz)"
    ;;
esac

# Die Wiederherstellungsanleitung liegt versioniert im Repo und wird bei jedem
# erfolgreichen Lauf auf den Stick gelegt -- dort wird sie im Ernstfall gelesen.
if [ -f "$RESTORE_DOC" ]; then
    cp -f "$RESTORE_DOC" "$USB/RESTORE_FROM_USB.md.part" \
        && mv -f "$USB/RESTORE_FROM_USB.md.part" "$USB/RESTORE_FROM_USB.md"
fi
