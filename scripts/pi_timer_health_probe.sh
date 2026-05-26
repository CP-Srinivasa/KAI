#!/usr/bin/env bash
# KAI systemd Timer Health Probe (DS-V-C 2026-05-21)
#
# Listet alle kai-*.timer Units und prüft auf inaktive/tote Timer.
# Schreibt JSONL-Audits und alarmiert den Operator via Telegram.
#
# Konfiguration aus .env wird automatisch geladen.

set -uo pipefail

# Global variable declarations to satisfy git-bash set -u parse-time checks
ALERT_TELEGRAM_TOKEN="${ALERT_TELEGRAM_TOKEN:-}"
ALERT_TELEGRAM_CHAT_ID="${ALERT_TELEGRAM_CHAT_ID:-}"
OPERATOR_TELEGRAM_BOT_TOKEN="${OPERATOR_TELEGRAM_BOT_TOKEN:-}"
OPERATOR_ADMIN_CHAT_IDS="${OPERATOR_ADMIN_CHAT_IDS:-}"
KAI_TIMER_PROBE_TIMERS="${KAI_TIMER_PROBE_TIMERS:-}"
KAI_TIMER_PROBE_TEST_STATES="${KAI_TIMER_PROBE_TEST_STATES:-}"
KAI_TIMER_PROBE_AUDIT_FILE="${KAI_TIMER_PROBE_AUDIT_FILE:-}"
KAI_TIMER_PROBE_IGNORE_DOTENV="${KAI_TIMER_PROBE_IGNORE_DOTENV:-}"
KAI_TIMER_PROBE_DRY_RUN="${KAI_TIMER_PROBE_DRY_RUN:-}"

line=""
trimmed=""
key=""
val=""
pattern=""
tmp=""
state=""
ts=""
t=""
AUDIT_FILE=""
ROOT=""
TELEGRAM_TOKEN=""
TELEGRAM_CHAT_ID=""
DRY_RUN=""
TIMERS=()
NON_ACTIVE=()
FINDINGS_JSON=""
DECISION_JSON=""
SHOULD_ALERT=""
DECISION_REASON=""
ALERT_TS=""
HOSTNAME_SHORT=""
MSG=""






# 1. Pfad-Ermittlung & Setup
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "${ROOT}/artifacts"
AUDIT_FILE="${KAI_TIMER_PROBE_AUDIT_FILE:-${ROOT}/artifacts/timer_health_audit.jsonl}"

# 2. Laden der Umgebungsvariablen (.env laden falls vorhanden und nicht im Test)
if [[ -f "${ROOT}/.env" && "${KAI_TIMER_PROBE_IGNORE_DOTENV:-0}" != "1" ]]; then
    # Filter comments and empty lines
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Strip leading whitespace
        trimmed="${line#"${line%%[![:space:]]*}"}"
        # Skip comments and empty lines
        [[ -z "$trimmed" || "$trimmed" == "#"* ]] && continue
        
        # Export keys using POSIX parameter expansion
        if [[ "$trimmed" == *"="* ]]; then
            key="${trimmed%%=*}"
            val="${trimmed#*=}"
            # Strip quotes if present
            val="${val%\"}"
            val="${val#\"}"
            val="${val%\'}"
            val="${val#\'}"
            export "$key=$val"
        fi
    done < "${ROOT}/.env"
fi

# Fallbacks für Telegram-Variablen
TELEGRAM_TOKEN="${ALERT_TELEGRAM_TOKEN:-${OPERATOR_TELEGRAM_BOT_TOKEN:-}}"
TELEGRAM_CHAT_ID="${ALERT_TELEGRAM_CHAT_ID:-${OPERATOR_ADMIN_CHAT_IDS:-}}"
DRY_RUN="${KAI_TIMER_PROBE_DRY_RUN:-0}"

echo "DEBUG: TIMERS_ENV='${KAI_TIMER_PROBE_TIMERS:-}'"
echo "DEBUG: TEST_STATES_ENV='${KAI_TIMER_PROBE_TEST_STATES:-}'"
echo "DEBUG: AUDIT_FILE_ENV='${KAI_TIMER_PROBE_AUDIT_FILE:-}'"
echo "DEBUG: AUDIT_FILE_RESOLVED='$AUDIT_FILE'"

# 3. Ermittlung aller zu prüfenden Timer
if [[ -n "${KAI_TIMER_PROBE_TIMERS:-}" ]]; then
    IFS=' ,' read -r -a TIMERS <<< "$KAI_TIMER_PROBE_TIMERS"
else
    # Finde alle kai-*.timer via systemctl list-unit-files
    # Verwende systemctl falls vorhanden, sonst Fallback
    if command -v systemctl >/dev/null 2>&1; then
        TIMERS=($(systemctl list-unit-files 'kai-*.timer' --no-legend --no-pager 2>/dev/null | awk '{print $1}' || true))
    else
        TIMERS=()
    fi
fi

