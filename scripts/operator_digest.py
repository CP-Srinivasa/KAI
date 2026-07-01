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
die Nachricht nur (Test-/Lokal-Pfad, kein Netz). Read-only gegenüber KAI-Zustand;
persistiert nur die eigene Reminder-Kadenz (operator_digest_milestone_state.json),
um tägliches FÄLLIG-Rauschen zu vermeiden — und nur bei echtem Versand, nicht bei
``--dry-run``.
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
# httpx loggt auf INFO die volle Request-URL — beim Telegram-Send enthielte das
# den Bot-Token (…/bot<TOKEN>/sendMessage) im journald. Auf WARNING heben, damit
# das Secret nicht in die Logs leakt; eigene Digest-Logs bleiben INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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

# Once a milestone threshold is crossed, re-nudge only on MATERIAL new evidence or
# the weekly cadence — NOT every single day. A daily "FÄLLIG" that never changes is
# zero-information noise; this trades daily nagging for state-delta triggering
# (ADR-0012 attention-hygiene, 2026-07-01). State lives in its own bookkeeping file.
MILESTONE_CADENCE_DAYS = 7
_MILESTONE_STATE_PATH = _ARTIFACTS / "operator_digest_milestone_state.json"


def _days_between(iso_a: str | None, iso_b: str) -> int | None:
    """Whole days from date ``iso_a`` to ``iso_b``; None if a is missing/unparseable."""
    if not iso_a:
        return None
    try:
        a = date.fromisoformat(iso_a[:10])
        b = date.fromisoformat(iso_b[:10])
    except (ValueError, TypeError):
        return None
    return (b - a).days


def v5_reminder_due(
    *,
    v5_day: int,
    state: dict[str, Any],
    today_iso: str,
    after_days: int = V5_REVIEW_AFTER_DAYS,
    cadence_days: int = MILESTONE_CADENCE_DAYS,
) -> bool:
    """Fire the V5 FÄLLIG nudge only past the review window AND (first time OR the
    weekly cadence has elapsed). No cheap per-day change signal exists for V5, so
    cadence is the trigger — it stops the daily repeat, not the reminder itself."""
    if v5_day < after_days:
        return False
    last_iso = state.get("last_iso")
    if not last_iso:
        return True
    days_since = _days_between(str(last_iso), today_iso)
    return days_since is None or days_since >= cadence_days


def edge_reminder_due(
    *,
    gen_resolved: int,
    gate: int,
    state: dict[str, Any],
    today_iso: str,
    min_delta: int,
    cadence_days: int = MILESTONE_CADENCE_DAYS,
) -> bool:
    """Fire the EDGE-REPORT FÄLLIG nudge only at/above the gate AND (first crossing
    OR >= ``min_delta`` NEW closes since the last nudge OR the weekly cadence
    elapsed). Sitting at the same n day after day stays quiet."""
    if gen_resolved < gate:
        return False
    last_n = state.get("last_n")
    if last_n is None:
        return True
    try:
        if gen_resolved - int(last_n) >= min_delta:
            return True
    except (TypeError, ValueError):
        return True
    last_iso = state.get("last_iso")
    days_since = _days_between(str(last_iso) if last_iso else None, today_iso)
    return days_since is None or days_since >= cadence_days


def _load_milestone_state(path: Path = _MILESTONE_STATE_PATH) -> dict[str, Any]:
    """Read reminder-cadence bookkeeping (never raises; {} if absent/corrupt)."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_milestone_state(state: dict[str, Any], path: Path = _MILESTONE_STATE_PATH) -> None:
    """Persist reminder-cadence bookkeeping (best-effort; never raises)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("could not persist milestone state to %s", path)


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


