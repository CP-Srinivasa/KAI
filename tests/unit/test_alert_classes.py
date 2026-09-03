"""Tests fuer die Alarmklassen P0–P3 (G6 Task 1, A4-005 / A4-024 / A4-026).

Zwei Dinge werden hier festgenagelt: dass **jede** Komponente des
Health-Checks eine Klasse hat (Vollstaendigkeit, per AST gegen die echte Datei
geprueft — nicht gegen eine gepflegte Liste), und dass der Cooldown das stille
Versagen nicht mehr verschluckt.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.alerts.alert_classes import (
    COMPONENT_CLASSES,
    AlertClass,
    classify,
    classify_issues,
    cooldown_applies,
    partition,
    render_grouped,
)

_ALERTS_DIR = Path(__file__).resolve().parents[2] / "app" / "alerts"
# health_check.py + die ausgelagerten Payment-/Input-Contract-Waechter: beide
# Dateien emittieren HealthIssue-Komponenten, beide muessen die Registry treffen.
HEALTH_CHECK_PATHS = (_ALERTS_DIR / "health_check.py", _ALERTS_DIR / "health_check_payments.py")


@dataclass(frozen=True)
class _Issue:
    severity: str
    component: str
    message: str = "..."


def _components_in_health_check() -> tuple[set[str], set[str]]:
    """(statische Komponentennamen, dynamische Suffixe) direkt aus dem Quelltext.

    Bewusst per AST gegen die echte Datei statt gegen eine Liste hier: eine neue
    Sonde soll diesen Test brechen, nicht still durchrutschen.
    """
    static: set[str] = set()
    dynamic: set[str] = set()
    nodes = [
        node
        for path in HEALTH_CHECK_PATHS
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    ]
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "component":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                static.add(kw.value.value)
            elif isinstance(kw.value, ast.JoinedStr):
                tail = [
                    v.value
                    for v in kw.value.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ]
                if tail:
                    dynamic.add(tail[-1])
    return static, dynamic


# ---------------------------------------------------------------------------
# Vollstaendigkeit
# ---------------------------------------------------------------------------


def test_every_health_component_has_a_class() -> None:
    """Der Contract-Test des Sprints: JEDER Alarmtyp genau einer Klasse."""
    static, _ = _components_in_health_check()
    unclassified = sorted(c for c in static if classify(c) is AlertClass.UNCLASSIFIED)
    assert unclassified == [], (
        f"Ohne Klasse: {unclassified} — in app/alerts/alert_classes.py eintragen. "
        "Eine neue Sonde ohne Dringlichkeit ist keine stille P2."
    )


def test_every_dynamic_component_family_has_a_class() -> None:
    static, dynamic = _components_in_health_check()
    for suffix in dynamic:
        assert classify(f"irgendwas{suffix}") is not AlertClass.UNCLASSIFIED, suffix
    assert static, "AST-Suche fand keine Komponenten — der Test misst sich selbst kaputt"


def test_registry_has_no_entries_for_vanished_components() -> None:
    """Gegenrichtung: eine Klasse fuer eine Komponente, die es nicht mehr gibt."""
    static, _ = _components_in_health_check()
    stale = sorted(set(COMPONENT_CLASSES) - static)
    assert stale == [], f"Klasse ohne Komponente: {stale}"


def test_unknown_component_is_unclassified_not_silently_p2() -> None:
    """Positivkontrolle: der Vollstaendigkeitstest kann ueberhaupt fehlschlagen."""
    assert classify("brandneue_sonde") is AlertClass.UNCLASSIFIED


# ---------------------------------------------------------------------------
# Zuteilung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("privilege_broker", AlertClass.P0),
        ("close_price_sanity", AlertClass.P0),
        ("alert_delivery.jsonl_schema", AlertClass.P0),
        ("alert_delivery", AlertClass.P1),
        ("tradingview_ingress_freshness", AlertClass.P1),
        ("annotations", AlertClass.P2),
        ("probe_location", AlertClass.P3),
    ],
)
def test_component_classes(component: str, expected: AlertClass) -> None:
    assert classify(component) is expected


def test_issues_are_sorted_most_urgent_first() -> None:
    issues = [
        _Issue("warning", "annotations"),
        _Issue("critical", "privilege_broker"),
        _Issue("warning", "alert_delivery"),
        _Issue("warning", "probe_location"),
    ]
    order = [c.component for c in classify_issues(issues)]
    assert order == ["privilege_broker", "alert_delivery", "annotations", "probe_location"]


def test_partition_groups_by_class() -> None:
    grouped = partition([_Issue("critical", "privilege_broker"), _Issue("warning", "precision")])
    assert set(grouped) == {AlertClass.P0, AlertClass.P2}


# ---------------------------------------------------------------------------
# Der Widerspruch, den das aufloest (A4-024/026)
# ---------------------------------------------------------------------------


def test_cooldown_does_not_apply_when_a_silent_failure_is_present() -> None:
    """Der Fatigue-Schutz darf die Klasse, gegen die er nie gerichtet war, nicht ersticken."""
    assert cooldown_applies([_Issue("warning", "tradingview_ingress_freshness")]) is False


def test_cooldown_applies_to_a_digest_only_report() -> None:
    assert cooldown_applies([_Issue("warning", "annotations"), _Issue("warning", "precision")])


def test_unclassified_breaks_the_cooldown_too() -> None:
    """Im Zweifel melden: eine Komponente ohne festgelegte Dringlichkeit zu
    unterdruecken waere genau die Stille, die dieses Modul beendet."""
    assert cooldown_applies([_Issue("warning", "brandneue_sonde")]) is False


def test_render_groups_instead_of_bundling_everything() -> None:
    """35 Komponenten reisten bisher in EINER Nachricht ohne Rangordnung."""
    text = render_grouped(
        [_Issue("critical", "privilege_broker", "broker weg"), _Issue("warning", "precision", "x")]
    )
    assert text.index("[P0]") < text.index("[P1]") if "[P1]" in text else True
    assert "[P0] 1:" in text
    assert "[P2] 1:" in text
    assert text.index("[P0] 1:") < text.index("[P2] 1:")
