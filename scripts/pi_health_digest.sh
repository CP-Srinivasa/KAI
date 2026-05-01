#!/usr/bin/env bash
# KAI Pi Post-Cutover Health Digest
# D-208 — one-shot health probe scheduled for 7 days after Pi cutover
# (2026-05-01 → 2026-05-08). Catches silent regressions in the first
# Pi-Live week without daily manual checks.
#
# Runs via kai-pi-health.timer (one-shot OnCalendar). Always exits 0
# so systemd doesn't double-alarm. Posts result to ALERT_TELEGRAM_CHAT_ID:
#   - OK case   : single line "Pi-health 7d: OK (...)"
#   - Alarm case: multi-line digest with which check tripped + remedy hints
#
# Required env (from .env via EnvironmentFile= in service unit):
#   ALERT_TELEGRAM_TOKEN, ALERT_TELEGRAM_CHAT_ID

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UNITS=(kai-server kai-agent-worker kai-tg-listener cloudflared)
WINDOW_DAYS=7
SIGNAL_FLOOR=5

ALARMS=()
NOTES=()

# 1. Service-Aktivität (alle 4 müssen `active` sein)
for u in "${UNITS[@]}"; do
    state="$(systemctl is-active "$u" 2>&1 || true)"
    if [[ "$state" != "active" ]]; then
        ALARMS+=("[svc] $u = $state (erwartet: active)")
    fi
done

# 2. Error-Priority Logs in 7d (per Service zählen)
ERR_TOTAL=0
ERR_DETAIL=""
for u in "${UNITS[@]}"; do
    n="$(journalctl -u "$u" --priority=err --since="-${WINDOW_DAYS} days" --no-pager 2>/dev/null | grep -cE '^\w' || echo 0)"
    ERR_TOTAL=$((ERR_TOTAL + n))
    if (( n > 0 )); then
        ERR_DETAIL+="${u}=${n} "
    fi
done
if (( ERR_TOTAL > 0 )); then
    ALARMS+=("[log] err-priority in 7d: ${ERR_DETAIL}(total=${ERR_TOTAL}) — siehe \`journalctl -u <unit> --priority=err --since=-7d\`")
fi

# 3. /health intern
HEALTH_HTTP="$(curl -s -o /tmp/.kai_health -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/health 2>&1 || echo "000")"
HEALTH_BODY="$(cat /tmp/.kai_health 2>/dev/null || echo '')"
rm -f /tmp/.kai_health
if [[ "$HEALTH_HTTP" != "200" ]]; then
    ALARMS+=("[api] /health = HTTP ${HEALTH_HTTP} (Body: ${HEALTH_BODY:0:80})")
fi

# 4. Tunnel-Connector-Liste — nur Pi-Connector erwartet
if command -v cloudflared >/dev/null 2>&1; then
    CONN_INFO="$(/usr/local/bin/cloudflared tunnel info kai 2>&1 || true)"
    NON_PI_CONNECTORS="$(echo "$CONN_INFO" | grep -E 'windows|darwin' | wc -l)"
    if (( NON_PI_CONNECTORS > 0 )); then
        ALARMS+=("[tun] ${NON_PI_CONNECTORS} non-Pi connectors noch registriert — \`cloudflared tunnel cleanup kai\` ausführen")
    fi
fi

# 5. TG-Premium-Channel Signale in 7d (Floor: SIGNAL_FLOOR)
SIGNAL_COUNT=0
RAW_LOG="artifacts/telegram_channel_raw.jsonl"
if [[ -f "$RAW_LOG" ]]; then
    SIGNAL_COUNT="$(python3 - <<PYEOF
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

cutoff = datetime.now(tz=timezone.utc) - timedelta(days=${WINDOW_DAYS})
n = 0
for line in Path("${RAW_LOG}").read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        ts = datetime.fromisoformat(json.loads(line)["timestamp_utc"])
        if ts >= cutoff:
            n += 1
    except (KeyError, ValueError, json.JSONDecodeError):
        continue
print(n)
PYEOF
)"
fi
if (( SIGNAL_COUNT < SIGNAL_FLOOR )); then
    ALARMS+=("[tg] nur ${SIGNAL_COUNT} TG-Signale in ${WINDOW_DAYS}d (Floor=${SIGNAL_FLOOR}) — Listener tot oder Channel still")
fi

NOTES+=("Signale 7d: ${SIGNAL_COUNT}")
NOTES+=("Err-Logs 7d: ${ERR_TOTAL}")
NOTES+=("Tunnel-Connectoren ok: $((NON_PI_CONNECTORS == 0 ? 1 : 0))")

# Telegram-Send (immer — OK kurz, Alarm ausführlich)
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || echo pi)"
DATE_NOW="$(date -u +%Y-%m-%dT%H:%MZ)"

if (( ${#ALARMS[@]} == 0 )); then
    MSG="✓ Pi-health 7d: OK ($(IFS=' | '; echo "${NOTES[*]}")) @ ${HOSTNAME_SHORT} ${DATE_NOW}"
else
    MSG="⚠ Pi-health 7d: ${#ALARMS[@]} ALARM(E) @ ${HOSTNAME_SHORT} ${DATE_NOW}"$'\n\n'
    for a in "${ALARMS[@]}"; do
        MSG+="• ${a}"$'\n'
    done
    MSG+=$'\n'"Kontext: $(IFS=' | '; echo "${NOTES[*]}")"
fi

if [[ -n "${ALERT_TELEGRAM_TOKEN:-}" && -n "${ALERT_TELEGRAM_CHAT_ID:-}" ]]; then
    curl -s --max-time 10 \
        -X POST "https://api.telegram.org/bot${ALERT_TELEGRAM_TOKEN}/sendMessage" \
        -d "chat_id=${ALERT_TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${MSG}" \
        >/dev/null 2>&1 || echo "WARN: Telegram-send fehlgeschlagen — Fallback stdout:" >&2
fi

# Immer auch zu stdout (journalctl) — nützlich falls TG ausfällt
echo "${MSG}"
exit 0
