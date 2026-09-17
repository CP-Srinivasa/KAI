# shellcheck shell=bash
# scripts/lib/pi_wait_until.sh — warten, bis ein Check besteht, mit Frist.
# Zum Sourcen gedacht (definiert nur Funktionen, keine Seiteneffekte).
#
# ANLASS 2026-09-17: `pi_release_deploy.sh` schlief nach den Restarts fest 20 s
# und pruefte dann EINMAL. Beim Deploy ff93050c antwortete /health in diesem
# Moment noch nicht -> DEPLOY_NOT_VERIFIED/Exit 1 fuer einen gesunden Deploy;
# bei d27e708d reichte es nur knapp (uptime_s 3,86). Die Startzeit von kai-server
# schwankt mit Importen und Kaltstart -- eine feste Pause ist entweder zu kurz
# oder verschenkt Zeit.
#
#   pi_wait_until <timeout_s> <interval_s> <befehl> [argumente...]
#
# Fuehrt <befehl> sofort und dann alle <interval_s> Sekunden aus, bis er Exit 0
# liefert (-> 0) oder <timeout_s> verstrichen sind (-> 1). Ohne Befehl -> 2.
# Der Befehl sollte still sein; die ausfuehrliche Diagnose macht der Aufrufer
# nach dem Warten genau einmal.

pi_wait_until() {
    local timeout="${1:-}" interval="${2:-}"
    shift 2 2>/dev/null || true
    if [ -z "$timeout" ] || [ -z "$interval" ] || [ "$#" -eq 0 ]; then
        echo "pi_wait_until: <timeout_s> <interval_s> <befehl> erwartet" >&2
        return 2
    fi
    # Millisekunden: ganze Sekunden liessen die Frist bis zu 1 s zu frueh enden.
    local deadline=$(( $(date +%s%3N) + timeout * 1000 ))
    while true; do
        if "$@"; then
            return 0
        fi
        if [ "$(date +%s%3N)" -ge "$deadline" ]; then
            return 1
        fi
        sleep "$interval"
    done
}
