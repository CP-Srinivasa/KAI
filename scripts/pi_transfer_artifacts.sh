#!/usr/bin/env bash
# Transfer operational artifacts from laptop to Pi before the 2026-05-01 cutover.
#
# D-191 / NEO-F-META-20260424-013 + META-019. Artifacts/ is gitignored (53 MB),
# so a fresh `git clone` on the Pi starts blind. Audit evidence, agent
# dropboxes, daily-strategy docs, counter state, retention snapshots and
# the Telegram MTProto session file all need to land on the Pi *before*
# the Laptop-Stack is stopped, otherwise Re-Entry calculations lose their
# baseline and the approval-bridge forgets its counters.
#
# Usage (run on the laptop):
#   bash scripts/pi_transfer_artifacts.sh kai@pi.local
#   bash scripts/pi_transfer_artifacts.sh kai@pi.local --dry-run
#   bash scripts/pi_transfer_artifacts.sh kai@pi.local --verify
#   bash scripts/pi_transfer_artifacts.sh kai@pi.local --group=audit
#
# Supported groups: database, audit, paper, tradingview, telegram, agents,
#                   metrics, state, retention, env. Default = all groups
#                   except retention (old snapshots, optional).
#
# `database` covers the SQLite domain DB at data/dev.db (canonical_documents,
# trading_cycles, sources, portfolio_states). Without it the Pi starts with
# an empty schema — Re-Entry baseline lost. Added 2026-04-26 (D-5 status memo).
#
# SSH-over-Cloudflare-Tunnel hint:
#   Setup cloudflared access on the Pi + client, then REMOTE_HOST can be
#   the tunnel alias (e.g. "kai@pi.kai-trader.org"). No router ports.

set -euo pipefail

# --- Argument parsing ------------------------------------------------------

REMOTE_HOST=""
DRY_RUN=0
VERIFY_ONLY=0
SELECTED_GROUPS=""
INCLUDE_RETENTION=0

for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRY_RUN=1 ;;
        --verify)    VERIFY_ONLY=1 ;;
        --group=*)   SELECTED_GROUPS="${SELECTED_GROUPS} ${arg#--group=}" ;;
        --include-retention) INCLUDE_RETENTION=1 ;;
        -h|--help)
            sed -n '3,25p' "$0"
            exit 0
            ;;
        -*)
            echo "unknown flag: $arg" >&2
            exit 2
            ;;
        *)
            if [[ -z "$REMOTE_HOST" ]]; then
                REMOTE_HOST="$arg"
            else
                echo "unexpected positional arg: $arg" >&2
                exit 2
            fi
            ;;
    esac
done

if [[ -z "$REMOTE_HOST" ]]; then
    echo "ERROR: remote host required (e.g. kai@pi.local)" >&2
    exit 2
fi

# Default: all groups except retention.
if [[ -z "$SELECTED_GROUPS" ]]; then
    SELECTED_GROUPS="database audit paper tradingview telegram agents metrics state env"
    if (( INCLUDE_RETENTION == 1 )); then
        SELECTED_GROUPS="${SELECTED_GROUPS} retention"
    fi
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

REMOTE_ROOT="/home/kai/ai_analyst_trading_bot"

# --- Path inventory --------------------------------------------------------
# Only curated paths — transient caches (.log, .tmp, *.bak older than 30 d)
# stay on the laptop. Each array holds paths RELATIVE to repo root.

# Domain DB (SQLite). Holds canonical_documents, trading_cycles, sources,
# portfolio_states, alembic_version. CRITICAL: a fresh Pi clone has an empty
# schema — without this transfer the Re-Entry baseline (4651+ docs, 1803+
# cycles as of 2026-04-26) is lost. Final pre-cutover sync should run AFTER
# the laptop server is stopped, otherwise the file is mid-write.
DATABASE_FILES=(
    "data/dev.db"
)

