"""Betriebsblock des Operator-Digests (MindBlow 2.0, E2): Backup-Kette + KI-Kosten.

Health meldet nur Fehler — und fuer das Pi-Tagesbackup und den Restore-Drill
nicht einmal das: es gibt keine Frische-Sonde, nur ``OnFailure=`` einer Unit,
die LAEUFT und scheitert. Ein Timer, der still aufhoert, faellt nirgends auf.
Dieser Block bestaetigt deshalb taeglich POSITIV, was sonst unsichtbar bleibt:

    🛟 Backup: Pi 26.09. 01:48Z ok · Drill 23.09. PASS · Vault 26.09. PASS (0 T)
    💶 KI-Kosten: Monat 12.34/31.00 USD (Hochrechnung 29.80) · heute 0.40/1.00 · OK
    ⚡ Lightning: Reconcile 07:25Z ok · 0 Orphans · SCB cb961652 (seit 25.09.)

Reine Leser; jeder Teil scheitert einzeln ("nicht lesbar") statt den Digest zu
kippen. Alarmierung bleibt bei Health — hier steht nur ein ⚠️ vor dem Teil, der
seine Schwelle reisst.

Liegt unter ``scripts/`` neben dem Digest und nicht unter ``app/``:
``backup_audit.jsonl`` schreiben Shell-Skripte (``kai_backup_artifacts.sh``),
kein App-Modul; der Stream-Ratchet zaehlt ``app/``.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Taeglicher Timer (03:47) + Spielraum: ein ausgefallener Lauf faellt am
#: naechsten Digest auf, nicht erst am uebernaechsten.
MAX_BACKUP_AGE_H = 26.0
#: Monatlicher Drill (``*-*-01 04:10``) + Spielraum.
MAX_DRILL_AGE_D = 35.0
_AUDIT = Path("backup_audit.jsonl")
_DRILL_DIR = Path("ops") / "backup_drill"
#: Beweise heissen nach ihrem Zeitstempel; ``standby_probe_*`` ist eine andere Probe.
_DRILL_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})(?:\.\d+)?Z\.json$")


def _parse_utc(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def collect_backup_daily(artifacts: Path, now: datetime) -> dict[str, Any]:
    """Neueste Laufzeile aus ``backup_audit.jsonl`` (Korrekturzeilen ohne ``status`` zaehlen nicht)."""
    path = artifacts / _AUDIT
    if not path.exists():
        return {"available": False}
    with path.open(encoding="utf-8", errors="replace") as fh:
        tail = deque(fh, maxlen=200)
    for line in reversed(tail):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        ts = _parse_utc(row.get("ts")) if isinstance(row, dict) else None
        if ts is None or "status" not in row:
            continue
        return {
            "available": True,
            "ts": ts,
            "status": str(row["status"]),
            "age_h": (now - ts).total_seconds() / 3600,
        }
    return {"available": False}


def collect_drill(artifacts: Path, now: datetime) -> dict[str, Any]:
    """Neuester Restore-Drill-Beweis (``artifacts/ops/backup_drill/<zeitstempel>.json``)."""
    folder = artifacts / _DRILL_DIR
    if not folder.is_dir():
        return {"available": False}
    proofs = sorted(p for p in folder.iterdir() if _DRILL_NAME.match(p.name))
    if not proofs:
        return {"available": False}
    newest = proofs[-1]
    m = _DRILL_NAME.match(newest.name)
    assert m is not None
    ts = datetime.fromisoformat(f"{m[1]}T{m[2]}:{m[3]}:{m[4]}+00:00")
    proof = json.loads(newest.read_text(encoding="utf-8"))
    return {
        "available": True,
        "ts": ts,
        "status": str(proof.get("status", "?")),
        "age_d": (now - ts).total_seconds() / 86400,
    }


def collect_vault(artifacts: Path, now: datetime) -> dict[str, Any]:
    """Neueste verifizierte Offsite-Generation (D:-Vault) aus den Quittungen."""
    from app.alerts.health_check_host import MAX_OFFPI_AGE_DAYS
    from app.observability.offpi_receipts import newest_verified

    newest = newest_verified(artifacts)
    if newest is None:
        return {"available": False, "max_age_d": MAX_OFFPI_AGE_DAYS}
    ts = datetime.fromtimestamp(newest[0], tz=UTC)
    return {
        "available": True,
        "ts": ts,
        "age_d": (now - ts).total_seconds() / 86400,
        "max_age_d": MAX_OFFPI_AGE_DAYS,
    }


def collect_ai_cost(now: datetime) -> dict[str, Any]:
    """Monat und heute gegen die Limits — dieselbe Quelle wie die Budget-Sperre."""
    from app.ai.spend import current_budget_status, month_projection

    status, today, month = current_budget_status(now=now)
    projection = month_projection(month, monthly_limit_usd=status.policy.monthly_limit_usd, now=now)
    return {
        "available": True,
        "state": status.state,
        "month_usd": month.known_cost_usd,
        "month_limit": status.policy.monthly_limit_usd,
        "projected": projection.projected_month_usd,
        "lower_bound": projection.lower_bound,
        "today_usd": today.known_cost_usd,
        "today_limit": status.policy.daily_limit_usd,
    }


def collect_ln(artifacts: Path, now: datetime) -> dict[str, Any]:
    """Zahlungsabgleich + SCB aus lokalen Zustaenden — NIE der Node (D-288)."""
    from app.payments.reconcile_types import RECONCILE_STALE_AFTER_MIN, STATE_FILENAME, load_state

    path = artifacts / "payments" / STATE_FILENAME
    if not path.is_file():
        return {"available": False}
    state = load_state(path)
    last_run = _parse_utc(state.last_run_utc)
    out: dict[str, Any] = {
        "available": last_run is not None,
        "ts": last_run,
        "status": state.last_status or "unknown",
        "orphans": state.last_orphans,
        # Seit D-288; ein Altzustand ohne Feld liest sich als None (unbekannt).
        "complete": state.last_complete,
        "stale": last_run is None
        or (now - last_run).total_seconds() / 60 >= RECONCILE_STALE_AFTER_MIN,
    }
    scb = artifacts / "scb_baseline.json"
    if scb.is_file():
        baseline = json.loads(scb.read_text(encoding="utf-8"))
        out["scb_sha"] = str(baseline.get("sha256", ""))[:8]
        out["scb_since"] = _parse_utc(baseline.get("recorded_at"))
    return out


def collect_ops_status(
    artifacts: Path = Path("artifacts"), now: datetime | None = None
) -> dict[str, dict[str, Any]]:
    """Alle Teile; ein kaputter Teil wird ``{"error": ...}``, der Rest bleibt."""
    jetzt = now or datetime.now(UTC)
    parts: dict[str, Callable[[], dict[str, Any]]] = {
        "backup": lambda: collect_backup_daily(artifacts, jetzt),
        "drill": lambda: collect_drill(artifacts, jetzt),
        "vault": lambda: collect_vault(artifacts, jetzt),
        "ai_cost": lambda: collect_ai_cost(jetzt),
        "ln": lambda: collect_ln(artifacts, jetzt),
    }
    out: dict[str, dict[str, Any]] = {}
    for name, collect in parts.items():
        try:
            out[name] = collect()
        except Exception as exc:  # noqa: BLE001 — ein Teil darf den Digest nicht kippen
            out[name] = {"error": type(exc).__name__}
    return out


def _day(ts: datetime) -> str:
    return ts.strftime("%d.%m.")


def _usd(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


def _backup_part(b: dict[str, Any]) -> str:
    if "error" in b:
        return "Pi nicht lesbar"
    if not b.get("available"):
        return "⚠️ Pi kein Lauf belegt"
    warn = b["status"] != "ok" or b["age_h"] > MAX_BACKUP_AGE_H
    age = f", {b['age_h']:.0f} h alt" if b["age_h"] > MAX_BACKUP_AGE_H else ""
    return f"{'⚠️ ' if warn else ''}Pi {b['ts']:%d.%m. %H:%MZ} {b['status']}{age}"


def _drill_part(d: dict[str, Any]) -> str:
    if "error" in d:
        return "Drill nicht lesbar"
    if not d.get("available"):
        return "⚠️ Drill kein Beweis"
    warn = d["status"] != "PASS" or d["age_d"] > MAX_DRILL_AGE_D
    return f"{'⚠️ ' if warn else ''}Drill {_day(d['ts'])} {d['status']}"


def _vault_part(v: dict[str, Any]) -> str:
    if "error" in v:
        return "Vault nicht lesbar"
    if not v.get("available"):
        return "⚠️ Vault keine verifizierte Kopie"
    warn = v["age_d"] >= v["max_age_d"]
    return f"{'⚠️ ' if warn else ''}Vault {_day(v['ts'])} PASS ({v['age_d']:.0f} T)"


def _cost_line(c: dict[str, Any]) -> str:
    if "error" in c:
        return f"💶 *KI-Kosten:* nicht lesbar ({c['error']})"
    bound = "≥" if c["lower_bound"] else ""
    month = f"Monat {bound}{_usd(c['month_usd'])}/{_usd(c['month_limit'])} USD"
    if c["projected"] is not None:
        month += f" (Hochrechnung {_usd(c['projected'])})"
    today = f"heute {_usd(c['today_usd'])}/{_usd(c['today_limit'])}"
    warn = "⚠️ " if c["state"] != "OK" else ""
    return f"💶 *KI-Kosten:* {month} · {today} · {warn}{c['state']}"


def _ln_line(ln: dict[str, Any]) -> str:
    if "error" in ln:
        return f"⚡ *Lightning:* nicht lesbar ({ln['error']})"
    if not ln.get("available"):
        return "⚡ *Lightning:* ⚠️ Reconcile kein Zustand"
    warn = ln["status"] != "ok" or ln["orphans"] > 0 or ln["complete"] is False or ln["stale"]
    parts = [f"{'⚠️ ' if warn else ''}Reconcile {ln['ts']:%H:%MZ} {ln['status']}"]
    if ln["complete"] is False:
        parts[0] += " (blind)"
    if ln["stale"]:
        parts[0] += " (veraltet)"
    parts.append(f"{ln['orphans']} Orphans")
    if ln.get("scb_sha"):
        since = f" (seit {_day(ln['scb_since'])})" if ln.get("scb_since") else ""
        parts.append(f"SCB {ln['scb_sha']}{since}")
    return "⚡ *Lightning:* " + " · ".join(parts)


def format_ops_lines(status: dict[str, dict[str, Any]]) -> list[str]:
    """Drei Zeilen fuer den Digest-Kopf: Backup, KI-Kosten, Lightning."""
    backup = " · ".join(
        (
            _backup_part(status.get("backup", {"error": "fehlt"})),
            _drill_part(status.get("drill", {"error": "fehlt"})),
            _vault_part(status.get("vault", {"error": "fehlt"})),
        )
    )
    return [
        f"🛟 *Backup:* {backup}",
        _cost_line(status.get("ai_cost", {"error": "fehlt"})),
        _ln_line(status.get("ln", {"error": "fehlt"})),
    ]


__all__ = [
    "MAX_BACKUP_AGE_H",
    "MAX_DRILL_AGE_D",
    "collect_ops_status",
    "format_ops_lines",
]
