"""Minimal read-only operator dashboard surface (Sprint 46).

Security and scope invariants:
- No business logic: reads canonical daily summary only.
- No second aggregate path.
- No guarded actions, no trading semantics.
- No JavaScript and no external template dependency.
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from app.agents import mcp_server
from app.core.settings import AppSettings, get_settings

router = APIRouter(tags=["dashboard"])


def _dashboard_error_payload(*, code: str, message: str) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
        },
        "execution_enabled": False,
        "write_back_allowed": False,
    }


def _safe_text(value: object, *, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return escape(text, quote=True)


def _readiness_class(readiness_status: str) -> str:
    if readiness_status == "ok":
        return "status-ok"
    if readiness_status == "warning":
        return "status-warning"
    return "status-error"


def _render_dashboard_html(summary_payload: dict[str, object]) -> str:
    readiness_status = _safe_text(summary_payload.get("readiness_status"))
    cycle_count_today = _safe_text(summary_payload.get("cycle_count_today", 0), default="0")
    last_cycle_status = _safe_text(summary_payload.get("last_cycle_status"), default="n/a")
    last_cycle_symbol = _safe_text(summary_payload.get("last_cycle_symbol"), default="n/a")
    last_cycle_at = _safe_text(summary_payload.get("last_cycle_at"), default="n/a")
    position_count = _safe_text(summary_payload.get("position_count", 0), default="0")
    total_exposure_pct = _safe_text(summary_payload.get("total_exposure_pct", 0.0), default="0.0")
    mark_to_market_status = _safe_text(
        summary_payload.get("mark_to_market_status"),
        default="unknown",
    )
    decision_pack_status = _safe_text(
        summary_payload.get("decision_pack_status"),
        default="unknown",
    )
    open_incidents = _safe_text(summary_payload.get("open_incidents", 0), default="0")
    execution_enabled = _safe_text(summary_payload.get("execution_enabled", False), default="False")
    write_back_allowed = _safe_text(
        summary_payload.get("write_back_allowed", False),
        default="False",
    )
    aggregated_at = _safe_text(summary_payload.get("aggregated_at"), default="unknown")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>KAI Operator Dashboard</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --panel: #ffffff;
      --ink: #1a2a3a;
      --muted: #5b6c7c;
      --ok: #1f8a4c;
      --warn: #b36b00;
      --err: #b32121;
      --border: #d6e0ea;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      background: linear-gradient(145deg, #eef4fa, var(--bg));
      color: var(--ink);
    }}
    main {{
      max-width: 960px;
      margin: 24px auto;
      padding: 0 16px 24px;
    }}
    .header {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 16px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
    }}
    .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .value {{
      margin-top: 6px;
      font-size: 20px;
      font-weight: 700;
    }}
    .status-ok {{ color: var(--ok); }}
    .status-warning {{ color: var(--warn); }}
    .status-error {{ color: var(--err); }}
    .mono {{
      font-family: Consolas, "SFMono-Regular", Menlo, monospace;
      font-size: 13px;
      color: var(--muted);
    }}
    .drilldown-ref {{
      margin-top: 12px;
    }}
    .drilldown-ref ul {{
      margin: 8px 0 0 18px;
      padding: 0;
    }}
    .drilldown-ref li {{
      margin: 2px 0;
    }}
  </style>
</head>
<body>
  <main>
    <section class="header">
      <h1>KAI Operator Dashboard</h1>
      <p class="mono">canonical source: get_daily_operator_summary</p>
      <p class="mono">aggregated_at={aggregated_at}</p>
    </section>
    <section class="grid">
      <article class="card">
        <div class="label">Readiness</div>
        <div class="value {_readiness_class(readiness_status)}">{readiness_status}</div>
      </article>
      <article class="card">
        <div class="label">Cycles Today</div>
        <div class="value">{cycle_count_today}</div>
        <div class="mono">last={last_cycle_status} | {last_cycle_symbol} | {last_cycle_at}</div>
      </article>
      <article class="card">
        <div class="label">Portfolio</div>
        <div class="value">{position_count} positions</div>
        <div class="mono">exposure={total_exposure_pct}% | mtm={mark_to_market_status}</div>
      </article>
      <article class="card">
        <div class="label">Decision Pack</div>
        <div class="value">{decision_pack_status}</div>
      </article>
      <article class="card">
        <div class="label">Open Incidents</div>
        <div class="value">{open_incidents}</div>
      </article>
      <article class="card">
        <div class="label">Safety Flags</div>
        <div class="mono">execution_enabled={execution_enabled}</div>
        <div class="mono">write_back_allowed={write_back_allowed}</div>
      </article>
    </section>
    <section class="card drilldown-ref">
      <div class="label">Drilldown (Bearer required)</div>
      <ul class="mono">
        <li>/operator/readiness</li>
        <li>/operator/decision-pack</li>
        <li>/operator/trading-loop/recent-cycles</li>
        <li>/operator/review-journal</li>
        <li>/operator/resolution-summary</li>
      </ul>
    </section>
  </main>
</body>
</html>
"""


def _render_unavailable_html(reason: str) -> str:
    safe_reason = _safe_text(reason, default="unknown")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>KAI Operator Dashboard - unavailable</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      background: #f9f1f1;
      color: #4a1b1b;
    }}
    .panel {{
      max-width: 720px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #f0c9c9;
      border-radius: 12px;
      padding: 18px;
    }}
    .mono {{
      font-family: Consolas, "SFMono-Regular", Menlo, monospace;
      color: #7c3a3a;
    }}
  </style>
</head>
<body>
  <section class="panel">
    <h1>Dashboard unavailable</h1>
    <p>daily summary source is temporarily unavailable.</p>
    <p class="mono">reason={safe_reason}</p>
    <p class="mono">status=unavailable</p>
    <p class="mono">execution_enabled=False | write_back_allowed=False</p>
  </section>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(
    settings: AppSettings = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Render the minimal read-only operator dashboard from canonical daily summary."""
    if not (settings.api_key or "").strip():
        raise HTTPException(
            status_code=503,
            detail=_dashboard_error_payload(
                code="dashboard_disabled",
                message="Dashboard is disabled until APP_API_KEY is configured (fail-closed)",
            ),
        )

    try:
        payload = await mcp_server.get_daily_operator_summary()
    except Exception as exc:
        return HTMLResponse(
            content=_render_unavailable_html(exc.__class__.__name__),
            status_code=200,
        )

    if not isinstance(payload, dict):
        return HTMLResponse(
            content=_render_unavailable_html("invalid_summary_payload"),
            status_code=200,
        )
    return HTMLResponse(content=_render_dashboard_html(payload), status_code=200)
