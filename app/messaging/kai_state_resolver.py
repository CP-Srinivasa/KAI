"""KAI State Resolver — Python pendant for the TS resolver in web/src/kai/stateResolver.ts.

Spec: docs/kai_persona/technical_ui_pack_v3_2.md §3, §4
Same priority order, same fail-closed rules. Backend uses this for /status responses,
Telegram-Bot rendering, audit events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Priority is unverhandelbar (per spec):
# ERROR > WARNING > SIGNAL > SECURITY > ANALYSIS > IDLE > OFFLINE.
KAI_STATE_PRIORITY: dict[str, int] = {
    "ERROR": 100,
    "WARNING": 90,
    "SIGNAL": 80,
    "SECURITY": 70,
    "ANALYSIS": 50,
    "IDLE": 10,
    "OFFLINE": 0,
}

KAI_STATE_COLOR: dict[str, str] = {
    "IDLE": "#00B8D9",
    "ANALYSIS": "#00E5FF",
    "SIGNAL": "#FF2BD6",
    "WARNING": "#FF6B00",
    "SECURITY": "#00FFA3",
    "ERROR": "#FF1744",
    "OFFLINE": "#64748B",
}

KAI_STATE_ICON: dict[str, str] = {
    "IDLE": "kai_idle",
    "ANALYSIS": "kai_analysis",
    "SIGNAL": "kai_signal",
    "WARNING": "kai_warning",
    "SECURITY": "kai_security",
    "ERROR": "kai_error",
    "OFFLINE": "kai_offline",
}

KAI_STATE_ANIMATION: dict[str, str] = {
    "IDLE": "idle_loop",
    "ANALYSIS": "data_scan",
    "SIGNAL": "signal_found_pulse",
    "WARNING": "warning_glitch",
    "SECURITY": "security_scan",
    "ERROR": "error_screen_tear",
    "OFFLINE": "static_fade",
}

KAI_STATUS_LABEL: dict[str, str] = {
    "IDLE": "IDLE",
    "ANALYSIS": "SCANNING",
    "SIGNAL": "SIGNAL FOUND",
    "WARNING": "WARNING",
    "SECURITY": "SECURITY CHECK",
    "ERROR": "ERROR",
    "OFFLINE": "OFFLINE",
}


@dataclass(frozen=True)
class KaiRuntimeState:
    state: str
    severity: str
    priority: int
    status_label: str
    color: str
    icon: str
    animation: str
    comment: str
    timestamp: str
    source: str | None = None
    next_action: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "state": self.state,
            "severity": self.severity,
            "priority": self.priority,
            "statusLabel": self.status_label,
            "color": self.color,
            "icon": self.icon,
            "animation": self.animation,
            "comment": self.comment,
            "timestamp": self.timestamp,
        }
        if self.source is not None:
            out["source"] = self.source
        if self.next_action is not None:
            out["nextAction"] = self.next_action
        if self.extra:
            out.update(self.extra)
        return out


def is_valid_kai_state(value: object) -> bool:
    return isinstance(value, str) and value in KAI_STATE_PRIORITY


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_fallback_state(state: str, comment: str) -> KaiRuntimeState:
    if not is_valid_kai_state(state):
        state = "OFFLINE"
    return KaiRuntimeState(
        state=state,
        severity="critical" if state == "ERROR" else "unknown" if state == "OFFLINE" else "info",
        priority=KAI_STATE_PRIORITY[state],
        status_label=KAI_STATUS_LABEL[state],
        color=KAI_STATE_COLOR[state],
        icon=KAI_STATE_ICON[state],
        animation=KAI_STATE_ANIMATION[state],
        comment=comment,
        timestamp=_now_iso(),
        source="fallback",
    )


def fail_closed_state(reason: str) -> KaiRuntimeState:
    """Force ERROR with critical severity — never silently degrade to IDLE/OK."""
    return KaiRuntimeState(
        state="ERROR",
        severity="critical",
        priority=KAI_STATE_PRIORITY["ERROR"],
        status_label=KAI_STATUS_LABEL["ERROR"],
        color=KAI_STATE_COLOR["ERROR"],
        icon=KAI_STATE_ICON["ERROR"],
        animation=KAI_STATE_ANIMATION["ERROR"],
        comment=f"Da knirscht etwas im Maschinenraum. {reason}",
        timestamp=_now_iso(),
        source="fail_closed_guard",
        next_action="System pruefen und Audit-Log oeffnen.",
    )


def resolve_kai_state(states: list[KaiRuntimeState]) -> KaiRuntimeState:
    """Pick the highest-priority valid state. Empty list -> OFFLINE fallback.

    Mirrors web/src/kai/stateResolver.ts so the same logic governs both surfaces.
    """
    if not states:
        return create_fallback_state("OFFLINE", "Kein Signal. Keine Verbindung.")

    sanitized: list[KaiRuntimeState] = []
    for s in states:
        if is_valid_kai_state(s.state):
            sanitized.append(s)
        else:
            sanitized.append(create_fallback_state("OFFLINE", f"Unbekannter State: {s.state!r}"))

    return sorted(
        sanitized,
        key=lambda s: KAI_STATE_PRIORITY.get(s.state, -1),
        reverse=True,
    )[0]
