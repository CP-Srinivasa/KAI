#!/usr/bin/env python3
"""Täglicher Operator-Digest (Sprint S6, 2026-06-11).

EINE lesbare Telegram-Nachricht statt vier Report-Silos: Entry-Mode/Routen
(D-233-Wahrheit), Paper-Lernströme (Fills nach Quelle, 24h), Premium-Bridge-
Stages (24h), Shadow-Real-Funnel (#175) und D-227-Blocked-Outcomes — plus die
**Auswertungs-Meilensteine** mit Auto-Erkennung:

  - V5-Messphase: „Tag X/7" seit Evidence-Aktivierung; ab Tag 7 explizite
    Aufforderung, die Shadow-Logs auszuwerten (trust-Entscheidung).
  - Edge-Report: „autonomous_generator resolved n/30" (das Edge-Gate
    min_resolved); ab Erreichen explizite Aufforderung, den Edge-Report zu
    fahren. Bewusst auf AUSGEFÜHRTE Generator-Closes statt shadow-resolved n
    — der bindende Engpass sind geschlossene Trades (Operator-Vorgabe
    2026-06-14). shadow-resolved n bleibt als Kontext sichtbar.

Versand über die etablierten ``ALERT_TELEGRAM_TOKEN``/``ALERT_TELEGRAM_CHAT_ID``
Env-Variablen (gleicher Vertrag wie pi_health_digest.sh). ``--dry-run`` druckt
die Nachricht nur (Test-/Lokal-Pfad, kein Netz). Read-only: der Digest ändert
nie Zustand, er berichtet ihn.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("operator-digest")

_ARTIFACTS = Path("artifacts")
# Meilenstein-Schwellen (Operator-Vorgabe 2026-06-11/06-14).
V5_REVIEW_AFTER_DAYS = 7
# Edge-Report-Meilenstein feuert auf AUSGEFÜHRTE Generator-Closes
# (autonomous_generator erreicht das Edge-Gate), NICHT auf shadow-resolved n.
# Der bindende Engpass sind geschlossene Trades, nicht Forward-Samples
# (Operator-Vorgabe 2026-06-14). Fallback, falls gate_config.min_resolved fehlt.
EDGE_GATE_MIN_RESOLVED = 30
# Telegram hard limit is 4096 chars — truncate honestly instead of failing.
_TELEGRAM_LIMIT = 4000


# ── Collectors (read-only) ───────────────────────────────────────────────────


def _read_jsonl_tail(path: Path, max_lines: int = 20_000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-max_lines:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError as exc:
        logger.warning("read failed %s: %s", path, exc)
    return out


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def collect_runtime() -> dict[str, Any]:
    try:
        from app.core.settings import get_settings
        from app.execution.entry_policy import resolve_entry_policy

        policy = resolve_entry_policy(get_settings())
        return {
            "entry_mode": policy.mode.value,
            "open_routes": [r.value for r, v in policy.verdicts.items() if v.allowed],
            "contradictions": list(policy.contradictions),
        }
    except Exception as exc:  # noqa: BLE001 — Digest degradiert, bricht nie
        return {"entry_mode": "unbekannt", "error": str(exc)}


def collect_paper_fills_24h(now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Fills/Closes der letzten 24h nach Quelle (Label-Join wie S1b-Report)."""
    now_utc = now or datetime.now(UTC)
    cutoff = now_utc - timedelta(hours=24)
    labels: dict[str, str] = {}
    rows = _read_jsonl_tail(_ARTIFACTS / "paper_execution_audit.jsonl")
    by_source: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("event_type") == "paper_trade_label":
            oid, src = r.get("order_id"), r.get("source_name") or r.get("feed_source")
            if isinstance(oid, str) and isinstance(src, str):
                labels[oid] = src
    for r in rows:
        et = r.get("event_type")
        if et not in ("order_filled", "position_closed"):
            continue
        ts = _parse_ts(r.get("timestamp_utc"))
        if ts is None or ts < cutoff:
            continue
        oid = r.get("order_id")
        src = labels.get(oid) if isinstance(oid, str) else None
        src = src or (r.get("source") if isinstance(r.get("source"), str) else "unlabeled")
        b = by_source.setdefault(str(src), {"fills": 0, "closes": 0, "pnl_usd": 0.0})
        if et == "order_filled":
            side = str(r.get("side") or "").lower()
            pos = str(r.get("position_side") or "long").lower()
            if (side == "buy" and pos == "long") or (side == "sell" and pos == "short"):
                b["fills"] += 1
        else:
            b["closes"] += 1
            for key in ("trade_pnl_usd", "realized_pnl_usd"):
                if r.get(key) is not None:
                    try:
                        b["pnl_usd"] += float(r[key])
                    except (TypeError, ValueError):
                        pass
                    break
    return by_source


