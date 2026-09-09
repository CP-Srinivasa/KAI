"""Ein abgelaufenes Ziel ist keine Freigabe — und darf auch nicht so klingen.

Operator-Entscheid 2026-09-09: kein neues Zieldatum setzen. Ein Datum ohne neu
definierte Freigabekriterien waere die kosmetische Verlaengerung eines
abgelaufenen Vertrags und wuerde genau die Wahrheit verschleiern, die der Audit
vom 2026-06-08 (Befund G / FS-4) beanstandet hat.

Sachlage:
    altes Ziel            2026-05-16
    damalige Kriterien    >=200 resolved signals ODER >=10 paper fills
    Ziel                  abgelaufen
    Nachfolge-Gate        nicht definiert
    aktuelle Freigabe     keine

Der Zustand hiess zuletzt ``no_active_target`` und war im Backend ausdruecklich
"neutral (kein Fehler)" gerahmt, im Frontend als Tone ``muted`` mit demselben
Text. Das liest sich wie "alles in Ordnung, nur noch nichts eingetragen" — und
ist damit dieselbe Verharmlosung wie das fruehere ``expired``, nur freundlicher.

Zentral: die Freigabe-Frage darf nicht am Status-String haengen. Sie bekommt ein
eigenes, maschinenlesbares Feld, das fail-closed ist.
"""

from __future__ import annotations

import pytest

from app.api.routers import dashboard as dashboard_mod


def _reentry(target: str | None):
    return dashboard_mod._reentry_status(target_date=target)


def test_lapsed_target_is_no_current_authorization() -> None:
    state = _reentry("2026-05-16")
    assert state["status"] == "no_current_authorization"
    assert state["reason"] == "target_lapsed"


def test_unparseable_target_is_also_no_current_authorization() -> None:
    """Zwei Woerter fuer denselben Freigabe-Zustand waeren eines zu viel."""
    state = _reentry("not-a-date")
    assert state["status"] == "no_current_authorization"
    assert state["reason"] == "target_unparseable"


def test_authorization_is_never_granted_by_this_surface() -> None:
    """FS-4: Der Re-Entry-Zustand ist Evidenz, nie Freigabe.

    Auch ein in der Zukunft liegendes Ziel bedeutet nur "ein Sammelziel laeuft",
    nicht "Ausfuehrung freigegeben". Die einzige Freigabe ist execution_enabled
    bzw. entry_mode. Kein Aufruf dieser Funktion darf je True liefern.
    """
    for target in ("2026-05-16", "2099-01-01", "not-a-date", "", None):
        state = _reentry(target)
        assert state["grants_execution_authorization"] is False, (
            f"Re-Entry-Zustand fuer {target!r} behauptet eine Ausfuehrungsfreigabe"
        )


def test_future_target_still_reads_as_active_target_not_as_clearance() -> None:
    state = _reentry("2099-01-01")
    assert state["status"] == "active_target"
    assert state["grants_execution_authorization"] is False


def test_warning_names_the_missing_gate_not_a_pending_config() -> None:
    """Der Text darf nicht mehr 'kein Fehler / Konfiguration ausstehend' sagen."""
    warning = _reentry("2026-05-16")["warning"]
    assert warning
    low = warning.lower()
    assert "kein fehler" not in low
    assert "ausstehend" not in low
    # Er muss benennen, was fehlt: eine Freigabe bzw. ein definiertes Gate.
    assert "freigabe" in low or "gate" in low


def test_no_successor_date_is_invented() -> None:
    """Das alte Ziel bleibt als Kontext sichtbar, wird aber nicht fortgeschrieben."""
    state = _reentry("2026-05-16")
    assert state["target_date"] == "2026-05-16"
    assert state["days_delta"] is not None and state["days_delta"] < 0


@pytest.mark.parametrize("target", ["2026-05-16", "not-a-date", "2099-01-01"])
def test_status_vocabulary_is_closed(target: str) -> None:
    """Nur zwei Zustaende — sonst schleicht sich ein drittes Synonym ein."""
    assert _reentry(target)["status"] in {"active_target", "no_current_authorization"}
