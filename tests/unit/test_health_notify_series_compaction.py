"""Fehlalarm-Serien im Health-Kanal verdichten (V5, Daily Review 2026-09-16).

Gemessen im Journal von ``kai-health-check.service``, 2026-09-15 03:00 bis
2026-09-16 08:30 (118 Laeufe): **43 gesendete Health-Alerts plus 16
Recovery-Nachrichten**. In den 43 Sendungen standen 99 Befundzeilen, davon **82
(83 %) unveraendert aus der vorigen Sendung** -- ``deferred_unit_active`` allein
42 Mal im Volltext. Ausloeser der Sendungen:

    neuer Befund                13
    Befund weg (Menge kleiner)  12
    unveraendert (Reassert)     17
    erste                        1

Zwei Muster tragen die Wiederholung, und beide sind keine Neuigkeit:

1. **Eine Menge, die nur schrumpft, sagt nichts Neues.** Der verschwundene Befund
   wird seit STAB-2026-09-01 §7 bereits EINZELN als ``RECOVERED`` gemeldet. Die
   zusaetzliche volle Meldung wiederholte nur die Befunde, die ohnehin standen.
2. **Ein unveraenderter Befund braucht nicht bei jeder Sendung seinen Volltext.**
   Innerhalb seines Klassenfensters (P0 60 min, P1 6 h, sonst 24 h) genuegt eine
   Zeile, dass er weiter steht. Ist das Fenster abgelaufen, kommt der Volltext --
   ein Dauer-P0 wird nie zur Fussnote.

Was sich NICHT aendert: was erkannt wird, wann ein neuer Befund durchbricht
(sofort), die Recovery-Meldungen, der Fingerprint und die Ausloeser-ID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.alerts.health_notify import dispatch_health_notification

P0_TEXT = (
    "Zurueckgestellte Unit ist aktiv: kai-litellm.service: laeuft (active), obwohl "
    "zurueckgestellt — entweder die Zurueckstellung foermlich aufheben"
)
P1_TEXT = "YouTube-Transkripte: 2/22 Videos der letzten 24h tragen einen Transkript-Text (9%)"
MARKER_TEXT = "process-runtime: DEPLOY_HOLD — 5 von 6 Diensten bezeugen e3011a58"


@dataclass
class _Issue:
    severity: str
    component: str
    message: str


@dataclass
class _Report:
    issues: list[_Issue] = field(default_factory=list)
    recent_alerts: int = 5
    recent_actionable_alerts: int = 1
    recent_cycles: int = 1200
    data_sources_stale: bool = False


class _Console:
    def print(self, *args: object, **kwargs: object) -> None:
        pass


def _p0() -> _Issue:
    return _Issue("critical", "deferred_unit_active", P0_TEXT)


def _p1() -> _Issue:
    return _Issue("warning", "youtube_transcript_coverage", P1_TEXT)


def _marker() -> _Issue:
    return _Issue("critical", "process_runtime_marker", MARKER_TEXT)


class _Channel:
    """Sammelt, was beim Operator ankommt. ``fail`` schaltet einzelne Sendungen ab."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_next: set[str] = set()

    def __call__(self, text: str) -> bool:
        for marker in list(self.fail_next):
            if marker in text:
                self.fail_next.discard(marker)
                return False
        self.sent.append(text)
        return True

    def alerts(self) -> list[str]:
        return [t for t in self.sent if t.startswith("KAI Health Alert")]

    def recoveries(self) -> list[str]:
        return [t for t in self.sent if t.startswith("✅ RECOVERED")]


def _run(report: _Report, state: Path, channel: _Channel, *, minute: float) -> bool:
    return dispatch_health_notification(
        report,
        lookback_hours=24,
        notify_cooldown_minutes=30,
        console=_Console(),
        state_file=state,
        now_ts=minute * 60.0,
        sender=channel,
    )


# ---------------------------------------------------------------------------
# 1. Eine nur schrumpfende Menge
# ---------------------------------------------------------------------------


def test_verschwundener_befund_wird_nur_als_recovery_gemeldet(tmp_path: Path) -> None:
    """12 der 43 Sendungen am 15./16.09.: der Marker flatterte weg, und neben der
    RECOVERED-Nachricht ging der unveraenderte P0 noch einmal im Volltext raus."""
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _marker()]), state, ch, minute=0)
    _run(_Report([_p0()]), state, ch, minute=15)

    assert len(ch.alerts()) == 1, "keine zweite volle Meldung fuer eine nur kleinere Menge"
    assert len(ch.recoveries()) == 1
    assert "process_runtime_marker" in ch.recoveries()[0]


