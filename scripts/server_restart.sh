#!/usr/bin/env bash
# KAI Pipeline Server — full restart
# Stops everything (server + agent-worker + tunnel), then starts the full
# stack (server + telegram-poller + RSSScheduler + PositionMonitorScheduler
# in-process, plus agent-worker + cloudflared tunnel + cron status check).
#
# Usage: bash scripts/server_restart.sh
#
# Env opt-outs (forwarded to server_start.sh):
#   KAI_AGENT_WORKER=0   skip agent-worker
#   KAI_TUNNEL=0         skip cloudflared tunnel
#   KAI_CRON=0           skip cron status check
#   KAI_BIND_LAN=1       bind 0.0.0.0 instead of 127.0.0.1

set -e
SCRIPT_DIR="$(dirname "$0")"

echo "=== KAI Restart: stopping ==="
bash "$SCRIPT_DIR/server_stop.sh" || true

# Brief pause to let OS release the TCP port and child processes settle.
sleep 2

echo ""
echo "=== KAI Restart: starting ==="
bash "$SCRIPT_DIR/server_start.sh"
