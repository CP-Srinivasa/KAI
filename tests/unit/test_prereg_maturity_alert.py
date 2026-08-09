"""Faellige Prä-Regs muessen den Rechner verlassen.

``kai-prereg-maturity`` lief woechentlich, rechnete die Faelligkeit korrekt aus
— und schrieb sie nach ``StandardOutput=journal``. Eine faellige Auswertung
existierte damit nur, solange jemand ins Journal schaute. Der Zustand war
richtig berechnet und trotzdem wirkungslos: dieselbe Klasse wie ein Health-Check,
dessen Befund die Maschine nie verlaesst.

Festgehalten wird:
* Ist nichts faellig, wird nichts gesendet (kein Wochen-Rauschen).
* Ist etwas faellig, traegt der Text die versiegelte ``prereg_id`` und den Grund.
* Eine Frist-Prä-Reg nennt ihr Fensterende, eine n-basierte ihren Zaehlerstand.
* Der Alarm sagt „wende die versiegelte Regel an", niemals „der Claim ist wahr".
"""

from __future__ import annotations

from app.research.prereg_maturity import (
    STATE_EVAL_CHECK_DUE,
    STATE_JUDGEABLE,
    STATE_NOT_DUE,
    build_maturity_alert,
)


def _row(
    *,
    name: str,
    state: str,
    due: bool,
    prereg_id: str = "abc123",
    kind: str = "count",
    **extra: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "name": name,
        "prereg_id": prereg_id,
        "state": state,
        "due": due,
        "kind": kind,
        "n_exact": None,
        "n_proxy": 0,
        "n_target": 30,
        "per_source": {},
    }
    row.update(extra)
    return row


def test_nichts_faellig_ergibt_keinen_alarm() -> None:
    rows = [
        _row(name="h1", state=STATE_NOT_DUE, due=False),
        _row(name="h2", state=STATE_NOT_DUE, due=False),
    ]

    assert build_maturity_alert(rows) is None


def test_leere_liste_ergibt_keinen_alarm() -> None:
    assert build_maturity_alert([]) is None


def test_faellige_frist_prereg_nennt_id_und_fensterende() -> None:
    rows = [
        _row(
            name="analyst_probe",
            prereg_id="f0e1a3a8073fd4c0",
            state=STATE_EVAL_CHECK_DUE,
            due=True,
            kind="deadline",
            per_source={"window_end_utc": "2026-08-10T00:13:00+00:00", "days_remaining": 0},
        ),
    ]

    text = build_maturity_alert(rows)

    assert text is not None
    assert "f0e1a3a8073fd4c0" in text
    assert "analyst_probe" in text
    assert "2026-08-10" in text


def test_faellige_n_prereg_nennt_zaehlerstand() -> None:
    rows = [
        _row(
            name="h1_signal_edge",
            prereg_id="fd6f5f78aabbccdd",
            state=STATE_JUDGEABLE,
            due=True,
            n_exact=200,
            n_target=200,
        ),
    ]

    text = build_maturity_alert(rows)

    assert text is not None
    assert "fd6f5f78aabbccdd" in text
    assert "200" in text


def test_nur_faellige_zeilen_stehen_im_text() -> None:
    rows = [
        _row(name="ruhig", state=STATE_NOT_DUE, due=False, prereg_id="stillstill"),
        _row(name="faellig", state=STATE_JUDGEABLE, due=True, prereg_id="lautlaut"),
    ]

    text = build_maturity_alert(rows)

    assert text is not None
    assert "lautlaut" in text
    assert "stillstill" not in text


def test_alarm_behauptet_kein_verdikt() -> None:
    """Faellig heisst „rechne jetzt", nicht „bestanden" — die Sprachregel gehoert in den Text."""
    rows = [_row(name="h1", state=STATE_JUDGEABLE, due=True)]

    text = build_maturity_alert(rows)

    assert text is not None
    lowered = text.lower()
    assert "bestanden" not in lowered
    assert "pass" not in lowered
    # Der Text muss zur Auswertung auffordern.
    assert "auswert" in lowered or "evaluator" in lowered
