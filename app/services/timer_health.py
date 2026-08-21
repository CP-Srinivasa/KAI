"""Timer Health Service — reads systemd-timer health audits (DALI-P-101)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Container, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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
    triggered_unit: str = ""
    triggered_unit_active: bool = False

    def with_triggered_state(self, running_units: Container[str]) -> TimerRuntimeFacts:
        """Kopie, die weiss, ob der ausgeloeste Service gerade laeuft."""
        return replace(self, triggered_unit_active=self.triggered_unit in running_units)


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

    Ebenso uebergangen wird ein Timer, dessen Service GERADE laeuft:
    ``OnUnitActiveSec`` ankert auf der Aktivierung dieses Services und hat
    waehrenddessen nichts zu rechnen — systemd meldet ``infinity``, obwohl
    nichts kaputt ist. ``kai-shadow-resolver`` laeuft 13-14 min von je 30, ist
    also fast die halbe Zeit regulaer ohne Termin.
    """
    return [
        f.unit
        for f in facts
        if f.enabled
        and f.active
        and not f.triggered_unit_active
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


# ``systemctl show`` rendert Zeitstempel in der Zone des AUFRUFERS, nicht in
# UTC — auf kai-pi5 also ``CEST``. ``%Z`` parst das nicht (Python akzeptiert dort
# faktisch nur UTC/GMT/lokale Namen), und ein stillschweigend gescheiterter
# Zeitstempel ist als ``None`` von "nie gelaufen" nicht zu unterscheiden. Der
# Collector erzwingt darum ``TZ=UTC``; diese Tabelle deckt zusaetzlich den Fall
# ab, dass jemand die Ausgabe ohne dieses Env von Hand hereinreicht.
_ZONE_OFFSET_HOURS = {"UTC": 0, "GMT": 0, "CET": 1, "CEST": 2}


def parse_systemd_timestamp(raw: str) -> datetime | None:
    """``Fri 2026-08-21 04:40:00 CEST`` -> aware ``datetime`` in UTC.

    Gibt ``None`` zurueck, wenn der Wert fehlt (``n/a``/``0``) ODER die Zone
    unbekannt ist. Raten waere hier schlimmer als Schweigen: ein um eine Stunde
    verschobener Zeitstempel wuerde eine Kadenz-Aussage falsch machen, ohne dass
    es jemand sieht.
    """
    value = (raw or "").strip()
    if not value or value.lower() in {"n/a", "0"}:
        return None
    head, _, zone = value.rpartition(" ")
    offset = _ZONE_OFFSET_HOURS.get(zone.upper())
    if offset is None:
        head, offset = value, None
    for fmt in ("%a %Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(head.strip(), fmt)
        except ValueError:
            continue
        if offset is None:
            return None
        return (naive - timedelta(hours=offset)).replace(tzinfo=UTC)
    return None


def _show_blocks(output: str) -> list[dict[str, str]]:
    """``systemctl show`` in Property-Bloecke schneiden — rein.

    Die Grenze ist die LEERZEILE zwischen den Units, nicht ``Id=``. systemd
    gibt die Properties in seiner eigenen Reihenfolge aus, und ``Id`` steht
    dort real an vierter Stelle — hinter den NextElapse-Feldern. Wer an ``Id``
    schneidet, verwirft die Werte der ersten Unit und schiebt jeder weiteren
    die Werte ihres Nachfolgers unter (2026-08-21: 55 von 55 Fakten falsch).

    Ein wiederholter Schluessel ohne vorangegangene Leerzeile gilt als
    Notgrenze — lieber ein Block zu viel als zwei Units in einem.
    """
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            if current:
                blocks.append(current)
                current = {}
            continue
        key, _, value = line.partition("=")
        if key in current:
            blocks.append(current)
            current = {}
        current[key] = value
    if current:
        blocks.append(current)
    return blocks


def parse_active_units(output: str) -> set[str]:
    """Welche Units laufen gerade? — aus ``systemctl show -p Id -p ActiveState``.

    ``activating`` zaehlt mit: ein laufender ``Type=oneshot`` steht waehrend
    seines ExecStart genau dort und nie in ``active``.
    """
    running = {"active", "activating", "reloading", "deactivating"}
    return {
        b["Id"].strip()
        for b in _show_blocks(output)
        if b.get("Id", "").strip() and b.get("ActiveState", "").strip() in running
    }


def parse_systemctl_show(output: str) -> list[TimerRuntimeFacts]:
    """``systemctl show <units> -p Id -p ...`` in Fakten uebersetzen — rein.

    Bloecke ohne ``Id`` werden uebersprungen statt halb interpretiert.
    """
    facts: list[TimerRuntimeFacts] = []
    for block in _show_blocks(output):
        unit = block.get("Id", "").strip()
        if not unit:
            continue
        last = parse_systemd_timestamp(block.get("LastTriggerUSec", ""))
        facts.append(
            TimerRuntimeFacts(
                unit=unit,
                enabled=block.get("UnitFileState", "").strip() == "enabled",
                active=block.get("ActiveState", "").strip() == "active",
                next_elapse_realtime=block.get("NextElapseUSecRealtime", ""),
                next_elapse_monotonic=block.get("NextElapseUSecMonotonic", ""),
                last_trigger_utc=last,
                triggered_unit=block.get("Unit", "").strip(),
            )
        )
    return facts


@dataclass(frozen=True)
class BrokerState:
    """Was ueber das NOPASSWD-Ziel bekannt ist — roh, ohne Deutung."""

    path: str
    exists: bool
    owner: str = ""
    group: str = ""
    mode: str = ""
    matches_repo_artifact: bool = False


def evaluate_privilege_broker(state: BrokerState) -> str | None:
    """Befundtext, wenn das privilegierte Ziel nicht vertrauenswuerdig ist.

    Vorfall 2026-08-20: die sudoers-Policy erlaubte passwortfrei genau
    ``/usr/local/sbin/kai-service-control`` — und die Datei existierte nicht.
    Damit war jeder passwortfreie privilegierte Pfad tot, inklusive der
    Auto-Recovery des Service-Watchdogs. Die Sicherheit ist dabei in die richtige
    Richtung gescheitert (fail-closed), aber KAI dokumentierte wochenlang eine
    Faehigkeit, die live nicht existierte.

    Drei Zustaende sind gleichermassen ein Befund:

    * **fehlt** — die Policy zeigt ins Leere.
    * **falscher Eigentuemer/Mode** — ist das Ziel fuer ``ubuntu`` schreibbar,
      ist NOPASSWD darauf exakt so viel wert wie ``NOPASSWD:ALL``. Der INHALT
      ist das Privileg, nicht der Dateiname.
    * **Drift zum Repo-Artefakt** — installiert laeuft etwas anderes als das,
      was geprueft und freigegeben wurde.
    """
    if not state.exists:
        return (
            f"Privilegien-Broker {state.path} FEHLT, aber die sudoers-Policy erlaubt "
            "ihn passwortfrei. Jeder passwortfreie privilegierte Pfad ist damit tot — "
            "inklusive der Auto-Recovery des Service-Watchdogs. Installieren via "
            "`sudo bash scripts/pi_install_systemd.sh`."
        )
    if (state.owner, state.group, state.mode) != ("root", "root", "755"):
        return (
            f"Privilegien-Broker {state.path} hat "
            f"{state.owner}:{state.group} {state.mode}, erwartet root:root 755. "
            "Ein fuer den Service-User schreibbares NOPASSWD-Ziel ist gleichbedeutend "
            "mit NOPASSWD:ALL."
        )
    if not state.matches_repo_artifact:
        return (
            f"Privilegien-Broker {state.path} weicht vom Repo-Artefakt ab. "
            "Installiert laeuft damit anderer Code als der gepruefte."
        )
    return None
