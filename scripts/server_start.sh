#!/usr/bin/env bash
# KAI Pipeline Server — start in background via nohup
# Usage: bash scripts/server_start.sh

set -e
cd "$(dirname "$0")/.."

PID_FILE=".server.pid"
LOG_FILE="logs/server.log"

# Cross-platform PID probe: kill -0 doesn't see native Windows processes
# from MSYS/Git-Bash, so fall back to tasklist on Windows.
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

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if is_pid_running "$OLD_PID"; then
        echo "Server already running (PID $OLD_PID)"
        echo "Use: bash scripts/server_stop.sh"
        exit 1
    fi
    rm -f "$PID_FILE"
fi

# Rotate log if > 10MB
if [ -f "$LOG_FILE" ] && [ $(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0) -gt 10485760 ]; then
    mv "$LOG_FILE" "${LOG_FILE}.$(date +%Y%m%d_%H%M%S).bak"
fi

# LAN binding is opt-in: KAI_BIND_LAN=1 → 0.0.0.0 (reachable in local network),
# otherwise loopback-only (default, safer). Firewall inbound rule for TCP/8000
# must be set separately on Windows (private profile).
BIND_HOST="127.0.0.1"
if [ "${KAI_BIND_LAN:-0}" = "1" ]; then
    BIND_HOST="0.0.0.0"
fi

echo "Starting KAI Pipeline Server (bind=${BIND_HOST})..."
nohup python -m uvicorn app.api.main:app \
    --host "$BIND_HOST" \
    --port 8000 \
    --log-level info \
    > "$LOG_FILE" 2>&1 &
SHELL_PID=$!

# Resolve the real Python PID. Under MSYS/Git-Bash, $! yields the Bash helper
# subshell, not the actual uvicorn worker. We locate the process that owns
# the uvicorn TCP listener on port 8000 via netstat — robust against varying
# python executable names (python.exe, python3.13.exe, py.exe, …).
resolve_python_pid() {
    local fallback=$1
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                local pid
                pid=$(netstat -ano 2>/dev/null \
                    | awk '/LISTEN|ABH/ && $2 ~ /:8000$/ {print $5; exit}')
                if [ -n "$pid" ] && [ "$pid" != "0" ]; then echo "$pid"; return 0; fi
                sleep 1
            done
            echo "$fallback"
            ;;
        *)
            echo "$fallback"
            ;;
    esac
}

REAL_PID=$(resolve_python_pid "$SHELL_PID")
echo "$REAL_PID" > "$PID_FILE"
sleep 2

if is_pid_running "$(cat "$PID_FILE")"; then
    echo "Server started (PID $(cat "$PID_FILE"))"
    echo "Log: $LOG_FILE"
    echo "Health (local):  http://127.0.0.1:8000/health"
    echo "Dashboard:       http://127.0.0.1:8000/dashboard/"
    if [ "$BIND_HOST" = "0.0.0.0" ]; then
        echo "LAN bind active — reachable on TCP/8000 from local network."
        echo "Windows firewall: allow inbound TCP/8000 for 'Private' profile once."
    fi
    curl -s http://127.0.0.1:8000/health 2>/dev/null && echo "" || echo "(health check pending...)"
else
    echo "ERROR: Server failed to start. Check $LOG_FILE"
    tail -20 "$LOG_FILE" 2>/dev/null
    exit 1
fi

# Start agent-worker unless opt-out is set. Keeps Dashboard/Telegram chat
# auto-replies alive across server restarts without a separate action.
if [ "${KAI_AGENT_WORKER:-1}" != "0" ]; then
    bash "$(dirname "$0")/agent_worker_start.sh" || echo "WARN: agent-worker failed to start"
fi
