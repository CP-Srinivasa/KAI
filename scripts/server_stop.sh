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
    stop_pid "$PID" 0
    sleep 2
    if is_pid_running "$PID"; then
        stop_pid "$PID" 1
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
