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

# Units, die der Broker NICHT starten kann und die deshalb ausschliesslich
# ueberwacht werden (Contract 2026-08-20). Der Broker verankert
# `^kai-...\.service$` und verlangt ein nichtleeres `User=`.
#
# `cloudflared` erfuellt das Praefix nicht — und `User=ubuntu` allein hiesse
# ohnehin nicht "sicher passwortfrei startbar": die Unit traegt ein
# `ExecStartPre=+`, und das `+` laesst diesen Schritt mit erhoehten
# systemd-Rechten laufen. Sie in die Broker-Ausnahme zu heben, waere also
# ein neuer Root-Pfad. Sie hat ohnehin `Restart=always` und heilt sich selbst;
# was fehlt, ist nur die Sichtbarkeit — und die liefert der Alarm.
#
# Wollen wir spaeter passwortfreie Recovery dafuer, wird ZUERST das
# privilegierte ExecStartPre=+ herausgezogen (Log-Verzeichnis via tmpfiles),
# und DANN cloudflared.service als exakte Broker-Ausnahme evaluiert. Nicht
# andersherum.
ALERT_ONLY_UNITS="${KAI_SERVICE_WATCHDOG_ALERT_ONLY:-cloudflared}"
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

# Timer-Reconcile ist ALERT-ONLY (Contract 2026-08-20). Der Broker akzeptiert
# ausschliesslich `.service`-Units; jeder Timer-Start ueber ihn wird
# abgewiesen. Das aufzuloesen, indem der Broker Timer akzeptiert, hiesse
# einen Root-Pfad fuer eine Unit-Klasse zu oeffnen, die dafuer nie geprueft
# wurde — der Gewinn waere gering, das Risiko neu.
#
# Erkennen genuegt hier: seit #738 findet der Scheduleability-Waechter auch
# den Fall, den dieser Reconcile gar nicht sah (aktiv, aber ohne Termin).
TIMER_RECONCILE_ALERT_ONLY="${KAI_WATCHDOG_TIMER_ALERT_ONLY:-1}"
TIMER_EXCLUDE="${KAI_WATCHDOG_TIMER_EXCLUDE:-kai-technical-paper-first-fill.timer kai-server-health-watchdog.timer}"

# Failed-units-Sweep (Voll-Audit 2026-08-06, Befund P0-2): ein .timer bleibt
# "active", auch wenn seine oneshot-.service bei JEDEM Feuern scheitert — die
# is-active-Probes oben sehen das nie. Mehrere Units dokumentieren ihren
# Non-Zero-Exit ausdruecklich als Tripwire, der "im failed-units-Smoke
# auffaellt" (kai-canonical-edge-attest, kai-ln-scb-monitor,
# kai-min-turnover-calibration, recalc_cycle) — dieser Sweep ist der bislang
# fehlende Konsument. BEWUSST ohne Auto-Restart/reset-failed: ein failed
# oneshot ist ein Befund, kein Reparaturkandidat; Tripwires muessen stehen
# bleiben, bis jemand hinschaut.
FAILED_SWEEP="${KAI_WATCHDOG_FAILED_SWEEP:-1}"
FAILED_EXCLUDE="${KAI_WATCHDOG_FAILED_EXCLUDE:-}"

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

# Nur die MUTIERENDEN systemctl-Aufrufe brauchen Rechte; is-active/list-* sind
# lesend. Die Unit lief bisher ohne `User=`, also als root, und fuehrte dabei
# dieses Skript aus — das fuer den unprivilegierten Service-User schreibbar ist
# (Audit 2026-08-09). Wer `ubuntu` kompromittiert, haette beim naechsten Tick
# root gehabt. Jetzt laeuft die Unit als `ubuntu` und hebt sich punktuell per
# NOPASSWD-sudo auf den Broker /usr/local/sbin/kai-service-control, statt
# durchgehend root zu sein. Der Broker (nicht sudoers) validiert Verb + Unit:
# ein sudoers-Argument-Glob wie `systemctl start kai-*` matcht auch
# Mehrfach-Argumente und war deshalb umgehbar (P0, 2026-08-19).
systemctl_start() {
    if [[ "$(id -u)" == "0" ]]; then
        systemctl start "$1" >/dev/null 2>&1
    else
        sudo -n /usr/local/sbin/kai-service-control start "$1" >/dev/null 2>&1
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
    case " $ALERT_ONLY_UNITS " in
        *" $unit "*)
            # Kein Broker-Versuch: er wuerde abgewiesen und `start_failed`
            # melden — ein Fehlschlag, der wie ein Defekt aussieht, obwohl die
            # Operation nie erlaubt war. Ehrlicher ist der explizite Zustand.
            restart_result="alert_only"
            ;;
        *)
    if [[ "$AUTO_RESTART" == "1" ]]; then
        if systemctl_start "$unit"; then
            sleep 2
            new_state="$(systemctl is-active "$unit" 2>&1 || true)"
            restart_result="start_ok:${new_state}"
        else
            restart_result="start_failed"
        fi
    fi
            ;;
    esac

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
        if [[ "$TIMER_RECONCILE_ALERT_ONLY" == "1" ]]; then
            restart_result="alert_only"
        elif [[ "$AUTO_RESTART" == "1" ]]; then
            if systemctl_start "$timer"; then
                sleep 1
                restart_result="start_ok:$(systemctl is-active "$timer" 2>&1 || true)"
            else
                restart_result="start_failed"
            fi
        fi
        ALARMS+=("[timer] ${timer}=${tstate}; restart=${restart_result}")
    done < <(systemctl list-unit-files 'kai-*.timer' --state=enabled --no-legend --no-pager 2>/dev/null)
fi

if [[ "$FAILED_SWEEP" == "1" ]]; then
    while read -r funit _; do
        [[ -n "$funit" ]] || continue
        case " $FAILED_EXCLUDE " in *" $funit "*) continue ;; esac
        ALARMS+=("[failed] ${funit}=failed; restart=not_attempted")
    done < <(systemctl list-units --state=failed --plain --no-legend --no-pager 2>/dev/null | awk '{print $1}')
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