def collect_bridge_stages_24h(now: datetime | None = None) -> dict[str, int]:
    now_utc = now or datetime.now(UTC)
    cutoff = now_utc - timedelta(hours=24)
    counts: dict[str, int] = {}
    for r in _read_jsonl_tail(_ARTIFACTS / "bridge_pending_orders.jsonl"):
        if not str(r.get("source", "")).startswith("telegram_premium"):
            continue
        ts = _parse_ts(r.get("timestamp_utc"))
        if ts is None or ts < cutoff:
            continue
        stage = str(r.get("stage", "?"))
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def collect_shadow_funnel() -> dict[str, Any] | None:
    rows = _read_jsonl_tail(_ARTIFACTS / "shadow_real_feed_funnel.jsonl", max_lines=50)
    for r in reversed(rows):
        if r.get("enabled"):
            return r
    return rows[-1] if rows else None


def collect_shadow_report() -> dict[str, Any]:
    """real_resolved & Co. über die kanonische CLI (eine Berechnungsquelle)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.cli.main", "trading", "shadow-report", "--json"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        parsed = json.loads(proc.stdout)
        return parsed if isinstance(parsed, dict) else {"error": "unexpected payload"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def collect_generator_edge() -> dict[str, Any]:
    """autonomous_generator resolved_count + Edge-Gate-Schwelle über die
    kanonische CLI (gleiche Quelle wie der Edge-Report). Read-only.

    Liefert {min_resolved, autonomous_generator_resolved, ..._verdict} oder
    {error}. Der Meilenstein hängt am AUSGEFÜHRTEN Generator-Strom, nicht an
    der shadow-resolved Forward-Zahl (Operator-Vorgabe 2026-06-14)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.cli.main", "trading", "generator-edge"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        parsed = json.loads(proc.stdout)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if not isinstance(parsed, dict):
        return {"error": "unexpected payload"}
    out: dict[str, Any] = {
        "min_resolved": (parsed.get("gate_config") or {}).get(
            "min_resolved", EDGE_GATE_MIN_RESOLVED
        ),
    }
    for prof in parsed.get("profiles") or []:
        if prof.get("cohort_key") == "autonomous_generator":
            out["autonomous_generator_resolved"] = prof.get("resolved_count")
            out["autonomous_generator_verdict"] = prof.get("verdict")
            break
    return out


def collect_d227() -> dict[str, Any]:
    try:
        from app.alerts.blocked_outcome_report import build_blocked_outcome_report

        report = build_blocked_outcome_report()
        return report if isinstance(report, dict) else json.loads(json.dumps(report, default=str))
    except Exception:
        # Fallback: CLI (gleiche Quelle, nur teurer).
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.cli.main",
                    "alerts",
                    "blocked-outcome-report",
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            parsed = json.loads(proc.stdout)
            return parsed if isinstance(parsed, dict) else {"error": "unexpected payload"}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}


def collect_v5_freshness() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, fn in (("funding", "funding_cache.json"), ("oi", "oi_cache.json")):
        p = _ARTIFACTS / fn
        out[name] = (
            round((datetime.now(UTC).timestamp() - p.stat().st_mtime) / 60, 1)
            if p.exists()
            else None
        )
    return out


