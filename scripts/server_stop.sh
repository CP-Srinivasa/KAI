#!/usr/bin/env bash
# KAI Pipeline Server — stop
# Usage: bash scripts/server_stop.sh

cd "$(dirname "$0")/.."

PID_FILE=".server.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found. Server not running (or started manually)."
    # Try to kill by process name as fallback
    pkill -f "uvicorn app.api.main" 2>/dev/null && echo "Killed uvicorn process." || true
    exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    sleep 2
    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" 2>/dev/null
    fi
    echo "Server stopped (PID $PID)"
else
    echo "Server was not running (stale PID $PID)"
fi

rm -f "$PID_FILE"