AUDIT_FILES=(
    "artifacts/alert_audit.jsonl"
    "artifacts/alert_outcomes.jsonl"
    "artifacts/blocked_alerts.jsonl"
    "artifacts/api_request_audit.jsonl"
    "artifacts/operator_api_guarded_audit.jsonl"
    "artifacts/mcp_write_audit.jsonl"
)

PAPER_FILES=(
    "artifacts/paper_execution_audit.jsonl"
    "artifacts/trading_loop_audit.jsonl"
    "artifacts/bridge_pending_orders.jsonl"
    "artifacts/decision_journal.jsonl"
    "artifacts/operator_commands.jsonl"
    "artifacts/operator_review_journal.jsonl"
    "artifacts/session_log.jsonl"
)

TRADINGVIEW_FILES=(
    "artifacts/tradingview_pending_signals.jsonl"
    "artifacts/tradingview_pending_decisions.jsonl"
    "artifacts/tradingview_promoted_signals.jsonl"
    "artifacts/tradingview_signal_audit.jsonl"
    "artifacts/tradingview_consumed_ids.json"
    "artifacts/tradingview_replay_cache.db"
)

TELEGRAM_FILES=(
    # Session file is SENSITIVE (MTProto auth). Still transfer — operator
    # approval bridge loses all state if the session restarts from scratch.
    "artifacts/telegram_channel.session"
    "artifacts/telegram_channel_raw.jsonl"
    "artifacts/telegram_message_envelope.jsonl"
    "artifacts/telegram_signal_handoff.jsonl"
)

AGENT_DIRS=(
    "artifacts/agents/"
)

METRICS_DIRS=(
    "artifacts/ph5_hold/"
    "artifacts/ph5_baseline/"
    "artifacts/ph5_feature_analysis.json"
    "artifacts/ph5_keyword_coverage/"
    "artifacts/daily_strategy/"
    "artifacts/freshness_status.json"
    "artifacts/resolved_analysis.json"
    "artifacts/routes/"
    "artifacts/active_route_profile.json"
    "artifacts/tradingview/"
)

# Hidden state files (counter markers, dates). These are what the cron uses
# to gate "every 4th/6th/12th tick" logic — without them the first 10 cron
# ticks on the Pi fire all gated stages simultaneously.
STATE_FILES=(
    "artifacts/.annotate_counter"
    "artifacts/.briefing_date"
    "artifacts/.daily_strategy_date"
    "artifacts/.newsdata_counter"
    "artifacts/.pipeline_counter"
    "artifacts/.twitter_counter"
    "artifacts/.youtube_counter"
)

RETENTION_DIRS=(
    "artifacts/retention_backups/"
)

# D-191 / NEO-F-META-20260424-019: .env + .env.backup go via a SEPARATE
# handler — we don't rsync them as part of the bulk paths because they
# need operator confirmation at the console (secrets, not audit data).
ENV_FILES=(
    ".env"
    ".env.backup.20260418_130027"
)

# --- Rsync helpers ---------------------------------------------------------

RSYNC_OPTS=(-avz --checksum --partial --human-readable --mkpath)
if (( DRY_RUN == 1 )); then
    RSYNC_OPTS+=(--dry-run --itemize-changes)
fi