def collect_edge_discovery(research_dir: Path | None = None) -> dict[str, Any]:
    """Latest edge-discovery run summary from ``artifacts/research/``. Read-only.

    Returns ``{"available": False}`` if no run exists yet, ``{"error": ...}`` on
    failure, else a compact summary of the most recent ``edge_search_*.json``
    (timeframe, lookback, symbols, survivors, best rule, cumulative count). The
    engine searches for a tradable edge on own OHLCV; zero survivors is the
    honest expected outcome, not an error.
    """
    base = research_dir if research_dir is not None else _ARTIFACTS / "research"
    try:
        runs = sorted(base.glob("edge_search_*.json"))
        if not runs:
            return {"available": False}
        doc = json.loads(runs[-1].read_text(encoding="utf-8"))
        hyps = doc.get("hypotheses") or []
        survivors = sum(int(h.get("symbols_survived", 0)) for h in hyps)
        best = max(hyps, key=lambda h: h.get("mean_net_bps", float("-inf")), default=None)
        return {
            "available": True,
            "timeframe": doc.get("timeframe"),
            "lookback_days": doc.get("lookback_days"),
            "n_symbols": len(doc.get("symbols") or []),
            "n_hypotheses": len(hyps),
            "survivors": survivors,
            "cumulative_tested": doc.get("hypotheses_tested_cumulative"),
            "best_name": best.get("name") if best else None,
            "best_mean_bps": best.get("mean_net_bps") if best else None,
        }
    except Exception as exc:  # noqa: BLE001 — Digest degradiert, bricht nie
        return {"error": str(exc)}


def collect_source_lifecycle(ranking_path: Path | None = None) -> dict[str, Any]:
    """Latest source-lifecycle ranking summary from ``monitor/source_ranking.json``.

    Read-only. ``{"available": False}`` if the recalc job hasn't written it yet,
    ``{"error": ...}`` on failure, else the counts (ranked/provisional/pinned/
    rotation_flagged) plus the current top source. Most sources are honestly
    ``provisional`` (n below the validated floor) — that is the expected state,
    not an error.
    """
    path = ranking_path if ranking_path is not None else Path("monitor/source_ranking.json")
    try:
        if not path.exists():
            return {"available": False}
        doc = json.loads(path.read_text(encoding="utf-8"))
        ranked = doc.get("ranked") or []
        top = ranked[0] if ranked and isinstance(ranked[0], dict) else None
        return {
            "available": True,
            "counts": doc.get("counts") or {},
            "top_name": top.get("source_name") if top else None,
            "top_wilson": top.get("wilson_lower_95") if top else None,
            "top_provisional": top.get("provisional") if top else None,
        }
    except Exception as exc:  # noqa: BLE001 — Digest degradiert, bricht nie
        return {"error": str(exc)}


