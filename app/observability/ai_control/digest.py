"""Zwei KI-Zeilen fuer den Operator-Digest -- eine Nachricht statt Silos (Spec §6.2).

Die vorhandene Zeile „KI-Kosten“ (``scripts/digest_ops_block._cost_line``) bleibt; diese
Zeilen kommen direkt dahinter: gestern je Anbieter, Konten mit Reichweite, offene Hinweise.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability.ai_control.config import ControlPaths


def collect(now: datetime, paths: ControlPaths | None = None) -> dict[str, Any]:
    from app.ai.spend import load_rows
    from app.observability.ai_control import history
    from app.observability.ai_control.accounts import read_accounts
    from app.observability.ai_control.alerts import load_state
    from app.observability.ai_control.workloads import provider_activity

    pfade = paths or ControlPaths()
    rows = load_rows(pfade.telemetry)
    gestern = (now.astimezone(UTC).date() - timedelta(days=1)).isoformat()
    tage = history.daily(rows, now=now, days=2)
    konten, _ = read_accounts(pfade.accounts)
    aktiv = provider_activity(rows, now=now)
    for k in konten:
        a = aktiv.get(str(k.get("provider")))
        rate = a.cost_7d_usd / 7 if a and a.cost_7d_usd > 0 else None
        bal = k.get("balance")
        k["runway_days"] = round(float(bal) / rate, 1) if (rate and bal is not None) else None
    offen = len(load_state(pfade.alert_state).get("open") or {})
    return {
        "yesterday": next((asdict(t) for t in tage if t.day == gestern), None),
        "accounts": konten,
        "open_hints": offen,
    }


def _k(n: int) -> str:
    return f"{round(n / 1000)}k" if n >= 1000 else str(n)


def format_lines(block: dict[str, Any]) -> list[str]:
    if "error" in block:
        return [f"🧠 *KI-Kontrolle:* nicht lesbar ({block['error']})"]
    g = block.get("yesterday")
    if g:
        kosten: dict[str, float] = g["cost_by_provider"]
        teile = " · ".join(f"{p} {v:.2f}" for p, v in sorted(kosten.items(), key=lambda x: -x[1]))
        ende = g.get("budget_exhausted_at")
        ende_text = (
            f"Budgetende {datetime.fromisoformat(ende):%H:%M} UTC" if ende else "Budget reichte"
        )
        erste = (
            f"🧠 *KI gestern:* {sum(kosten.values()):.2f} $ ({teile or '–'}) · "
            f"{g['calls']} Aufrufe · Token {_k(g['input_tokens'])}/{_k(g['output_tokens'])} · "
            f"{ende_text}"
        )
    else:
        erste = "🧠 *KI gestern:* keine Daten"
    konten: list[str] = []
    for k in block.get("accounts") or []:
        detail = k.get("detail") or {}
        if detail.get("kind") == "month_cost" and detail.get("month_cost_usd") is not None:
            konten.append(f"{k['provider']} Monat {float(detail['month_cost_usd']):.2f} $")
            continue
        if k.get("balance") is None:
            continue
        reichweite = f" (~{k['runway_days']:.0f} T)" if k.get("runway_days") is not None else ""
        konten.append(f"{k['provider']} {float(k['balance']):.2f} ${reichweite}")
    zweite = (
        "🏦 *KI-Konten:* "
        + (" · ".join(konten) or "keine Abfrage")
        + f" · offen: {block.get('open_hints', 0)} Hinweise"
    )
    return [erste, zweite]


__all__ = ["collect", "format_lines"]