def test_der_takt_der_wiederholung_bleibt_am_letzten_volltext_verankert(tmp_path: Path) -> None:
    """Das Weglassen darf den Dauer-P0 nicht eine weitere Stunde verschieben."""
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _marker()]), state, ch, minute=0)
    _run(_Report([_p0()]), state, ch, minute=15)
    _run(_Report([_p0()]), state, ch, minute=45)
    assert len(ch.alerts()) == 1

    _run(_Report([_p0()]), state, ch, minute=61)
    assert len(ch.alerts()) == 2, "P0-Wiederholung 60 min nach dem letzten Volltext"
    assert P0_TEXT in ch.alerts()[-1]


def test_nach_dem_weglassen_bricht_ein_neuer_befund_sofort_durch(tmp_path: Path) -> None:
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _marker()]), state, ch, minute=0)
    _run(_Report([_p0()]), state, ch, minute=15)
    _run(_Report([_p0(), _p1()]), state, ch, minute=30)

    assert len(ch.alerts()) == 2
    assert P1_TEXT in ch.alerts()[-1]


def test_scheitert_die_recovery_meldung_geht_die_volle_meldung_raus(tmp_path: Path) -> None:
    """Fail-open: erfaehrt der Operator vom Verschwinden nichts, sagt es der Alarm."""
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _marker()]), state, ch, minute=0)
    ch.fail_next.add("RECOVERED process_runtime_marker")
    _run(_Report([_p0()]), state, ch, minute=15)

    assert len(ch.alerts()) == 2
    assert P0_TEXT in ch.alerts()[-1], "eine Meldung nur aus Einzeilern sagte nichts"


def test_hat_der_operator_die_vorige_menge_nie_gesehen_gilt_das_weglassen_nicht(
    tmp_path: Path,
) -> None:
    """Die letzte VOLLE Meldung zeigte {P0, P1}. Die naechste mit {P0, P1, Marker}
    scheiterte. Verschwindet der Marker dann wieder, ist die Menge gegenueber dem
    letzten Lauf kleiner -- gegenueber dem, was der Operator sah, aber nicht
    dieselbe. Dann wird voll gemeldet."""
    state, ch = tmp_path / "s.json", _Channel()
    _run(
        _Report([_p0(), _p1(), _Issue("warning", "annotations", "rueckstand")]), state, ch, minute=0
    )
    ch.fail_next.add(MARKER_TEXT)
    _run(_Report([_p0(), _p1(), _marker()]), state, ch, minute=15)
    assert len(ch.alerts()) == 1

    _run(_Report([_p0(), _p1()]), state, ch, minute=30)
    assert len(ch.alerts()) == 2
    assert any(t in ch.alerts()[-1] for t in (P0_TEXT, P1_TEXT))


# ---------------------------------------------------------------------------
# 2. Verdichtung unveraenderter Befunde
# ---------------------------------------------------------------------------


def test_neuer_befund_kommt_voll_bekannte_als_einzeiler(tmp_path: Path) -> None:
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _p1()]), state, ch, minute=0)
    _run(_Report([_p0(), _p1(), _marker()]), state, ch, minute=15)

    text = ch.alerts()[-1]
    assert MARKER_TEXT in text, "der neue Befund steht im Volltext"
    assert P0_TEXT not in text, "der unveraenderte P0 nicht noch einmal"
    assert P1_TEXT not in text
    assert "[CRITICAL] deferred_unit_active — unveraendert seit" in text
    assert "[WARNING] youtube_transcript_coverage — unveraendert seit" in text


def test_die_gruppierung_zaehlt_verdichtete_befunde_mit(tmp_path: Path) -> None:
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _p1()]), state, ch, minute=0)
    _run(_Report([_p0(), _p1(), _marker()]), state, ch, minute=15)

    assert "== P0 (2) ==" in ch.alerts()[-1]
    assert "== P1 (1) ==" in ch.alerts()[-1]


