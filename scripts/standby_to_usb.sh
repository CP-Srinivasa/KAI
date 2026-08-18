#!/usr/bin/env bash
# KAI cold-standby to the attached USB (SanDisk Extreme Pro, /mnt/kai-data, exfat).
# 2026-06-13. Captures the LOCAL restore set so a dead boot-SD can be recovered
# fast WITHOUT depending on the Windows PC or the network -- and uniquely captures
# the SYSTEM/deps/config layer that the off-Pi backups (data only) leave out.
#
# Non-destructive: writes ONLY under /mnt/kai-data/kai-standby/. Leaves the
# existing eow_snapshots/ untouched.
#
# Tiers (two systemd timers):
#   system  (weekly): repo code + .venv + systemd units + rebuild hints.
#                     Excludes data/ + artifacts/ (captured by 'data') + caches/.git.
#   data    (6h):     data/ + artifacts/ -- the irreplaceable append-only `n`.
#
# Recovery (see RESTORE_FROM_USB.md): flash stock Ubuntu for Pi 5 -> untar newest
# system_ + etc_ -> untar newest data_ -> fix fstab UUID -> systemctl enable --now.
#
# exfat note: no Unix perms/symlinks on the FS itself, but tar PRESERVES them
# inside the archive, so .venv symlinks + file modes survive the round-trip.
# Secrets (.env, session) land plaintext-in-archive on the USB -- acceptable: the
# USB shares the Pi's physical trust boundary. The OFF-SITE copy (OneDrive) is the
# encrypted one. Do NOT carry this USB off-premises unencrypted.
set -euo pipefail

MODE="${1:?usage: standby_to_usb.sh system|data}"
REPO=/home/ubuntu/ai_analyst_trading_bot
USB=/mnt/kai-data/kai-standby
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG=$USB/standby.log

log() { echo "$(date -u +%FT%TZ)  [$MODE] $*" | tee -a "$LOG" >&2; }

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

# Guard: target must be the real mounted USB, not a fallback dir on the SD.
mountpoint -q /mnt/kai-data || { echo "FAIL: /mnt/kai-data not mounted" >&2; exit 1; }
mkdir -p "$USB"
log "start ($TS)"

case "$MODE" in
  system)
    # Code + deps + config. Exclude volatile data (own tier), git, caches.
    tar_snapshot "$USB/system_$TS.tar.gz.part" \
        --exclude=./data --exclude=./artifacts --exclude=./.git \
        --exclude='./.mypy_cache' --exclude='./.ruff_cache' \
        --exclude='./.pytest_cache' --exclude='./.hypothesis' \
        -C "$REPO" .
    mv "$USB/system_$TS.tar.gz.part" "$USB/system_$TS.tar.gz"
    # Config bits outside the repo needed for a clean rebuild.
    tar_snapshot "$USB/etc_$TS.tar.gz.part" -C / etc/systemd/system etc/fstab || true
    [ -f "$USB/etc_$TS.tar.gz.part" ] && mv "$USB/etc_$TS.tar.gz.part" "$USB/etc_$TS.tar.gz"
    # Rebuild hints (versions + package state) for a faithful restore.
    {
        echo "# KAI standby rebuild hints  $TS"
        echo "## uname"; uname -a
        echo "## python"; python3 --version 2>&1
        echo "## repo HEAD"; git -C "$REPO" rev-parse HEAD 2>/dev/null
        echo "## fstab kai-data UUID"; grep kai-data /etc/fstab 2>/dev/null
        echo "## kai/cloudflared units"; ls /etc/systemd/system/ | grep -Ei 'kai|cloudflared' 2>/dev/null
    } > "$USB/REBUILD_HINTS_$TS.txt" 2>/dev/null || true
    # Retention: keep newest 4 weekly sets.
    ls -1t "$USB"/system_*.tar.gz 2>/dev/null | tail -n +5 | xargs -r rm -f
    ls -1t "$USB"/etc_*.tar.gz 2>/dev/null | tail -n +5 | xargs -r rm -f
    ls -1t "$USB"/REBUILD_HINTS_*.txt 2>/dev/null | tail -n +5 | xargs -r rm -f
    sz=$(du -h "$USB/system_$TS.tar.gz" | cut -f1)
    log "done: system_$TS.tar.gz ($sz)"
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
