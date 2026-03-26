#!/usr/bin/env bash
# KAI Pipeline Server — start in background via nohup
# Usage: bash scripts/server_start.sh

set -e
cd "$(dirname "$0")/.."

PID_FILE=".server.pid"
LOG_FILE="logs/server.log"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
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

echo "Starting KAI Pipeline Server..."
nohup python -m uvicorn app.api.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --log-level info \
    > "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
sleep 3

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Server started (PID $(cat "$PID_FILE"))"
    echo "Log: $LOG_FILE"
    echo "Health: http://127.0.0.1:8000/health"
    # Quick health check
    curl -s http://127.0.0.1:8000/health 2>/dev/null && echo "" || echo "(health check pending...)"
else
    echo "ERROR: Server failed to start. Check $LOG_FILE"
    cat "$LOG_FILE" | tail -20
    exit 1
fi
