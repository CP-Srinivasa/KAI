"""STAB-2026-09-01 — INVALIDATED_PENDING_SUCCESSOR: abgebrochen, Nachfolger offen.

``INVALIDATED_BEFORE_MEASUREMENT`` verlangt ``replaced_by``. Beim Abbruch des
zweiten G8-Akts (``ebbf451f432cbc80``, 2026-09-01T20:38:51Z) existierte die
Nachfolge-ID noch gar nicht: sie faellt deterministisch aus dem DEPLOYTEN Code
(Mainline-SHA + evaluator_sha256 + health_notify_sha256 + Config-SHA), und der
Deploy stand aus.

Eine vorausberechnete Platzhalter-ID waere exakt der Fehler gewesen, den #843
teuer nachgewiesen hat: die erste Vorausberechnung ergab ``b7f9a8e204e40e23`` und
traf nicht, weil der gepinnte Evaluator danach noch einmal bearbeitet wurde.

Der Entscheid ist damit NICHT aufgeschoben — er ist getroffen, datiert und
gehasht. Nur die Verkettung zum Nachfolger fehlt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.invalidation_evidence import (
    EVIDENCE_SCHEMA,
    INSPECTION_SCOPE_DEFECT_PROOF,
    PROBLEM_ARTIFACT_MISSING,
    PROBLEM_CONTRADICTS_COUNT,
    PROBLEM_COUNT_MISSING,
    PROBLEM_ID_MISMATCH,
    PROBLEM_MIRROR_PIN_MISMATCH,
    PROBLEM_OUTCOME_INSPECTED,
    PROBLEM_SCOPE_MISSING,
    PROBLEM_SHA_MISMATCH,
    PROBLEM_SUBSTANTIVE_VERDICT,
    sha256_of_bytes,
    verify_invalidation_evidence,
)
from app.research.prereg_maturity import (
    INVALIDATED_STATES,
    INVALIDATION_SUCCESSOR_TRANSITION,
    SUPERVISING_DECISION_STATES,
    validate_invalidated_entry,
)

REGISTER = Path(__file__).resolve().parents[2] / "config" / "prereg_supervision.json"
ACT2 = "ebbf451f432cbc80"


def _register() -> dict:
    return json.loads(REGISTER.read_text(encoding="utf-8"))


def _entry(prereg_id: str) -> dict:
    for e in _register()["entries"]:
        if e.get("prereg_id") == prereg_id:
            return e
    raise AssertionError(f"{prereg_id} not in register")


def _valid_pending(**over) -> dict:
    base = {
        "decision_state": "INVALIDATED_PENDING_SUCCESSOR",
        "substantive_verdict": "NONE",
        "invalidation_reason": "MEASUREMENT_INSTRUMENT_DEFECT_DISCOVERED_POST_T0",
        "invalidated_at_utc": "2026-09-01T20:38:51Z",
        "replacement_pending": True,
        "replaced_by": None,
        "MATURITY_SPEC": "none",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------
def test_the_state_is_supervised_so_it_is_not_reported_as_a_gap() -> None:
    """An invalidated act is DECIDED, not overlooked.

    If the watchdog treated it as an oversight gap it would emit a finding about
    its own register every run — and that self-finding is precisely what poisoned
    the FIRST G8 act.
    """
    assert "INVALIDATED_PENDING_SUCCESSOR" in SUPERVISING_DECISION_STATES
    assert "INVALIDATED_BEFORE_MEASUREMENT" in SUPERVISING_DECISION_STATES


def test_a_wellformed_pending_entry_validates() -> None:
    assert validate_invalidated_entry(_valid_pending()) == []


def test_the_only_allowed_transition_is_to_before_measurement() -> None:
    assert INVALIDATION_SUCCESSOR_TRANSITION == (
        "INVALIDATED_PENDING_SUCCESSOR",
        "INVALIDATED_BEFORE_MEASUREMENT",
    )
    assert set(INVALIDATED_STATES) == set(INVALIDATION_SUCCESSOR_TRANSITION)


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS — exactly the ones the order demands
# --------------------------------------------------------------------------
def test_a_substantive_verdict_is_refused() -> None:
    """An aborted measurement must never carry a result. It measured nothing."""
    for verdict in ("MET", "NOT_MET", "INVALID", "INCONCLUSIVE"):
        errors = validate_invalidated_entry(_valid_pending(substantive_verdict=verdict))
        assert any("substantive_verdict" in e for e in errors), verdict


def test_a_watcher_is_refused() -> None:
    """A dead measurement must not keep being watched."""
    errors = validate_invalidated_entry(_valid_pending(watcher_id="kai-prereg-maturity"))
    assert any("watcher_id" in e for e in errors)


def test_a_review_date_is_refused() -> None:
    errors = validate_invalidated_entry(_valid_pending(next_review_utc="2026-09-15T14:00:00Z"))
    assert any("next_review_utc" in e for e in errors)


def test_a_cadence_is_refused() -> None:
    errors = validate_invalidated_entry(_valid_pending(cadence="one deadline"))
    assert any("cadence" in e for e in errors)


def test_a_maturity_spec_is_refused() -> None:
    errors = validate_invalidated_entry(_valid_pending(MATURITY_SPEC="back_edge_v2"))
    assert any("MATURITY_SPEC" in e for e in errors)


def test_a_placeholder_successor_id_is_refused() -> None:
    """THE point of this state. No invented replaced_by."""
    errors = validate_invalidated_entry(_valid_pending(replaced_by="deadbeefdeadbeef"))
    assert any("replaced_by" in e for e in errors)


def test_pending_without_the_pending_flag_is_refused() -> None:
    errors = validate_invalidated_entry(_valid_pending(replacement_pending=False))
    assert any("replacement_pending" in e for e in errors)


@pytest.mark.parametrize("missing", ["invalidation_reason", "invalidated_at_utc"])
def test_reason_and_timestamp_are_mandatory(missing: str) -> None:
    entry = _valid_pending()
    entry[missing] = None
    errors = validate_invalidated_entry(entry)
    assert any(missing in e for e in errors)


def test_the_successor_state_still_requires_a_real_id() -> None:
    """NEGATIVE CONTROL for the other side: the transition must not be free."""
    entry = _valid_pending(decision_state="INVALIDATED_BEFORE_MEASUREMENT")
    errors = validate_invalidated_entry(entry)
    assert any("replaced_by" in e for e in errors)

    entry["replaced_by"] = "abc123abc123abc1"
    entry.pop("replacement_pending", None)
    assert validate_invalidated_entry(entry) == []


def test_a_non_invalidated_state_is_not_policed_by_this_rule() -> None:
    assert validate_invalidated_entry({"decision_state": "WATCH", "watcher_id": "x"}) == []


# --------------------------------------------------------------------------
# The live register entry
# --------------------------------------------------------------------------
def test_g8_act2_is_recorded_as_aborted_without_a_result() -> None:
    e = _entry(ACT2)
    assert e["decision_state"] == "INVALIDATED_PENDING_SUCCESSOR"
    assert e["substantive_verdict"] == "NONE"
    assert e["invalidation_reason"] == "MEASUREMENT_INSTRUMENT_DEFECT_DISCOVERED_POST_T0"
    assert e["invalidated_at_utc"] == "2026-09-01T20:38:51Z"
    assert e["replacement_pending"] is True
    assert e["replaced_by"] is None
    assert validate_invalidated_entry(e) == []


def test_the_live_entry_carries_its_audit_artifact_hash() -> None:
    e = _entry(ACT2)
    assert e["audit_artifact"].endswith("G8_ACT2_INVALIDATION_20260901T203851Z.json")
    assert len(e["audit_artifact_sha256"]) == 64


def test_the_reason_is_the_instrument_not_the_branch_hash() -> None:
    """The distinction the operator insisted on, pinned in the record itself.

    A waiting branch changes nothing in production. The hard reason is that the
    RUNNING instrument was measured and found wrong.
    """
    rationale = _entry(ACT2)["rationale"]
    assert "ALTERSBLINDE" in rationale
    assert "NULL" in rationale
    assert "veraendert Produktion nicht" in rationale
    # Die korrigierte Fassung, nicht irgendeine Verneinung: "KEIN emitted-Count"
    # waere wieder die Behauptung, die der Beleg selbst widerlegt.
    assert "KEIN Evaluator gelaufen" in rationale
    assert "KEIN acted-Count gelesen" in rationale
    assert "insoweit eingesehen" in rationale  # Schreibweise ss/ss offen gelassen
    assert "KEIN emitted- oder acted-Count" not in rationale


# --------------------------------------------------------------------------
# Der Beleg selbst — fail-closed.
#
# Der Vorgaenger dieses Blocks hiess ``test_no_count_was_read`` und uebersprang
# sich, wenn das Artefakt fehlte. Es lag in ``KAI-mirror/``, ausserhalb des
# Repos — also fehlte es in CI immer. Der Waechter war gruen durch Abwesenheit
# und hat deshalb nie bemerkt, dass der Beleg sich selbst widersprach:
# ``emitted_count_inspected = false`` neben ``post_t0_emissions_observed = 15``.
#
# Jetzt liegt der kanonische Beleg IM Repo, der Spiegel muss byte-gleich sein,
# und ein fehlender Beleg ist ein Fehlschlag.
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
# Der Spiegel haengt am Benutzerverzeichnis, NICHT am Elternverzeichnis des
# Checkouts. Die Vorgaengerfassung leitete ihn aus ``REPO_ROOT.parent`` ab und
# zeigte damit auf ``.local/bin/KAI-mirror`` — ein Pfad, den es nie gab. Der
# Test uebersprang sich folglich auf JEDER Maschine.
MIRROR = Path.home() / "KAI-mirror" / "reports" / "G8_ACT2_INVALIDATION_20260901T203851Z.json"


def _evidence_doc() -> dict:
    entry = _entry(ACT2)
    return json.loads((REPO_ROOT / entry["audit_artifact"]).read_text(encoding="utf-8"))


def test_der_kanonische_beleg_liegt_im_repo_und_traegt_seinen_hash() -> None:
    entry = _entry(ACT2)
    path = REPO_ROOT / entry["audit_artifact"]
    assert path.is_file(), "kanonischer Beleg fehlt: " + str(entry["audit_artifact"])
    assert entry["audit_artifact"].startswith("artifacts/research/supervision/")
    assert sha256_of_bytes(path.read_bytes()) == entry["audit_artifact_sha256"]


def test_der_beleg_widerspricht_sich_nicht() -> None:
    """Die Regressionssperre gegen genau den Defekt vom 2026-09-01."""
    assert verify_invalidation_evidence(REPO_ROOT, _entry(ACT2)) == []


def test_der_beleg_benennt_die_einsicht_statt_sie_zu_leugnen() -> None:
    doc = _evidence_doc()
    nd = doc["not_done"]
    assert nd["emitted_count_inspected"] is True
    assert nd["emitted_inspection_scope"] == INSPECTION_SCOPE_DEFECT_PROOF
    assert nd["evaluator_executed"] is False
    assert nd["acted_count_inspected"] is False
    assert nd["outcome_inspected"] is False
    assert nd["interim_result_taken"] is False
    assert nd["substantive_outcome_evaluated"] is False
    # Die alte Behauptung darf nur noch ZITIERT werden — im Korrekturvermerk,
    # der erklaert, was falsch war. In der Aussage selbst hat sie nichts verloren.
    assert "No count of any kind was read" not in json.dumps(nd)
    assert "No count of any kind was read" in doc["correction"]["reason"]


def test_die_emissionszahl_bleibt_im_beleg_stehen() -> None:
    """Korrigiert wurde die Behauptung, nicht die Messung."""
    doc = _evidence_doc()
    proof = doc["evidence"]["age_blind_annotation_warning_proof"]
    assert proof["post_t0_emissions_observed"] == 15
    assert doc["substantive_verdict"] == "NONE"
    assert doc["invalidation_decided_at_utc"] == "2026-09-01T20:38:51Z"
    assert all(row["due_unannotated"] == 0 for row in proof["post_t0_replay"])


def test_die_vorgaengerfassung_ist_dokumentiert_nicht_geloescht() -> None:
    entry = _entry(ACT2)
    old = "a6974b985746272e8ec7ac08d65fe5bd158f4fa4ee6ffd075c02ba5537fcb727"
    assert entry["previous_invalidation_artifact_sha256"] == old
    assert entry["superseded_by_corrected_evidence"] == entry["audit_artifact_sha256"]
    assert entry["audit_artifact_sha256"] != old
    assert _evidence_doc()["correction"]["previous_artifact_sha256"] == old


def test_repo_und_spiegel_sind_derselbe_beleg() -> None:
    """Ohne Skip — der Vertrag lebt im Register, nicht in der Anwesenheit einer Datei.

    Die Vorgaengerfassung haengte an ``skipif(not MIRROR.exists())``. Auf dem
    CI-Runner gibt es kein ``~/KAI-mirror``, also uebersprang sie sich dort
    deterministisch: der Tests-Job von 7a80ec97 meldete 9433 passed / 8 skipped,
    und dieser evidenzkritische Guard war einer der acht. Eine gruene CI hatte
    ihn nie ausgefuehrt.

    Der Pin ``audit_artifact_mirror_sha256 == audit_artifact_sha256`` gilt
    IMMER — er ist eine Aussage des Registers, keine Eigenschaft der Platte.
    Die physische Byte-Pruefung kommt zusaetzlich obendrauf, wo der Spiegel
    liegt, und wird nicht zur Bedingung fuer den Test.
    """
    entry = _entry(ACT2)
    assert entry["audit_artifact_mirror_sha256"] == entry["audit_artifact_sha256"]
    assert entry["audit_artifact_mirror"].startswith("KAI-mirror/")
    assert verify_invalidation_evidence(REPO_ROOT, entry) == []

    if MIRROR.exists():  # Zusatz, nie Bedingung
        assert sha256_of_bytes(MIRROR.read_bytes()) == entry["audit_artifact_sha256"]


def test_ein_abweichender_spiegel_pin_ist_eine_zweite_wahrheit(tmp_path: Path) -> None:
    """Negativkontrolle zum Pin — sonst prueft die Zeile oben nichts."""
    root, entry = _sandbox(tmp_path, _good_doc())
    entry["audit_artifact_mirror_sha256"] = "c" * 64
    assert PROBLEM_MIRROR_PIN_MISMATCH in verify_invalidation_evidence(root, entry)


# --------------------------------------------------------------------------
# Negativkontrollen — jede muss FAIL erzeugen, sonst prueft der Waechter nichts.
# --------------------------------------------------------------------------


def _sandbox(tmp_path: Path, doc: dict, *, pin: str | None = None) -> tuple[Path, dict]:
    rel = "artifacts/research/supervision/x/evidence.json"
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
    target.write_bytes(raw)
    entry = {
        "prereg_id": doc.get("prereg_id", ACT2),
        "audit_artifact": rel,
        "audit_artifact_sha256": pin or sha256_of_bytes(raw),
    }
    return tmp_path, entry


def _good_doc() -> dict:
    return {
        "schema": EVIDENCE_SCHEMA,
        "prereg_id": ACT2,
        "substantive_verdict": "NONE",
        "evidence": {"proof": {"post_t0_emissions_observed": 15}},
        "not_done": {
            "outcome_inspected": False,
            "evaluator_executed": False,
            "acted_count_inspected": False,
            "interim_result_taken": False,
            "substantive_outcome_evaluated": False,
            "emitted_count_inspected": True,
            "emitted_inspection_scope": INSPECTION_SCOPE_DEFECT_PROOF,
        },
    }


def test_positivkontrolle_ein_sauberer_beleg_besteht(tmp_path: Path) -> None:
    root, entry = _sandbox(tmp_path, _good_doc())
    assert verify_invalidation_evidence(root, entry) == []


def test_negativ_fehlender_beleg(tmp_path: Path) -> None:
    entry = {
        "prereg_id": ACT2,
        "audit_artifact": "artifacts/research/supervision/x/weg.json",
        "audit_artifact_sha256": "0" * 64,
    }
    assert PROBLEM_ARTIFACT_MISSING in verify_invalidation_evidence(tmp_path, entry)


def test_negativ_ein_byte_geaendert(tmp_path: Path) -> None:
    root, entry = _sandbox(tmp_path, _good_doc())
    path = root / entry["audit_artifact"]
    path.write_bytes(path.read_bytes() + b" ")
    assert PROBLEM_SHA_MISMATCH in verify_invalidation_evidence(root, entry)


def test_negativ_falscher_pin_im_register(tmp_path: Path) -> None:
    root, entry = _sandbox(tmp_path, _good_doc(), pin="b" * 64)
    assert PROBLEM_SHA_MISMATCH in verify_invalidation_evidence(root, entry)


def test_negativ_einsicht_geleugnet_aber_zahl_genannt(tmp_path: Path) -> None:
    """Exakt der Defekt vom 2026-09-01."""
    doc = _good_doc()
    doc["not_done"]["emitted_count_inspected"] = False
    doc["not_done"].pop("emitted_inspection_scope")
    root, entry = _sandbox(tmp_path, doc)
    assert PROBLEM_CONTRADICTS_COUNT in verify_invalidation_evidence(root, entry)


def test_negativ_umfang_fehlt(tmp_path: Path) -> None:
    doc = _good_doc()
    doc["not_done"].pop("emitted_inspection_scope")
    root, entry = _sandbox(tmp_path, doc)
    assert PROBLEM_SCOPE_MISSING in verify_invalidation_evidence(root, entry)


def test_negativ_freier_text_als_umfang_genuegt_nicht(tmp_path: Path) -> None:
    doc = _good_doc()
    doc["not_done"]["emitted_inspection_scope"] = "kurz reingeschaut"
    root, entry = _sandbox(tmp_path, doc)
    assert PROBLEM_SCOPE_MISSING in verify_invalidation_evidence(root, entry)


def test_negativ_acted_count_gelesen(tmp_path: Path) -> None:
    doc = _good_doc()
    doc["not_done"]["acted_count_inspected"] = True
    root, entry = _sandbox(tmp_path, doc)
    assert PROBLEM_OUTCOME_INSPECTED in verify_invalidation_evidence(root, entry)


def test_negativ_sachverdikt_vorhanden(tmp_path: Path) -> None:
    doc = _good_doc()
    doc["substantive_verdict"] = "NOT_MET"
    root, entry = _sandbox(tmp_path, doc)
    assert PROBLEM_SUBSTANTIVE_VERDICT in verify_invalidation_evidence(root, entry)


def test_negativ_einsicht_behauptet_aber_keine_zahl(tmp_path: Path) -> None:
    doc = _good_doc()
    doc["evidence"] = {}
    root, entry = _sandbox(tmp_path, doc)
    assert PROBLEM_COUNT_MISSING in verify_invalidation_evidence(root, entry)


def test_negativ_beleg_gehoert_zu_einer_anderen_praereg(tmp_path: Path) -> None:
    doc = _good_doc()
    doc["prereg_id"] = "f0803d911744e0c2"
    root, entry = _sandbox(tmp_path, doc)
    entry["prereg_id"] = ACT2
    assert PROBLEM_ID_MISMATCH in verify_invalidation_evidence(root, entry)


# --------------------------------------------------------------------------
# Deklarationspflicht — durchgesetzt in CI, nicht als Produktionsalarm.
#
# Eine Warnung, auf die der Operator nicht reagieren kann, gehoert nicht in die
# Alarm-Population; genau solche Warnungen haben den zweiten G8-Akt vergiftet.
# Also erzwingt der Test, was der Waechter nicht ausrufen darf.
# --------------------------------------------------------------------------

#: ``f0803d911744e0c2`` wurde am 2026-09-01T12:05:00Z invalidiert, BEVOR es den
#: Beleg-Vertrag gab. Sein Beleg liegt in ``G8_PRE_T0.md`` und in #841 — als Prosa,
#: nicht als gepinntes Artefakt. Ausdruecklich benannt statt stillschweigend
#: uebergangen; die Menge darf nicht wachsen.
EVIDENCE_CONTRACT_EXEMPT = frozenset({"f0803d911744e0c2"})


def test_jede_invalidierung_deklariert_einen_beleg() -> None:
    fehlend = sorted(
        e["prereg_id"]
        for e in _register()["entries"]
        if str(e.get("decision_state")) in INVALIDATED_STATES
        and not e.get("audit_artifact")
        and e["prereg_id"] not in EVIDENCE_CONTRACT_EXEMPT
    )
    assert fehlend == [], "Invalidierung ohne gepinnten Beleg: " + ", ".join(fehlend)


def test_die_ausnahmeliste_waechst_nicht() -> None:
    assert EVIDENCE_CONTRACT_EXEMPT == frozenset({"f0803d911744e0c2"})


def test_alle_deklarierten_belege_tragen() -> None:
    """Positivkontrolle ueber das gesamte Register, nicht nur ueber Akt 2."""
    for e in _register()["entries"]:
        if str(e.get("decision_state")) in INVALIDATED_STATES and e.get("audit_artifact"):
            assert verify_invalidation_evidence(REPO_ROOT, e) == [], e["prereg_id"]
