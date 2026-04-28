#!/usr/bin/env bash
# KAI Pipeline Server — stop
# Usage:
#   bash scripts/server_stop.sh
#   bash scripts/server_stop.sh --prepare-cutover=kai@pi.local
#   bash scripts/server_stop.sh --prepare-cutover=kai@pi.local --remote-root=/home/kai/kai
#
# --prepare-cutover=<ssh-host> stops the server normally (so SQLite is
# write-quiescent) and then rsync-equivalent-syncs data/dev.db to the Pi
# via scp + sha256 verification. Closes A3/C1 from the 2026-05-01
# Pi-migration memo: previously the operator had to do server_stop +
# sha256sum + scp + remote-sha256 by hand (memo §10 step 3.3a-d) and
# nothing prevented an out-of-order keystroke. The DB transfer is
# implemented inline (single file) instead of delegating to
# pi_transfer_artifacts.sh, so this works today even though that script
# still requires rsync (B4 refactor pending).

cd "$(dirname "$0")/.."

PID_FILE=".server.pid"
DB_PATH="data/dev.db"

# Cutover args (only set when --prepare-cutover= is passed)
CUTOVER_HOST=""
CUTOVER_REMOTE_ROOT="/home/kai/ai_analyst_trading_bot"

for arg in "$@"; do
    case "$arg" in
        --prepare-cutover=*) CUTOVER_HOST="${arg#--prepare-cutover=}" ;;
        --prepare-cutover)
            echo "ERROR: --prepare-cutover requires =<ssh-host>" >&2
            echo "  example: --prepare-cutover=kai@pi.local" >&2
            exit 2
            ;;
        --remote-root=*) CUTOVER_REMOTE_ROOT="${arg#--remote-root=}" ;;
        -h|--help)
            sed -n '1,16p' "$0"
            exit 0
            ;;
        *)
            echo "unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

# Cross-platform PID probe + kill: MSYS/Git-Bash kill can't see native
# Windows processes, so fall back to tasklist/taskkill on Windows.
IS_WINDOWS=0
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
esac

is_pid_running() {
    local pid=$1
    if [ "$IS_WINDOWS" = "1" ]; then
        tasklist //FO CSV //NH //FI "PID eq $pid" 2>/dev/null | grep -q "\"$pid\""
    else
        kill -0 "$pid" 2>/dev/null
    fi
}

stop_pid() {
    # Returns 0 on success, non-zero when the OS refuses the kill
    # (e.g. access-denied because the target runs in another session).
    # Callers MUST verify the process is actually gone afterwards via
    # is_pid_running — a zero return only means the OS accepted the call.
    local pid=$1
    local force=${2:-0}
    if [ "$IS_WINDOWS" = "1" ]; then
        if [ "$force" = "1" ]; then
            taskkill //PID "$pid" //F >/dev/null 2>&1
        else
            taskkill //PID "$pid" >/dev/null 2>&1
        fi
    else
        if [ "$force" = "1" ]; then
            kill -9 "$pid" 2>/dev/null
        else
            kill "$pid" 2>/dev/null
        fi
    fi
    return $?
}

# A3/C1: pre-flight before we touch the server. The cutover invariant is
# "DB is consistent at sync time" — that requires a clean stop AND a
# reachable target. If we discover after stopping the server that ssh is
# broken, we are stuck with a stopped laptop and no Pi-side DB. So the
# probe runs *first*; on failure the server stays up.
if [[ -n "$CUTOVER_HOST" ]]; then
    echo "Cutover requested: $CUTOVER_HOST:$CUTOVER_REMOTE_ROOT/$DB_PATH"
    for tool in scp ssh sha256sum awk; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            echo "ERROR: required tool '$tool' not on PATH" >&2
            echo "  Cutover needs scp + ssh + sha256sum + awk." >&2
            echo "  Aborting BEFORE server stop — server stays up." >&2
            exit 2
        fi
    done
    if [[ ! -f "$DB_PATH" ]]; then
        echo "ERROR: $DB_PATH does not exist on this laptop" >&2
        echo "  Nothing to sync — aborting before server stop." >&2
        exit 2
    fi
    if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$CUTOVER_HOST" "echo cutover_probe_ok" >/dev/null 2>&1; then
        echo "ERROR: ssh probe to $CUTOVER_HOST failed" >&2
        echo "  (ConnectTimeout=10s, BatchMode=yes — passwordless key auth required)" >&2
        echo "  Verify interactively: ssh $CUTOVER_HOST hostname" >&2
        echo "  Aborting BEFORE server stop — server stays up." >&2
        exit 2
    fi
    echo "  pre-flight OK — proceeding with server stop, then DB-sync"
fi

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found. Server not running (or started manually)."
    # Try to kill by process name as fallback (Unix only; Windows equivalent
    # is harder because uvicorn runs inside python.exe).
    if [ "$IS_WINDOWS" = "0" ]; then
        pkill -f "uvicorn app.api.main" 2>/dev/null && echo "Killed uvicorn process." || true
    fi
