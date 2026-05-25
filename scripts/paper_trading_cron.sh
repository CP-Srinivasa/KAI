#!/usr/bin/env bash
# KAI Paper-Trading cron — Bash-Port von paper_trading_cron.ps1
#
# Runs every invocation (scheduled via systemd timer on Pi, every 10 min).
# Functional parity with the Windows PS1: same counters, same daily markers,
# same CLI commands. Intentionally NO server-watchdog — systemd Restart=on-failure
# on kai-server.service replaces Ensure-Server{}.
#
# Usage (manual):   bash scripts/paper_trading_cron.sh
# Systemd:          ExecStart=/home/kai/ai_analyst_trading_bot/scripts/paper_trading_cron.sh
#
# Log: artifacts/paper_trading_cron.log (append-only, UTF-8).

set -uo pipefail  # no -e: single CLI failures must not abort the whole tick

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_FILE="$ROOT/artifacts/paper_trading_cron.log"
PYTHON="${PYTHON:-python}"
SLEEP_BIN="${SLEEP_BIN:-sleep}"
CRON_PROFILE_REQUEST="${PAPER_CRON_PROFILE:-conservative}"
CRON_PROFILE_SAFETY="explicit"

mkdir -p "$ROOT/artifacts"

# --- helpers ----------------------------------------------------------------

write_log() {
    local ts
    ts=$(date +'%Y-%m-%d %H:%M:%S')
    printf '%s  %s\n' "$ts" "$1" >> "$LOG_FILE"
}

# Canary profiles are explicit paper-only cron probes. Missing operator
# sign-off/config must keep the scheduler on the conservative profile.
case "$CRON_PROFILE_REQUEST" in
    ""|"conservative")
        CRON_PROFILE_REQUEST="conservative"
        CRON_ANALYSIS_PROFILE="conservative"
        ;;
    "canary_bullish")
        CRON_ANALYSIS_PROFILE="bullish"
        ;;
    "canary_bearish")
        CRON_ANALYSIS_PROFILE="bearish"
        ;;
    *)
        CRON_PROFILE_SAFETY="invalid_fallback_conservative"
        CRON_ANALYSIS_PROFILE="conservative"
        ;;
esac

# Extract "key=value" from command output. Returns "unknown" on miss.
extract_field() {
    local text="$1" key="$2"
    local val
    val=$(printf '%s' "$text" | grep -oE "${key}=[^[:space:]]+" | head -1 | cut -d= -f2-)
    printf '%s' "${val:-unknown}"
}

# Counter helpers — file holds a single integer, missing = 0.
read_counter() {
    local file="$1"
    if [[ -f "$file" ]]; then
        local v
        v=$(cat "$file" 2>/dev/null)
        [[ "$v" =~ ^[0-9]+$ ]] && { printf '%s' "$v"; return; }
    fi
    printf '0'
}

write_counter() { printf '%s' "$1" > "$2"; }

run_cycle() {
    local symbol="$1"
    local out
    out=$("$PYTHON" -m app.cli.main trading run-once \
        --symbol "$symbol" \
        --mode paper \
        --provider coingecko \
        --analysis-profile "$CRON_ANALYSIS_PROFILE" 2>&1) || true
    local cycle status fill
    cycle=$(extract_field "$out" cycle_id)
    status=$(extract_field "$out" status)
    fill=$(extract_field "$out" fill_simulated)
    write_log "$symbol  profile=$CRON_ANALYSIS_PROFILE  cycle=$cycle  status=$status  fill=$fill"
}

monitor_positions() {
    local out
    out=$("$PYTHON" -m app.cli.main trading monitor-positions \
        --provider coingecko 2>&1) || true
    local checked triggered nomd
    checked=$(extract_field "$out" checked)
    triggered=$(extract_field "$out" triggered)
    nomd=$(extract_field "$out" no_market_data)
    write_log "monitor  checked=$checked  triggered=$triggered  no_market_data=$nomd"
}

