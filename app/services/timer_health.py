"""Timer Health Service — reads systemd-timer health audits (DALI-P-101)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# FS-2 (2026-06-08, #198): timer taxonomy. A timer reported "inactive" is NOT
# uniformly a fault — a one-shot timer pinned to a fixed past date (e.g.
# kai-risk-gate-audit-review OnCalendar=2026-06-04 16:00:00) is EXPECTED inactive
# after it fired, while a recurring timer (wildcard OnCalendar / repeating
# OnBootSec / OnUnitActiveSec) being inactive is a real fault. Categories:
#   recurring_required        — must stay active(waiting); inactive => critical
#   one_shot_expected_inactive — fixed past date; inactive after run is OK
#   disabled_by_design        — no trigger at all
TIMER_CATEGORIES = (
    "recurring_required",
    "one_shot_expected_inactive",
    "disabled_by_design",
)

# A fixed single-date OnCalendar (starts with YYYY-MM-DD, no wildcard) is a
# one-shot. Wildcards (``*``) make it recurring.
_FIXED_DATE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\b")


def classify_timer_schedule(
    oncalendar: str | None,
    onboot: str | None = None,
    onactive: str | None = None,
) -> str:
    """Pure: map a timer's schedule fields to a taxonomy category.

    Fail-SAFE: when the schedule is unknown/ambiguous we return
    ``recurring_required`` so a genuinely-stuck timer is never silently excused
    as "expected inactive".
    """
    cal = (oncalendar or "").strip()
    if cal:
        if "*" in cal:
            return "recurring_required"
        if _FIXED_DATE_RE.match(cal):
            return "one_shot_expected_inactive"
        # Named/relative calendar without wildcard (e.g. "weekly") — recurring.
        return "recurring_required"
    if (onboot or "").strip() or (onactive or "").strip():
        # Relative timers re-arm on boot / after activation → treat as recurring.
        return "recurring_required"
    return "disabled_by_design"


def _find_timer_file(base: str) -> Path | None:
    """Locate deploy/systemd/<base>.timer by walking up from this module.

    The deployed layout puts ``deploy/`` at the repo root; the local/worktree
    nesting differs, so we search upward rather than hard-coding a parents[N].
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "deploy" / "systemd" / f"{base}.timer"
        if candidate.is_file():
            return candidate
    return None


def _read_timer_schedule(unit: str) -> tuple[str | None, str | None, str | None] | None:
    """Read OnCalendar/OnBootSec/OnUnitActiveSec from deploy/systemd/<unit>.timer.

    Returns ``None`` when the .timer file cannot be found/read (so the caller
    fails SAFE to recurring_required rather than excusing an unknown timer).
    """
    try:
        # ``unit`` may carry a ".timer"/".service" suffix or a trailing state.
        base = unit.strip().split(" ", 1)[0]
        base = base.removesuffix(".timer").removesuffix(".service")
        timer_file = _find_timer_file(base)
        if timer_file is None:
            return None
        text = timer_file.read_text(encoding="utf-8")
    except Exception:
        return None
    cal = re.search(r"^\s*OnCalendar=(.+)$", text, re.MULTILINE)
    boot = re.search(r"^\s*OnBootSec=(.+)$", text, re.MULTILINE)
    active = re.search(r"^\s*OnUnitActiveSec=(.+)$", text, re.MULTILINE)
    return (
        cal.group(1).strip() if cal else None,
        boot.group(1).strip() if boot else None,
        active.group(1).strip() if active else None,
    )


def timer_category(unit: str) -> str:
    """Taxonomy category for a timer unit, derived from its .timer schedule.

    Fail-SAFE: an unresolvable unit is ``recurring_required`` (never silently
    excused as expected-inactive)."""
    schedule = _read_timer_schedule(unit)
    if schedule is None:
        return "recurring_required"
    return classify_timer_schedule(*schedule)


def _get_default_total() -> int:
    """Dynamically count kai-*.timer units in deploy/systemd as default timer count."""
    default_total = 10  # Standard fallback
    try:
        workspace_root = Path(__file__).resolve().parents[3]
        deploy_dir = workspace_root / "deploy" / "systemd"
        if deploy_dir.is_dir():
            default_total = len(list(deploy_dir.glob("kai-*.timer")))
    except Exception:
        pass
    return default_total


