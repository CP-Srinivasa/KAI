#!/usr/bin/env bash
# KAI off-site backup of critical audit/decision artifacts.
#
# Bundles a curated list of JSONL/JSON files into a timestamped tar.gz,
# encrypts it with AES-256-CBC (PBKDF2 key derivation, KAI_BACKUP_PASSPHRASE
# env), stages the encrypted archive in artifacts/backups/, and optionally
# pushes to a configured rclone remote.
#
# Without this, alert_audit / alert_outcomes / paper execution history is
# gone in a single hardware failure — and the TV-Pivot Re-Entry-Gate at
# 2026-05-16 has no datasource to evaluate against. Backup is the cheap
# step that makes every later step survivable.
#
# Required env:
#   KAI_BACKUP_PASSPHRASE  passphrase for archive encryption (>=32 chars).
#
# Optional env:
#   KAI_BACKUP_RCLONE_REMOTE  rclone target (e.g. "kai-r2:kai-backups").
#                             When unset, backup stays local-only and a
#                             warning is logged — useful while operator
#                             is still configuring the R2 bucket.
#   KAI_BACKUP_KEEP_DAYS      retention of local stage, default 30.
#   KAI_BACKUP_EXTRA_FILES    space-separated additional files to include.
#
# Exit codes:
#   0  success (encrypted backup written, push attempted per config)
#   2  missing passphrase
#   3  zero source files exist — nothing to back up (likely path mistake)
#   4  encryption or archive failure
#   5  rclone push failed (local copy still kept)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_FILE="$ROOT/artifacts/kai_backup.log"
AUDIT_FILE="$ROOT/artifacts/backup_audit.jsonl"
STAGE_DIR="$ROOT/artifacts/backups"
KEEP_DAYS="${KAI_BACKUP_KEEP_DAYS:-30}"

mkdir -p "$ROOT/artifacts" "$STAGE_DIR"

# --- helpers ----------------------------------------------------------------

write_log() {
    local ts
    ts=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
    printf '%s  %s\n' "$ts" "$1" >> "$LOG_FILE"
}

# Append a single JSON line to backup_audit.jsonl. We hand-roll the JSON so
# we don't pull jq in as a Pi dependency. All values are escaped through
# printf %s with the quote characters pre-escaped by the caller — keep
# string values free of newlines and double-quotes.
write_audit() {
    local status="$1" archive="$2" sha256="$3" bytes="$4" \
          remote="$5" remote_status="$6" note="$7"
    local ts
    ts=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
    printf '{"ts":"%s","status":"%s","archive":"%s","sha256":"%s","bytes":%s,"remote":"%s","remote_status":"%s","note":"%s"}\n' \
        "$ts" "$status" "$archive" "$sha256" "$bytes" "$remote" "$remote_status" "$note" \
        >> "$AUDIT_FILE"
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        printf 'unavailable'
    fi
}

# --- preconditions ----------------------------------------------------------

if [[ -z "${KAI_BACKUP_PASSPHRASE:-}" ]]; then
    write_log "ERROR: KAI_BACKUP_PASSPHRASE not set — refusing to write unencrypted backup."
    write_audit "fail_no_passphrase" "" "" 0 "" "" "passphrase missing"
    exit 2
fi

