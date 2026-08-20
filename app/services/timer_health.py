"""Timer Health Service — reads systemd-timer health audits (DALI-P-101)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
        # Relative timers are recurring in INTENT. Note what this does NOT say:
        # that they reliably re-arm. The earlier comment here claimed exactly
        # that ("re-arm on boot / after activation") and it is false —
        # ``OnBootSec`` fires once per boot, ``OnUnitActiveSec`` anchors on the
        # SERVICE's last activation. A timer restarted long after boot whose
        # service has not run gets NO next elapse at all
        # (kai-tv-auto-promote, 2026-07-12 → 2026-08-19, five weeks silent).
        # Restart-safety is a separate property — see
        # ``has_restart_safe_initial_trigger``.
        return "recurring_required"
    return "disabled_by_design"


def has_restart_safe_initial_trigger(
    oncalendar: str | None,
    onactive_sec: str | None,
) -> bool:
    """Pure: does this timer get a fresh elapse whenever it is (re)started?

    Only two trigger kinds survive a restart:

    * ``OnCalendar=`` — wall-clock, independent of activation history.
    * ``OnActiveSec=`` — relative to the TIMER's own activation.

    ``OnBootSec=`` is anchored to boot and is spent afterwards; a timer restarted
    later never sees it again. ``OnUnitActiveSec=`` is anchored to the triggered
    SERVICE, so it provides nothing while that service is not running. A unit
    carrying only those two can end up ``enabled`` + ``active`` + never firing —
    the exact state that hid a dead promotion path for five weeks.
    """
    return bool((oncalendar or "").strip() or (onactive_sec or "").strip())


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
    # OnActiveSec is deliberately folded into the "boot" slot for classification:
    # both are initial triggers, and the taxonomy only asks "is this recurring".
    # The restart-safety question is answered separately by
    # ``has_restart_safe_initial_trigger``, which needs OnActiveSec distinctly.
    if not boot:
        boot = re.search(r"^\s*OnActiveSec=(.+)$", text, re.MULTILINE)
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


@dataclass(frozen=True)
class TimerRuntimeFacts:
    """Was systemd ueber einen Timer sagt — roh, ohne Deutung.

    Getrennte Next-Elapse-Felder, weil systemd fuer kalender- und monotone Timer
    getrennt rechnet: ein Kalender-Timer traegt ``NextElapseUSecRealtime``, ein
    monotoner ``NextElapseUSecMonotonic``. Ein Waechter, der nur eines davon
    liest, haelt die jeweils andere Haelfte des Bestands fuer terminlos.
    """

    unit: str
    enabled: bool
    active: bool
    next_elapse_realtime: str
    next_elapse_monotonic: str
    last_trigger_utc: datetime | None = None


def has_future_trigger(facts: TimerRuntimeFacts) -> bool:
    """Besitzt der Timer ueberhaupt einen naechsten Termin?

    systemd meldet Terminlosigkeit auf zwei Arten: leeres Realtime-Feld und
    ``infinity`` im Monotonic-Feld. Beide muessen zutreffen, damit der Timer
    wirklich keinen Termin hat — sonst wuerde jeder monotone Timer (leeres
    Realtime-Feld) faelschlich als tot gelten.
    """
    realtime = (facts.next_elapse_realtime or "").strip()
    monotonic = (facts.next_elapse_monotonic or "").strip().lower()
    has_realtime = bool(realtime) and realtime.lower() not in {"0", "infinity", "n/a"}
    has_monotonic = bool(monotonic) and monotonic not in {"0", "infinity", "n/a"}
    return has_realtime or has_monotonic


def find_unscheduled_recurring_timers(
    facts: Sequence[TimerRuntimeFacts],
    *,
    category_of: Callable[[str], str] = timer_category,
) -> list[str]:
    """INVARIANTE 1 (Scheduleability): laeuft wiederkehrend, hat aber keinen Termin.

    Genau der Zustand von ``kai-tv-auto-promote`` am 2026-08-19: ``enabled``,
    ``active``, ``NextElapseUSecMonotonic=infinity``, letzter Lauf fuenf Wochen
    zuvor. ``systemctl --failed`` zeigt ihn nicht (nichts ist gescheitert), und
    die Timer-Probe sammelt ``NON_ACTIVE`` (er WAR aktiv) — er faellt durch
    beide bestehenden Netze.

    Nur ``recurring_required`` wird geprueft: ein One-Shot nach seinem Termin
    besitzt legitim keinen naechsten, und ein Daueralarm darauf wuerde den Kanal
    entwerten.
    """
    return [
        f.unit
        for f in facts
        if f.enabled
        and f.active
        and category_of(f.unit) == "recurring_required"
        and not has_future_trigger(f)
    ]


def find_stalled_recurring_timers(
    facts: Sequence[TimerRuntimeFacts],
    *,
    now: datetime,
    expected_interval_s: Mapping[str, float],
    grace_factor: float = 3.0,
) -> list[str]:
    """INVARIANTE 2 (Cadence): Termin vorhanden, trotzdem laeuft nichts.

    Die erste Invariante faengt den terminlosen Timer. Sie faengt NICHT den Fall,
    in dem formal ein Termin existiert, der Lauf aber trotzdem ausbleibt — etwa
    weil die Unit dauerhaft scheitert und sofort neu terminiert wird, oder weil
    ein Zeitsprung die Rechnung verschoben hat.

    ``grace_factor`` ist bewusst grosszuegig: gemeldet wird erst, wenn das
    Dreifache der erwarteten Kadenz verstrichen ist. Ein flatternder Waechter
    wird ignoriert, und dann faellt der echte Befund mit durch.

    Timer ohne bekannte Erwartung und Timer ohne je erfolgten Lauf werden NICHT
    gemeldet — fuer sie ist die Frage unbeantwortbar, und Raten waere schlimmer
    als Schweigen.
    """
    stalled: list[str] = []
    for f in facts:
        interval = expected_interval_s.get(f.unit)
        if interval is None or interval <= 0 or f.last_trigger_utc is None:
            continue
        age_s = (now - f.last_trigger_utc).total_seconds()
        if age_s > interval * grace_factor:
            stalled.append(f.unit)
    return stalled


def parse_systemctl_show(output: str) -> list[TimerRuntimeFacts]:
    """``systemctl show <units> -p Id -p ...`` in Fakten uebersetzen — rein.

    ``systemctl show`` haengt die Property-Bloecke mehrerer Units aneinander.
    ``Id=`` beginnt jeweils einen neuen Block; ohne dieses Feld waere die
    Zuordnung geraten. Unvollstaendige Bloecke werden uebersprungen statt
    halb interpretiert.
    """
    facts: list[TimerRuntimeFacts] = []
    current: dict[str, str] = {}

    def flush() -> None:
        unit = current.get("Id", "").strip()
        if not unit:
            return
        raw_last = current.get("LastTriggerUSec", "").strip()
        last: datetime | None = None
        if raw_last and raw_last.lower() not in {"n/a", "0"}:
            for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S"):
                try:
                    last = datetime.strptime(raw_last, fmt).replace(tzinfo=UTC)
                    break
                except ValueError:
                    continue
        facts.append(
            TimerRuntimeFacts(
                unit=unit,
                enabled=current.get("UnitFileState", "").strip() == "enabled",
                active=current.get("ActiveState", "").strip() == "active",
                next_elapse_realtime=current.get("NextElapseUSecRealtime", ""),
                next_elapse_monotonic=current.get("NextElapseUSecMonotonic", ""),
                last_trigger_utc=last,
            )
        )

    for line in output.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "Id" and current:
            flush()
            current = {}
        current[key] = value
    if current:
        flush()
    return facts
