#!/usr/bin/env bash
# Start the KAI Telegram Premium-Channel MTProto listener as a background
# process. Idempotent: re-running is safe and will not spawn a duplicate.
#
# Invoked from server_start.sh unless KAI_TELEGRAM_LISTENER=0.
# Required config (read from .env via pydantic-settings):
#   INGESTION_TELEGRAM_CHANNEL_ENABLED=true
#   INGESTION_TELEGRAM_CHANNEL_API_ID / _API_HASH / _TARGET_CHAT_ID
# Session file: artifacts/telegram_channel.session (must be pre-authed once
# via: python -m app.cli.main ingestion telegram-channel setup).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PID_FILE="$ROOT/.telegram_listener.pid"
LOG_FILE="$ROOT/logs/telegram_listener.log"
mkdir -p "$ROOT/logs" "$ROOT/artifacts"

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
        echo "telegram-listener already running (PID $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# Rotate log if > 5MB — MTProto listener is chatty on reconnects.
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    mv "$LOG_FILE" "${LOG_FILE}.$(date +%Y%m%d_%H%M%S).bak"
fi

# Guard: the ingestion config must be enabled, otherwise the CLI exits 2
# and the "failure" pollutes server_start.sh's stack summary. We surface a
# clean info line and skip instead.
ENABLED="$(python -c "from app.core.settings import get_settings; print(get_settings().telegram_channel_ingest.enabled)" 2>/dev/null || echo "False")"
if [[ "$ENABLED" != "True" ]]; then
    echo "telegram-listener skipped (INGESTION_TELEGRAM_CHANNEL_ENABLED=false)"
    exit 0
fi

export PYTHONIOENCODING=utf-8
nohup python -m app.cli.main ingestion telegram-channel run \
    >"$LOG_FILE" 2>&1 &
SHELL_PID=$!

# Under MSYS/Git-Bash, $! is the bash helper subshell, not the python
# process. Resolve the real PID via command-line match — the ingestion
# CLI subcommand string is unique enough to identify our worker.
resolve_listener_pid() {
    local fallback=$1
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                local pid
                pid=$(powershell -NoProfile -Command \
                    "(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | \
                      Where-Object { \$_.CommandLine -match 'telegram-channel.*run' }).ProcessId" \
                    2>/dev/null | tr -d '\r' | grep -E '^[0-9]+$' | head -1)
                if [ -n "$pid" ] && [ "$pid" != "0" ]; then echo "$pid"; return 0; fi
                sleep 1
            done
            echo "$fallback"
            ;;
        *) echo "$fallback" ;;
    esac
}

REAL_PID=$(resolve_listener_pid "$SHELL_PID")
sleep 2  # Telethon connect+auth takes ~1s; give it a moment before we probe.

if is_pid_running "$REAL_PID"; then
    echo "$REAL_PID" >"$PID_FILE"
    echo "telegram-listener started (PID $REAL_PID) — log: $LOG_FILE"
elif [[ -s "$LOG_FILE" ]] && grep -qE "Starting channel listener|client\.start|connected" "$LOG_FILE"; then
    echo "$REAL_PID" >"$PID_FILE"
    echo "telegram-listener started (PID $REAL_PID, log confirms startup)"
else
    echo "ERROR: telegram-listener failed to start — check $LOG_FILE"
    tail -20 "$LOG_FILE" 2>/dev/null || true
    rm -f "$PID_FILE"
    exit 1
fi