transfer_paths() {
    local group="$1"
    shift
    local paths=("$@")
    local existing=()

    for p in "${paths[@]}"; do
        if [[ -e "$p" ]]; then
            existing+=("$p")
        else
            echo "  [skip] $p (not present on laptop)"
        fi
    done

    if (( ${#existing[@]} == 0 )); then
        echo "  [group=$group] nothing to transfer"
        return 0
    fi

    echo "  [group=$group] rsync ${#existing[@]} path(s) -> $REMOTE_HOST:$REMOTE_ROOT/"
    if (( VERIFY_ONLY == 1 )); then
        verify_paths "${existing[@]}"
    else
        # --relative with . anchor keeps the artifacts/... directory structure.
        rsync "${RSYNC_OPTS[@]}" --relative "${existing[@]/#/./}" "$REMOTE_HOST:$REMOTE_ROOT/"
    fi
}

transfer_env() {
    echo ""
    echo "  [group=env] SENSITIVE — secrets files handled manually"
    for f in "${ENV_FILES[@]}"; do
        if [[ -f "$f" ]]; then
            local sha
            sha=$(sha256sum "$f" | awk '{print $1}')
            echo "    $f  ($(wc -c < "$f") bytes, sha256=${sha:0:16}…)"
        else
            echo "    $f  (NOT PRESENT)"
        fi
    done
    echo ""
    echo "  Transfer these yourself with:"
    for f in "${ENV_FILES[@]}"; do
        [[ -f "$f" ]] || continue
        echo "    scp $f $REMOTE_HOST:$REMOTE_ROOT/$f"
    done
    echo ""
    echo "  On the Pi, verify with:"
    for f in "${ENV_FILES[@]}"; do
        [[ -f "$f" ]] || continue
        echo "    ssh $REMOTE_HOST \"sha256sum $REMOTE_ROOT/$f\"    # must match above"
    done
}

verify_paths() {
    local paths=("$@")
    for p in "${paths[@]}"; do
        if [[ -d "$p" ]]; then
            local local_sha
            local_sha=$(find "$p" -type f -print0 | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | awk '{print $1}')
            local remote_sha
            remote_sha=$(ssh "$REMOTE_HOST" "cd $REMOTE_ROOT && find $p -type f -print0 2>/dev/null | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | awk '{print \$1}'" || echo "")
            if [[ "$local_sha" == "$remote_sha" ]]; then
                echo "    OK  $p  ${local_sha:0:12}"
            else
                echo "    MISMATCH  $p  local=${local_sha:0:12}  remote=${remote_sha:0:12}"
            fi
        elif [[ -f "$p" ]]; then
            local local_sha
            local_sha=$(sha256sum "$p" | awk '{print $1}')
            local remote_sha
            remote_sha=$(ssh "$REMOTE_HOST" "sha256sum $REMOTE_ROOT/$p 2>/dev/null | awk '{print \$1}'" || echo "")
            if [[ "$local_sha" == "$remote_sha" ]]; then
                echo "    OK  $p  ${local_sha:0:12}"
            else
                echo "    MISMATCH  $p  local=${local_sha:0:12}  remote=${remote_sha:0:12}"
            fi
        fi
    done
}

# --- Main ------------------------------------------------------------------

echo "KAI artifact transfer"
echo "  source: $REPO_ROOT (laptop)"
echo "  target: $REMOTE_HOST:$REMOTE_ROOT"
echo "  groups: $SELECTED_GROUPS"
(( DRY_RUN == 1 )) && echo "  mode:   DRY-RUN (no changes)"
(( VERIFY_ONLY == 1 )) && echo "  mode:   VERIFY-ONLY (compare sha256)"
echo ""

for group in $SELECTED_GROUPS; do
    case "$group" in
        database)     transfer_paths database     "${DATABASE_FILES[@]}" ;;
        audit)        transfer_paths audit        "${AUDIT_FILES[@]}" ;;
        paper)        transfer_paths paper        "${PAPER_FILES[@]}" ;;
        tradingview)  transfer_paths tradingview  "${TRADINGVIEW_FILES[@]}" ;;
        telegram)     transfer_paths telegram     "${TELEGRAM_FILES[@]}" ;;
        agents)       transfer_paths agents       "${AGENT_DIRS[@]}" ;;
        metrics)      transfer_paths metrics      "${METRICS_DIRS[@]}" ;;
        state)        transfer_paths state        "${STATE_FILES[@]}" ;;
        retention)    transfer_paths retention    "${RETENTION_DIRS[@]}" ;;
        env)          transfer_env ;;
        *)
            echo "unknown group: $group" >&2
            exit 2
            ;;
    esac
done

echo ""
echo "Done."
if (( DRY_RUN == 0 && VERIFY_ONLY == 0 )); then
    echo "Next: rerun with --verify to compare sha256 sums."
fi
