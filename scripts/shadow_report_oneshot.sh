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
)

# DS-20260604-V2: Interpretations-Sprachregelung (Neo-V1, 04.06.). INSUFFICIENT_DATA
# darf NICHT als "kein Edge" gelesen werden: der autonome Cron faehrt das statische
# conservative-Probe-Profil (hartkodiert priority=1, unter dem strikten Gate blockiert);
# der echte SignalGenerator wird in diesem Pfad nicht ausgefuehrt (trading_loop.py:1475,
# D-182-Gate returnt vor _signals.generate). INSUFFICIENT_DATA = kein echtes
# Generator-Signal gemessen, NICHT "Markt/Generator schweigt", NICHT "kein Edge".
_pc = str(d.get("primary_class") or "")
_INTERP = {
    "INSUFFICIENT_DATA": (
        "INTERPRETATION: INSUFFICIENT_DATA = kein echtes Generator-Signal gemessen. "
        "Der autonome Cron faehrt das statische conservative-Probe-Profil (hartkodiert "
        "priority=1, by-design unter dem Gate blockiert); der echte SignalGenerator "
        "wird in diesem Pfad NICHT ausgefuehrt. Das heisst NICHT 'kein Edge' und NICHT "
        "'Markt/Generator schweigt'. Naechster Schritt: echten Generator gate-unabhaengig "
        "in den Shadow-Pfad verdrahten (NEO-P-002-r3)."
    ),
}
if _pc in _INTERP:
    msg += _INTERP[_pc] + "\n"
elif _pc.startswith("report_load_error"):
    msg += f"INTERPRETATION: Report-Load-Fehler ({_pc}) - Rohdaten manuell pruefen.\n"

msg += "-> An Claude weiterleiten fuer Triage. entry_mode bleibt disabled."
ok = asyncio.run(send_operator_notification(msg))
print("notify:", ok)
PY