if [[ ${#KAI_BACKUP_PASSPHRASE} -lt 32 ]]; then
    write_log "WARN: KAI_BACKUP_PASSPHRASE is shorter than 32 characters; archive is still encrypted but consider strengthening."
fi

if ! command -v openssl >/dev/null 2>&1; then
    write_log "ERROR: openssl binary not in PATH — cannot encrypt."
    write_audit "fail_no_openssl" "" "" 0 "" "" "openssl missing"
    exit 4
fi

# --- source list ------------------------------------------------------------
# Critical artifacts that, if lost, cannot be reconstructed from code alone.
# Keep this list explicit — globbing artifacts/* would sweep up cron logs,
# experiment outputs, and very large trading_loop_audit history that we
# don't actually need offsite every run.
DEFAULT_SOURCES=(
    "artifacts/alert_audit.jsonl"
    "artifacts/alert_outcomes.jsonl"
    "artifacts/paper_execution_audit.jsonl"
    "artifacts/trading_loop_audit.jsonl"
    "artifacts/telegram_message_envelope.jsonl"
    "artifacts/telegram_approval_send.jsonl"
    "artifacts/telegram_channel_raw.jsonl"
    "artifacts/telegram_channel_checkpoint.json"
    "artifacts/ph5_hold_metrics_report.json"
    "DECISION_LOG.md"
)

EXTRAS=()
if [[ -n "${KAI_BACKUP_EXTRA_FILES:-}" ]]; then
    # word-split intentional — each token is a path
    # shellcheck disable=SC2206
    EXTRAS=( ${KAI_BACKUP_EXTRA_FILES} )
fi

# Filter to only existing files. Missing files are logged but do not abort
# (e.g. checkpoint.json doesn't exist before the first run; that's fine).
EXISTING=()
MISSING=()
for f in "${DEFAULT_SOURCES[@]}" "${EXTRAS[@]}"; do
    if [[ -f "$ROOT/$f" ]]; then
        EXISTING+=( "$f" )
    else
        MISSING+=( "$f" )
    fi
done

if [[ ${#EXISTING[@]} -eq 0 ]]; then
    write_log "ERROR: zero source files exist — refusing empty backup. ROOT=$ROOT"
    write_audit "fail_no_sources" "" "" 0 "" "" "no source files present"
    exit 3
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    write_log "INFO: ${#MISSING[@]} configured source(s) missing, skipped: ${MISSING[*]}"
fi

# --- bundle + encrypt -------------------------------------------------------

TS=$(date -u +'%Y-%m-%dT%H-%M-%SZ')
DAY=$(date -u +'%Y-%m-%d')
DAY_DIR="$STAGE_DIR/$DAY"
mkdir -p "$DAY_DIR"

ARCHIVE_NAME="kai_artifacts_${TS}.tar.gz"
ARCHIVE_TMP="$DAY_DIR/$ARCHIVE_NAME"
ARCHIVE_ENC="$DAY_DIR/${ARCHIVE_NAME}.enc"

write_log "Bundling ${#EXISTING[@]} file(s) into $ARCHIVE_NAME"
if ! tar -czf "$ARCHIVE_TMP" -C "$ROOT" "${EXISTING[@]}" 2>>"$LOG_FILE"; then
    write_log "ERROR: tar failed."
    rm -f "$ARCHIVE_TMP"
    write_audit "fail_tar" "$ARCHIVE_NAME" "" 0 "" "" "tar exit non-zero"
    exit 4
fi

# Encrypt with PBKDF2 + AES-256-CBC. -pbkdf2 + a non-trivial iteration count
# defends against passphrase-bruteforce in case the encrypted blob ends up
# in cloud-storage where access logs aren't strict. CBC alone is not AEAD,
# but the threat model here is offline storage, not active tampering — for
# integrity we record sha256 in the audit log and verify on restore.
if ! openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
        -in "$ARCHIVE_TMP" -out "$ARCHIVE_ENC" \
        -pass "env:KAI_BACKUP_PASSPHRASE" 2>>"$LOG_FILE"; then
    write_log "ERROR: openssl encryption failed."
    rm -f "$ARCHIVE_TMP" "$ARCHIVE_ENC"
    write_audit "fail_encrypt" "$ARCHIVE_NAME" "" 0 "" "" "openssl exit non-zero"
    exit 4
fi

# Always shred the plaintext archive — it must never linger on disk.
rm -f "$ARCHIVE_TMP"

ENC_BYTES=$(wc -c <"$ARCHIVE_ENC" | tr -d ' ')
ENC_SHA=$(sha256_of "$ARCHIVE_ENC")
write_log "Encrypted archive ready: $ARCHIVE_ENC bytes=$ENC_BYTES sha256=$ENC_SHA"

# --- optional remote push ---------------------------------------------------

REMOTE="${KAI_BACKUP_RCLONE_REMOTE:-}"
REMOTE_STATUS="skipped"

if [[ -n "$REMOTE" ]]; then
    if ! command -v rclone >/dev/null 2>&1; then
        write_log "WARN: KAI_BACKUP_RCLONE_REMOTE set but rclone not in PATH — keeping local copy only."
        REMOTE_STATUS="rclone_missing"
    else
        write_log "Pushing to $REMOTE"
        if rclone copy --quiet "$ARCHIVE_ENC" "$REMOTE/$DAY/" 2>>"$LOG_FILE"; then
            REMOTE_STATUS="pushed"
            write_log "Push OK: $REMOTE/$DAY/$(basename "$ARCHIVE_ENC")"
        else
            REMOTE_STATUS="push_failed"
            write_log "ERROR: rclone push failed."
            write_audit "fail_push" "$ARCHIVE_NAME" "$ENC_SHA" "$ENC_BYTES" \
                "$REMOTE" "push_failed" "see kai_backup.log"
            exit 5
        fi
    fi
else
    write_log "WARN: KAI_BACKUP_RCLONE_REMOTE unset — local-only mode (configure R2 to make this offsite)."
    REMOTE_STATUS="local_only"
fi

# --- local retention --------------------------------------------------------

# Keep STAGE_DIR/<day>/ for KEEP_DAYS days, prune older folders.
if [[ "$KEEP_DAYS" -gt 0 ]]; then
    find "$STAGE_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" \
        -print -exec rm -rf {} + 2>>"$LOG_FILE" \
        | while read -r purged; do
            write_log "Pruned old stage: $purged"
        done
fi

# --- audit ------------------------------------------------------------------

write_audit "ok" "$ARCHIVE_NAME" "$ENC_SHA" "$ENC_BYTES" \
    "${REMOTE:-(none)}" "$REMOTE_STATUS" "files=${#EXISTING[@]} skipped=${#MISSING[@]}"
write_log "Done: status=ok files=${#EXISTING[@]} remote=$REMOTE_STATUS"
exit 0
