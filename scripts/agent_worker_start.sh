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

# Same trick as server_start.sh: under MSYS, $! is the bash helper, not the
# python process. Best-effort resolution via tasklist for `python*` owning
# our state file path isn't trivial, so we just verify the shell helper
# stayed alive briefly and trust nohup.
sleep 1
if is_pid_running "$SHELL_PID"; then
    echo "$SHELL_PID" >"$PID_FILE"
    echo "agent-worker started (PID $SHELL_PID) — log: $LOG_FILE"
elif [[ -s "$LOG_FILE" ]] && grep -q "agent-worker starting" "$LOG_FILE"; then
    # Log confirms startup even if parent PID already exited. Record SHELL_PID
    # anyway so stop script has something to try.
    echo "$SHELL_PID" >"$PID_FILE"
    echo "agent-worker started (parent exited, check log: $LOG_FILE)"
else
    echo "agent-worker failed to start — check $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi
