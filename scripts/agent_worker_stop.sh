#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT/.agent_worker.pid"

IS_WINDOWS=0
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
esac

is_pid_running() {
    local pid=$1
    if [[ "$IS_WINDOWS" = "1" ]]; then
        tasklist //FO CSV //NH //FI "PID eq $pid" 2>/dev/null | grep -q "\"$pid\""
    else
        kill -0 "$pid" 2>/dev/null
    fi
}

stop_pid() {
    local pid=$1
    local force=${2:-0}
    if [[ "$IS_WINDOWS" = "1" ]]; then
        if [[ "$force" = "1" ]]; then
            taskkill //PID "$pid" //F >/dev/null 2>&1
        else
            taskkill //PID "$pid" >/dev/null 2>&1
        fi
    else
        if [[ "$force" = "1" ]]; then
            kill -9 "$pid" 2>/dev/null || true
        else
            kill "$pid" 2>/dev/null || true
        fi
    fi
}

# Also sweep any stray python processes running the worker module — the
# parent PID in our file may be the bash helper that already exited under
# MSYS, while the actual python worker is still alive.
sweep_stray_workers() {
    if [[ "$IS_WINDOWS" = "1" ]]; then
        # wmic CLI is deprecated; use PowerShell for reliable command-line inspection.
        powershell.exe -NoProfile -Command \
            "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | \
             Where-Object { \$_.CommandLine -match 'app\.agents\.worker' } | \
             ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" \
            2>/dev/null || true
    else
        pkill -f "app.agents.worker" 2>/dev/null || true
    fi
}

if [[ ! -f "$PID_FILE" ]]; then
    echo "agent-worker not running (no PID file)"
    sweep_stray_workers
    exit 0
fi

PID="$(cat "$PID_FILE")"
if is_pid_running "$PID"; then
    stop_pid "$PID" 0
    sleep 1
    if is_pid_running "$PID"; then
        stop_pid "$PID" 1
    fi
    echo "agent-worker stopped (PID $PID)"
else
    echo "agent-worker not running (stale PID $PID)"
fi
sweep_stray_workers
rm -f "$PID_FILE"