# Turn accepted operator-signal envelopes into paper fills.
# Fail-closed: silent no-op unless EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED=true.
bridge_tick() {
    local out
    out=$("$PYTHON" -m app.cli.main trading operator-signal-bridge-tick 2>&1) || true
    # Silent when disabled (fail-closed default).
    if printf '%s' "$out" | grep -q 'enabled=False'; then
        return
    fi
    local filled pending repend expired rejrisk
    filled=$(extract_field "$out" filled)
    pending=$(extract_field "$out" newly_pending)
    repend=$(extract_field "$out" re_pending)
    expired=$(extract_field "$out" expired)
    rejrisk=$(extract_field "$out" rejected_risk)
    write_log "bridge  filled=$filled  pending=$pending  repending=$repend  expired=$expired  rejrisk=$rejrisk"
}

entry_watch() {
    local out
    out=$("$PYTHON" -m app.cli.main trading operator-signal-entry-watch \
        --duration-seconds 55 \
        --poll-interval-seconds 5 2>&1) || true
    if printf '%s' "$out" | grep -q 'enabled=False'; then
        return
    fi
    local triggered filled held stale
    triggered=$(extract_field "$out" triggered)
    filled=$(extract_field "$out" bridge_filled)
    held=$(extract_field "$out" held)
    stale=$(extract_field "$out" stale_or_unavailable)
    write_log "entry-watch  triggered=$triggered  bridge_filled=$filled  held=$held  stale=$stale"
}

# --- main -------------------------------------------------------------------

write_log "--- cron start ---"
write_log "profile  requested=$CRON_PROFILE_REQUEST  active=$CRON_ANALYSIS_PROFILE  mode=paper  safety=$CRON_PROFILE_SAFETY"

# Server-watchdog entfällt — systemd kai-server.service hat Restart=on-failure.

monitor_positions
bridge_tick
entry_watch
run_cycle "BTC/USDT"
"$SLEEP_BIN" 15
run_cycle "ETH/USDT"

# Auto-annotate every 6th run (~hourly).
annotate_marker="$ROOT/artifacts/.annotate_counter"
counter=$(read_counter "$annotate_marker")
counter=$((counter + 1))
if (( counter >= 6 )); then
    counter=0
    write_log "auto-annotate starting"
    out=$("$PYTHON" -m app.cli.main alerts auto-annotate 2>&1) || true
    n=$(printf '%s' "$out" | grep -oE '[0-9]+ annotated' | head -1 | awk '{print $1}')
    write_log "auto-annotate done: ${n:-0} annotations"
fi
write_counter "$counter" "$annotate_marker"

