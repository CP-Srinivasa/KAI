#!/usr/bin/env bash
# KAI Pipeline Server — start in background via nohup
# Usage: bash scripts/server_start.sh

set -e
cd "$(dirname "$0")/.."

PID_FILE=".server.pid"
LOG_FILE="logs/server.log"
TUNNEL_PID_FILE=".tunnel.pid"
TUNNEL_LOG_FILE="logs/tunnel.log"

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

# Bind-host resolution (NEO-P-001 B):
# 1. APP_API_BIND_HOST (new canonical source — also validated by AppSettings)
# 2. KAI_BIND_LAN=1 → 0.0.0.0 (legacy operator override, unchanged behaviour)
# 3. 127.0.0.1 (default, safer — tunnel ingress only)
# In production envs a non-loopback bind is rejected by the AppSettings
# validator unless APP_ALLOW_NON_LOOPBACK_BIND=1 is set.
# Firewall inbound rule for TCP/8000 must be set separately on Windows
# (private profile) when exposing beyond loopback.
BIND_HOST="${APP_API_BIND_HOST:-127.0.0.1}"
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

# Wait for the /health endpoint to respond. Port-listening alone is not a
# readiness signal — uvicorn binds before in-process startup hooks finish,
# and on Windows the tasklist/netstat probes are themselves slow enough that
# a naive retry budget (10×1s) expired before the server was ready, causing
# this script to falsely report failure while uvicorn was actually running.
STARTUP_TIMEOUT=${KAI_STARTUP_TIMEOUT:-60}
HEALTH_OK=0
for _ in $(seq 1 "$STARTUP_TIMEOUT"); do
    if curl -sf -m 2 "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    # Bail early if uvicorn itself died (PID file helper still tracks SHELL_PID
    # at this point, but we can spot a hard crash by watching for the log to
    # stop growing — cheap check: grep for a fatal traceback).
    if grep -qE "Traceback|ERROR:.*failed|address already in use" "$LOG_FILE" 2>/dev/null; then
        break
    fi
    sleep 1
done

# Resolve the real Python PID via the TCP listener. At this point either
# /health responded (server is definitely bound) or we timed out (PID probe
# may still succeed if the process is mid-startup). Single netstat call —
# no retry loop — because readiness is already established above.
# LISTENING sockets have foreign address "0.0.0.0:0" on every Windows locale,
# which sidesteps localized state strings (ABHÖREN/LISTEN).
resolve_python_pid() {
    local fallback=$1
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            local pid
            pid=$(netstat -ano 2>/dev/null \
                | awk '$1=="TCP" && $2 ~ /:8000$/ && $3=="0.0.0.0:0" {print $5; exit}')
            if [ -n "$pid" ] && [ "$pid" != "0" ]; then echo "$pid"; return 0; fi
            echo "$fallback"
            ;;
        *)
            echo "$fallback"
            ;;
    esac
}

REAL_PID=$(resolve_python_pid "$SHELL_PID")
echo "$REAL_PID" > "$PID_FILE"

if [ "$HEALTH_OK" = "1" ] && is_pid_running "$REAL_PID"; then
    echo "Server started (PID $REAL_PID)"
    echo "Log: $LOG_FILE"
    echo "Health (local):  http://127.0.0.1:8000/health"
    echo "Dashboard:       http://127.0.0.1:8000/dashboard/"
    if [ "$BIND_HOST" = "0.0.0.0" ]; then
        echo "LAN bind active — reachable on TCP/8000 from local network."
        echo "Windows firewall: allow inbound TCP/8000 for 'Private' profile once."
    fi
    curl -s http://127.0.0.1:8000/health 2>/dev/null && echo ""
else
    echo "ERROR: Server failed to start (health_ok=$HEALTH_OK, pid=$REAL_PID). Check $LOG_FILE"
    tail -20 "$LOG_FILE" 2>/dev/null
    exit 1
fi

# Start agent-worker unless opt-out is set. Keeps Dashboard/Telegram chat
# auto-replies alive across server restarts without a separate action.
if [ "${KAI_AGENT_WORKER:-1}" != "0" ]; then
    bash "$(dirname "$0")/agent_worker_start.sh" || echo "WARN: agent-worker failed to start"
