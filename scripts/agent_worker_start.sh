#!/usr/bin/env bash
# Start the KAI agent conversation worker as a background process.
# Idempotent: re-run is safe; will not spawn a second copy.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PID_FILE="$ROOT/.agent_worker.pid"
LOG_FILE="$ROOT/logs/agent_worker.log"
mkdir -p "$ROOT/logs" "$ROOT/artifacts/agents"

# Windows and Unix need different liveness probes: MSYS/Git-Bash `ps -p`
# cannot see native Windows processes spawned via nohup python.
is_pid_running() {
    local pid=$1
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            tasklist //FO CSV //NH //FI "PID eq $pid" 2>/dev/null | grep -q "\"$pid\""
            ;;
        *)
            kill -0 "$pid" 2>/dev/null
            ;;
    esac
}

if [[ -f "$PID_FILE" ]]; then
    OLD_PID="$(cat "$PID_FILE" 2>/dev/null || echo)"
    if [[ -n "${OLD_PID:-}" ]] && is_pid_running "$OLD_PID"; then
        echo "agent-worker already running (PID $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

export PYTHONIOENCODING=utf-8
nohup python -m app.agents.worker --loop >"$LOG_FILE" 2>&1 &
SHELL_PID=$!

# Resolve the real Python PID. Under MSYS/Git-Bash, $! is the bash helper
# subshell, not the actual python process. We query Win32_Process via
# PowerShell to find the python.exe whose command line matches our worker
# module — language-independent, robust across python.exe / py.exe variants.
resolve_worker_pid() {
    local fallback=$1
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                local pid
                pid=$(powershell -NoProfile -Command \
                    "(Get-CimInstance Win32_Process -Filter \"Name like 'python%' and CommandLine like '%app.agents.worker%'\").ProcessId" \
                    2>/dev/null | tr -d '\r' | grep -E '^[0-9]+$' | head -1)
                if [ -n "$pid" ] && [ "$pid" != "0" ]; then echo "$pid"; return 0; fi
                sleep 1
            done
            echo "$fallback"
            ;;
        *) echo "$fallback" ;;
    esac
}

REAL_PID=$(resolve_worker_pid "$SHELL_PID")
sleep 1
if is_pid_running "$REAL_PID"; then
    echo "$REAL_PID" >"$PID_FILE"
    echo "agent-worker started (PID $REAL_PID) — log: $LOG_FILE"
elif [[ -s "$LOG_FILE" ]] && grep -q "agent-worker starting" "$LOG_FILE"; then
    echo "$REAL_PID" >"$PID_FILE"
    echo "agent-worker started (PID $REAL_PID, log confirms startup) — log: $LOG_FILE"
else
    echo "agent-worker failed to start — check $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi
