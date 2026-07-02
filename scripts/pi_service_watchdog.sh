#!/usr/bin/env bash
# KAI Pi Service Watchdog
#
# Runs from systemd every few minutes, outside kai-agent-worker. This avoids
# the circular failure mode where the agent-worker is dead and therefore no
# in-process watchdog can report that it is dead.
#
# Required env for Telegram alarms (optional, from .env):
#   ALERT_TELEGRAM_TOKEN, ALERT_TELEGRAM_CHAT_ID

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UNITS_DEFAULT="kai-server kai-agent-worker kai-tg-listener cloudflared"
UNITS=(${KAI_SERVICE_WATCHDOG_UNITS:-$UNITS_DEFAULT})
THROTTLE_SECONDS="${KAI_SERVICE_WATCHDOG_THROTTLE_SECONDS:-3600}"
AUTO_RESTART="${KAI_SERVICE_WATCHDOG_AUTO_RESTART:-1}"
STATE_DIR="${KAI_SERVICE_WATCHDOG_STATE_DIR:-artifacts/pi_service_watchdog}"

# Timer reconcile (defense-in-depth): any *enabled* kai-*.timer that drifts to
# inactive — e.g. a stray `systemctl stop kai-server` cascade, a bad Requires=, a
# manual stop — is restarted here within one watchdog cycle (~5 min) instead of
# staying silently dead until the next reboot. Respects `is-enabled` so deliberately
# disabled timers (e.g. kai-hype-refresh) are left alone. Excluded by default:
#   - fire-once timers (kai-technical-paper-first-fill) stay inactive by design
#     after firing and must never be re-armed;
#   - kai-server-health-watchdog.timer is intentionally lifecycle-coupled to
#     kai-server — force-resurrecting it during a deliberate server stop would let
#     it restart the server mid-maintenance (3-failure hysteresis), fighting the
#     operator. Its own recovery is by design, not via this generic reconciler.
RECONCILE_TIMERS="${KAI_WATCHDOG_RECONCILE_TIMERS:-1}"
TIMER_EXCLUDE="${KAI_WATCHDOG_TIMER_EXCLUDE:-kai-technical-paper-first-fill.timer kai-server-health-watchdog.timer}"

mkdir -p "$STATE_DIR"

ALARMS=()
NOTES=()
NOW_EPOCH="$(date -u +%s)"
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || echo pi)"
DATE_NOW="$(date -u +%Y-%m-%dT%H:%MZ)"

sanitize_unit_name() {
    echo "$1" | tr -c 'A-Za-z0-9_.@-' '_'
}

should_notify() {
    local unit="$1"
    local marker="${STATE_DIR}/$(sanitize_unit_name "$unit").last_alert"
    local last="0"
    if [[ -f "$marker" ]]; then
        last="$(cat "$marker" 2>/dev/null || echo 0)"
    fi
    if ! [[ "$last" =~ ^[0-9]+$ ]]; then
        last="0"
    fi
    if (( NOW_EPOCH - last >= THROTTLE_SECONDS )); then
        echo "$NOW_EPOCH" > "$marker"
        return 0
    fi
    return 1
}

send_telegram() {
    local msg="$1"
    if [[ -n "${ALERT_TELEGRAM_TOKEN:-}" && -n "${ALERT_TELEGRAM_CHAT_ID:-}" ]]; then
        curl -s --max-time 10 \
            -X POST "https://api.telegram.org/bot${ALERT_TELEGRAM_TOKEN}/sendMessage" \
            -d "chat_id=${ALERT_TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=${msg}" \
            >/dev/null 2>&1 || echo "WARN: Telegram-send failed; falling back to stdout" >&2
    fi
}

for unit in "${UNITS[@]}"; do
    state="$(systemctl is-active "$unit" 2>&1 || true)"
    # Transient states during a normal restart (deploy / health-watchdog /
    # reload) are NOT a failure — racing them produced noisy
    # "kai-server=deactivating; restart=start_ok" alarms and a redundant restart.
    # Re-check once after a short settle before declaring the unit down.
    if [[ "$state" == "activating" || "$state" == "deactivating" || "$state" == "reloading" ]]; then
        sleep "${KAI_SERVICE_WATCHDOG_TRANSIENT_SETTLE_SEC:-3}"
        state="$(systemctl is-active "$unit" 2>&1 || true)"
    fi
    if [[ "$state" == "active" ]]; then
        NOTES+=("${unit}=active")
        rm -f "${STATE_DIR}/$(sanitize_unit_name "$unit").last_alert" 2>/dev/null || true
        continue
    fi

    restart_result="not_attempted"
    if [[ "$AUTO_RESTART" == "1" ]]; then
        if systemctl start "$unit" >/dev/null 2>&1; then
            sleep 2
            new_state="$(systemctl is-active "$unit" 2>&1 || true)"
            restart_result="start_ok:${new_state}"
        else
            restart_result="start_failed"
        fi
    fi

    ALARMS+=("[svc] ${unit}=${state}; restart=${restart_result}")
done

if [[ "$RECONCILE_TIMERS" == "1" ]]; then
    while read -r timer _; do
        [[ "$timer" == kai-*.timer ]] || continue
        case " $TIMER_EXCLUDE " in *" $timer "*) continue ;; esac
        tstate="$(systemctl is-active "$timer" 2>&1 || true)"
        if [[ "$tstate" == "active" ]]; then
            rm -f "${STATE_DIR}/$(sanitize_unit_name "$timer").last_alert" 2>/dev/null || true
            continue
        fi
        restart_result="not_attempted"
        if [[ "$AUTO_RESTART" == "1" ]]; then
            if systemctl start "$timer" >/dev/null 2>&1; then
                sleep 1
                restart_result="start_ok:$(systemctl is-active "$timer" 2>&1 || true)"
            else
                restart_result="start_failed"
            fi
        fi
        ALARMS+=("[timer] ${timer}=${tstate}; restart=${restart_result}")
    done < <(systemctl list-unit-files 'kai-*.timer' --state=enabled --no-legend --no-pager 2>/dev/null)
fi

if (( ${#ALARMS[@]} == 0 )); then
    echo "KAI service-watchdog: OK ($(IFS=' | '; echo "${NOTES[*]}")) @ ${HOSTNAME_SHORT} ${DATE_NOW}"
    exit 0
fi

MSG="KAI service-watchdog: ${#ALARMS[@]} alarm(s) @ ${HOSTNAME_SHORT} ${DATE_NOW}"$'\n\n'
for alarm in "${ALARMS[@]}"; do
    MSG+="- ${alarm}"$'\n'
done
MSG+=$'\n'"Next: journalctl -u kai-agent-worker -u kai-tg-listener --since '2026-05-02 20:00' --no-pager"

SEND_MSG=""
for alarm in "${ALARMS[@]}"; do
    unit="${alarm#\[*\] }"
    unit="${unit%%=*}"
    if should_notify "$unit"; then
        SEND_MSG="$MSG"
    fi
done

if [[ -n "$SEND_MSG" ]]; then
    send_telegram "$SEND_MSG"
fi

echo "$MSG"
exit 0