fi

# Start Telegram Premium-Channel listener unless opt-out is set.
# Previously manual-only — the listener silently died on 2026-04-21 and
# 6 premium signals (3 on 2026-04-23, 3 on 2026-04-24) never entered the
# pipeline. Tying it to server lifecycle closes that gap until the Pi
# migration (2026-05-01) introduces proper service supervision.
if [ "${KAI_TELEGRAM_LISTENER:-1}" != "0" ]; then
    bash "$(dirname "$0")/telegram_listener_start.sh" || echo "WARN: telegram-listener failed to start"
fi

# Start Cloudflare Named Tunnel unless opt-out is set.
# Exposes the local server as https://kai-trader.org via Cloudflare.
if [ "${KAI_TUNNEL:-1}" != "0" ] && command -v cloudflared &>/dev/null; then
    if [ -f "$TUNNEL_PID_FILE" ]; then
        OLD_TPID=$(cat "$TUNNEL_PID_FILE")
        if is_pid_running "$OLD_TPID"; then
            echo "Tunnel already running (PID $OLD_TPID)"
        else
            rm -f "$TUNNEL_PID_FILE"
        fi
    fi

    if [ ! -f "$TUNNEL_PID_FILE" ]; then
        echo "Starting Cloudflare Tunnel (kai-trader.org)..."
        nohup cloudflared tunnel --config "$HOME/.cloudflared/config.yml" run kai \
            > "$TUNNEL_LOG_FILE" 2>&1 &
        TUNNEL_SHELL_PID=$!

        resolve_tunnel_pid() {
            local fallback=$1
            case "$(uname -s)" in
                MINGW*|MSYS*|CYGWIN*)
                    for _ in 1 2 3 4 5; do
                        local pid
                        pid=$(tasklist //FO CSV //NH //FI "IMAGENAME eq cloudflared.exe" 2>/dev/null \
                            | head -1 | awk -F'","' '{gsub(/"/, "", $2); print $2}')
                        if [ -n "$pid" ] && [ "$pid" != "0" ]; then echo "$pid"; return 0; fi
                        sleep 1
                    done
                    echo "$fallback"
                    ;;
                *) echo "$fallback" ;;
            esac
        }

        TUNNEL_REAL_PID=$(resolve_tunnel_pid "$TUNNEL_SHELL_PID")
        echo "$TUNNEL_REAL_PID" > "$TUNNEL_PID_FILE"
        sleep 3
        if is_pid_running "$(cat "$TUNNEL_PID_FILE")"; then
            echo "Tunnel started (PID $(cat "$TUNNEL_PID_FILE"))"
            echo "Public URL: https://kai-trader.org"
        else
            echo "WARN: Tunnel failed to start. Check $TUNNEL_LOG_FILE"
        fi
    fi
else
    if [ "${KAI_TUNNEL:-1}" != "0" ]; then
        echo "WARN: cloudflared not found — tunnel not started"
    fi
fi

# -----------------------------------------------------------------------------
# Cron (Windows Task Scheduler: KAI-PaperTrading)
# Autonomous from this script — runs every 10 min via Windows Task Scheduler,
# has its own server-watchdog. We just make sure it's enabled at start.
# Opt-out: KAI_CRON=0
# -----------------------------------------------------------------------------
CRON_STATUS="not_checked"
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) IS_WIN_CRON=1 ;; *) IS_WIN_CRON=0 ;; esac
if [ "${KAI_CRON:-1}" != "0" ] && [ "$IS_WIN_CRON" = "1" ]; then
    if schtasks //Query //TN "\KAI-PaperTrading" >/dev/null 2>&1; then
        # Enable if disabled (German "Deaktiviert" / English "Disabled").
        TASK_STATE=$(schtasks //Query //TN "\KAI-PaperTrading" //FO LIST //V 2>/dev/null \
            | grep -iE "Status der geplanten|Scheduled Task State" | head -1)
        if echo "$TASK_STATE" | grep -qiE "Deaktiviert|Disabled"; then
            schtasks //Change //TN "\KAI-PaperTrading" //ENABLE >/dev/null 2>&1 \
                && echo "Cron task KAI-PaperTrading re-enabled"
        fi
        CRON_STATUS="enabled"
    else
        echo "WARN: Cron task KAI-PaperTrading not installed."
        echo "      Install: powershell -ExecutionPolicy Bypass -File scripts/paper_trading_cron.ps1 -Install"
        CRON_STATUS="missing"
    fi
