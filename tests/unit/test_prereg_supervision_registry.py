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
    # Neu 2026-08-31: die terminierte Wiedervorlage HAT stattgefunden und endete
    # in einem Abschluss in der Truth-Kette. Ohne diesen Zustand konnte das
    # Register einen vollzogenen Review gar nicht ausdruecken — er haette ein
    # Datum in der Zukunft luegen oder ``next_review_utc: null`` tragen muessen,
    # was dieser Vertrag zu Recht zurueckweist.
    #
    # Der Name ist ABSICHTLICH eng (Operator-Entscheidung 2026-08-31): das
    # kuerzere ``REVIEW_COMPLETED`` haette gelesen werden koennen als "irgendein
    # Review ist fertig". Ein Review kann aber sehr wohl abgeschlossen werden und
    # als Ergebnis "weiter beobachten, neuer Termin" tragen — das ist NICHT
    # dieser Zustand. Hier gilt genau eine Kette:
    #   MANUAL_SCHEDULED_REVIEW -> Review durchgefuehrt
    #   -> Truth-Kette terminalisiert den Claim -> SCHEDULED_REVIEW_COMPLETED
    "SCHEDULED_REVIEW_COMPLETED",
    "RETIRE",
    "NO_WATCH_REQUIRED",
    # 2026-09-01: eine vor ihrem Ende formal invalidierte Messung ist terminal —
    # sie wird nicht weiterbeobachtet und traegt kein Sachverdikt.
    "INVALIDATED_BEFORE_MEASUREMENT",
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
    "SCHEDULED_REVIEW_COMPLETED": (
        "owner",
        "previous_decision_state",
        "closure_reason",
        "substantive_verdict",
        "terminal_verdict_class",
        "truth_seq",
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


def test_sofort_faellige_verdikte_tragen_due_now(entries: list[dict[str, Any]]) -> None:
    """Praezisiert 2026-09-01: DUE_NOW gilt fuer OFFENE Sofortverdikte.

    Vorher forderte diese Regel DUE_NOW fuer *jeden* MANUAL_IMMEDIATE_VERDICT —
    auch fuer laengst attestierte. Vier Eintraege standen dadurch als faellige
    Aufgabe im Register, obwohl ihre Verdikte am 2026-08-27 attestiert wurden
    (Truth-seq 102-105). Ein Aufsichtsregister, das erledigte Arbeit als offen
    fuehrt, ist genauso falsch wie eines, das offene Arbeit verschweigt.
    """
    for entry in entries:
        if entry["decision_state"] != "MANUAL_IMMEDIATE_VERDICT":
            continue
        pid = entry["prereg_id"]
        assert entry.get("watcher_id") is None, pid
        if entry.get("open") is False:
            assert entry["next_review_utc"] is None, pid
            att = entry.get("attestation") or {}
            assert att.get("truth_seq"), pid
            assert att.get("verification_sha256"), pid
        else:
            assert entry["next_review_utc"] == "DUE_NOW", pid


# --- STAB-06a-Reconciliation 2026-09-01 -------------------------------------
# Die vier Verdikte wurden am 2026-08-27 attestiert (Truth-seq 102-105), aber das
# Aufsichtsregister wusste bis 2026-09-01 nichts davon. Es fuehrte sie als
# DUE_NOW-Aufgaben. Diese Tests pinnen die Reconciliation — sie erzeugen KEINE
# neue Attestierung, sie halten fest, dass Register und Kette uebereinstimmen.

_STAB06A_ATTESTIERT = {
    "81c41ae153e5d427": 102,
    "836b1c7e28eed49a": 103,
    "8b21040ad7935a4a": 104,
    "0879a65c5fd01f65": 105,
}


def test_stab06a_verdikte_sind_im_register_geschlossen(
    entries: list[dict[str, Any]],
) -> None:
    """Alle vier tragen ihre Truth-seq und gelten als geschlossen."""
    by_id = {e["prereg_id"]: e for e in entries}
    for pid, seq in _STAB06A_ATTESTIERT.items():
        entry = by_id[pid]
        att = entry.get("attestation") or {}
        assert att.get("attested") is True, pid
        assert att.get("truth_seq") == seq, f"{pid}: {att.get('truth_seq')} statt {seq}"
        assert att.get("verification_sha256"), pid
        assert entry.get("open") is False, pid


def test_kein_attestiertes_verdikt_bleibt_als_aufgabe_stehen(
    entries: list[dict[str, Any]],
) -> None:
    """Keine DUE_NOW-Zombies: attestiert und trotzdem faellig gibt es nicht."""
    zombies = [
        e["prereg_id"]
        for e in entries
        if (e.get("attestation") or {}).get("attested") is True
        and e.get("next_review_utc") == "DUE_NOW"
    ]
    assert zombies == [], f"attestiert, aber weiterhin faellig: {zombies}"


def test_die_verdikt_besonderheiten_bleiben_am_eintrag(
    entries: list[dict[str, Any]],
) -> None:
    """POST_HOC_SEAL und SAFETY_AXIS_ONLY duerfen nicht zu blossem PASS verflachen."""
    by_id = {e["prereg_id"]: e for e in entries}
    assert "POST_HOC_SEAL" in (by_id["8b21040ad7935a4a"]["attestation"]["verdict_headline"])
    assert "SAFETY_AXIS_ONLY" in (by_id["0879a65c5fd01f65"]["attestation"]["verdict_headline"])


def test_die_821_invarianten_bleiben_vollstaendig_erhalten(
    registry: dict[str, Any],
) -> None:
    """Die Reconciliation darf #821 nicht zurueckdrehen.

    Ein naiver Merge des alten Closure-PRs #794 haette genau diese vier Regeln
    geloescht: er trug den Stand VOR #821 (7 Invarianten statt 10).
    """
    text = " ".join(registry["invariants"])
    for fragment in (
        "SCHEDULED_REVIEW_COMPLETED entsteht NUR aus MANUAL_SCHEDULED_REVIEW",
        "terminalen Truth-Chain-Beleg",
        "Ein durchgefuehrter Review OHNE terminalen Abschluss",
        "SCHEDULED_REVIEW_COMPLETED bekommt NIE einen Watcher",
    ):
        assert fragment in text, f"#821-Invariante verloren: {fragment}"
    assert len(registry["invariants"]) >= 10


def test_die_uebrigen_claims_bleiben_unberuehrt(entries: list[dict[str, Any]]) -> None:
    """Nur die vier STAB-06a-Eintraege aendern sich — sonst nichts."""
    by_id = {e["prereg_id"]: e for e in entries}
    assert by_id["6751bc3364d39ec2"]["decision_state"] == "SCHEDULED_REVIEW_COMPLETED"
    assert by_id["6751bc3364d39ec2"].get("next_review_utc") is None
    assert by_id["4a3b1b0c5a94b73c"]["decision_state"] == "SUPERSEDED"
    assert by_id["c489079289070a8c"]["decision_state"] == "WATCH"
    assert by_id["c489079289070a8c"]["next_review_utc"] not in (None, "DUE_NOW")
    for pid in ("6751bc3364d39ec2", "4a3b1b0c5a94b73c", "c489079289070a8c"):
        assert pid not in _STAB06A_ATTESTIERT


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
    # 2026-09-01: G8 setzte T0 fuer operator_back_edge_v1 (f0803d911744e0c2).
    # Der neue Claim bekam im selben Zug seinen Aufsichtseintrag — ohne ihn
    # meldete der Health-Check sofort eine Aufsichtsluecke, und diese
    # selbstverschuldete Meldung waere in die 14-Tage-Population der Praereg
    # eingegangen, die sie beobachten soll. TOTAL 7 -> 8, WATCH 1 -> 2.
    # 2026-09-01, zweiter G8-Akt: der erste (f0803d911744e0c2) wurde formal
    # invalidiert (POST_T0_INSTRUMENTATION_CONTAMINATION) und der Nachfolger
    # b7f9a8e204e40e23 bekam seine Aufsicht VOR der Registrierung — sonst
    # meldet der Health-Check die Aufsichtsluecke der Messung selbst in ihre
    # eigene Population. TOTAL 8 -> 9, WATCH bleibt 2 (v1 raus, v2 rein).
    assert agg["TOTAL"] == len(entries) == 9
    assert agg["WATCH"] == counts.get("WATCH", 0) == 2
    assert (
        agg["INVALIDATED_BEFORE_MEASUREMENT"]
        == counts.get("INVALIDATED_BEFORE_MEASUREMENT", 0)
        == 1
    )
    assert agg["SUPERSEDED"] == counts.get("SUPERSEDED", 0) == 1
    assert agg["MANUAL_IMMEDIATE_VERDICT"] == counts.get("MANUAL_IMMEDIATE_VERDICT", 0) == 4
    # 2026-08-31: die einzige terminierte Wiedervorlage (6751bc33) wurde
    # durchgefuehrt und endete in CLOSED_UNMEASURABLE (Truth-seq 113). Sie
    # zaehlt seitdem als SCHEDULED_REVIEW_COMPLETED — die Summe der manuellen Faelle
    # bleibt 5, nur ihre Verteilung hat sich verschoben.
    assert agg["MANUAL_SCHEDULED_REVIEW"] == counts.get("MANUAL_SCHEDULED_REVIEW", 0) == 0
    assert agg["SCHEDULED_REVIEW_COMPLETED"] == counts.get("SCHEDULED_REVIEW_COMPLETED", 0) == 1
    assert agg["MANUAL"] == 5
    assert agg["RETIRE"] == 0 and agg["NO_WATCH_REQUIRED"] == 0
    assert agg["UNRESOLVED"] == 0
    assert agg["WATCH_INSTALLED"] == sum(1 for e in entries if e.get("spec_installed")) == 1
    assert agg["stab_06a_closed"] is False, (
        "Die Klassifikation allein schliesst STAB-06a nicht — erst die vier Attestierungen."
    )


def test_kein_zustand_ohne_definition_wird_benutzt(
    registry: dict[str, Any], entries: list[dict[str, Any]]
) -> None:
    """Ein Name, dessen Definition woertlich "Nicht vergeben." lautet, ist keine Klasse.

    Vorher stand hier eine feste Liste aus RETIRE und NO_WATCH_REQUIRED. Die
    Regel dahinter ist aber allgemeiner und wichtiger: wer einen undefinierten
    Namen benutzt, DEFINIERT ihn dabei — und zwar unter dem Druck, einen Claim
    schliessen zu wollen. Genau diese Versuchung gab es am 2026-08-31 bei K1,
    wo RETIRE oberflaechlich gepasst haette ("wegen Irrelevanz beendet").
    Die Sperre haengt jetzt an der Definition, nicht an zwei Namen.
    """
    undefiniert = {
        name
        for name, text in registry["decision_states"].items()
        if text.strip() == "Nicht vergeben."
    }
    assert undefiniert, "Erwartet mindestens einen als 'Nicht vergeben.' markierten Zustand"

    benutzt = {e["decision_state"] for e in entries}
    verletzung = undefiniert & benutzt
    assert not verletzung, (
        f"Zustaende ohne Definition in Benutzung: {sorted(verletzung)} - erst definieren "
        "(Pflichtfelder, Abgrenzung, Praezedenz), dann vergeben."
    )


def test_undefinierte_zustaende_sind_auch_nicht_auswaehlbar(registry: dict[str, Any]) -> None:
    """Gegenprobe im Code: sie duerfen gar nicht erst zur Wahl stehen."""
    from app.research.terminal_classes import TERMINAL_CLASSES

    undefiniert = {
        name
        for name, text in registry["decision_states"].items()
        if text.strip() == "Nicht vergeben."
    }
    assert not (undefiniert & set(TERMINAL_CLASSES))
    assert registry["decision_states"]["RETIRE"] == "Nicht vergeben."


def test_ein_vollzogener_review_traegt_keinen_offenen_termin(
    entries: list[dict[str, Any]],
) -> None:
    """Abgeschlossen heisst abgeschlossen — kein Watcher, kein Datum, keine Reifezaehlung.

    Spiegelbild zur SUPERSEDED-Regel. Ohne diese Invariante koennte ein
    geschlossener Claim wieder auf der Handlungsliste auftauchen, sobald jemand
    versehentlich ein Datum ergaenzt.
    """
    for entry in entries:
        if entry["decision_state"] != "SCHEDULED_REVIEW_COMPLETED":
            continue
        assert entry.get("next_review_utc") is None, entry["prereg_id"]
        assert entry.get("watcher_id") is None, entry["prereg_id"]
        assert entry.get("spec_installed") is False, entry["prereg_id"]
        assert entry["substantive_verdict"] == "NONE", entry["prereg_id"]
        assert isinstance(entry["truth_seq"], int), entry["prereg_id"]


def test_der_geschlossene_sec_claim_nennt_seine_unerreichbare_population(
    entries: list[dict[str, Any]],
) -> None:
    """Der Abschluss muss den GRUND tragen, nicht nur den Zustand.

    'Zu langsam' und 'unmessbar' sind verschiedene Dinge: das erste laedt zum
    Warten ein, das zweite verbietet es. Live gemessen am 2026-08-31: 19
    Dokumente seit der Versiegelung, davon **0** mit Richtung und **0** mit
    Ticker — auch n=100 haette 0 auswertbare Ereignisse ergeben.
    """
    entry = next(e for e in entries if e["prereg_id"] == "6751bc3364d39ec2")

    assert entry["decision_state"] == "SCHEDULED_REVIEW_COMPLETED"
    assert entry["closure_reason"] == "UNMEASURABLE_POPULATION"
    assert "0 mit Richtung und 0 mit Ticker" in entry["rationale"]
    assert "neue Prae-Registrierung" in entry["reactivation_condition"]


# ── Die drei Invarianten des Zustands (Operator-Vorgabe 2026-08-31) ──────────
#
# Der Zustand ist eng gemeint, und Enge muss man erzwingen, sonst franst sie aus.
# Aufsichtsstatus und Sachverdikt bleiben ausdruecklich getrennt: ``decision_state``
# sagt, WER wann hingesehen hat; ``outcome``/``substantive_verdict``/
# ``terminal_verdict_class`` sagen, WAS dabei herauskam.


def test_invariante_1_entsteht_nur_aus_einer_terminierten_wiedervorlage(
    entries: list[dict[str, Any]],
) -> None:
    """Vorgaenger ist explizit, nicht erzaehlt.

    Ohne ``previous_decision_state`` liesse sich der Zustand aus jedem beliebigen
    anderen heraus vergeben — etwa aus ``WATCH``, wo nie ein Mensch einen Termin
    hatte. Der Uebergang ist Teil der Aussage.
    """
    for entry in entries:
        if entry["decision_state"] != "SCHEDULED_REVIEW_COMPLETED":
            continue
        assert entry["previous_decision_state"] == "MANUAL_SCHEDULED_REVIEW", entry["prereg_id"]


def test_invariante_2_braucht_einen_terminalen_truth_chain_beleg(
    entries: list[dict[str, Any]],
) -> None:
    """Ein Abschluss ohne Ketten-Beleg ist eine Behauptung, kein Abschluss.

    Der Vertrag pruefen kann hier nur die FORM (seq + terminale Klasse); dass die
    Kette diesen Claim wirklich terminal fuehrt, prueft der Health-Check auf dem
    Pi gegen ``artifacts/`` — das Verzeichnis ist nicht im Repo, eine CI-Pruefung
    waere hier also nur Theater.
    """
    terminal_classes = {"MET", "NOT_MET", "CLOSED_NO_VERDICT"}
    for entry in entries:
        if entry["decision_state"] != "SCHEDULED_REVIEW_COMPLETED":
            continue
        assert isinstance(entry["truth_seq"], int) and entry["truth_seq"] > 0, entry["prereg_id"]
        assert entry["terminal_verdict_class"] in terminal_classes, entry["prereg_id"]


def test_invariante_3_ein_review_ohne_abschluss_ist_dieser_zustand_nicht(
    entries: list[dict[str, Any]],
) -> None:
    """Der eigentliche Zweck des engen Namens.

    Ein durchgefuehrter Review darf ergeben: weiter beobachten, neuer Termin,
    keine terminale Entscheidung. Das ist ``WATCH`` oder erneut
    ``MANUAL_SCHEDULED_REVIEW`` mit einem ECHTEN Datum — niemals dieser Zustand.
    Mechanisch heisst das: wer hier steht, traegt ein Ergebnis, und wer ein
    offenes Ergebnis traegt, steht nicht hier.
    """
    for entry in entries:
        if entry["decision_state"] != "SCHEDULED_REVIEW_COMPLETED":
            continue
        assert entry.get("next_review_utc") is None, entry["prereg_id"]
        assert entry.get("watcher_id") is None, entry["prereg_id"]
        assert entry.get("cadence") is None, entry["prereg_id"]
        assert entry["outcome"], entry["prereg_id"]
        assert entry["substantive_verdict"] in ("NONE", "MET", "NOT_MET"), entry["prereg_id"]


def test_aufsichtsstatus_und_sachverdikt_bleiben_getrennte_felder(
    entries: list[dict[str, Any]],
) -> None:
    """``decision_state`` ist keine Ergebnisspalte — sonst verschwimmt beides.

    ``6751bc33`` ist der Praezedenzfall: Aufsicht abgeschlossen (Status), aber
    OHNE Sachverdikt (``substantive_verdict = NONE``). Wer beides in ein Feld
    zoege, muesste hier luegen.
    """
    entry = next(e for e in entries if e["prereg_id"] == "6751bc3364d39ec2")

    assert entry["decision_state"] == "SCHEDULED_REVIEW_COMPLETED"
    assert entry["outcome"] == "CLOSED_UNMEASURABLE"
    assert entry["substantive_verdict"] == "NONE"
    assert entry["terminal_verdict_class"] == "CLOSED_NO_VERDICT"
    assert entry["truth_seq"] == 113
    assert entry["closure_reason"] == "UNMEASURABLE_POPULATION"
