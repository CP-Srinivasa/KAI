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
* ein offener ``blocking_finding`` verbietet ``spec_installed``,
* ein ``SUPERSEDED``-Claim bekommt NIE einen Watcher, Termin oder Spec,
* die Aggregate im Kopf folgen aus den Zeilen, nicht aus Prosa.

Die beiden letzten Punkte sind die wichtigsten: sie verhindern, dass eine
Klassifikation mechanisch in einen Zähler übersetzt wird, obwohl die Faktenlage
ihr widerspricht. ``4a3b1b0c5a94b73c`` wurde laut Ledger **vor** dem ersten
Out-of-Sample-Datenpunkt durch ``b20ef1487ccba99d`` ersetzt; der Operator hat ihn
am 2026-08-27 als ``SUPERSEDED`` geschlossen. Ein Zähler dort würde eine Frage
weitermessen, deren Antwort dem Nachfolger gehört — und der ist terminal FAILED.
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
    "SUPERSEDED",
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
    "SUPERSEDED": (
        "owner",
        "superseded_by",
        "closure_reason",
        "substantive_verdict",
        "successor_terminal_verdict",
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


def test_sofort_faellige_verdikte_tragen_due_now_solange_offen(
    entries: list[dict[str, Any]],
) -> None:
    """``DUE_NOW`` ist ein Zustand des OFFENEN Falls.

    Nach der Attestierung waere ein stehengebliebenes ``DUE_NOW`` eine Daueraufgabe
    fuer eine bereits getroffene Entscheidung — deshalb faellt der Termin dann weg.
    """
    for entry in entries:
        if entry["decision_state"] != "MANUAL_IMMEDIATE_VERDICT":
            continue
        assert entry.get("watcher_id") is None, entry["prereg_id"]
        if entry.get("open") is False:
            assert entry["next_review_utc"] is None, entry["prereg_id"]
        else:
            assert entry["next_review_utc"] == "DUE_NOW", entry["prereg_id"]


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


def test_abgeloester_claim_ist_terminal_geschlossen(entries: list[dict[str, Any]]) -> None:
    """Operator-Entscheidung 2026-08-27: 4a3b1b0c = SUPERSEDED, terminal, ohne Sachverdikt."""
    entry = next(e for e in entries if e["prereg_id"] == "4a3b1b0c5a94b73c")
    assert entry["decision_state"] == "SUPERSEDED"
    assert entry["closure_reason"] == "SUPERSEDED_BEFORE_OOS"
    assert entry["superseded_by"] == "b20ef1487ccba99d"
    assert entry["superseded_before_oos"] is True
    assert entry["successor_terminal_verdict"] == "FAILED"
    assert entry["research_line_status"] == "CLOSED"
    assert entry["operator_decision_required"] is False
    assert entry["blocking_finding"] is None


def test_superseded_bekommt_niemals_einen_watcher(entries: list[dict[str, Any]]) -> None:
    """Die Kern-Invariante der Operator-Entscheidung: kein Zaehler auf einer ersetzten Frage.

    Ein vor Out-of-Sample-Daten abgeloester Claim darf NIE eine Reifezaehlung, einen
    Watcher oder einen Termin bekommen — sonst misst das System eine Frage weiter,
    deren Antwort dem Nachfolger gehoert.
    """
    from app.research.prereg_maturity import MATURITY_SPECS

    spec_ids = {
        str(s.get("prereg_id")) for s in MATURITY_SPECS if isinstance(s.get("prereg_id"), str)
    }
    for entry in entries:
        if entry["decision_state"] != "SUPERSEDED":
            continue
        pid = entry["prereg_id"]
        assert entry["spec_installed"] is False, pid
        assert entry.get("spec_required") is False, pid
        assert entry["watcher_id"] is None, pid
        assert entry["cadence"] is None, pid
        assert entry["next_review_utc"] is None, pid
        assert pid not in spec_ids, f"{pid} darf keinen MATURITY_SPEC haben"


def test_superseded_erzeugt_kein_eigenes_sachverdikt(entries: list[dict[str, Any]]) -> None:
    """SUPERSEDED ist weder FAILED noch RETIRED noch NO_WATCH_REQUIRED."""
    for entry in entries:
        if entry["decision_state"] != "SUPERSEDED":
            continue
        assert entry["substantive_verdict"] == "NONE", entry["prereg_id"]
        assert set(entry["not_this"]) == {"FAILED", "RETIRED", "NO_WATCH_REQUIRED"}
        assert "SUPERSEDES" in entry["successor_criteria_quote"]


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


def test_aggregate_stimmen_mit_den_eintraegen(
    registry: dict[str, Any], entries: list[dict[str, Any]]
) -> None:
    """Die Zahlen im Kopf muessen aus den Zeilen folgen, nie aus Prosa."""
    from collections import Counter

    counts = Counter(e["decision_state"] for e in entries)
    agg = registry["aggregates"]
    assert agg["TOTAL"] == len(entries) == 7
    assert agg["WATCH"] == counts.get("WATCH", 0) == 1
    assert agg["SUPERSEDED"] == counts.get("SUPERSEDED", 0) == 1
    assert agg["MANUAL_IMMEDIATE_VERDICT"] == counts.get("MANUAL_IMMEDIATE_VERDICT", 0) == 4
    assert agg["MANUAL_SCHEDULED_REVIEW"] == counts.get("MANUAL_SCHEDULED_REVIEW", 0) == 1
    assert agg["MANUAL"] == 5
    assert agg["RETIRE"] == 0 and agg["NO_WATCH_REQUIRED"] == 0
    assert agg["UNRESOLVED"] == 0
    assert agg["WATCH_INSTALLED"] == sum(1 for e in entries if e.get("spec_installed")) == 1
    assert agg["ATTESTED"] == 4
    assert agg["MANUAL_IMMEDIATE_OPEN"] == 0
    assert agg["stab_06a_closed"] is True, (
        "Geschlossen erst mit den vier Attestierungen — die Klassifikation allein reicht nicht."
    )


def test_keine_stillen_abschluesse(registry: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    """RETIRE/NO_WATCH_REQUIRED sind in dieser Runde ausdrücklich nicht vergeben."""
    states = {e["decision_state"] for e in entries}
    assert "RETIRE" not in states
    assert "NO_WATCH_REQUIRED" not in states
    assert registry["decision_states"]["RETIRE"] == "Nicht vergeben."


# --- Closure: ein Verdikt gilt erst mit Kette UND Archiv ---


def test_jedes_sofortige_verdikt_ist_attestiert_und_archiviert(
    entries: list[dict[str, Any]],
) -> None:
    """Attestiert heisst: in der Truth-Kette, mit Sequenz, mit Verifikations-Beleg.

    Ein Register, das ``open=false`` behauptet, ohne auf eine Kettenposition zu
    zeigen, waere genau die Sorte Selbstauskunft, gegen die STAB-06a gebaut wurde.
    """
    seqs = []
    for entry in entries:
        if entry["decision_state"] != "MANUAL_IMMEDIATE_VERDICT":
            continue
        att = entry.get("attestation") or {}
        pid = entry["prereg_id"]
        assert att.get("attested") is True, pid
        assert isinstance(att.get("truth_seq"), int), pid
        assert len(str(att.get("attestation_hash", ""))) == 64, pid
        assert len(str(att.get("verification_sha256", ""))) == 64, pid
        assert att.get("verdict_class") in {"MET", "NOT_MET", "CLOSED_NO_VERDICT"}, pid
        assert int(att.get("verification_clauses", 0)) > 0, pid
        assert entry.get("open") is False, pid
        assert len(entry.get("archive_contents") or []) >= 2, pid
        seqs.append(att["truth_seq"])
    assert sorted(seqs) == [102, 103, 104, 105]


def test_closure_belegt_append_only(registry: dict[str, Any]) -> None:
    """Vier neue Zeilen, keine bearbeitete — mit Zahlen statt Zusicherung."""
    closure = registry["closure"]
    assert closure["attestations_appended"] == 4
    assert closure["historical_rows_edited"] == 0
    assert closure["ledger_after_lines"] - closure["ledger_before_lines"] == 4
    assert closure["truth_chain_valid"] is True
    assert closure["truth_tip_seq"] == 105
    assert closure["manual_immediate_open"] == 0
    assert "byte-identisch" in closure["append_only_proof"]
    assert "skipped=11" in closure["idempotency_proof"]


def test_auflagen_stehen_in_der_attestierten_ueberschrift(
    entries: list[dict[str, Any]],
) -> None:
    """Die zwei Vorbehalte muessen im Verdikttext selbst stehen, nicht nur im Register."""
    by_id = {e["prereg_id"]: e for e in entries}
    cleanroom = by_id["8b21040ad7935a4a"]["attestation"]["verdict_headline"]
    assert "POST_HOC_SEAL" in cleanroom
    ln = by_id["0879a65c5fd01f65"]["attestation"]["verdict_headline"]
    assert "SAFETY_AXIS_ONLY" in ln
    assert not ln.startswith("PASS_"), (
        "Der Unterstrich ist keine Token-Grenze: 'PASS_SAFETY_AXIS_ONLY' wird als UNKNOWN "
        "klassifiziert und erzeugt RESOLUTION_HOLD statt RESOLVED."
    )


def test_verdikt_ueberschriften_sind_terminal_klassifizierbar(
    entries: list[dict[str, Any]],
) -> None:
    """Gegen die echte Klassifikationsfunktion, nicht gegen eine Annahme."""
    from app.research.prereg_maturity import _terminal_verdict_class

    for entry in entries:
        att = entry.get("attestation") or {}
        if not att.get("attested"):
            continue
        headline = att["verdict_headline"]
        assert _terminal_verdict_class(headline) == att["verdict_class"], (
            f"{entry['prereg_id']}: {headline[:60]!r} -> {_terminal_verdict_class(headline)}"
        )


def test_der_zuvor_ungeprueften_evidenz_wurde_nachgegangen(
    entries: list[dict[str, Any]],
) -> None:
    """8b21040a fuehrte latest_evidence_sha256=null — das Doc ist jetzt gefunden."""
    entry = next(e for e in entries if e["prereg_id"] == "8b21040ad7935a4a")
    resolved = entry["attestation"]["evidence_doc_resolved"]
    assert resolved["sha256"].startswith("e47eedc41a43a674")
    assert resolved["path"].endswith("KAI_Verifier_v0_1_CleanRoom_2026-07-12.md")


def test_offene_seal_konstanten_sind_jetzt_gemessen(entries: list[dict[str, Any]]) -> None:
    """836b1c7e fuehrte sie ausdruecklich als NICHT geprueft."""
    entry = next(e for e in entries if e["prereg_id"] == "836b1c7e28eed49a")
    measured = entry["attestation"]["previously_open_constants_now_measured"]
    joined = " ".join(measured)
    for token in (
        "runtime_baseline_sha=1d2e565ef847efaa019e58753a65dc8f6531b0dd",
        "11-invariants-5-active",
        "pipeline_scoped",
        "execution_influence=false",
        "INCONCLUSIVE=3",
    ):
        assert token in joined, token
    assert entry["attestation"]["verification_clauses"] == 14
