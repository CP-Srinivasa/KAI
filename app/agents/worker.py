"""Agent conversation worker.

Tails `artifacts/agents/{slug}/commands.jsonl` and `conversation.jsonl`
for SENTR / Watchdog / Architect, executes queued commands, and appends
agent replies into the unified conversation stream.

Runs as a background process alongside the FastAPI server. One loop handles
all three agents. Replies are deterministic, rule-based; there is no LLM
call here — the agents remain Claude-Code-only for *new* features, but
routine check/report/inspect/review/propose runs are automated to close
the user-visible feedback loop (Dashboard + Telegram see replies within
~5 seconds).

Start:  python -m app.agents.worker --loop
Oneshot: python -m app.agents.worker --once
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.api.routers.agents import _AGENTS, _agent_dir, append_conversation_event

logger = logging.getLogger(__name__)

_STATE_FILENAME = "worker_state.json"
_POLL_INTERVAL_S = 5.0
_MESSAGE_ACK_COOLDOWN_S = 60


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_jsonl_raw(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _rewrite_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _append_finding(slug: str, severity: str, title: str, detail: str) -> None:
    p = _agent_dir(slug) / "findings.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": _now_iso(), "severity": severity, "title": title, "detail": detail}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _append_run(slug: str, mode: str, result: str, duration_ms: int, **extra: Any) -> None:
    p = _agent_dir(slug) / "runs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _now_iso(),
        "mode": mode,
        "result": result,
        "duration_ms": duration_ms,
        **extra,
    }
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Agent handlers — each returns (summary_text, extra_findings, result_code).
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


def _hours_since(path: Path) -> float | None:
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return (datetime.now(UTC) - mtime).total_seconds() / 3600.0


def _watchdog_check(note: str | None) -> tuple[str, str]:
    checks: list[tuple[str, str, str]] = []  # (severity, title, detail)
    targets = [
        ("alert_audit", REPO_ROOT / "artifacts" / "alert_audit.jsonl", 6.0),
        ("trading_loop_audit", REPO_ROOT / "artifacts" / "trading_loop_audit.jsonl", 6.0),
        (
            "hold_report",
            REPO_ROOT / "artifacts" / "ph5_hold" / "ph5_hold_metrics_report.json",
            48.0,
        ),
    ]
    for label, path, warn_h in targets:
        h = _hours_since(path)
        if h is None:
            checks.append(("warn", f"{label}_missing", f"{path.name} fehlt"))
        elif h > warn_h:
            checks.append(
                ("warn", f"{label}_stale", f"{path.name} ist {h:.1f}h alt (Schwelle {warn_h}h)"),
            )
        else:
            checks.append(("info", f"{label}_fresh", f"{path.name} vor {h:.1f}h aktualisiert"))

    warns = sum(1 for s, _, _ in checks if s == "warn")
    for sev, title, detail in checks:
        _append_finding("watchdog", sev, title, detail)

    lines = [f"Watchdog check abgeschlossen ({len(checks)} Checks, {warns} Warn)."]
    if note:
        lines.append(f"Note: {note}")
    for sev, title, detail in checks:
        tag = "OK" if sev == "info" else "WARN"
        lines.append(f"  [{tag}] {title} — {detail}")
    return ("\n".join(lines), "warn" if warns else "ok")


def _watchdog_report(note: str | None) -> tuple[str, str]:
    findings = _read_jsonl_raw(_agent_dir("watchdog") / "findings.jsonl")[-20:]
    runs = _read_jsonl_raw(_agent_dir("watchdog") / "runs.jsonl")[-10:]
    by_sev: dict[str, int] = {}
    for f in findings:
        by_sev[f.get("severity", "info")] = by_sev.get(f.get("severity", "info"), 0) + 1
    sev_str = ", ".join(f"{k}={v}" for k, v in sorted(by_sev.items())) or "keine"
    lines = [
        f"Watchdog report — {len(findings)} letzte Findings ({sev_str}), {len(runs)} letzte Runs.",
    ]
    if note:
        lines.append(f"Note: {note}")
    lines.append("Letzte 5 Findings:")
    for f in findings[-5:]:
        detail = str(f.get("detail", ""))[:120]
        lines.append(f"  [{f.get('severity','?')}] {f.get('title','?')} — {detail}")
    return ("\n".join(lines), "ok")


_HARDCODED_KEY_RE = re.compile(r"sk-[A-Za-z0-9]{20,}")


def _run_pip_audit() -> tuple[str, list[str], int]:
    """Run pip-audit and return (severity, detail_lines, vuln_count)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--format", "json", "--progress-spinner", "off"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        vulns: list[dict[str, Any]] = []
        if result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    vulns = data.get("dependencies", [])
                elif isinstance(data, list):
                    vulns = data
            except json.JSONDecodeError:
                pass

        affected = [v for v in vulns if v.get("vulns")]
        if not affected:
            return ("info", ["pip-audit: 0 bekannte Schwachstellen"], 0)

        detail: list[str] = []
        total = 0
        for dep in affected:
            name = dep.get("name", "?")
            version = dep.get("version", "?")
            for vuln in dep.get("vulns", []):
                vid = vuln.get("id", "?")
                fix = vuln.get("fix_versions", [])
                fix_str = f" (fix: {', '.join(fix)})" if fix else ""
                detail.append(f"  {name}=={version}: {vid}{fix_str}")
                total += 1
        severity = "crit" if total >= 3 else "warn"
        return (severity, [f"pip-audit: {total} Schwachstelle(n)"] + detail[:10], total)
    except subprocess.TimeoutExpired:
        return ("info", ["pip-audit: Timeout (>120s)"], 0)
    except FileNotFoundError:
        return ("info", ["pip-audit: nicht installiert"], 0)


