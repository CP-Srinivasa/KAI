"""Das Aufsichtsregister muss gegen seine Quelle stimmen — nicht gegen sich selbst.

Drei Wachlisten stimmten am 2026-08-18 am selben Tag nicht mit ihrer Quelle
überein (8/19 Prä-Regs, 59/113 Units, 2/3 Eingänge unbeobachtet). Ein Register,
das Aufsicht *behauptet*, ist wertlos, sobald es von der Grundgesamtheit
abweicht — und die Abweichung fällt genau dann nicht auf, wenn niemand sie prüft.

Diese Tests pinnen deshalb die Invarianten aus dem Register selbst:

* jede geführte ``prereg_id`` existiert im versiegelten Ledger (oder in der
  ausdrücklichen Ausnahmeliste),
* kein Eintrag steht auf ``UNWATCHED``/``UNRESOLVED`` — genau das war der
  Zustand, den STAB-06a beenden sollte,
* jeder ``decision_state`` trägt seine Pflichtfelder,
* ``WATCH`` nennt einen Watcher, der bereits existiert (dieses Register legt
  keine Timer und keine Dienste an),
* ein offener ``blocking_finding`` verbietet ``spec_installed``.

Der letzte Punkt ist der wichtigste: er verhindert, dass eine Klassifikation
mechanisch in einen Zähler übersetzt wird, obwohl die Faktenlage ihr
widerspricht (``4a3b1b0c5a94b73c`` wurde laut Ledger vor Datenanfall durch
``b20ef1487ccba99d`` abgelöst).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "prereg_supervision.json"

_TERMINAL_STATES = {
    "WATCH",
    "MANUAL_IMMEDIATE_VERDICT",
    "MANUAL_SCHEDULED_REVIEW",
    "RETIRE",
    "NO_WATCH_REQUIRED",
}
_FORBIDDEN_STATES = {"UNWATCHED", "UNRESOLVED", "PENDING", None, ""}

# Watcher, die es GIBT. Ein Register darf keinen Dienst erfinden: STAB-06a war
# ausdrücklich "kein neuer Timer, kein neuer Service".
_EXISTING_WATCHERS = {"kai-prereg-maturity"}

_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "WATCH": ("owner", "watcher_id", "cadence", "next_review_utc", "archive_path"),
    "MANUAL_IMMEDIATE_VERDICT": (
        "owner",
        "decision_question",
        "rationale",
        "evidence_path",
        "archive_path",
    ),
    "MANUAL_SCHEDULED_REVIEW": (
        "owner",
        "next_review_utc",
        "decision_question",
        "rationale",
        "archive_path",
    ),
}


@pytest.fixture(scope="module")
def registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entries(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return list(registry["entries"])


def test_registry_ist_lesbar_und_versioniert(registry: dict[str, Any]) -> None:
    assert registry["schema"] == "prereg_supervision/v1"
    assert registry["classified_by"] == "operator"
    assert registry["source_of_truth"] == "artifacts/research/prereg_ledger.jsonl"


def test_kein_eintrag_bleibt_unentschieden(entries: list[dict[str, Any]]) -> None:
    """UNWATCHED war der Ausgangszustand — er darf hier nicht wieder auftauchen."""
    for entry in entries:
        state = entry.get("decision_state")
        assert state not in _FORBIDDEN_STATES, f"{entry['prereg_id']}: {state}"
        assert state in _TERMINAL_STATES, f"{entry['prereg_id']}: {state}"


def test_jede_prereg_id_ist_eindeutig(entries: list[dict[str, Any]]) -> None:
    ids = [e["prereg_id"] for e in entries]
    assert len(ids) == len(set(ids))
    assert all(isinstance(i, str) and len(i) == 16 for i in ids)


def test_pflichtfelder_je_zustand_sind_gesetzt(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        required = _REQUIRED_FIELDS.get(str(entry["decision_state"]), ())
        for field in required:
            value = entry.get(field)
            assert value not in (None, "", []), f"{entry['prereg_id']}: {field} fehlt"


def test_watch_nennt_nur_bestehende_watcher(entries: list[dict[str, Any]]) -> None:
    """Kein neuer Timer, kein neuer Dienst — die Klassifikation nutzt, was läuft."""
    for entry in entries:
        if entry["decision_state"] != "WATCH":
            continue
        assert entry["watcher_id"] in _EXISTING_WATCHERS, entry["prereg_id"]


def test_sofort_faellige_verdikte_tragen_due_now(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        if entry["decision_state"] != "MANUAL_IMMEDIATE_VERDICT":
            continue
        assert entry["next_review_utc"] == "DUE_NOW", entry["prereg_id"]
        assert entry.get("watcher_id") is None


def test_terminierte_wiedervorlage_hat_ein_echtes_datum(entries: list[dict[str, Any]]) -> None:
    """'später' und 'bei Gelegenheit' sind keine Termine (H2-Lehre)."""
    from datetime import datetime

    for entry in entries:
        if entry["decision_state"] != "MANUAL_SCHEDULED_REVIEW":
            continue
        parsed = datetime.fromisoformat(str(entry["next_review_utc"]))
        assert parsed.tzinfo is not None, entry["prereg_id"]


def test_offener_befund_verbietet_die_mechanische_umsetzung(
    entries: list[dict[str, Any]],
) -> None:
    """Ein blocking_finding darf nie stillschweigend in einen Zähler übersetzt werden."""
    for entry in entries:
        finding = entry.get("blocking_finding")
        if not finding:
            continue
        assert entry.get("spec_installed") is False, entry["prereg_id"]
        assert finding.get("operator_decision_required") is True
        assert finding.get("evidence"), "ein Befund ohne Beleg ist eine Behauptung"


def test_abgeloester_claim_ist_als_solcher_belegt(entries: list[dict[str, Any]]) -> None:
    """Regression: 4a3b1b0c wurde laut Ledger VOR Datenanfall durch v2 abgelöst."""
    entry = next(e for e in entries if e["prereg_id"] == "4a3b1b0c5a94b73c")
    finding = entry["blocking_finding"]
    assert finding["type"] == "SUPERSEDED_BEFORE_DATA"
    assert finding["superseded_by"] == "b20ef1487ccba99d"
    assert entry["spec_installed"] is False


def test_verdikttext_auflagen_haengen_am_eintrag(entries: list[dict[str, Any]]) -> None:
    """Zwei Fälle dürfen nicht ohne ihren Vorbehalt attestiert werden."""
    by_id = {e["prereg_id"]: e for e in entries}
    assert by_id["8b21040ad7935a4a"]["verdict_text_requirement"] == "POST_HOC_SEAL"
    assert by_id["0879a65c5fd01f65"]["verdict_text_requirement"] == "PASS_SAFETY_AXIS_ONLY"


def test_register_und_wachliste_widersprechen_sich_nicht(entries: list[dict[str, Any]]) -> None:
    """Ein Eintrag mit installiertem Spec muss in MATURITY_SPECS stehen — und umgekehrt."""
    from app.research.prereg_maturity import MATURITY_SPECS

    spec_ids = {
        str(s.get("prereg_id")) for s in MATURITY_SPECS if isinstance(s.get("prereg_id"), str)
    }
    for entry in entries:
        installed = bool(entry.get("spec_installed"))
        in_specs = entry["prereg_id"] in spec_ids
        assert installed == in_specs, (
            f"{entry['prereg_id']}: spec_installed={installed}, in MATURITY_SPECS={in_specs}"
        )


def test_m3_frist_ankert_am_revisit_datum_nicht_am_horizont() -> None:
    """90 d ab Versiegelung wären der 02.10. — drei Tage nach der Entscheidung."""
    from app.research.prereg_maturity import MATURITY_SPECS

    spec = next(s for s in MATURITY_SPECS if s.get("prereg_id") == "c489079289070a8c")
    assert spec["kind"] == "deadline"
    assert spec["window_end_utc"].startswith("2026-09-29")
    assert "Kalt-Ansprache" in spec["note"]


def test_keine_stillen_abschluesse(registry: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    """RETIRE/NO_WATCH_REQUIRED sind in dieser Runde ausdrücklich nicht vergeben."""
    states = {e["decision_state"] for e in entries}
    assert "RETIRE" not in states
    assert "NO_WATCH_REQUIRED" not in states
    assert registry["decision_states"]["RETIRE"] == "Nicht vergeben."
