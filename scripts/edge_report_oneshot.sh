#!/usr/bin/env bash
# Nightly edge-report persistence (Pre-Re-Enable-Blocker #5, 2026-06-03).
# Read-only: dump the cost-adjusted edge diagnostics to a stable artifact plus a
# dated copy, and Telegram-ping the operator with the headline verdict. Triage
# reasoning happens when the operator forwards it to Claude. entry_mode stays
# disabled; this script never trades and never changes execution state.
#
# Install as a nightly systemd timer on the Pi (see docs/strategy/entry_safety_runbook).
set -uo pipefail
ROOT=/home/kai/ai_analyst_trading_bot
cd "$ROOT" || exit 1
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true
set -a; source .env 2>/dev/null || true; set +a

LOG="$ROOT/artifacts/edge_report_oneshot.log"
TS=$(date -u +%Y%m%d)
OUT="$ROOT/artifacts/edge_report.json"
DATED="$ROOT/artifacts/edge_report_${TS}.json"

{
  echo "=== edge-report oneshot $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
} >> "$LOG" 2>&1 || true

# Stable artifact (overwritten nightly) + immutable dated snapshot.
python -m app.cli.main trading edge-report --json > "$OUT" 2>>"$LOG" || true
cp -f "$OUT" "$DATED" 2>>"$LOG" || true

python - "$OUT" <<'PY' >> "$LOG" 2>&1 || true
import sys, json
try:
    from app.alerts.notify import send_operator_notification  # noqa: F401
    _notify = True
except Exception:
    _notify = False

try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    d = {"error": f"report_load_error:{exc}"}

overall = d.get("overall", {}) if isinstance(d.get("overall"), dict) else {}
excl = d.get("excluded_quarantined", {}) if isinstance(d.get("excluded_quarantined"), dict) else {}
msg = (
    "KAI Edge-Report (nightly, read-only)\n"
    f"venue={d.get('venue')} closed_trades={d.get('closed_trade_count')}\n"
    f"net_bps_per_notional_mean={overall.get('net_bps_per_notional_mean')} "
    f"winrate={overall.get('winrate')}\n"
    f"excluded_quarantined={excl.get('excluded_count')} "
    f"reasons={excl.get('reasons')}\n"
    "CAVEAT: includes any active canary/probe rows unless source-attributed; "
    "evaluate exit/regime geometry, not degenerate confidence. entry_mode stays disabled."
)
print(msg)
if _notify:
    try:
        import asyncio
        from app.alerts.notify import send_operator_notification
        asyncio.run(send_operator_notification(msg))
    except Exception as exc:
        print(f"notify_failed:{exc}")
PY

echo "edge-report written to $OUT (+ $DATED)" >> "$LOG" 2>&1 || true
