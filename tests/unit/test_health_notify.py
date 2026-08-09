"""Versand-Logik des Health-Befunds, jetzt ohne CLI-Rahmen prüfbar.

Vorher lag sie als Block in ``app/cli/main.py`` und war nur über den
Typer-Befehl erreichbar — deshalb war weder das Cooldown-Gate noch der
Alarmtext je direkt getestet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.alerts.health_notify import (
    build_health_alert_text,
    dispatch_health_notification,
)


@dataclass
class _Issue:
    component: str
    message: str
    severity: str = "warning"


@dataclass
class _Report:
    issues: list[_Issue] = field(default_factory=list)
    data_sources_stale: bool = False
    recent_alerts: int = 3
    recent_actionable_alerts: int = 1
    recent_cycles: int = 12


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args: Any, **kwargs: Any) -> None:
        self.lines.append(" ".join(str(a) for a in args))


class TestAlertText:
    def test_enthaelt_jeden_befund_mit_schwere(self) -> None:
        report = _Report(
            issues=[
                _Issue("tradingview_ingress", "6d ohne Eingang", "critical"),
                _Issue("bayes_recalc", "stale", "warning"),
            ]
        )

        text = build_health_alert_text(report, lookback_hours=24)

        assert "[CRITICAL] tradingview_ingress: 6d ohne Eingang" in text
        assert "[WARNING] bayes_recalc: stale" in text
        assert "Window: 24h" in text

    def test_stale_hinweis_nur_wenn_stale(self) -> None:
        assert "stale" not in build_health_alert_text(_Report(), lookback_hours=6)
        stale = build_health_alert_text(_Report(data_sources_stale=True), lookback_hours=6)
        assert "data sources stale" in stale


class TestCooldown:
    def test_frischer_zeitstempel_unterdrueckt(self, tmp_path: Path) -> None:
        state = tmp_path / "last"
        state.write_text(str(time.time()), encoding="utf-8")
        console = _Console()

        sent = dispatch_health_notification(
            _Report(issues=[_Issue("x", "y")]),
            lookback_hours=24,
            notify_cooldown_minutes=30,
            console=console,
            state_file=state,
        )

        assert sent is False
        assert any("cooldown" in line for line in console.lines)

    def test_abgelaufener_zeitstempel_unterdrueckt_nicht(self, tmp_path: Path, monkeypatch) -> None:
        state = tmp_path / "last"
        state.write_text(str(time.time() - 3600), encoding="utf-8")
        console = _Console()
        seen: list[str] = []

        async def _fake_send(text: str) -> bool:
            seen.append(text)
            return True

        monkeypatch.setattr("app.alerts.notify.send_operator_notification", _fake_send)

        sent = dispatch_health_notification(
            _Report(issues=[_Issue("x", "y")]),
            lookback_hours=24,
            notify_cooldown_minutes=30,
            console=console,
            state_file=state,
        )

        assert sent is True
        assert seen and "KAI Health Alert" in seen[0]

    def test_unlesbarer_zustand_schaltet_nicht_stumm(self, tmp_path: Path, monkeypatch) -> None:
        """Ein kaputter Zeitstempel darf keinen echten Befund verschlucken."""
        state = tmp_path / "last"
        state.write_text("nicht-numerisch", encoding="utf-8")
        console = _Console()

        async def _fake_send(text: str) -> bool:
            return True

        monkeypatch.setattr("app.alerts.notify.send_operator_notification", _fake_send)

        sent = dispatch_health_notification(
            _Report(issues=[_Issue("x", "y")]),
            lookback_hours=24,
            notify_cooldown_minutes=30,
            console=console,
            state_file=state,
        )

        assert sent is True

    def test_fehlgeschlagener_versand_schreibt_keinen_zeitstempel(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Sonst schaltet ein Sendefehler den naechsten echten Alarm stumm."""
        state = tmp_path / "last"
        console = _Console()

        async def _fake_send(text: str) -> bool:
            return False

        monkeypatch.setattr("app.alerts.notify.send_operator_notification", _fake_send)

        sent = dispatch_health_notification(
            _Report(issues=[_Issue("x", "y")]),
            lookback_hours=24,
            notify_cooldown_minutes=30,
            console=console,
            state_file=state,
        )

        assert sent is False
        assert not state.exists()