# 4. Statusprüfung
NON_ACTIVE=()
for t in "${TIMERS[@]}"; do
    [[ -z "$t" ]] && continue
    
    state=""
    if [[ -n "${KAI_TIMER_PROBE_TEST_STATES:-}" ]]; then
        # Mock-Format: timer1:state1,timer2:state2
        tmp="${KAI_TIMER_PROBE_TEST_STATES#*$t:}"
        if [[ "$tmp" != "$KAI_TIMER_PROBE_TEST_STATES" ]]; then
            state="${tmp%%,*}"
        else
            state="active"
        fi
    else
        if command -v systemctl >/dev/null 2>&1; then
            state="$(systemctl is-active "$t" 2>/dev/null || echo "inactive")"
        else
            state="inactive"
        fi
    fi
    
    if [[ "$state" != "active" ]]; then
        NON_ACTIVE+=("$t ($state)")
    fi
done

# Wenn alle Timer gesund sind, schreiben wir ein OK-Audit und beenden
if (( ${#NON_ACTIVE[@]} == 0 )); then
    ts=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
    echo "{\"timestamp_utc\":\"$ts\",\"event\":\"timer_health_probe.ok\",\"findings\":[]}" >> "$AUDIT_FILE"
    echo "KAI Timer-Health: OK (all timers active)"
    exit 0
fi

# 5. Konstruieren der Findings als JSON-Array
FINDINGS_JSON=$(printf '%s\n' "${NON_ACTIVE[@]}" | python3 -c 'import sys, json; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))')

# 6. Idempotenz-Prüfung via Python
DECISION_JSON=$(python3 - "$AUDIT_FILE" "$FINDINGS_JSON" << 'PYEOF'
import sys
import json
from datetime import datetime, timezone, timedelta

audit_file = sys.argv[1]
current_findings = json.loads(sys.argv[2])

last_findings = []
last_alerted_utc = None

try:
    with open(audit_file, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    data = json.loads(line)
                    if data.get("event") == "timer_health_probe.findings":
                        last_findings = data.get("findings", [])
                        last_alert_str = data.get("last_alerted_utc")
                        if last_alert_str:
                            last_alerted_utc = datetime.fromisoformat(last_alert_str)
                except Exception:
                    continue
except FileNotFoundError:
    pass

should_alert = False
reason = ""

if last_alerted_utc is None:
    should_alert = True
    reason = "first_alert"
elif set(current_findings) != set(last_findings):
    should_alert = True
    reason = "new_findings"
else:
    now = datetime.now(timezone.utc)
    if now - last_alerted_utc >= timedelta(days=7):
        should_alert = True
        reason = "weekly_reminder"
    else:
        should_alert = False
        reason = "throttled"

print(json.dumps({
    "should_alert": should_alert,
    "reason": reason,
    "last_alerted_utc_str": last_alerted_utc.isoformat() if last_alerted_utc else None
}))
PYEOF
)

SHOULD_ALERT=$(echo "$DECISION_JSON" | python3 -c 'import sys, json; print(int(json.load(sys.stdin)["should_alert"]))')
DECISION_REASON=$(echo "$DECISION_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["reason"])')

# 7. Audit-Eintrag schreiben
ts=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
# Falls wir alarmieren, aktualisieren wir last_alerted_utc auf jetzt, ansonsten behalten wir den alten Stand bei
if (( SHOULD_ALERT == 1 )); then
    ALERT_TS="$ts"
else
    ALERT_TS=$(echo "$DECISION_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["last_alerted_utc_str"] or "")')
fi

echo "{\"timestamp_utc\":\"$ts\",\"event\":\"timer_health_probe.findings\",\"findings\":$FINDINGS_JSON,\"last_alerted_utc\":\"$ALERT_TS\",\"decision_reason\":\"$DECISION_REASON\"}" >> "$AUDIT_FILE"

# 8. Alarmieren via Telegram falls erforderlich
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || echo pi)"
MSG="⚠️ KAI Timer-Health: inactive timer(s) detected @ ${HOSTNAME_SHORT} ${ts}"$'\n\n'
for f in "${NON_ACTIVE[@]}"; do
    MSG+="• ${f}"$'\n'
done
MSG+=$'\n'"Reason: ${DECISION_REASON}"

if (( SHOULD_ALERT == 1 )); then
    echo "KAI Timer-Health: INACTIVE TIMERS DETECTED - ALERTING Operator (reason: $DECISION_REASON)"
    echo "$MSG"
    
    if [[ "$DRY_RUN" == "0" ]]; then
        if [[ -n "$TELEGRAM_TOKEN" && -n "$TELEGRAM_CHAT_ID" ]]; then
            curl -s --max-time 10 \
                -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
                -d "chat_id=${TELEGRAM_CHAT_ID}" \
                --data-urlencode "text=${MSG}" \
                >/dev/null 2>&1 || echo "WARN: Telegram sendMessage failed." >&2
        else
            echo "WARN: ALERT_TELEGRAM_TOKEN or CHAT_ID not set. Skipping Telegram notification." >&2
        fi
    else
        echo "Dry Run active: Telegram notification bypassed."
    fi
else
    echo "KAI Timer-Health: INACTIVE TIMERS DETECTED - Alert throttled (reason: $DECISION_REASON)"
fi

exit 0