def _sentr_inspect(note: str | None) -> tuple[str, str]:
    app_dir = REPO_ROOT / "app"
    leak_hits: list[str] = []
    scanned = 0
    for p in app_dir.rglob("*.py"):
        scanned += 1
        try:
            if _HARDCODED_KEY_RE.search(p.read_text(encoding="utf-8", errors="ignore")):
                leak_hits.append(str(p.relative_to(REPO_ROOT)))
        except OSError:
            continue

    env_in_gitignore = False
    gi = REPO_ROOT / ".gitignore"
    if gi.exists():
        env_in_gitignore = any(
            line.strip() == ".env" for line in gi.read_text(encoding="utf-8").splitlines()
        )

    _append_finding(
        "sentr",
        "info" if not leak_hits else "crit",
        "hardcoded_openai_keys",
        f"{len(leak_hits)} Treffer in {scanned} Python-Files"
        + (f" ({leak_hits[:3]})" if leak_hits else ""),
    )
    _append_finding(
        "sentr",
        "info" if env_in_gitignore else "warn",
        "env_gitignored",
        ".env in .gitignore" if env_in_gitignore else ".env NICHT in .gitignore",
    )

    audit_sev, audit_lines, vuln_count = _run_pip_audit()
    _append_finding("sentr", audit_sev, "pip_audit", audit_lines[0])

    worst = "ok"
    if leak_hits:
        worst = "crit"
    elif audit_sev == "crit":
        worst = "crit"
    elif audit_sev == "warn":
        worst = "warn"

    lines = [
        f"SENTR inspect — {scanned} Python-Files gescannt.",
        f"  Hardcoded sk-keys: {len(leak_hits)}",
        f"  .env in .gitignore: {'ja' if env_in_gitignore else 'nein'}",
        f"  {audit_lines[0]}",
    ]
    if vuln_count:
        lines.extend(audit_lines[1:])
    if note:
        lines.append(f"Note: {note}")
    if leak_hits:
        lines.append("  Betroffen: " + ", ".join(leak_hits[:5]))
    return ("\n".join(lines), worst)