def collect_promotion_gate(target: str = "paper") -> dict[str, Any]:
    """Tägliche Routine-Konsultation des fail-closed Promotion-Gates (MUST-USE
    Punkt 2, 2026-06-11): würde eine risiko-erhöhende Promotion auf ``target``
    JETZT durchgehen? Read-only; das Gate ändert nie Zustand. Exit≠0 == BLOCKED
    (fail-closed by design)."""
    out_path = _ARTIFACTS / "promotion_gate_decision.json"
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.cli.main",
                "trading",
                "promotion-check",
                "--target",
                target,
                "--out",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        decision: dict[str, Any] = {}
        if out_path.exists():
            try:
                decision = json.loads(out_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                decision = {}
        return {
            "target": target,
            "allowed": proc.returncode == 0,
            "status": decision.get("status"),
            "reason_codes": decision.get("reason_codes") or decision.get("reasons") or [],
        }
    except Exception as exc:  # noqa: BLE001
        return {"target": target, "error": str(exc)}


# ── Compose (pure, getestet) ─────────────────────────────────────────────────


def compose_digest_message(
    *,
    today: date,
    runtime: dict[str, Any],
    fills_by_source: dict[str, dict[str, Any]],
    bridge_stages: dict[str, int],
    shadow_funnel: dict[str, Any] | None,
    shadow_report: dict[str, Any],
    generator_edge: dict[str, Any],
    d227: dict[str, Any],
    v5_freshness: dict[str, Any],
    v5_activated_on: date,
    promotion: dict[str, Any] | None = None,
) -> str:
    """Baut die EINE lesbare Operator-Nachricht. Pure Funktion — testbar."""
    lines: list[str] = [f"📡 *KAI Operator-Digest* — {today.isoformat()}"]

    # Runtime / Modus (D-233).
    mode = runtime.get("entry_mode", "unbekannt")
    routes = runtime.get("open_routes") or []
    if runtime.get("contradictions"):
        lines.append(f"🛑 *Modus:* {mode} — KONTRADIKTION: {', '.join(runtime['contradictions'])}")
    else:
        route_str = ", ".join(routes) if routes else "keine Route offen"
        lines.append(f"⚙️ *Modus:* {mode} · offen: {route_str}")

    # Paper-Lernströme 24h.
    if fills_by_source:
        parts = []
        for src, b in sorted(fills_by_source.items()):
            pnl = b.get("pnl_usd", 0.0)
            pnl_str = f", PnL {pnl:+.0f}$" if b.get("closes") else ""
            parts.append(f"{src}: {b.get('fills', 0)} Fills/{b.get('closes', 0)} Closes{pnl_str}")
        lines.append("📒 *Paper 24h:* " + " · ".join(parts))
    else:
        lines.append("📒 *Paper 24h:* keine Fills/Closes")

    # Premium-Bridge 24h.
    if bridge_stages:
        top = sorted(bridge_stages.items(), key=lambda kv: -kv[1])[:4]
        lines.append("📨 *Premium-Bridge 24h:* " + " · ".join(f"{s}: {n}" for s, n in top))
    else:
        lines.append("📨 *Premium-Bridge 24h:* keine Premium-Events")

    # Shadow-Real-Funnel (#175).
    if shadow_funnel and shadow_funnel.get("enabled"):
        in_loop = shadow_funnel.get("in_loop") or {}
        lines.append(
            "🔬 *Shadow-Feed:* "
            f"seen {shadow_funnel.get('seen', '?')} → eligible {shadow_funnel.get('eligible', '?')} "
            f"→ injiziert {shadow_funnel.get('injected', '?')} → Kandidat "
            f"{in_loop.get('shadow_candidate_written', '?')} "
            f"(prio-rej {in_loop.get('priority_rejected', '?')})"
        )
    else:
        lines.append("🔬 *Shadow-Feed:* aus / noch kein armed Tick")

    # D-227 kompakt.
    if "error" not in d227:
        raw = d227.get("raw_events_count", "?")
        distinct = d227.get("distinct_document_id_count", "?")
        lines.append(f"🧾 *D-227:* {raw} Events / {distinct} Docs (Details im Pull-Report)")
    else:
        lines.append(f"🧾 *D-227:* Fehler — {d227['error'][:80]}")

    # Promotion-Gate Routine-Check (MUST-USE Punkt 2): das fail-closed Gate
    # wird täglich konsultiert statt nur ad-hoc vor Promotionen.
    if promotion is not None:
        if promotion.get("error"):
            lines.append(f"🛡️ *Promotion-Gate:* nicht prüfbar — {str(promotion['error'])[:80]}")
        elif promotion.get("allowed"):
            lines.append(
                f"🛡️ *Promotion-Gate (→{promotion.get('target', '?')}):* ALLOWED — "
                "eine Promotion wäre aktuell nicht durch Bleed/Unknown-Positionen blockiert."
            )
        else:
            reasons = ", ".join(str(r) for r in (promotion.get("reason_codes") or [])[:4])
            lines.append(
                f"🛡️ *Promotion-Gate (→{promotion.get('target', '?')}):* BLOCKED"
                + (f" — {reasons}" if reasons else " (fail-closed)")
            )

    # Wöchentlicher D-227-Auswertungs-Loop (MUST-USE Punkt 1): montags die
    # Block-Reason-Präzision als Entscheidungs-Futter für Gate-/Threshold-
    # Reviews — die Entscheidung selbst bleibt beim Operator.
    if today.isoweekday() == 1 and "error" not in d227:
        axis = d227.get("hit_miss_by_block_reason") or []
        reviewable = [
            r
            for r in axis
            if isinstance(r, dict)
            and (r.get("resolved") or 0) >= 5
            and r.get("precision_pct") is not None
        ]
        if reviewable:
            reviewable.sort(key=lambda r: -(r.get("resolved") or 0))
            lines.append("📊 *D-227-Wochenreview (Block-Reason → Precision):*")
            for r in reviewable[:5]:
                lines.append(
                    f"  • {r.get('block_reason')}: {r.get('precision_pct')}% "
                    f"({r.get('hit')}/{r.get('resolved')} hits, n={r.get('resolved')})"
                )
            lines.append(
                "  ↳ hohe Precision bei hartem Block = Kandidat für Gate-Review; "
                "niedrige bestätigt den Block."
            )

    # V5-Evidence-Frische.
    fund_age, oi_age = v5_freshness.get("funding"), v5_freshness.get("oi")
    if fund_age is not None:
        v5_state = "frisch" if fund_age < 60 else f"STALE ({fund_age:.0f} min)"
        lines.append(
            f"🧲 *V5-Evidence:* funding {v5_state}, OI {oi_age if oi_age is not None else '?'} min"
        )
    else:
        lines.append("🧲 *V5-Evidence:* Cache fehlt")

    # ── Meilensteine (Auto-Erkennung, Operator-Vorgabe 2026-06-11) ──
    lines.append("")
    lines.append("🎯 *Meilensteine:*")
    v5_day = (today - v5_activated_on).days
    if v5_day >= V5_REVIEW_AFTER_DAYS:
        lines.append(
            f"  ➡️ *V5-Auswertung FÄLLIG* (Tag {v5_day}/{V5_REVIEW_AFTER_DAYS}): "
            "Shadow-Logs (funding/oi_evidence_shadow.jsonl) gegen Outcomes auswerten, "
            "dann trust-Entscheidung (0.5 → ?)."
        )
    else:
        lines.append(f"  • V5-Messphase: Tag {v5_day}/{V5_REVIEW_AFTER_DAYS}")

    # Edge-Report-Meilenstein: feuert auf AUSGEFÜHRTE Generator-Closes
    # (autonomous_generator erreicht das Edge-Gate min_resolved), NICHT auf
    # shadow-resolved n. shadow-n bleibt als sichtbarer Kontext, damit die
    # Diskrepanz (Forward-Samples ≫ geschlossene Trades) nicht verschwindet
    # (Operator-Vorgabe 2026-06-14).
    shadow_n = shadow_report.get("real_resolved")
    shadow_ctx = f"shadow-resolved n={shadow_n}" if isinstance(shadow_n, int) else "shadow-n n/a"
    gate = generator_edge.get("min_resolved", EDGE_GATE_MIN_RESOLVED)
    gen_resolved = generator_edge.get("autonomous_generator_resolved")
    if isinstance(gen_resolved, int):
        if gen_resolved >= gate:
            lines.append(
                f"  ➡️ *EDGE-REPORT FÄLLIG*: autonomous_generator resolved n={gen_resolved}≥{gate} "
                "(Edge-Gate erreicht) — `trading generator-edge` / `trading edge-report` für "
                f"belastbares Verdict fahren. [{shadow_ctx}]"
            )
        else:
            verdict = generator_edge.get("autonomous_generator_verdict", "?")
            lines.append(
                f"  • Edge-Beweis: autonomous_generator resolved n={gen_resolved}/{gate} "
                f"(Verdict: {verdict}) · {shadow_ctx}"
            )
    else:
        lines.append(
            f"  • Edge-Beweis: generator-edge nicht lesbar "
            f"({generator_edge.get('error', '?')}) · {shadow_ctx}"
        )

    msg = "\n".join(lines)
    if len(msg) > _TELEGRAM_LIMIT:
        msg = msg[: _TELEGRAM_LIMIT - 25] + "\n… (gekürzt, 4096-Limit)"
    return msg


# ── Send ─────────────────────────────────────────────────────────────────────


# Telegram-Legacy-Markdown-Härtung: dynamische Bezeichner tragen `_` (z.B.
# paper_learning, autonomous_generator), und der Digest nutzt `[…]` als Klartext-
# Klammern — beides sind Markdown-Entities, die den Parser brechen ("Bad Request:
# can't parse entities"). Strukturell genutzt werden NUR *bold* und `code` (deren
# Spans enthalten kein `_`/`[`); daher escapen wir die NICHT-strukturellen
# Specials `_ [ ]` im fertigen Text, ohne Bold/Code zu beschädigen.
def _telegram_safe(text: str) -> str:
    return text.replace("_", "\\_").replace("[", "\\[").replace("]", "\\]")


def send_telegram(message: str) -> bool:
    token = os.environ.get("ALERT_TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("ALERT_TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("ALERT_TELEGRAM_TOKEN/CHAT_ID fehlen — kein Versand")
        return False
    import httpx

    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": _telegram_safe(message), "parse_mode": "Markdown"},
        timeout=15,
    )
    ok = resp.status_code == 200
    if not ok:
        logger.error("telegram send failed: %s %s", resp.status_code, resp.text[:200])
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Täglicher KAI Operator-Digest")
    parser.add_argument("--dry-run", action="store_true", help="nur drucken, nicht senden")
    args = parser.parse_args(argv)
    try:
        v5_activated_on = date.fromisoformat(
            os.environ.get("APP_V5_EVIDENCE_ACTIVATED_AT", "2026-06-11")
        )
        message = compose_digest_message(
            today=datetime.now(UTC).date(),
            runtime=collect_runtime(),
            fills_by_source=collect_paper_fills_24h(),
            bridge_stages=collect_bridge_stages_24h(),
            shadow_funnel=collect_shadow_funnel(),
            shadow_report=collect_shadow_report(),
            generator_edge=collect_generator_edge(),
            d227=collect_d227(),
            v5_freshness=collect_v5_freshness(),
            promotion=collect_promotion_gate(),
            v5_activated_on=v5_activated_on,
        )
    except Exception:  # noqa: BLE001 — entrypoint boundary
        logger.exception("digest compose failed")
        return 1
    if args.dry_run:
        print(message)
        return 0
    return 0 if send_telegram(message) else 1


if __name__ == "__main__":
    raise SystemExit(main())
