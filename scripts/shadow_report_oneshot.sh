#!/usr/bin/env bash
# Phase-B Shadow-Report one-shot (T+48h evaluation, 2026-06-04).
# Read-only: resolve pending shadow candidates, dump the report to a dated
# artifact, and Telegram-ping the operator with the headline. Triage reasoning
# happens when the operator forwards it to Claude. entry_mode stays disabled;
# this script never trades.
set -uo pipefail
ROOT=/home/kai/ai_analyst_trading_bot
cd "$ROOT" || exit 1
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true
set -a; source .env 2>/dev/null || true; set +a

LOG="$ROOT/artifacts/shadow_oneshot.log"
TS=$(date -u +%Y%m%d)
OUT="$ROOT/artifacts/shadow_report_${TS}.json"

{
  echo "=== shadow oneshot $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  python -m app.cli.main trading shadow-resolve
} >> "$LOG" 2>&1 || true

python -m app.cli.main trading shadow-report --json > "$OUT" 2>>"$LOG" || true

python - "$OUT" <<'PY' >> "$LOG" 2>&1 || true
import sys, json, asyncio
from app.alerts.notify import send_operator_notification

try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    d = {"primary_class": f"report_load_error:{exc}"}

msg = (
    "KAI Shadow-Report (Phase B, T+48h)\n"
    f"primary_class={d.get('primary_class')}\n"
    f"resolved={d.get('n_resolved')} total={d.get('total_candidates')} "
    f"pending={d.get('pending')} cov={d.get('resolution_coverage_pct')}%\n"
    f"mfe_before_mae={d.get('mfe_before_mae_rate')} "
    f"take={d.get('reached_take_rate')} stop={d.get('reached_stop_rate')}\n"
    f"med_mfe={d.get('median_mfe_bps')} med_mae={d.get('median_mae_bps')} "
    f"med_take_dist={d.get('median_take_dist_bps')}\n"
    f"fwd 1m/5m/15m/60m={d.get('median_fwd_60s_bps')}/{d.get('median_fwd_300s_bps')}/"
    f"{d.get('median_fwd_900s_bps')}/{d.get('median_fwd_3600s_bps')}\n"
    "-> An Claude weiterleiten fuer Triage. entry_mode bleibt disabled."
)
ok = asyncio.run(send_operator_notification(msg))
print("notify:", ok)
PY
