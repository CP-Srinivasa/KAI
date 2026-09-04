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
# Secrets (.env, session) land plaintext-in-archive on the USB -- acceptable: the
# USB shares the Pi's physical trust boundary. The OFF-SITE copy (OneDrive) is the
# encrypted one. Do NOT carry this USB off-premises unencrypted.
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
MOUNT_GUARD="${KAI_STANDBY_MOUNT_GUARD:-/mnt/kai-data}"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG=$USB/standby.log

DEPLOY_MARKER="$STATE_ROOT/artifacts/runtime/deployment_marker.json"

log() { echo "$(date -u +%FT%TZ)  [$MODE] $*" | tee -a "$LOG" >&2; }

# Ein Vertragsbruch endet den Lauf. Der Grund steht im Log UND auf stderr, damit
# er im systemd-Journal auftaucht und nicht nur in einer Datei auf dem USB, die
# beim Restore vielleicht gerade nicht lesbar ist.
fail() {
    log "BACKUP_FAIL: $*"
    exit 1
}

# tar over a LIVE tree: the running bot appends to JSONL while we read, so tar
# returns 1 ("file changed as we read it"). That is benign for an append-only
# snapshot (worst case a partial trailing line). Accept 0 and 1; fail only on >=2.
tar_snapshot() {
    local out=$1; shift
    local rc=0
    tar czf "$out" "$@" 2>>"$LOG" || rc=$?
    if [ "$rc" -ge 2 ]; then log "FAIL: tar rc=$rc for $out"; return "$rc"; fi
    [ "$rc" -eq 1 ] && log "note: tar rc=1 (live file changed during read) -- accepted"
    return 0
}

# Ein JSON-Feld ohne Python: dieses Skript laeuft im Wiederherstellungspfad und
# darf nicht davon abhaengen, dass ein Interpreter mit passenden Paketen da ist.
json_field() {
    local file=$1 key=$2
    grep -o "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$file" 2>/dev/null \
        | head -1 | sed 's/.*"\([^"]*\)"[[:space:]]*$/\1/'
}

# Enthaelt das Archiv wirklich, was es enthalten soll? Ein tar, das leise nichts
# eingepackt hat, ist die Kernvariante des falschen Gruens.
archive_has() {
    local archive=$1 pattern=$2
    tar tzf "$archive" 2>/dev/null | grep -qE "$pattern"
}

# Guard: target must be the real mounted USB, not a fallback dir on the SD.
if [ -n "$MOUNT_GUARD" ]; then
    mountpoint -q "$MOUNT_GUARD" || { echo "FAIL: $MOUNT_GUARD not mounted" >&2; exit 1; }
fi
mkdir -p "$USB"
log "start ($TS)"

case "$MODE" in
  system)
    # ---- 1. Quell-Checkout, unveraendert wie bisher -------------------------
    [ -d "$REPO" ] || fail "CHECKOUT_MISSING ($REPO)"
    tar_snapshot "$USB/system_$TS.tar.gz.part" \
        --exclude=./data --exclude=./artifacts --exclude=./.git \
        --exclude='./.mypy_cache' --exclude='./.ruff_cache' \
        --exclude='./.pytest_cache' --exclude='./.hypothesis' \
        -C "$REPO" .
    mv "$USB/system_$TS.tar.gz.part" "$USB/system_$TS.tar.gz"

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
    tar_snapshot "$USB/release_$TS.tar.gz.part" -C "$RELEASE_PATH" . \
        || fail "RELEASE_TAR_FAILED ($RELEASE_PATH)"
    mv "$USB/release_$TS.tar.gz.part" "$USB/release_$TS.tar.gz"

    tar_snapshot "$USB/deploymarker_$TS.tar.gz.part" \
        -C "$(dirname "$DEPLOY_MARKER")" "$(basename "$DEPLOY_MARKER")" \
        || fail "DEPLOYMENT_MARKER_TAR_FAILED"
    mv "$USB/deploymarker_$TS.tar.gz.part" "$USB/deploymarker_$TS.tar.gz"

    # ---- 5. Inventar: enthaelt das Archiv wirklich, was es soll? -----------
    archive_has "$USB/release_$TS.tar.gz" '(^|/)release\.json$' \
        || fail "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (release.json)"
    archive_has "$USB/release_$TS.tar.gz" '(^|/)\.venv/' \
        || fail "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (.venv)"
    archive_has "$USB/release_$TS.tar.gz" '(^|/)app/' \
        || fail "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (app/)"
    archive_has "$USB/deploymarker_$TS.tar.gz" 'deployment_marker\.json$' \
        || fail "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (deployment_marker.json)"

    # Config bits outside the repo needed for a clean rebuild.
    tar_snapshot "$USB/etc_$TS.tar.gz.part" -C / etc/systemd/system etc/fstab || true
    [ -f "$USB/etc_$TS.tar.gz.part" ] && mv "$USB/etc_$TS.tar.gz.part" "$USB/etc_$TS.tar.gz"
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
    # Retention: keep newest 4 weekly sets.
    ls -1t "$USB"/system_*.tar.gz 2>/dev/null | tail -n +5 | xargs -r rm -f
    ls -1t "$USB"/release_*.tar.gz 2>/dev/null | tail -n +5 | xargs -r rm -f
    ls -1t "$USB"/deploymarker_*.tar.gz 2>/dev/null | tail -n +5 | xargs -r rm -f
    ls -1t "$USB"/etc_*.tar.gz 2>/dev/null | tail -n +5 | xargs -r rm -f
    ls -1t "$USB"/REBUILD_HINTS_*.txt 2>/dev/null | tail -n +5 | xargs -r rm -f
    sz=$(du -h "$USB/system_$TS.tar.gz" | cut -f1)
    rsz=$(du -h "$USB/release_$TS.tar.gz" | cut -f1)
    log "done: system_$TS.tar.gz ($sz) + release_$TS.tar.gz ($rsz) [$RELEASE_SHA]"
    ;;
  data)
    tar_snapshot "$USB/data_$TS.tar.gz.part" -C "$REPO" data artifacts
    mv "$USB/data_$TS.tar.gz.part" "$USB/data_$TS.tar.gz"
    # Retention: keep newest 28 sets (~7d @ 6h).
    ls -1t "$USB"/data_*.tar.gz 2>/dev/null | tail -n +29 | xargs -r rm -f
    sz=$(du -h "$USB/data_$TS.tar.gz" | cut -f1)
    log "done: data_$TS.tar.gz ($sz)"
    ;;
  *)
    echo "unknown mode: $MODE (use system|data)" >&2; exit 2 ;;
esac
