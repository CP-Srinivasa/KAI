#!/usr/bin/env bash
# KAI Pipeline Server — status check
# Usage: bash scripts/server_status.sh

cd "$(dirname "$0")/.."

PID_FILE=".server.pid"

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

echo "=== KAI Pipeline Server Status ==="
echo ""

# Process check
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if is_pid_running "$PID"; then
        echo "Process:  RUNNING (PID $PID)"
    else
        echo "Process:  STOPPED (stale PID $PID)"
    fi
else
    echo "Process:  NOT STARTED"
fi

# Health check
echo -n "Health:   "
HEALTH=$(curl -s --connect-timeout 3 http://127.0.0.1:8000/health 2>/dev/null)
if [ $? -eq 0 ]; then
    echo "$HEALTH"
else
    echo "UNREACHABLE"
fi

# DB stats
echo -n "Sources:  "
python -c "
import sqlite3
conn = sqlite3.connect('data/dev.db')
src = conn.execute(\"SELECT COUNT(*) FROM sources WHERE status='active' AND source_type='rss_feed'\").fetchone()[0]
docs = conn.execute('SELECT COUNT(*) FROM canonical_documents').fetchone()[0]
analyzed = conn.execute('SELECT COUNT(*) FROM canonical_documents WHERE is_analyzed=1').fetchone()[0]
print(f'{src} active RSS feeds | {docs} documents ({analyzed} analyzed)')
conn.close()
" 2>/dev/null || echo "DB unavailable"

# Log tail
echo ""
if [ -f "logs/server.log" ]; then
    echo "=== Last 5 log lines ==="
    tail -5 logs/server.log
fi