# Daily briefing + health-check once per day after 08:00.
hour=$(date +%H); hour=$((10#$hour))
today=$(date +%Y-%m-%d)
briefing_marker="$ROOT/artifacts/.briefing_date"
last_briefing=""
[[ -f "$briefing_marker" ]] && last_briefing=$(cat "$briefing_marker" 2>/dev/null)
if (( hour >= 8 )) && [[ "$last_briefing" != "$today" ]]; then
    write_log "daily-briefing starting"
    briefing=$("$PYTHON" -m app.cli.main alerts daily-briefing --notify 2>&1) || true
    write_log "briefing: $(printf '%s' "$briefing" | tr '\n' ' ' | cut -c1-500)"
    health=$("$PYTHON" -m app.cli.main alerts health-check --notify 2>&1) || true
    write_log "health-check: $(printf '%s' "$health" | tr -d '\n' | cut -c1-200)"
    printf '%s' "$today" > "$briefing_marker"
fi

# Daily strategy review skeleton once per day after 08:00.
strategy_marker="$ROOT/artifacts/.daily_strategy_date"
last_strategy=""
[[ -f "$strategy_marker" ]] && last_strategy=$(cat "$strategy_marker" 2>/dev/null)
if (( hour >= 8 )) && [[ "$last_strategy" != "$today" ]]; then
    write_log "daily-strategy bootstrap starting"
    strat_out=$("$PYTHON" -m app.cli.main daily-strategy bootstrap 2>&1) || true
    write_log "daily-strategy: $(printf '%s' "$strat_out" | tr -d '\n' | cut -c1-200)"
    printf '%s' "$today" > "$strategy_marker"
fi

# Pipeline run-all every 4th run (~40 min).
pipeline_marker="$ROOT/artifacts/.pipeline_counter"
counter=$(read_counter "$pipeline_marker")
counter=$((counter + 1))
if (( counter >= 4 )); then
    counter=0
    write_log "pipeline run-all starting"
    "$PYTHON" -m app.cli.main pipeline run-all --top-n 1 >/dev/null 2>&1 || true
    write_log "pipeline run-all done"
fi
write_counter "$counter" "$pipeline_marker"

# NewsData.io every 3rd run (~30 min).
newsdata_marker="$ROOT/artifacts/.newsdata_counter"
counter=$(read_counter "$newsdata_marker")
counter=$((counter + 1))
if (( counter >= 3 )); then
    counter=0
    write_log "newsdata fetch starting"
    "$PYTHON" -m app.cli.main pipeline newsdata "crypto bitcoin ethereum solana" \
        --language en --category business --size 10 --top-n 3 >/dev/null 2>&1 || true
    write_log "newsdata done"
fi
write_counter "$counter" "$newsdata_marker"

# YouTube ingestion every 12th run (~2h).
youtube_marker="$ROOT/artifacts/.youtube_counter"
counter=$(read_counter "$youtube_marker")
counter=$((counter + 1))
if (( counter >= 12 )); then
    counter=0
    channel_file="$ROOT/monitor/youtube_channels.txt"
    if [[ -f "$channel_file" ]]; then
        yt_total=0
        while IFS= read -r line; do
            [[ "$line" =~ ^https ]] || continue
            "$PYTHON" -m app.cli.main pipeline youtube "$line" \
                --max-results 3 --top-n 1 >/dev/null 2>&1 || \
                write_log "youtube ERROR for $line"
            yt_total=$((yt_total + 1))
        done < "$channel_file"
        write_log "youtube done: $yt_total channels processed"
    fi
fi
write_counter "$counter" "$youtube_marker"

# TV-4 bridge every run.
tv_out=$("$PYTHON" -m app.cli.main tradingview run 2>&1) || true
n=$(printf '%s' "$tv_out" | grep -oE '[0-9]+ signals processed' | head -1 | awk '{print $1}')
[[ -n "$n" ]] && write_log "tv4-bridge: $n signals processed"

# X/Twitter every 6th run (~hourly).
twitter_marker="$ROOT/artifacts/.twitter_counter"
counter=$(read_counter "$twitter_marker")
counter=$((counter + 1))
if (( counter >= 6 )); then
    counter=0
    write_log "twitter fetch starting"
    "$PYTHON" -m app.cli.main pipeline twitter --top-n 5 >/dev/null 2>&1 || true
    write_log "twitter done"
fi
write_counter "$counter" "$twitter_marker"

# Dashboard freshness self-test — cheap loopback probes, no DB writes.
# External-edge probe (D-212) auto-activates when KAI_FRESHNESS_EXTERNAL_BASE
# is set in the environment (Pi systemd loads .env via EnvironmentFile=).
# No CLI arg needed — freshness_check.py reads the env-var itself.
fresh_out=$("$PYTHON" "$ROOT/scripts/freshness_check.py" 2>&1) || true
overall=$(printf '%s' "$fresh_out" | grep -oE 'KAI Freshness \([A-Z]+\)' | head -1 \
    | sed 's/KAI Freshness (//;s/)//')
write_log "freshness: ${overall:-?}"
if [[ "$overall" == "CRIT" || "$overall" == "WARN" ]]; then
    printf '%s\n' "$fresh_out" | grep -E '^\s*\[(WARN|CRIT|DOWN)\]' | while IFS= read -r line; do
        write_log "  $(printf '%s' "$line" | sed 's/^[[:space:]]*//')"
    done
fi

write_log "--- cron end ---"
