# Quelle: Repo scripts/workstation/kai_session_lagebild.py (MindBlow 2.0, W3). Hier NICHT editieren --
# im Repo aendern, dann: pwsh -File scripts/workstation/install_workstation.ps1 -Apply
"""KAI-Lagebild beim Sitzungsstart (SessionStart-Hook im Haupt-Checkout).

Ersetzt den vollen pytest-Lauf (~120 s) und die toten Skripte
daily_review_status.py / session_summary.py. Nur lesen, gemessen ~1,5 s:
Mainline-Tip (nach kurzem Fetch), Abstand des Haupt-Checkouts, offene PRs,
aktive Claims aus ACTIVE_CLAIMS.md. Jeder Teil darf scheitern, ohne den Rest
zu verhindern; Exit ist immer 0.

Hook (gitignorierte .claude/settings.json im Haupt-Checkout):
    python -X utf8 /c/Users/sasch/KAI-mirror/scripts/kai_session_lagebild.py 2>/dev/null || true
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(r"C:\Users\sasch\.local\bin\ai_analyst_trading_bot")
MAINLINE = "claude/p7/reentry-ia-codex-cycle"
GH_REPO = "CP-Srinivasa/KAI"
CLAIMS = Path(r"C:\Users\sasch\KAI-mirror\ACTIVE_CLAIMS.md")
OPEN_STATES = {"active", "open", "blocked", "paused", "uebergeben"}
LEASE = timedelta(hours=24)  # Regel (2) des Claim-Registers


def run(args: list[str], timeout: float = 3.0) -> str | None:
    try:
        done = subprocess.run(  # noqa: S603
            args,
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def short(text: str, width: int = 72) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def parse_ts(raw: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(raw.strip().strip("`"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def open_claims(text: str, now: datetime) -> tuple[list[str], list[str]]:
    """(aktive Zeilen, abgelaufene IDs). Ohne expires_at gilt created_at + 24 h."""
    active: list[str] = []
    stale: list[str] = []
    for line in text.splitlines():
        if not line.startswith("|") or line.startswith("|---") or "claim_id" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            continue
        state = cells[-1].replace("*", "").split(" ")[0].lower().rstrip(".,:")
        if state not in OPEN_STATES:
            continue
        expires = parse_ts(cells[-2])
        created = parse_ts(cells[-3])
        if expires is None and created is not None:
            expires = created + LEASE
        if expires is not None and expires < now:
            stale.append(cells[0])
            continue
        active.append(f"  {cells[0]} [{cells[1]}] {state} bis {cells[-2]}")
    return active, stale


def mainline_lines(now: datetime) -> list[str]:
    fetched = run(["git", "fetch", "-q", "origin", MAINLINE], timeout=4.0) is not None
    ref = f"origin/{MAINLINE}"
    tip = run(["git", "log", "-1", "--format=%h|%cI|%s", ref])
    if tip is None:
        return [f"Mainline {ref}: nicht lesbar"]
    sha, when, subject = tip.split("|", 2)
    age_h = (now - datetime.fromisoformat(when)).total_seconds() / 3600
    note = "" if fetched else " (Fetch fehlgeschlagen, lokaler Stand)"
    lines = [f"Mainline {sha} vor {age_h:.1f} h: {short(subject)}{note}"]
    head = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    counts = run(["git", "rev-list", "--left-right", "--count", f"HEAD...{ref}"])
    if head and counts:
        ahead, behind = counts.split()
        lines.append(
            f"Haupt-Checkout [{head}] {behind} hinter / {ahead} vor Mainline — "
            "Snapshot, nicht darin arbeiten; frischer Worktree (CLAUDE.local.md)"
        )
    return lines


def pr_lines() -> list[str]:
    raw = run(
        [
            "gh", "pr", "list", "--repo", GH_REPO, "--state", "open",
            "--json", "number,title,headRefName,isDraft",
        ],
        timeout=4.0,
    )  # fmt: skip
    if raw is None:
        return ["Offene PRs: gh nicht erreichbar"]
    prs = sorted(json.loads(raw), key=lambda p: p["number"])
    lines = [f"Offene PRs ({len(prs)}):"]
    for p in prs:
        draft = " [Draft]" if p["isDraft"] else ""
        lines.append(f"  #{p['number']}{draft} {p['headRefName']} — {short(p['title'], 60)}")
    return lines


def claim_lines(now: datetime) -> list[str]:
    try:
        text = CLAIMS.read_text(encoding="utf-8")
    except OSError:
        return ["Claims: ACTIVE_CLAIMS.md nicht lesbar"]
    active, stale = open_claims(text, now)
    out = [f"Aktive Claims ({len(active)}):", *active]
    if stale:
        out.append(f"  + {len(stale)} abgelaufen, nie geschlossen (frei; als expired markieren)")
    return out


def main() -> None:
    now = datetime.now(UTC)
    out = [f"KAI-Lagebild {now:%Y-%m-%d %H:%MZ} (SessionStart, nur lesend)"]
    parts: list[Callable[[], list[str]]] = [
        lambda: mainline_lines(now),
        pr_lines,
        lambda: claim_lines(now),
    ]
    for part in parts:
        try:
            out.extend(part())
        except Exception as exc:  # noqa: BLE001 — Lagebild darf nie den Start blockieren
            out.append(f"(Teil fehlgeschlagen: {type(exc).__name__})")
    print("\n".join(out))


if __name__ == "__main__":
    main()
