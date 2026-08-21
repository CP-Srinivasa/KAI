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
# Manifest der archivierten Dateien: {"pfad": "sha256", ...}. Wird nach dem
# Packen aus dem ARCHIV berechnet (nicht aus den Live-Dateien) und hier
# eingehaengt, damit die acht bestehenden write_audit-Aufrufe unveraendert
# bleiben. Leer, solange kein Archiv existiert.
FILE_SHA_JSON=""

write_audit() {
    local status="$1" archive="$2" sha256="$3" bytes="$4" \
          remote="$5" remote_status="$6" note="$7"
    local ts manifest=""
    ts=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
    [[ -n "$FILE_SHA_JSON" ]] && manifest=",\"file_sha256\":$FILE_SHA_JSON"
    printf '{"ts":"%s","status":"%s","archive":"%s","sha256":"%s","bytes":%s,"remote":"%s","remote_status":"%s","note":"%s"%s}\n' \
        "$ts" "$status" "$archive" "$sha256" "$bytes" "$remote" "$remote_status" "$note" "$manifest" \
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
    # --- Wahrheits-Schicht: buchstaeblich unersetzbar ------------------------
    # Diese Dateien SIND das Produkt. Ein OTS-Anker beweist einen Hash zu einem
    # Inhalt — geht der Inhalt verloren, beweist der Anker nichts mehr. Sie
    # fehlten in dieser Liste, obwohl die Ueberschrift genau sie meint (Audit
    # 09.08.): der Prae-Reg-Ledger traegt die versiegelten Kriterien samt Hash,
    # das Attestierungs-Ledger die Kette darueber, der Hypothesen-Ledger den
    # Trial-Count, an dem die DSR-Deflation haengt.
    "artifacts/research/prereg_ledger.jsonl"
    "artifacts/truth/attestation_ledger.jsonl"
    "artifacts/research/hypothesis_ledger.jsonl"
    "artifacts/research/falsification_verdicts.jsonl"
    "artifacts/research/ln_reconciliation_verdict.jsonl"
    # Auswertungsregeln: vor den Daten fixiert, danach nicht rekonstruierbar.
    "artifacts/research/c1_evaluation_rule_20260802.json"
    "artifacts/research/analyst_probe_evaluation_rule_20260805.json"
    # --- Evidenz-Stroeme ----------------------------------------------------
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

# Verzeichnisse, die als Ganzes mitmuessen (versiegelte Prognosen wachsen um
# Dateien, nicht um Zeilen — eine feste Namensliste wuerde neue verpassen).
DEFAULT_SOURCE_DIRS=(
    "artifacts/research/forecaster_panel"
    # Laufzeit-Zustand der Praeregistrierung: Activation, Checkpoint-Journal,
    # Verdikt-Journal und die eingefrorenen Datenschnitte. Bewusst NICHT in Git
    # (ein T1-Ereignis darf keinen Commit brauchen, an dem wiederum
    # research_code_sha haengt) — damit ist das Backup der EINZIGE Rueckweg.
    # Geht es verloren, ist nicht das Ergebnis weg, sondern der Beweis, unter
    # welchen Daten es entstand.
    "artifacts/research/prereg"
)

# Dateien, deren Fehlen ein FEHLER ist, kein Hinweis. Fuer die Evidenz-Stroeme
# ist "noch nicht da" ein normaler Zustand; fuer die Wahrheits-Schicht heisst
# es, dass entweder der Pfad falsch ist oder etwas geloescht wurde — beides
# darf nicht in einem gruenen Backup enden.
REQUIRED_SOURCES=(
    "artifacts/research/prereg_ledger.jsonl"
    "artifacts/truth/attestation_ledger.jsonl"
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

# Verzeichnisse als Ganzes (tar nimmt sie rekursiv).
for d in "${DEFAULT_SOURCE_DIRS[@]}"; do
    if [[ -d "$ROOT/$d" ]]; then
        EXISTING+=( "$d" )
    else
        MISSING+=( "$d" )
    fi
done

if [[ ${#EXISTING[@]} -eq 0 ]]; then
    write_log "ERROR: zero source files exist — refusing empty backup. ROOT=$ROOT"
    write_audit "fail_no_sources" "" "" 0 "" "" "no source files present"
    exit 3
fi

# Fail-closed auf die Wahrheits-Schicht: ein Backup ohne Prae-Reg- oder
# Attestierungs-Ledger ist kein Backup, sondern eine Illusion davon. Lieber
# hart abbrechen und rot faerben, als ein gruenes Archiv ohne das Produkt.
ABSENT_REQUIRED=()
for f in "${REQUIRED_SOURCES[@]}"; do
    [[ -f "$ROOT/$f" ]] || ABSENT_REQUIRED+=( "$f" )
done
if [[ ${#ABSENT_REQUIRED[@]} -gt 0 ]]; then
    write_log "ERROR: required truth-layer source(s) missing: ${ABSENT_REQUIRED[*]}"
    write_audit "fail_missing_required" "" "" 0 "" "" "${ABSENT_REQUIRED[*]}"
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

# --- Manifest: was liegt WIRKLICH im Archiv? --------------------------------
#
# WARUM aus dem Archiv und nicht aus den Quelldateien (2026-08-27, live
# gefunden): Der erste echte Restore-Drill auf der Pi meldete
# "content mismatch" fuer trading_loop_audit.jsonl. Das Backup war
# einwandfrei — der archivierte Inhalt war bit-genau ein PRAEFIX der
# Live-Datei — nur hatte der Trading-Loop zwischen Backup (08:37) und Drill
# (08:41) 5.044 Bytes angehaengt. Der Drill verglich gegen die LEBENDE Datei,
# also gegen ein bewegliches Ziel, und waere ab dem ersten Timer-Lauf jeden
# Monat rot geworden. Ein Waechter, der immer schlaegt, wird abgeschaltet.
#
# Deshalb haelt das Manifest den Zustand fest, der eingepackt WURDE. Der Drill
# prueft dagegen und beantwortet damit die einzige Frage, die ein Restore-Drill
# stellen muss: laesst sich aus DIESEM Archiv wiederherstellen?
#
# Der Umweg ueber das Entpacken ist Absicht: Hashes vor dem tar zu bilden
# haette dieselbe Race nur verschoben. Nebenbei beweist der Durchgang, dass
# das Archiv ueberhaupt lesbar ist — ein Backup, das sich nicht entpacken
# laesst, ist keines.
MANIFEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kai-backup-manifest.XXXXXX")" || MANIFEST_DIR=""
if [[ -n "$MANIFEST_DIR" ]] && tar -xzf "$ARCHIVE_TMP" -C "$MANIFEST_DIR" 2>>"$LOG_FILE"; then
    FILE_SHA_JSON="$(
        cd "$MANIFEST_DIR" && find . -type f -print0 \
            | sort -z \
            | while IFS= read -r -d '' entry; do
                  rel="${entry#./}"
                  printf '%s\t%s\n' "$rel" "$(sha256_of "$entry")"
              done \
            | awk -F'\t' 'BEGIN{printf "{"} {printf "%s\"%s\":\"%s\"", (NR>1?",":""), $1, $2} END{printf "}"}'
    )"
    write_log "Manifest: $(printf '%s' "$FILE_SHA_JSON" | grep -o '":"' | wc -l | tr -d ' ') file hash(es) recorded from the archive"
    printf '%s\n' "$FILE_SHA_JSON" > "${ARCHIVE_ENC}.manifest.json"
else
    # Fail-soft, aber sichtbar: ohne Manifest faellt der Drill auf den
    # Live-Vergleich zurueck und wird dadurch unzuverlaessig.
    write_log "WARN: manifest could not be computed — the restore drill will fall back to comparing against live files."
fi
[[ -n "$MANIFEST_DIR" ]] && rm -rf "$MANIFEST_DIR"

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
