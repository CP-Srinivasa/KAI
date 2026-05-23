#!/usr/bin/env python3
"""EOW Priority-Scoring Decision Snapshot — DS-20260520-NEW-1 follow-up.

Reads Pi-local audit streams and emits a markdown snapshot to
/mnt/kai-data/eow_snapshots/priority_scoring_eow_snapshot_<date>.md
for the Operator EOW-Review.

Designed to run as a one-shot via `at` at 2026-05-23 07:00 UTC
(09:00 CEST). Pure read-only — no DB/state mutation.

Compares against the 2026-05-20 Mid-Window baseline:
  p>=10: 418 entries, 43.5% directional (50% neutral, 36% bullish, 7% bearish, 6% mixed)
  p=8/9: 475 entries, 87.8% directional
  p<8 : 917 entries, 31.3% directional
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path("/home/kai/ai_analyst_trading_bot")
ARTIFACTS = REPO / "artifacts"
OUT_DIR = Path("/mnt/kai-data/eow_snapshots")
TODAY = datetime.now(UTC).date()
OUT_FILE = OUT_DIR / f"priority_scoring_eow_snapshot_{TODAY.isoformat()}.md"

WINDOW_DAYS = 7
WINDOW_START = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_ts(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=UTC)
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def cross_tab_alert_audit() -> str:
    """Priority x Sentiment cross-tab over last WINDOW_DAYS, alert_audit.jsonl."""
    path = ARTIFACTS / "alert_audit.jsonl"
    total = 0
    in_window = 0
    with_sentiment = 0
    buckets: dict[str, Counter] = {
        "p>=10": Counter(),
        "p=8/9": Counter(),
        "p<8": Counter(),
    }
    for rec in iter_jsonl(path):
        total += 1
        ts = parse_ts(rec.get("timestamp_utc") or rec.get("dispatched_at") or rec.get("annotated_at") or rec.get("timestamp") or rec.get("ts") or rec.get("created_at") or rec.get("decided_at"))
        if ts and ts < WINDOW_START:
            continue
        in_window += 1
        sentiment = (rec.get("sentiment_label") or "").lower()
        if sentiment not in ("bullish", "bearish", "neutral", "mixed"):
            continue
        with_sentiment += 1
        priority = rec.get("priority")
        if priority is None:
            continue
        if priority >= 10:
            bucket = "p>=10"
        elif priority >= 8:
            bucket = "p=8/9"
        else:
            bucket = "p<8"
        buckets[bucket][sentiment] += 1

    lines = [f"## Cross-Tab Priority x Sentiment (alert_audit, letzte {WINDOW_DAYS}d)\n"]
    lines.append(f"- alert_audit total entries: {total}")
    lines.append(f"- in window ({WINDOW_DAYS}d): {in_window}")
    lines.append(f"- with sentiment_label: {with_sentiment}\n")
    lines.append("| Bucket | n | directional% | bullish | bearish | neutral | mixed |")
    lines.append("|---|---|---|---|---|---|---|")
    for name in ("p>=10", "p=8/9", "p<8"):
        c = buckets[name]
        n = sum(c.values())
        directional = c["bullish"] + c["bearish"]
        pct = f"{(directional / n * 100):.1f}%" if n else "—"
        lines.append(
            f"| {name} | {n} | {pct} | "
            f"{c['bullish']} | {c['bearish']} | {c['neutral']} | {c['mixed']} |"
        )

    baseline = [
        "",
        "**Baseline 2026-05-20 (Mid-Window):**",
        "- p>=10: 418 / 43.5% / b151 / br31 / n209 / m27",
        "- p=8/9: 475 / 87.8% / b354 / br63 / n14 / m44",
        "- p<8 : 917 / 31.3% / b171 / br116 / n367 / m263",
    ]
    return "\n".join(lines + baseline) + "\n"


def stream_growth(name: str, path: Path) -> str:
    if not path.exists():
        return f"- **{name}**: file missing ({path})"
    total = 0
    in_window = 0
    last_ts = None
    for rec in iter_jsonl(path):
        total += 1
        ts = parse_ts(
            rec.get("timestamp_utc")
            or rec.get("dispatched_at")
            or rec.get("annotated_at")
            or rec.get("timestamp")
            or rec.get("ts")
            or rec.get("created_at")
            or rec.get("decided_at")
        )
        if ts:
            last_ts = ts if last_ts is None or ts > last_ts else last_ts
            if ts >= WINDOW_START:
                in_window += 1
    last_str = last_ts.isoformat(timespec="seconds") if last_ts else "—"
    return f"- **{name}**: total={total} | letzte {WINDOW_DAYS}d=+{in_window} | last={last_str}"


def audit_streams() -> str:
    streams = [
        ("bayes_confidence_audit", ARTIFACTS / "bayes_confidence_audit.jsonl"),
        ("bayes_posterior_audit", ARTIFACTS / "bayes_posterior_audit.jsonl"),
        ("premium_signal_actions", ARTIFACTS / "premium_signal_actions.jsonl"),
        ("source_confluence_audit", ARTIFACTS / "source_confluence_audit.jsonl"),
        ("alert_outcomes", ARTIFACTS / "alert_outcomes.jsonl"),
    ]
    lines = [f"## Audit-Stream-Wachstum (letzte {WINDOW_DAYS}d)\n"]
    for name, path in streams:
        lines.append(stream_growth(name, path))
    return "\n".join(lines) + "\n"


def outcome_distribution() -> str:
    path = ARTIFACTS / "alert_outcomes.jsonl"
    counts: Counter = Counter()
    in_window = 0
    notes_backfill = 0
    for rec in iter_jsonl(path):
        ts = parse_ts(
            rec.get("annotated_at")
            or rec.get("timestamp_utc")
            or rec.get("timestamp")
            or rec.get("decided_at")
        )
        if ts and ts < WINDOW_START:
            continue
        in_window += 1
        outcome = rec.get("outcome") or rec.get("status") or "unknown"
        counts[outcome] += 1
        note = (rec.get("note") or "").lower()
        if "backfill" in note:
            notes_backfill += 1
    lines = [f"## Alert-Outcomes-Verteilung (letzte {WINDOW_DAYS}d)\n"]
    lines.append(f"- in window total: {in_window}")
    lines.append(f"- davon mit backfill-note: {notes_backfill}")
    for outcome, n in counts.most_common():
        pct = f"{(n / in_window * 100):.1f}%" if in_window else "—"
        lines.append(f"- `{outcome}`: {n} ({pct})")
    return "\n".join(lines) + "\n"


def env_snapshot() -> str:
    env_path = REPO / ".env"
    keys_of_interest = [
        "EXECUTION_PAPER_MIN_PRIORITY",
        "RISK_BAYES_CONFIDENCE_ENABLED",
        "RISK_BAYES_CONFIDENCE_SHADOW_ONLY",
        "RE_ENTRY_MODE_ENABLED",
        "RISK_MAX_OPEN_POSITIONS",
        "LIVE_MODE",
    ]
    lines = ["## .env-Snapshot (Decision-Relevant)\n"]
    if not env_path.exists():
        lines.append(f"- .env not found at {env_path}")
        return "\n".join(lines) + "\n"
    values = {}
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k in keys_of_interest:
            values[k] = v.strip().strip('"').strip("'")
    for k in keys_of_interest:
        lines.append(f"- `{k}` = `{values.get(k, '<unset>')}`")
    return "\n".join(lines) + "\n"


def git_head() -> str:
    try:
        head = subprocess.check_output(
            ["git", "-C", str(REPO), "log", "-1", "--oneline"],
            stderr=subprocess.STDOUT,
            timeout=5,
        ).decode().strip()
        branch = subprocess.check_output(
            ["git", "-C", str(REPO), "branch", "--show-current"],
            stderr=subprocess.STDOUT,
            timeout=5,
        ).decode().strip()
        return f"## Repo-Stand\n\n- branch: `{branch}`\n- HEAD: `{head}`\n"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"## Repo-Stand\n\n- git read failed: {e}\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    header = (
        f"# Priority-Scoring EOW-Snapshot — {TODAY.isoformat()}\n\n"
        f"**Generated:** {now.isoformat(timespec='seconds')} (UTC)\n"
        f"**Pi-Host:** {os.uname().nodename}\n"
        f"**Window:** last {WINDOW_DAYS} days "
        f"({WINDOW_START.date().isoformat()} -> {TODAY.isoformat()})\n\n"
        f"**Decision-Options:** Brief-A (6. Faktor) | A' (Penalty, PR #58) | "
        f"Brief-B (separater trade_priority_score) | Brief-C (Bridge-Workaround) | "
        f"Brief-D (Status quo bis 2026-05-30)\n\n"
        f"Querverweise: `artifacts/operator_memos/priority_scoring_inspection_2026-05-20.md`, "
        f"`artifacts/operator_memos/priority_scoring_decision_brief_2026-05-23.md`, "
        f"`artifacts/operator_memos/priority_sentiment_penalty_patch_2026-05-21.md`.\n\n---\n\n"
    )
    sections = [
        git_head(),
        env_snapshot(),
        cross_tab_alert_audit(),
        audit_streams(),
        outcome_distribution(),
    ]
    OUT_FILE.write_text(header + "\n".join(sections), encoding="utf-8")
    print(f"wrote {OUT_FILE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