def collect_source_discovery(
    proposals_path: Path | None = None,
    runs_path: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Autonomer Discovery-Loop-Stand aus den monitor-Files (read-only, fail-soft).

    ``{"available": False}`` solange weder ein Lauf noch eine Probation-Quelle
    existiert. Zeigt sonst: Vorschläge-Zahl, Quellen in Probation (+ wie viele nahe
    der Graduation: ≥ Run-Schwelle), letzten Lauf (mode/onboardet/eingewechselt)
    und ob die Schleife scharf ist. Datei-basiert — kein DB-Zugriff im Digest.
    """
    out: dict[str, Any] = {"available": False, "discovery_enabled": False, "scout_enabled": False}
    try:
        from app.core.settings import SourceSettings

        cfg = SourceSettings()
        out["discovery_enabled"] = bool(cfg.discovery_enabled)
        out["scout_enabled"] = bool(cfg.scout_enabled)
    except Exception:  # noqa: BLE001 — Digest degradiert, bricht nie
        pass

    try:
        from app.learning.source_graduation import DEFAULT_MIN_PROBATION_RUNS

        min_runs = int(DEFAULT_MIN_PROBATION_RUNS)
    except Exception:  # noqa: BLE001
        min_runs = 3

    pp = proposals_path or Path("monitor/source_proposals.jsonl")
    rp = runs_path or Path("monitor/source_discovery_runs.jsonl")
    sp = state_path or Path("monitor/source_probation_state.json")

    out["proposals"] = len(_read_jsonl_tail(pp))
    runs = _read_jsonl_tail(rp)
    last = runs[-1] if runs else None
    if last:
        out["available"] = True
        out["last_mode"] = last.get("mode")
        out["last_onboarded"] = last.get("onboarded")
        out["last_swaps"] = last.get("swaps_executed")
    try:
        sdata = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
        runs_map = sdata.get("runs", {}) if isinstance(sdata, dict) else {}
        out["probation"] = len(runs_map)
        out["near_graduation"] = sum(
            1 for v in runs_map.values() if isinstance(v, (int, float)) and v >= min_runs
        )
        if runs_map:
            out["available"] = True
    except (OSError, ValueError):
        pass
    return out


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
    edge_discovery: dict[str, Any] | None = None,
    source_lifecycle: dict[str, Any] | None = None,
    source_discovery: dict[str, Any] | None = None,
    milestone_state: dict[str, Any] | None = None,
) -> str:
    """Baut die EINE lesbare Operator-Nachricht. Testbar.

    ``milestone_state`` (Reminder-Kadenz-Bookkeeping) steuert die threshold-
    getriggerten FÄLLIG-Nudges: fehlt es (``None``), feuert jeder Meilenstein wie
    früher bei jeder Schwellenüberschreitung; wird es übergeben, feuert der Nudge
    nur bei materieller Änderung / Wochenkadenz und wird bei Feuer in-place
    fortgeschrieben (der Aufrufer persistiert es).
    """
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
    # Block-Reason-Präzision als KONTEXT (nicht als Gate-Lockerungs-Trigger).
    # Diese Precision ist über einen langen, variablen Batch-Horizont gemessen
    # (Median ~Stunden) und ist KEIN handelbarer Edge — Gate-Entscheidungen
    # brauchen side-adjusted Median-Return vs. Kosten. Befund 2026-06-22
    # (blocked-cohort-vetting). Die Entscheidung bleibt beim Operator.
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
                "  ↳ ACHTUNG: Precision hier = Richtung über langen, variablen "
                "Batch-Horizont (Median Stunden), KEIN handelbarer Edge und "
                "allein KEIN Grund für Gate-Lockerung. Tradeable Edge nur via "
                "side-adjusted Median-Return gegen Kostenhürde belegbar (separat)."
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

    # ── Meilensteine (Auto-Erkennung; threshold-getriggert seit 2026-07-01) ──
    lines.append("")
    lines.append("🎯 *Meilensteine:*")
    state = milestone_state if milestone_state is not None else {}
    today_iso = today.isoformat()

    v5_day = (today - v5_activated_on).days
    v5_state = state.get("v5") if isinstance(state.get("v5"), dict) else {}
    if v5_reminder_due(v5_day=v5_day, state=v5_state, today_iso=today_iso):
        lines.append(
            f"  ➡️ *V5-Auswertung FÄLLIG* (Tag {v5_day}/{V5_REVIEW_AFTER_DAYS}): "
            "Shadow-Logs (funding/oi_evidence_shadow.jsonl) gegen Outcomes auswerten, "
            "dann trust-Entscheidung (0.5 → ?)."
        )
        if milestone_state is not None:
            milestone_state["v5"] = {"last_iso": today_iso, "day": v5_day}
    elif v5_day >= V5_REVIEW_AFTER_DAYS:
        last = v5_state.get("last_iso", "?")
        lines.append(
            f"  • V5-Auswertung ruht (Tag {v5_day}/{V5_REVIEW_AFTER_DAYS}; zuletzt erinnert "
            f"{last}, nächster Nudge nach {MILESTONE_CADENCE_DAYS}d)"
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
        edge_state = state.get("edge") if isinstance(state.get("edge"), dict) else {}
        min_delta = max(1, gate // 2)
        if edge_reminder_due(
            gen_resolved=gen_resolved,
            gate=gate,
            state=edge_state,
            today_iso=today_iso,
            min_delta=min_delta,
        ):
            lines.append(
                f"  ➡️ *EDGE-REPORT FÄLLIG*: autonomous_generator resolved n={gen_resolved}≥{gate} "
                "(Edge-Gate erreicht) — `trading generator-edge` / `trading edge-report` für "
                f"belastbares Verdict fahren. [{shadow_ctx}]"
            )
            if milestone_state is not None:
                milestone_state["edge"] = {"last_iso": today_iso, "last_n": gen_resolved}
        elif gen_resolved >= gate:
            last_n = edge_state.get("last_n", "?")
            lines.append(
                f"  • Edge-Report ruht: n={gen_resolved}≥{gate} ohne materiellen Zuwachs seit "
                f"letzter Erinnerung (n={last_n}); Nudge bei +{min_delta} Closes oder "
                f"{MILESTONE_CADENCE_DAYS}d · {shadow_ctx}"
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

    # Edge-Discovery-Engine: systematische Hypothesen-Suche auf eigenem OHLCV.
    ed = edge_discovery or {}
    if ed.get("error"):
        lines.append(f"🔎 *Edge-Discovery:* nicht lesbar — {str(ed['error'])[:80]}")
    elif not ed.get("available"):
        lines.append("🔎 *Edge-Discovery:* noch kein Lauf (`python -m app.research.runner`)")
    else:
        cum = ed.get("cumulative_tested")
        cum_str = f" · {cum} Configs kumulativ" if isinstance(cum, int) else ""
        lines.append(
            f"🔎 *Edge-Discovery:* {ed.get('timeframe', '?')}/{ed.get('lookback_days', '?')}d, "
            f"{ed.get('n_symbols', 0)} Symbole · "
            f"{ed.get('survivors', 0)}/{ed.get('n_hypotheses', 0)} Survivors{cum_str}"
        )
        best_name = ed.get("best_name")
        best_bps = ed.get("best_mean_bps")
        if best_name is not None and isinstance(best_bps, (int, float)):
            if ed.get("survivors", 0) == 0:
                lines.append(
                    f"  • kein robuster Edge — beste Regel {best_name} {best_bps:+.1f}bps netto"
                )
            else:
                lines.append(
                    f"  ➡️ *KANDIDAT(EN) PRÜFEN* — beste Regel {best_name} {best_bps:+.1f}bps netto"
                )

    # Source-Lifecycle: Quellen-Ranking + Rotation/Pin-Flags aus dem recalc-Job.
    sl = source_lifecycle or {}
    if sl.get("error"):
        lines.append(f"📚 *Quellen-Lifecycle:* nicht lesbar — {str(sl['error'])[:80]}")
    elif not sl.get("available"):
        lines.append("📚 *Quellen-Lifecycle:* noch kein Ranking (source_lifecycle_recalc)")
    else:
        c = sl.get("counts") or {}
        top = sl.get("top_name")
        tw = sl.get("top_wilson")
        tw_str = f" @ {tw * 100:.0f}%" if isinstance(tw, (int, float)) else ""
        prov = " (provisorisch)" if sl.get("top_provisional") else ""
        lines.append(
            f"📚 *Quellen-Lifecycle:* {c.get('ranked', 0)} gerankt "
            f"({c.get('provisional', 0)} provisorisch, {c.get('pinned', 0)} pinned, "
            f"{c.get('rotation_flagged', 0)} Rotation-Flag)"
            + (f" · Top: {top}{tw_str}{prov}" if top else "")
        )

    # Source-Discovery: der autonome Loop (Scout → Probation → Graduation),
    # konsistent mit dem Dashboard-Panel „Quellen-Discovery".
    sd = source_discovery or {}
    if not sd.get("available"):
        lines.append("🔭 *Quellen-Discovery:* kein Lauf / Loop aus")
    else:
        armed = "scharf" if sd.get("discovery_enabled") else "Beobachtung"
        last_str = ""
        if sd.get("last_mode"):
            last_str = (
                f" · letzte Runde: {sd.get('last_onboarded', 0)} onboardet, "
                f"{sd.get('last_swaps', 0)} eingewechselt"
            )
        lines.append(
            f"🔭 *Quellen-Discovery ({armed}):* {sd.get('probation', 0)} in Probation "
            f"({sd.get('near_graduation', 0)} nahe Graduation) · "
            f"{sd.get('proposals', 0)} Vorschläge{last_str}"
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
        milestone_state = _load_milestone_state()
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
            edge_discovery=collect_edge_discovery(),
            source_lifecycle=collect_source_lifecycle(),
            source_discovery=collect_source_discovery(),
            v5_activated_on=v5_activated_on,
            milestone_state=milestone_state,
        )
    except Exception:  # noqa: BLE001 — entrypoint boundary
        logger.exception("digest compose failed")
        return 1
    if args.dry_run:
        print(message)
        return 0
    if send_telegram(message):
        _save_milestone_state(milestone_state)  # advance cadence only on a real send
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
