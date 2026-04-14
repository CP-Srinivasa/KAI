"""Operator dashboard with quality-bar tracking, alerts, and paper-trading views.

Reads directly from JSONL artifacts and the hold metrics report.
No external template dependencies — pure inline HTML + vanilla JS + Chart.js CDN.
Auto-refreshes every 60 seconds.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["dashboard"])

_ARTIFACTS = Path("artifacts")
_HOLD_REPORT = _ARTIFACTS / "ph5_hold" / "ph5_hold_metrics_report.json"
_ALERT_AUDIT = _ARTIFACTS / "alert_audit.jsonl"
_ALERT_OUTCOMES = _ARTIFACTS / "alert_outcomes.jsonl"
_TRADING_LOOP_AUDIT = _ARTIFACTS / "trading_loop_audit.jsonl"
_PAPER_EXECUTION_AUDIT = _ARTIFACTS / "paper_execution_audit.jsonl"


def _safe(value: object, default: str = "—") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return escape(text, quote=True) if text else default


def _load_jsonl(path: Path, tail: int = 0) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except json.JSONDecodeError:
            continue
    return rows[-tail:] if tail else rows


def _load_hold_report() -> dict[str, Any] | None:
    if not _HOLD_REPORT.exists():
        return None
    try:
        return json.loads(_HOLD_REPORT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# JSON API endpoint for dashboard data
# ---------------------------------------------------------------------------

@router.get("/dashboard/api/quality", tags=["dashboard"])
async def dashboard_quality_api() -> JSONResponse:
    """Return quality-bar metrics as JSON for the dashboard."""
    report = _load_hold_report()
    if report is None:
        return JSONResponse({"error": "hold_report_not_found"}, status_code=404)

    quality = report.get("signal_quality_validation", {})
    hit_rate = report.get("alert_hit_rate_evidence", {})
    paper = report.get("paper_trading_evidence", {})
    gate = report.get("hold_gate_evaluation", {})

    # Paper fills with PnL
    exec_rows = _load_jsonl(_PAPER_EXECUTION_AUDIT)
    fills = [r for r in exec_rows if r.get("event_type") == "order_filled"]

    # Recent alerts (last 20 non-digest)
    audit_rows = _load_jsonl(_ALERT_AUDIT)
    non_digest = [r for r in audit_rows if not r.get("is_digest")]
    recent_alerts = non_digest[-20:]

    # Outcome map
    outcome_rows = _load_jsonl(_ALERT_OUTCOMES)
    outcomes_by_doc: dict[str, str] = {}
    for o in outcome_rows:
        outcomes_by_doc[o.get("document_id", "")] = o.get("outcome", "")

    # Trading loop status counts
    loop_rows = _load_jsonl(_TRADING_LOOP_AUDIT)
    status_counts: dict[str, int] = {}
    for r in loop_rows:
        s = r.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    fwd = report.get("forward_simulation", {})

    return JSONResponse({
        "precision_pct": quality.get("resolved_precision_pct"),
        "false_positive_pct": quality.get("resolved_false_positive_rate_pct"),
        "resolved_count": hit_rate.get("resolved_directional_documents", 0),
        "directional_count": hit_rate.get("directional_alert_documents", 0),
        "hits": hit_rate.get("alert_hits", 0),
        "misses": hit_rate.get("alert_misses", 0),
        "priority_corr": quality.get("priority_hit_correlation"),
        "forward_precision_pct": fwd.get("precision_pct"),
        "forward_resolved": fwd.get("resolved", 0),
        "forward_hits": fwd.get("hits", 0),
        "forward_miss": fwd.get("miss", 0),
        "paper_fills": len(fills),
        "paper_cycles": paper.get("loop_metrics", {}).get("total_cycles", 0),
        "real_price_cycles": quality.get("paper_real_price_cycle_count", 0),
        "gate_status": gate.get("overall_status"),
        "blocking_reasons": gate.get("blocking_reasons", []),
        "actionable_rate_pct": quality.get("directional_actionable_rate_pct"),
        "high_priority_hit_rate_pct": quality.get("high_priority_hit_rate_pct"),
        "low_priority_hit_rate_pct": quality.get("low_priority_hit_rate_pct"),
        "loop_status_counts": status_counts,
        "recent_alerts": [
            {
                "doc_id": r.get("document_id", "")[:12],
                "sentiment": r.get("sentiment_label", ""),
                "priority": r.get("priority"),
                "assets": r.get("affected_assets", []),
                "dispatched_at": r.get("dispatched_at", "")[:16],
                "outcome": outcomes_by_doc.get(r.get("document_id", ""), ""),
            }
            for r in reversed(recent_alerts)
        ],
        "generated_at": report.get("generated_at", ""),
    })


# ---------------------------------------------------------------------------
# HTML Dashboard
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """\
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KAI Operator Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #0f1923; --panel: #1a2736; --ink: #e0e8f0; --muted: #7a8fa3;
      --accent: #3b82f6; --ok: #22c55e; --warn: #f59e0b; --err: #ef4444;
      --border: #2a3a4d;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Segoe UI", system-ui, sans-serif;
      background: var(--bg); color: var(--ink);
    }
    header {
      background: var(--panel); border-bottom: 1px solid var(--border);
      padding: 12px 24px; display: flex; align-items: center; gap: 16px;
    }
    header h1 { font-size: 18px; font-weight: 600; }
    header .meta { color: var(--muted); font-size: 12px; margin-left: auto; }
    main { max-width: 1200px; margin: 0 auto; padding: 20px; }

    .quality-bar {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 12px; padding: 20px; margin-bottom: 16px;
    }
    .quality-bar h2 { font-size: 14px; color: var(--muted); text-transform: uppercase;
      letter-spacing: 0.05em; margin-bottom: 16px; }
    .metrics-row { display: flex; gap: 16px; flex-wrap: wrap; }
    .metric {
      flex: 1; min-width: 140px; background: var(--bg);
      border-radius: 8px; padding: 14px; text-align: center;
    }
    .metric .label { font-size: 11px; color: var(--muted); text-transform: uppercase; }
    .metric .value { font-size: 28px; font-weight: 700; margin-top: 4px; }
    .metric .sub { font-size: 11px; color: var(--muted); margin-top: 2px; }

    .progress-track {
      background: var(--bg); border-radius: 6px; height: 10px;
      margin-top: 12px; overflow: hidden;
    }
    .progress-fill {
      height: 100%; border-radius: 6px;
      transition: width 0.5s ease;
    }
    .progress-label {
      display: flex; justify-content: space-between;
      font-size: 11px; color: var(--muted); margin-top: 4px;
    }

    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
    .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }
    @media (max-width: 768px) {
      .grid-2, .grid-3 { grid-template-columns: 1fr; }
    }

    .card {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 12px; padding: 16px;
    }
    .card h3 { font-size: 13px; color: var(--muted); text-transform: uppercase;
      letter-spacing: 0.05em; margin-bottom: 12px; }

    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; color: var(--muted); font-size: 11px;
      text-transform: uppercase; padding: 6px 8px; border-bottom: 1px solid var(--border); }
    td { padding: 6px 8px; border-bottom: 1px solid var(--border); }
    tr:last-child td { border-bottom: none; }

    .badge {
      display: inline-block; padding: 2px 8px; border-radius: 4px;
      font-size: 11px; font-weight: 600;
    }
    .badge-hit { background: #16382a; color: var(--ok); }
    .badge-miss { background: #3b1a1a; color: var(--err); }
    .badge-bullish { background: #1a3328; color: var(--ok); }
    .badge-bearish { background: #331a1a; color: var(--err); }
    .badge-pending { background: #2a2a1a; color: var(--warn); }

    .gate-status {
      display: inline-block; padding: 4px 12px; border-radius: 6px;
      font-weight: 700; font-size: 13px;
    }
    .gate-hold { background: #3b1a1a; color: var(--err); }
    .gate-releasable { background: #16382a; color: var(--ok); }

    .stat-row { display: flex; justify-content: space-between; padding: 4px 0;
      font-size: 13px; }
    .stat-row .k { color: var(--muted); }
    .stat-row .v { font-weight: 600; }

    #loading { text-align: center; padding: 60px; color: var(--muted); }
    .refresh-hint { color: var(--muted); font-size: 11px; }
  </style>
</head>
<body>
  <header>
    <h1>KAI Operator Dashboard</h1>
    <span class="refresh-hint">Auto-refresh 60s</span>
    <span class="meta" id="meta"></span>
  </header>
  <main id="app">
    <div id="loading">Lade Dashboard-Daten...</div>
  </main>

<script>
const $ = s => document.querySelector(s);
const API = '/dashboard/api/quality';

function pct(v) { return v != null ? v.toFixed(1) + '%' : '--'; }
function num(v) { return v != null ? v : '--'; }
function cls(v, good, bad) {
  if (v == null) return '';
  return v >= good ? 'color:var(--ok)' : v <= bad ? 'color:var(--err)' : 'color:var(--warn)';
}

function progressBar(value, target, color) {
  const pctVal = value != null ? Math.min((value / target) * 100, 100) : 0;
  return `
    <div class="progress-track">
      <div class="progress-fill" style="width:${pctVal}%;background:${color}"></div>
    </div>
    <div class="progress-label">
      <span>${value != null ? value.toFixed(1) + '%' : '--'}</span>
      <span>Ziel: ${target}%</span>
    </div>`;
}

function sentimentBadge(s) {
  if (!s) return '';
  const c = s === 'bullish' ? 'badge-bullish' : s === 'bearish' ? 'badge-bearish' : '';
  return `<span class="badge ${c}">${s}</span>`;
}

function outcomeBadge(o) {
  if (!o) return '<span class="badge badge-pending">pending</span>';
  const c = o === 'hit' ? 'badge-hit' : o === 'miss' ? 'badge-miss' : 'badge-pending';
  return `<span class="badge ${c}">${o}</span>`;
}

function render(d) {
  const isReleasable = d.gate_status === 'hold_releasable';
  const gateClass = isReleasable ? 'gate-releasable' : 'gate-hold';
  const gateLabel = isReleasable ? 'RELEASABLE' : 'HOLD ACTIVE';

  const alertRows = (d.recent_alerts || []).map(a => `
    <tr>
      <td style="font-family:monospace;font-size:11px">${a.doc_id}</td>
      <td>${sentimentBadge(a.sentiment)}</td>
      <td style="text-align:center">${a.priority || '--'}</td>
      <td style="font-size:11px">${(a.assets || []).join(', ')}</td>
      <td style="font-size:11px">${a.dispatched_at}</td>
      <td>${outcomeBadge(a.outcome)}</td>
    </tr>`).join('');

  const loopEntries = Object.entries(d.loop_status_counts || {})
    .sort((a,b) => b[1] - a[1])
    .map(([k,v]) => '<div class="stat-row">' +
      '<span class="k">'+k+'</span>' +
      '<span class="v">'+v+'</span></div>')
    .join('');

  const pp = d.precision_pct;
  const fp = d.forward_precision_pct;
  const fr = d.forward_resolved;
  const fh = d.forward_hits;
  const fm = d.forward_miss;
  const pc = d.priority_corr;
  const pcVal = pc != null ? pc.toFixed(4) : '--';
  const pcPct = pc != null ? pc * 100 : 0;
  const pf = d.paper_fills;
  const rc = d.resolved_count;
  const fpS = cls(100-(d.false_positive_pct||0), 60, 40);
  function precColor() {
    if (pp >= 60) return 'var(--ok)';
    return pp >= 50 ? 'var(--warn)' : 'var(--err)';
  }
  function sr(k, v, s) {
    const st = s ? ' style="'+s+'"' : '';
    return '<div class="stat-row"><span class="k">'
      +k+'</span><span class="v"'+st+'>'+v+'</span></div>';
  }

  $('#app').innerHTML = `
    <div class="quality-bar">
      <h2>Quality Bar &mdash;
        <span class="${gateClass} gate-status">
          ${gateLabel}</span></h2>
      <div class="metrics-row">
        <div class="metric">
          <div class="label">Forward Precision</div>
          <div class="value" style="${cls(fp, 60, 40)}">
            ${pct(fp)}</div>
          <div class="sub">${fh} hits / ${fm} miss (${fr} resolved)</div>
          ${progressBar(fp, 60,
            fp >= 60 ? 'var(--ok)' : 'var(--warn)')}
        </div>
        <div class="metric">
          <div class="label">Raw Precision</div>
          <div class="value" style="${cls(pp, 60, 40)}">
            ${pct(pp)}</div>
          <div class="sub">Alle ${rc} resolved</div>
          ${progressBar(pp, 60, precColor())}
        </div>
        <div class="metric">
          <div class="label">Resolved</div>
          <div class="value" style="${cls(rc, 50, 20)}">
            ${num(rc)}</div>
          <div class="sub">${d.hits} hits / ${d.misses} misses</div>
          ${progressBar(rc, 50,
            rc >= 50 ? 'var(--ok)' : 'var(--warn)')}
        </div>
        <div class="metric">
          <div class="label">Priority-Hit Korr.</div>
          <div class="value" style="${cls(pc, 0.4, 0.1)}">
            ${pcVal}</div>
          <div class="sub">Ziel: &ge;0.40</div>
          ${progressBar(pcPct, 40,
            pc >= 0.4 ? 'var(--ok)' : 'var(--warn)')}
        </div>
        <div class="metric">
          <div class="label">Paper Fills</div>
          <div class="value" style="${cls(pf, 10, 3)}">
            ${num(pf)}</div>
          <div class="sub">Ziel: &ge;10</div>
          ${progressBar(pf, 10,
            pf >= 10 ? 'var(--ok)' : 'var(--warn)')}
        </div>
      </div>
    </div>

    <div class="grid-3">
      <div class="card">
        <h3>Signal-Qualitat</h3>
        ${sr('Actionable Rate', pct(d.actionable_rate_pct))}
        ${sr('False Positive', pct(d.false_positive_pct), fpS)}
        ${sr('High-P Hit Rate', pct(d.high_priority_hit_rate_pct))}
        ${sr('Low-P Hit Rate', pct(d.low_priority_hit_rate_pct))}
        ${sr('Directional Docs', num(d.directional_count))}
      </div>
      <div class="card">
        <h3>Paper Trading</h3>
        ${sr('Total Cycles', num(d.paper_cycles))}
        ${sr('Real-Price Cycles', num(d.real_price_cycles))}
        ${sr('Fills', num(pf), cls(pf, 10, 3))}
      </div>
      <div class="card">
        <h3>Trading Loop Status</h3>
        ${loopEntries || '<div class="stat-row"><span class="k">Keine Daten</span></div>'}
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <h3>Letzte Directional Alerts</h3>
      <table>
        <thead><tr>
          <th>Doc ID</th><th>Sentiment</th><th>P</th>
          <th>Assets</th><th>Dispatched</th><th>Outcome</th>
        </tr></thead>
        <tbody>${alertRows || '<tr><td colspan="6"' +
          ' style="color:var(--muted)">Keine Alerts</td></tr>'
        }</tbody>
      </table>
    </div>
  `;

  $('#meta').textContent = 'Report: ' + (d.generated_at || '').substring(0, 19);
}

async function load() {
  try {
    const r = await fetch(API);
    if (!r.ok) throw new Error(r.status);
    render(await r.json());
  } catch(e) {
    $('#app').innerHTML = '<div id="loading" ' +
      'style="color:var(--err)">Fehler: ' +
      e.message + '</div>';
  }
}

load();
setInterval(load, 60000);
</script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard() -> HTMLResponse:
    """Render the operator dashboard with quality-bar tracking."""
    return HTMLResponse(content=_DASHBOARD_HTML, status_code=200)
