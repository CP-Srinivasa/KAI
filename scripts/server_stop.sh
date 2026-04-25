#!/usr/bin/env bash
# KAI Pipeline Server — stop
# Usage: bash scripts/server_stop.sh

cd "$(dirname "$0")/.."

PID_FILE=".server.pid"

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

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found. Server not running (or started manually)."
    # Try to kill by process name as fallback (Unix only; Windows equivalent
    # is harder because uvicorn runs inside python.exe).
    if [ "$IS_WINDOWS" = "0" ]; then
        pkill -f "uvicorn app.api.main" 2>/dev/null && echo "Killed uvicorn process." || true
    fi
    exit 0
fi

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
        exit 1
    fi
    echo "Server stopped (PID $PID)"
else
    echo "Server was not running (stale PID $PID)"
fi

rm -f "$PID_FILE"

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