def read_latest_timer_audit(path: Path) -> dict[str, Any]:
    """Read the latest entry from the timer health audit JSONL file and return state.

    Fehlertolerant:
    - Datei fehlt oder leer -> state="no_data"
    - Letzte Zeile korrupt -> state="corrupt" mit Fallback auf vorletzte Zeile
    - checked_at älter als 2h -> state="stale" (auch wenn inactive=0)
    - FS-2 taxonomy: each inactive timer is categorised (recurring_required /
      one_shot_expected_inactive / disabled_by_design). A recurring/failed timer
      that is inactive -> state="critical"; an expected-inactive one-shot (fixed
      past date) does NOT raise an alarm.
    - sonst -> state="ok"
    """
    default_total = _get_default_total()

    default_response: dict[str, Any] = {
        "state": "no_data",
        "checked_at": None,
        "stale_minutes": None,
        "total": default_total,
        "active": default_total,
        "inactive": [],
    }

    if not path.exists():
        return default_response

    lines = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    lines.append(s)
    except Exception:
        return default_response

    if not lines:
        return default_response

    parsed_data = None
    is_corrupt = False

    # Versuche den letzten Eintrag
    try:
        parsed_data = json.loads(lines[-1])
    except Exception:
        is_corrupt = True
        # Fallback auf vorletzten Eintrag
        if len(lines) > 1:
            try:
                parsed_data = json.loads(lines[-2])
            except Exception:
                parsed_data = None
        else:
            parsed_data = None

    if parsed_data is None:
        return {
            "state": "corrupt",
            "checked_at": None,
            "stale_minutes": None,
            "total": default_total,
            "active": default_total,
            "inactive": [],
        }

    # Bestimme checked_at
    checked_at_str = parsed_data.get("timestamp_utc")
    checked_at = None
    stale_minutes = None
    state = "ok"

    if checked_at_str:
        try:
            checked_at = datetime.fromisoformat(checked_at_str.replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=UTC)
            checked_at = checked_at.astimezone(UTC)

            now = datetime.now(UTC)
            diff = now - checked_at
            stale_minutes = int(diff.total_seconds() // 60)
            if diff.total_seconds() > 7200:  # 2 Stunden = 7200 Sekunden
                state = "stale"
        except Exception:
            pass

    # Parse inaktive Timer aus findings
    raw_findings = parsed_data.get("findings", [])
    inactive_timers = []

    for f in raw_findings:
        if not isinstance(f, str) or not f.strip():
            continue
        unit_name = f
        unit_state = "inactive"
        if " (" in f and f.endswith(")"):
            parts = f.rsplit(" (", 1)
            unit_name = parts[0]
            unit_state = parts[1][:-1]

        category = timer_category(unit_name)
        # Per-timer severity (FS-2): a systemd-failed unit is always critical; a
        # one-shot that fired and went inactive is expected; a disabled-by-design
        # timer is fine; a recurring timer being inactive/failed is critical.
        if unit_state == "failed":
            severity = "critical"
        elif category == "one_shot_expected_inactive":
            severity = "expected_inactive"
        elif category == "disabled_by_design":
            severity = "ok"
        else:  # recurring_required and inactive
            severity = "critical"

        inactive_timers.append(
            {
                "unit": unit_name,
                "state": unit_state,
                "category": category,
                "severity": severity,
                "last_trigger": None,
            }
        )

    # Resolve total und active
    total_from_audit = parsed_data.get("total_timers")
    active_from_audit = parsed_data.get("active_timers")

    total = default_total
    if total_from_audit is not None:
        try:
            total = int(total_from_audit)
        except Exception:
            pass
    elif active_from_audit is not None:
        try:
            total = int(active_from_audit) + len(inactive_timers)
        except Exception:
            pass

    if len(inactive_timers) > total:
        total = len(inactive_timers)
    active = total - len(inactive_timers)

    # FS-2 taxonomy counts.
    critical_count = sum(1 for t in inactive_timers if t.get("severity") == "critical")
    expected_inactive_count = sum(
        1 for t in inactive_timers if t.get("severity") == "expected_inactive"
    )

    # Status-Priorisierung (FS-2): an expected-inactive one-shot (e.g. a fixed-
    # date timer that already fired) must NOT raise an alarm. Only genuinely-
    # stuck recurring timers / failed units are critical; everything else is ok.
    if is_corrupt:
        state = "corrupt"
    elif state != "stale":
        state = "critical" if critical_count > 0 else "ok"

    if state in ("corrupt", "no_data"):
        severity = "warning"
    elif state == "critical":
        severity = "critical"
    elif state == "stale":
        severity = "warning"
    else:
        severity = "ok"

    return {
        "state": state,
        "severity": severity,
        "checked_at": checked_at_str,
        "stale_minutes": stale_minutes,
        "total": total,
        "active": active,
        "critical_count": critical_count,
        "expected_inactive_count": expected_inactive_count,
        "inactive": inactive_timers,
    }


def timers_warranting_alert(result: dict[str, Any]) -> list[str]:
    """Pure: units that justify an ACTIVE operator alert (FS-2).

    Only critical recurring/failed timers — never an expected-inactive one-shot
    nor a disabled-by-design timer. The caller is responsible for dedupe and the
    actual push (a 1×/day deduped Telegram alert); this function only decides
    WHICH units qualify so the policy is unit-testable without any I/O.
    """
    out: list[str] = []
    for t in result.get("inactive", []) or []:
        if isinstance(t, dict) and t.get("severity") == "critical":
            unit = t.get("unit")
            if isinstance(unit, str) and unit:
                out.append(unit)
    return out
