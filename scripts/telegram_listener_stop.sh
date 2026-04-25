#!/usr/bin/env bash
# Stop the KAI Telegram Premium-Channel MTProto listener. Idempotent.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT/.telegram_listener.pid"

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

sweep_stray_listeners() {
    if [[ "$IS_WINDOWS" = "1" ]]; then
        powershell.exe -NoProfile -Command \
            "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | \
             Where-Object { \$_.CommandLine -match 'telegram-channel.*run' } | \
             ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" \
            2>/dev/null || true
    else
        pkill -f "telegram-channel.*run" 2>/dev/null || true
    fi
}

if [[ ! -f "$PID_FILE" ]]; then
    echo "telegram-listener not running (no PID file)"
    sweep_stray_listeners
    exit 0
fi

PID="$(cat "$PID_FILE")"
if is_pid_running "$PID"; then
    stop_pid "$PID" 0
    sleep 1
    if is_pid_running "$PID"; then
        stop_pid "$PID" 1
    fi
    echo "telegram-listener stopped (PID $PID)"
else
    echo "telegram-listener not running (stale PID $PID)"
fi
sweep_stray_listeners
rm -f "$PID_FILE"