def _sentr_report(note: str | None) -> tuple[str, str]:
    findings = _read_jsonl_raw(_agent_dir("sentr") / "findings.jsonl")[-15:]
    crit = sum(1 for f in findings if f.get("severity") == "crit")
    warn = sum(1 for f in findings if f.get("severity") == "warn")
    lines = [f"SENTR report — {len(findings)} Findings (crit={crit}, warn={warn})."]
    if note:
        lines.append(f"Note: {note}")
    for f in findings[-5:]:
        lines.append(
            f"  [{f.get('severity','?')}] {f.get('title','?')} — {str(f.get('detail',''))[:120]}"
        )
    return ("\n".join(lines), "warn" if warn or crit else "ok")


def _architect_review(note: str | None) -> tuple[str, str]:
    modules = list((REPO_ROOT / "app").glob("*/"))
    modules = [m for m in modules if m.is_dir() and not m.name.startswith("_")]
    total_py = sum(1 for _ in (REPO_ROOT / "app").rglob("*.py"))

    try:
        lint = subprocess.run(
            ["python", "-m", "ruff", "check", "app", "--output-format", "concise"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        ruff_lines = lint.stdout.splitlines() if lint.returncode != 0 else []
        ruff_summary = f"ruff: {len(ruff_lines)} Findings" if ruff_lines else "ruff: clean"
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        ruff_summary = f"ruff: nicht ausgefuehrt ({exc.__class__.__name__})"

    _append_finding(
        "architect",
        "info",
        "module_count",
        f"{len(modules)} Top-Level-Module in app/, {total_py} .py-Files gesamt",
    )
    _append_finding("architect", "info", "lint_status", ruff_summary)

    lines = [
        "Architect review abgeschlossen.",
        f"  Module in app/: {len(modules)} ({', '.join(sorted(m.name for m in modules)[:8])}...)",
        f"  Python-Files: {total_py}",
        f"  {ruff_summary}",
    ]
    if note:
        lines.append(f"Note: {note}")
    return ("\n".join(lines), "ok")


def _architect_propose(note: str | None) -> tuple[str, str]:
    note_txt = note or "(keine konkrete Frage)"
    lines = [
        "Architect propose — ohne konkreten Kontext nur generische Leitplanken:",
        "  - Neue Features erst nach Quality-Bar (Precision >= 60% oder Kombi-Metriken).",
        "  - Keine neuen Sprint-Kontrakt-Dokumente, nur DECISION_LOG-Zeilen.",
        "  - Kleine, testbare Schritte; Auditierbarkeit vor Ergonomie.",
        f"Kontext: {note_txt}",
    ]
    _append_finding(
        "architect",
        "info",
        "propose_handled",
        f"Generische Antwort generiert ({note_txt[:80]})",
    )
    return ("\n".join(lines), "ok")


HANDLERS: dict[tuple[str, str], Callable[[str | None], tuple[str, str]]] = {
    ("watchdog", "check"): _watchdog_check,
    ("watchdog", "report"): _watchdog_report,
    ("sentr", "inspect"): _sentr_inspect,
    ("sentr", "report"): _sentr_report,
    ("architect", "review"): _architect_review,
    ("architect", "propose"): _architect_propose,
}


# ---------------------------------------------------------------------------
# Tailer loop
# ---------------------------------------------------------------------------


def _load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _process_agent(slug: str, state: dict[str, Any]) -> int:
    """Process queued commands + unanswered free-text for one agent. Returns handled count."""
    d = _agent_dir(slug)
    if not d.exists():
        return 0

    # 1) Commands: handle all still-queued entries, flip to done.
    cmd_path = d / "commands.jsonl"
    changed = 0
    if cmd_path.exists():
        rows = _read_jsonl_raw(cmd_path)
        dirty = False
        for row in rows:
            if row.get("status") != "queued":
                continue
            mode = row.get("mode", "")
            note = row.get("note")
            handler = HANDLERS.get((slug, mode))
            t0 = time.monotonic()
            if handler is None:
                summary = f"{slug}: mode `{mode}` hat keinen Worker-Handler."
                result = "skipped"
            else:
                try:
                    summary, result = handler(note)
                except Exception as exc:  # noqa: BLE001
                    summary = f"{slug}/{mode} fehlgeschlagen: {exc.__class__.__name__}: {exc}"
                    result = "error"
                    logger.exception("handler failed for %s/%s", slug, mode)
            dur = int((time.monotonic() - t0) * 1000)

            append_conversation_event(
                slug,
                source="agent",
                role="agent",
                content=summary,
                kind="report",
                meta={"command_id": row.get("id"), "mode": mode, "result": result},
            )
            _append_run(slug, mode, result, dur, command_id=row.get("id"), note=note)
            row["status"] = "done"
            row["done_ts"] = _now_iso()
            row["result"] = result
            dirty = True
            changed += 1
        if dirty:
            _rewrite_jsonl(cmd_path, rows)

    # 2) Free-text operator messages without agent reply: auto-ack once per cooldown.
    conv_path = d / "conversation.jsonl"
    if conv_path.exists():
        events = _read_jsonl_raw(conv_path)
        last_ack_ts = state.setdefault("last_ack", {}).get(slug)
        last_op_msg = None
        for ev in reversed(events):
            if ev.get("kind") == "message" and ev.get("role") == "operator":
                last_op_msg = ev
                break
        if last_op_msg is not None:
            # Is there any agent reply newer than this message?
            op_ts = str(last_op_msg.get("ts", ""))
            has_newer_agent_reply = any(
                ev.get("role") == "agent" and str(ev.get("ts", "")) > op_ts
                for ev in events
            )
            if not has_newer_agent_reply and last_ack_ts != op_ts:
                content = str(last_op_msg.get("content", ""))[:200]
                modes = ", ".join(_AGENTS[slug].modes)
                ack = (
                    f"Nachricht erhalten: \"{content[:120]}\".\n"
                    f"Freitext wird derzeit nicht inhaltlich beantwortet.\n"
                    f"Sende `!{_AGENTS[slug].modes[0]}` oder `!{modes.split(', ')[-1]}` "
                    f"(ggf. mit Note) fuer eine Aktion."
                )
                append_conversation_event(
                    slug,
                    source="agent",
                    role="agent",
                    content=ack,
                    kind="message",
                    meta={"auto_ack": True, "in_reply_to": last_op_msg.get("id")},
                )
                state["last_ack"][slug] = op_ts
                changed += 1
    return changed


def run_once(state_path: Path) -> int:
    state = _load_state(state_path)
    total = 0
    for slug in _AGENTS:
        try:
            total += _process_agent(slug, state)
        except Exception:
            logger.exception("agent loop failed for %s", slug)
    _save_state(state_path, state)
    return total


def run_loop(state_path: Path, interval: float = _POLL_INTERVAL_S) -> None:
    logger.info("agent-worker starting (interval=%.1fs)", interval)
    while True:
        try:
            handled = run_once(state_path)
            if handled:
                logger.info("agent-worker tick handled=%d", handled)
        except KeyboardInterrupt:
            logger.info("agent-worker stopping (keyboard interrupt)")
            return
        except Exception:
            logger.exception("agent-worker tick failed")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KAI Agent Conversation Worker")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--loop", action="store_true", help="run continuously")
    parser.add_argument("--interval", type=float, default=_POLL_INTERVAL_S)
    parser.add_argument(
        "--state",
        type=Path,
        default=REPO_ROOT / "artifacts" / "agents" / _STATE_FILENAME,
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.loop:
        run_loop(args.state, interval=args.interval)
        return 0
    # default: once
    handled = run_once(args.state)
    logger.info("agent-worker oneshot handled=%d", handled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