else
    PID=$(cat "$PID_FILE")
    if is_pid_running "$PID"; then
        stop_pid "$PID" 0 || true   # graceful SIGTERM; ignore exit for now
        sleep 2
        if is_pid_running "$PID"; then
            stop_pid "$PID" 1 || true   # force SIGKILL
            sleep 1
        fi
        # D-185: verify the process is actually gone — taskkill/kill can silently
        # fail with access-denied on cross-session targets, and the old script
        # reported "stopped" anyway. Preserve the PID file so the operator can
        # diagnose; a silent success led to a confused restart cycle.
        if is_pid_running "$PID"; then
            echo "ERROR: Server process $PID still running after SIGTERM+SIGKILL." >&2
            echo "  Likely cause: access-denied (target in a more-privileged session)." >&2
            echo "  Remedy: run 'Stop-Process -Id $PID -Force' from an elevated" >&2
            echo "  PowerShell, then re-run this script or scripts/server_start.sh." >&2
            echo "  PID file left intact for inspection: $PID_FILE" >&2
            if [[ -n "$CUTOVER_HOST" ]]; then
                echo "  Cutover-sync ABORTED — DB is potentially mid-write." >&2
            fi
            exit 1
        fi
        echo "Server stopped (PID $PID)"
    else
        echo "Server was not running (stale PID $PID)"
    fi
    rm -f "$PID_FILE"
fi

# Also stop the agent-worker (idempotent, silent if not running).
if [ -x "$(dirname "$0")/agent_worker_stop.sh" ] || [ -f "$(dirname "$0")/agent_worker_stop.sh" ]; then
    bash "$(dirname "$0")/agent_worker_stop.sh" || true
fi

# Stop the Telegram premium-channel listener (idempotent).
if [ -f "$(dirname "$0")/telegram_listener_stop.sh" ]; then
    bash "$(dirname "$0")/telegram_listener_stop.sh" || true
fi

# Stop Cloudflare Tunnel if running.
TUNNEL_PID_FILE=".tunnel.pid"
if [ -f "$TUNNEL_PID_FILE" ]; then
    TPID=$(cat "$TUNNEL_PID_FILE")
    if is_pid_running "$TPID"; then
        stop_pid "$TPID" 1 || true
        sleep 1
        if is_pid_running "$TPID"; then
            echo "WARNING: Tunnel process $TPID still running after SIGKILL." >&2
            echo "  PID file left intact: $TUNNEL_PID_FILE" >&2
        else
            echo "Tunnel stopped (PID $TPID)"
            rm -f "$TUNNEL_PID_FILE"
        fi
    else
        echo "Tunnel was not running (stale PID $TPID)"
        rm -f "$TUNNEL_PID_FILE"
    fi
fi

# A3/C1: DB-final-sync. Runs ONLY after a confirmed stop reached this
# point (early exit 1 above blocks unsafe syncs). Single-file scp +
# sha256 verification — no rsync dependency, so this works on the
# current laptop where rsync is missing.
if [[ -n "$CUTOVER_HOST" ]]; then
    echo ""
    echo "=== Cutover-Sync (DB final-sync after server stop) ==="

    LOCAL_SHA=$(sha256sum "$DB_PATH" | awk '{print $1}')
    LOCAL_SIZE=$(wc -c < "$DB_PATH")
    echo "  local : $DB_PATH  ($LOCAL_SIZE bytes, sha256=${LOCAL_SHA:0:16}...)"

    REMOTE_DIR="$CUTOVER_REMOTE_ROOT/data"
    REMOTE_DB="$CUTOVER_REMOTE_ROOT/$DB_PATH"

    if ! ssh -o BatchMode=yes "$CUTOVER_HOST" "mkdir -p $REMOTE_DIR" 2>&1; then
        echo "ERROR: remote mkdir failed for $REMOTE_DIR" >&2
        echo "  Server is already stopped — DB stays on the laptop until resolved." >&2
        exit 2
    fi

    if ! scp -o BatchMode=yes "$DB_PATH" "$CUTOVER_HOST:$REMOTE_DB" 2>&1; then
        echo "ERROR: scp transfer failed" >&2
        echo "  Server is already stopped — DB stays on the laptop until resolved." >&2
        exit 2
    fi

    REMOTE_SHA=$(ssh -o BatchMode=yes "$CUTOVER_HOST" "sha256sum $REMOTE_DB 2>/dev/null | awk '{print \$1}'")
    echo "  remote: $CUTOVER_HOST:$REMOTE_DB  (sha256=${REMOTE_SHA:0:16}...)"

    if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
        echo "  sha256 match — DB-final-sync verified"
        echo "Cutover-Sync complete."
        exit 0
    else
        echo "ERROR: sha256 MISMATCH after scp" >&2
        echo "    local : $LOCAL_SHA" >&2
        echo "    remote: $REMOTE_SHA" >&2
        echo "  Investigate transfer corruption; manual re-scp required." >&2
        exit 2
    fi
fi