def test_nach_ablauf_des_klassenfensters_kommt_der_volltext_zurueck(tmp_path: Path) -> None:
    """P0 60 min: ein verdichteter P0 darf nicht dauerhaft Fussnote bleiben."""
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _p1()]), state, ch, minute=0)
    _run(_Report([_p0(), _p1(), _marker()]), state, ch, minute=70)

    text = ch.alerts()[-1]
    assert P0_TEXT in text, "P0-Fenster (60 min) abgelaufen -> Volltext"
    assert P1_TEXT not in text, "P1-Fenster (6 h) laeuft noch -> Einzeiler"


def test_eskalation_kommt_im_volltext(tmp_path: Path) -> None:
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _p1()]), state, ch, minute=0)
    escalated = _Issue("critical", "youtube_transcript_coverage", "jetzt kritisch: 0/22")
    _run(_Report([_p0(), escalated]), state, ch, minute=15)

    assert "jetzt kritisch: 0/22" in ch.alerts()[-1]


def test_wiederholung_bringt_den_faelligen_befund_im_volltext(tmp_path: Path) -> None:
    """Die Reassert-Meldung existiert, damit ein Dauerbefund sichtbar bleibt."""
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _p1()]), state, ch, minute=0)
    _run(_Report([_p0(), _p1()]), state, ch, minute=61)

    text = ch.alerts()[-1]
    assert len(ch.alerts()) == 2
    assert P0_TEXT in text
    assert "[WARNING] youtube_transcript_coverage — unveraendert seit" in text


def test_jede_gesendete_meldung_traegt_mindestens_einen_volltext(tmp_path: Path) -> None:
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _p1()]), state, ch, minute=0)
    for minute in (15, 30, 61, 75, 125, 140, 200, 400):
        issues = [_p0(), _p1()] + ([_marker()] if minute % 2 else [])
        _run(_Report(issues), state, ch, minute=minute)

    for text in ch.alerts():
        full = [
            ln for ln in text.splitlines() if ln.startswith("[") and "— unveraendert seit" not in ln
        ]
        assert full, f"Meldung ohne Volltext:\n{text}"


def test_gescheiterte_sendung_zaehlt_nicht_als_gesehen(tmp_path: Path) -> None:
    state, ch = tmp_path / "s.json", _Channel()
    ch.fail_next.add(P0_TEXT)
    _run(_Report([_p0()]), state, ch, minute=0)
    assert ch.alerts() == []

    _run(_Report([_p0(), _p1()]), state, ch, minute=5)
    assert P0_TEXT in ch.alerts()[-1], "der P0 hat den Operator nie erreicht"


def test_zurueckgekehrter_befund_ist_eine_neue_episode_und_kommt_voll(tmp_path: Path) -> None:
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0(), _marker()]), state, ch, minute=0)
    _run(_Report([_p0()]), state, ch, minute=15)
    _run(_Report([_p0(), _marker()]), state, ch, minute=30)

    assert MARKER_TEXT in ch.alerts()[-1]


def test_der_einzeiler_nennt_den_beginn_der_episode(tmp_path: Path) -> None:
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0()]), state, ch, minute=0)
    _run(_Report([_p0(), _marker()]), state, ch, minute=15)

    line = next(
        ln
        for ln in ch.alerts()[-1].splitlines()
        if ln.startswith("[CRITICAL] deferred_unit_active")
    )
    assert line == (
        "[CRITICAL] deferred_unit_active — unveraendert seit 01.01. 00:00Z (Volltext 00:00Z)"
    )


def test_unlesbarer_befundzustand_verdichtet_nichts(tmp_path: Path) -> None:
    """Fail-open: ohne belastbaren Zustand lieber einmal zu viel Volltext."""
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0()]), state, ch, minute=0)
    findings = state.with_name(state.name + ".findings.json")
    findings.write_text("{kaputt", encoding="utf-8")
    _run(_Report([_p0(), _marker()]), state, ch, minute=15)

    assert P0_TEXT in ch.alerts()[-1]


def test_der_volltext_zeitpunkt_steht_im_befundzustand(tmp_path: Path) -> None:
    state, ch = tmp_path / "s.json", _Channel()
    _run(_Report([_p0()]), state, ch, minute=0)
    findings = json.loads(
        state.with_name(state.name + ".findings.json").read_text(encoding="utf-8")
    )

    assert findings["deferred_unit_active"]["last_full_sent_ts"] == 0.0
    assert findings["deferred_unit_active"]["last_full_severity"] == "critical"
