"""Ein toter Eingang ist ein Systembefund, keine unzuverlässige Probe.

``data_sources_stale`` bedeutet: *diese Messung ist womöglich wertlos, weil sie
gespiegelte Workstation-Artefakte liest*. Es steuert über ``--exit-on-stale``
den Abbruch und ist eine Aussage über die **Probe**, nicht über das System.

Als der TradingView-Eingang am 09.08. in die Wachliste kam, setzte er dieses
Flag mit — und damit war die Unit dauerhaft ``failed``, solange die Quelle
schweigt, mit der irreführenden Meldung „check Pi sync", obwohl die Probe auf
dem Pi lief (beobachtet: mtime 10146 min alt). Ein täglich rot gemeldeter
Dienst, dessen Begründung in die falsche Richtung zeigt, ist der Anfang von
Alert-Fatigue.

Der Befund selbst muss bleiben — er wird gemeldet, er alarmiert, er verschwindet
nur nicht mehr hinter einer falschen Ursache.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

from app.alerts.health_check import _check_data_freshness


def _aged(adir: Path, name: str, *, age_seconds: float) -> Path:
    adir.mkdir(parents=True, exist_ok=True)
    p = adir / name
    p.write_text('{"x":1}\n', encoding="utf-8")
    stamp = time.time() - age_seconds
    os.utime(p, (stamp, stamp))
    return p


# Ohne diese beiden (required=True) setzt schon ihr FEHLEN ``stale`` — dann
# misst der Test nicht den Eingangsstrom, sondern ein leeres Verzeichnis.
_REQUIRED_FRESH = ("alert_audit.jsonl", "trading_loop_audit.jsonl")


def _baseline(adir: Path) -> None:
    """Alle Pflicht-Streams frisch anlegen, damit nur der Prüffall wirkt."""
    for name in _REQUIRED_FRESH:
        _aged(adir, name, age_seconds=1)


def test_toter_eingang_meldet_aber_setzt_nicht_stale(tmp_path: Path) -> None:
    adir = tmp_path / "artifacts"
    _baseline(adir)
    _aged(adir, "tradingview_webhook_audit.jsonl", age_seconds=7 * 24 * 3600)

    issues, stale = _check_data_freshness(adir, datetime.now(UTC))

    ingress = [i for i in issues if "tradingview_ingress" in i.component]
    assert ingress, "Der Befund muss bleiben — er ist der ganze Zweck des Waechters"
    assert stale is False, (
        "Ein toter Eingang darf die Probe nicht als unzuverlaessig markieren; "
        "sonst bricht --exit-on-stale ab und die Unit ist dauerhaft failed."
    )


def test_eingangs_meldung_zeigt_auf_die_quelle_nicht_auf_pi_sync(tmp_path: Path) -> None:
    adir = tmp_path / "artifacts"
    _baseline(adir)
    _aged(adir, "tradingview_webhook_audit.jsonl", age_seconds=7 * 24 * 3600)

    issues, _ = _check_data_freshness(adir, datetime.now(UTC))
    msg = next(i.message for i in issues if "tradingview_ingress" in i.component)

    assert "Pi sync" not in msg
    assert "Quelle" in msg or "Alerts" in msg


def test_veralteter_ausgang_setzt_weiterhin_stale(tmp_path: Path) -> None:
    """Rein additiv: die Mirror-Erkennung für Ausgänge bleibt unverändert."""
    adir = tmp_path / "artifacts"
    _baseline(adir)
    _aged(adir, "trading_loop_audit.jsonl", age_seconds=7 * 24 * 3600)

    issues, stale = _check_data_freshness(adir, datetime.now(UTC))

    assert any("trading_loop" in i.component for i in issues)
    assert stale is True


def test_frischer_eingang_erzeugt_weder_befund_noch_stale(tmp_path: Path) -> None:
    adir = tmp_path / "artifacts"
    _baseline(adir)
    _aged(adir, "tradingview_webhook_audit.jsonl", age_seconds=60)

    issues, stale = _check_data_freshness(adir, datetime.now(UTC))

    assert not [i for i in issues if "tradingview_ingress" in i.component]
    assert stale is False