fi

# -----------------------------------------------------------------------------
# Final stack health summary — verifies every component is actually up,
# not just that we tried to start it. Reads the live server log to confirm
# in-process schedulers (Telegram poller, RSS, PositionMonitor) initialized.
# -----------------------------------------------------------------------------
sleep 2  # give in-process schedulers time to log their startup events
echo ""
echo "=== KAI Stack Status ==="

# Server
if [ -f "$PID_FILE" ] && is_pid_running "$(cat "$PID_FILE")"; then
    echo "[OK]   Server          PID $(cat "$PID_FILE")  http://127.0.0.1:8000"
else
    echo "[FAIL] Server          (no live PID)"
fi

# In-process components — read from server log
log_has() { grep -q "$1" "$LOG_FILE" 2>/dev/null; }

if log_has '"event": "telegram_poller_start_requested"' && log_has '"polling_enabled": true'; then
    echo "[OK]   Telegram        polling active"
else
    echo "[WARN] Telegram        polling not confirmed in log"
fi

if log_has '"event": "rss_scheduler_started"'; then
    INTERVAL=$(grep -oE '"interval_minutes": [0-9]+' "$LOG_FILE" | tail -1 | grep -oE '[0-9]+')
    echo "[OK]   RSSScheduler    every ${INTERVAL:-?}min"
else
    echo "[WARN] RSSScheduler    not confirmed in log"
fi

if log_has '"event": "position_monitor_scheduler_started"'; then
    P_INTERVAL=$(grep -oE '"interval_seconds": [0-9]+' "$LOG_FILE" | tail -1 | grep -oE '[0-9]+')
    echo "[OK]   PositionMonitor every ${P_INTERVAL:-?}s"
else
    echo "[WARN] PositionMonitor not confirmed in log"
fi

# Agent-Worker
WORKER_PID_FILE=".agent_worker.pid"
if [ -f "$WORKER_PID_FILE" ] && is_pid_running "$(cat "$WORKER_PID_FILE")"; then
    echo "[OK]   Agent-Worker    PID $(cat "$WORKER_PID_FILE")"
else
    echo "[WARN] Agent-Worker    not running"
fi

# Telegram-Listener (MTProto premium-channel ingest)
LISTENER_PID_FILE=".telegram_listener.pid"
if [ -f "$LISTENER_PID_FILE" ] && is_pid_running "$(cat "$LISTENER_PID_FILE")"; then
    echo "[OK]   TG-Listener     PID $(cat "$LISTENER_PID_FILE")"
elif [ "${KAI_TELEGRAM_LISTENER:-1}" = "0" ]; then
    echo "[--]   TG-Listener     opt-out (KAI_TELEGRAM_LISTENER=0)"
else
    echo "[WARN] TG-Listener     not running"
fi

# Tunnel
if [ -f "$TUNNEL_PID_FILE" ] && is_pid_running "$(cat "$TUNNEL_PID_FILE")"; then
    echo "[OK]   Tunnel          PID $(cat "$TUNNEL_PID_FILE")  https://kai-trader.org"
else
    if [ "${KAI_TUNNEL:-1}" != "0" ]; then
        echo "[WARN] Tunnel          not running"
    else
        echo "[--]   Tunnel          opt-out (KAI_TUNNEL=0)"
    fi
fi

# Cron
case "$CRON_STATUS" in
    enabled) echo "[OK]   Cron            KAI-PaperTrading (every 10min)" ;;
    missing) echo "[WARN] Cron            KAI-PaperTrading not installed" ;;
    *)       echo "[--]   Cron            $CRON_STATUS" ;;
esac
echo ""
