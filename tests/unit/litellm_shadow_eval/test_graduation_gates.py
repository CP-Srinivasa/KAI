"""Gegenbeispiele aus dem externen Audit vom 27.09.2026 (Basis 04046c68).

Beide Datensaetze ergaben READY / ``primary_ready=true``, obwohl ein Mensch,
der die Freigabe liest, bei keinem von beiden PRIMARY verantworten koennte:

* G1 -- ein einziges qualitaetsbewertetes Paar (und das mit Totalausfall),
  unbelegte Identitaet, unbekannte Kosten, unbekannte Wiederholungen und vier
  nie erbrachte Ausfallnachweise.
* G2 -- hundert saubere Paare, versteckt hinter tausend Paaren, denen die
  Schattenseite fehlt.

Das Werkzeug aktiviert nichts. Aber sein Ergebnis ist die Vorlage fuer eine
menschliche PRIMARY-Freigabe -- ein falsches READY ist deshalb kein
Schoenheitsfehler, sondern eine fehlgeleitete Entscheidung.

Jedes Tor wird einzeln gezeigt: ausgehend von einem Datensatz, der unter der
STRENGEN Standardpolitik READY ist, kippt genau eine Eigenschaft -- und genau
ein Grund erscheint. Sonst waere nicht belegt, welches Tor was faengt.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from scripts.litellm_shadow_eval.engine import TOOL_VERSION, evaluate
from scripts.litellm_shadow_eval.loader import normalize_record
from scripts.litellm_shadow_eval.models import (
    RUNTIME_PROOF_FLAGS,
    EvaluationReport,
    GraduationPolicy,
    GraduationStatus,
    RuntimeEvidenceFlags,
)
from scripts.litellm_shadow_eval.policy import (
    PolicyError,
    policy_from_dict,
    policy_hash,
    runtime_flags_from_dict,
)
from scripts.litellm_shadow_eval.reporting import comparable_json, markdown_summary

from tests.unit.litellm_shadow_eval.helpers import (
    proof,
    proven_flags,
    row,
    runtime_evidence,
    write_jsonl,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)

Mutation = Callable[[int, dict[str, Any], dict[str, Any]], None]


def _rows(count: int = 100, mutate: Mutation | None = None) -> list[dict[str, Any]]:
    zeilen: list[dict[str, Any]] = []
    for number in range(count):
        direct = row("DIRECT", number, quality_score=0.8)
        shadow = row("SHADOW", number, quality_score=0.8)
        if mutate is not None:
            mutate(number, direct, shadow)
        zeilen.extend((direct, shadow))
    return zeilen


def _run(
    tmp_path: Path,
    zeilen: list[dict[str, Any]],
    *,
    flags: RuntimeEvidenceFlags | None = None,
    now: datetime = NOW,
    **policy: Any,
) -> EvaluationReport:
    return evaluate(
        [write_jsonl(tmp_path / "evidence.jsonl", list(zeilen))],
        GraduationPolicy(**policy),
        flags if flags is not None else proven_flags(),
        clock=lambda: now,
    )


def _reasons(report: EvaluationReport) -> tuple[str, ...]:
    return report.decisions["standard"].reasons


# ---------------------------------------------------------------------------
# Die Gegenbeispiele des Audits.
# ---------------------------------------------------------------------------


def _g1_rows() -> list[dict[str, Any]]:
    zeilen: list[dict[str, Any]] = []
    for number in range(100):
        direct = row("DIRECT", number)
        shadow = row(
            "SHADOW",
            number,
            identity_proven=False,
            cost_known=False,
            cost_usd=None,
            retry_count=None,
        )
        if number == 0:
            direct["quality_score"] = 1.0
            shadow["quality_score"] = 0.0
        zeilen.extend((direct, shadow))
    return zeilen


def test_g1_ein_qualitaetspaar_und_unbelegte_betriebsdaten_sind_nicht_ready(
    tmp_path: Path,
) -> None:
    # Wie im Audit: nackte Booleans, vier Ausfallnachweise nicht erbracht.
    flags = proven_flags(
        referenced=False,
        timeout_retry_proven=False,
        rate_limit_retry_proven=False,
        server_error_retry_proven=False,
        circuit_proven=False,
    )
    report = _run(tmp_path, _g1_rows(), flags=flags)
    entscheidung = report.decisions["standard"]

    assert entscheidung.status is GraduationStatus.NOT_READY
    assert entscheidung.primary_ready is False
    assert report.to_dict()["primary_ready_routes"] == []
    for grund in (
        "QUALITY_SAMPLE_TOO_SMALL",
        "QUALITY_REGRESSION",
        "IDENTITY_NOT_PROVEN",
        "COST_NOT_FULLY_KNOWN",
        "ATTEMPT_ACCOUNTING_INCOMPLETE",
        "TIMEOUT_RETRY_NOT_PROVEN",
        "RATE_LIMIT_RETRY_NOT_PROVEN",
        "SERVER_ERROR_RETRY_NOT_PROVEN",
        "CIRCUIT_NOT_PROVEN",
        "RUNTIME_PROOF_UNREFERENCED:off_mode_proven",
    ):
        assert grund in entscheidung.reasons, (grund, entscheidung.reasons)


def _g2_rows() -> list[dict[str, Any]]:
    nur_direkt = [row("DIRECT", number) for number in range(100, 1100)]
    return [*_rows(100), *nur_direkt]


def test_g2_tausend_halbe_paare_verstecken_sich_nicht_hinter_hundert_ganzen(
    tmp_path: Path,
) -> None:
    report = _run(tmp_path, _g2_rows())
    entscheidung = report.decisions["standard"]
    metrics = report.metrics["standard"]

    assert metrics.complete_pair_count == 100
    assert metrics.incomplete_pair_count == 1000
    assert metrics.unexplained_incomplete_pair_count == 1000
    assert metrics.unexplained_incomplete_rate == pytest.approx(1000 / 1100, abs=1e-9)
    assert entscheidung.status is GraduationStatus.NOT_READY
    assert entscheidung.primary_ready is False
    assert entscheidung.reasons == ("INCOMPLETE_PAIRS_UNEXPLAINED",)


# ---------------------------------------------------------------------------
# Ausgangspunkt: vollstaendige, belegte Evidenz ist unter der Standardpolitik
# READY. Ohne diesen Nachweis beweisen die Einzeltests unten nichts.
# ---------------------------------------------------------------------------


def test_vollstaendig_belegte_evidenz_ist_unter_der_standardpolitik_ready(
    tmp_path: Path,
) -> None:
    report = _run(tmp_path, _rows())
    entscheidung = report.decisions["standard"]

    assert entscheidung.status is GraduationStatus.READY, entscheidung.reasons
    assert entscheidung.reasons == ()
    assert entscheidung.primary_ready is True
    assert report.to_dict()["primary_ready_routes"] == ["standard"]


# ---------------------------------------------------------------------------
# a) Qualitaet: Umfang und Grenze.
# ---------------------------------------------------------------------------


def _score_only(first: int) -> Mutation:
    def mutate(number: int, direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        if number >= first:
            direct.pop("quality_score")
            shadow.pop("quality_score")

    return mutate


def test_ein_einziges_unbewertetes_paar_verfehlt_die_volle_abdeckung(tmp_path: Path) -> None:
    report = _run(tmp_path, _rows(mutate=_score_only(99)))

    assert report.metrics["standard"].quality.coverage == 0.99
    assert _reasons(report) == ("QUALITY_SAMPLE_TOO_SMALL",)


def test_gelockerte_abdeckung_braucht_trotzdem_die_mindestzahl(tmp_path: Path) -> None:
    """Eine Quote allein laesst bei kleiner Population zu wenig Paare uebrig."""
    report = _run(tmp_path, _rows(mutate=_score_only(60)), minimum_quality_coverage=0.5)
    assert _reasons(report) == ("QUALITY_SAMPLE_TOO_SMALL",)


def test_gelockerte_qualitaetsstichprobe_ist_moeglich_aber_steht_im_hash(tmp_path: Path) -> None:
    locker = {"minimum_quality_coverage": 0.5, "minimum_quality_sample_count": 50}
    report = _run(tmp_path, _rows(mutate=_score_only(60)), **locker)

    assert report.decisions["standard"].status is GraduationStatus.READY
    assert policy_hash(GraduationPolicy(**locker)) != policy_hash(GraduationPolicy())


def test_die_qualitaetsmindestzahl_folgt_der_stichprobenmindestzahl() -> None:
    """``None`` heisst: dieselbe Zusage wie ``minimum_sample_count`` -- auch je Route."""
    assert GraduationPolicy().minimum_quality_sample_count is None
    assert GraduationPolicy().minimum_quality_coverage == 1.0


def _shadow_quality(value: float) -> Mutation:
    def mutate(_: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        shadow["quality_score"] = value

    return mutate


def test_schlechtere_schattenqualitaet_ueber_der_grenze_ist_nicht_ready(tmp_path: Path) -> None:
    report = _run(tmp_path, _rows(mutate=_shadow_quality(0.77)))
    assert _reasons(report) == ("QUALITY_REGRESSION",)


def test_schattenqualitaet_genau_auf_der_grenze_ist_zulaessig(tmp_path: Path) -> None:
    report = _run(tmp_path, _rows(mutate=_shadow_quality(0.78)))
    assert report.decisions["standard"].status is GraduationStatus.READY, _reasons(report)


def test_gemessene_regression_blockiert_auch_bei_beratender_qualitaetspolitik(
    tmp_path: Path,
) -> None:
    """Beratend heisst: FEHLENDE Belege blockieren nicht. Ein gemessener Schaden schon."""
    report = _run(tmp_path, _rows(mutate=_shadow_quality(0.5)), require_quality_evidence=False)
    assert "QUALITY_REGRESSION" in _reasons(report)
    assert report.decisions["standard"].status is GraduationStatus.NOT_READY


def test_zu_kleine_stichprobe_ist_bei_beratender_politik_nur_ein_hinweis(tmp_path: Path) -> None:
    report = _run(tmp_path, _rows(mutate=_score_only(1)), require_quality_evidence=False)

    assert report.decisions["standard"].status is GraduationStatus.READY
    assert _reasons(report) == ("QUALITY_SAMPLE_TOO_SMALL_ADVISORY",)


# ---------------------------------------------------------------------------
# b) Identitaet: gesetzte Felder sind kein Beweis.
# ---------------------------------------------------------------------------


def test_gesetzte_identitaetsfelder_ohne_beweis_sind_nicht_ready(tmp_path: Path) -> None:
    def mutate(number: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        if number == 0:
            shadow["identity_proven"] = False

    report = _run(tmp_path, _rows(mutate=mutate))

    assert report.metrics["standard"].provider_identity_known_rate == 1.0
    assert report.metrics["standard"].unknown_identity_count == 1
    assert _reasons(report) == ("IDENTITY_NOT_PROVEN",)


# ---------------------------------------------------------------------------
# c) Versuchs- und Kostenzaehlung: UNKNOWN rutscht nicht durch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("feld", ["retry_count", "attempt_count"])
def test_unbekannte_versuchszaehlung_ist_nicht_ready(tmp_path: Path, feld: str) -> None:
    def mutate(number: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        if number == 0:
            shadow[feld] = None

    report = _run(tmp_path, _rows(mutate=mutate))

    assert report.metrics["standard"].unknown_attempt_accounting_count == 1
    assert _reasons(report) == ("ATTEMPT_ACCOUNTING_INCOMPLETE",)


def test_unbekannte_wiederholungen_umgehen_die_retry_grenze_nicht(tmp_path: Path) -> None:
    """Vorher: nur UNKNOWN in der Verteilung -> keine Zahl > 2 -> kein Befund."""

    def mutate(_: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        shadow["retry_count"] = None

    report = _run(tmp_path, _rows(mutate=mutate))

    assert report.metrics["standard"].retry_distribution == {"UNKNOWN": 100}
    assert report.decisions["standard"].status is GraduationStatus.NOT_READY
    assert "ATTEMPT_ACCOUNTING_INCOMPLETE" in _reasons(report)


def test_abgeschaltete_versuchszaehlung_bleibt_als_hinweis_sichtbar(tmp_path: Path) -> None:
    def mutate(_: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        shadow["retry_count"] = None

    report = _run(tmp_path, _rows(mutate=mutate), require_complete_attempt_accounting=False)

    assert report.decisions["standard"].status is GraduationStatus.READY
    assert _reasons(report) == ("ATTEMPT_ACCOUNTING_INCOMPLETE_ADVISORY",)


def test_unbekannte_kosten_sind_nicht_ready(tmp_path: Path) -> None:
    def mutate(number: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        if number == 0:
            shadow.update({"cost_known": False, "cost_usd": None})

    report = _run(tmp_path, _rows(mutate=mutate))

    assert report.metrics["standard"].cost_known_rate == 0.99
    assert _reasons(report) == ("COST_NOT_FULLY_KNOWN",)


def test_abgeschaltete_kostenpflicht_bleibt_als_hinweis_sichtbar(tmp_path: Path) -> None:
    def mutate(_: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        shadow.update({"cost_known": False, "cost_usd": None})

    report = _run(tmp_path, _rows(mutate=mutate), require_cost_known=False)

    assert report.decisions["standard"].status is GraduationStatus.READY
    assert _reasons(report) == ("COST_NOT_FULLY_KNOWN_ADVISORY",)


# ---------------------------------------------------------------------------
# d) Unvollstaendige Paare: nur ein vorregistrierter Grund erklaert sie.
# ---------------------------------------------------------------------------


def _mit_halbem_paar(reason: str | None) -> list[dict[str, Any]]:
    extra = row("DIRECT", 500)
    if reason is not None:
        extra["exclusion_reason"] = reason
    return [*_rows(), extra]


def test_ein_einziges_unerklaertes_halbes_paar_blockiert(tmp_path: Path) -> None:
    report = _run(tmp_path, _mit_halbem_paar(None))

    metrics = report.metrics["standard"]
    assert metrics.unexplained_incomplete_pair_count == 1
    assert metrics.exclusion_reason_distribution == {"(none)": 1}
    assert _reasons(report) == ("INCOMPLETE_PAIRS_UNEXPLAINED",)


def test_ein_nicht_vorregistrierter_grund_erklaert_nichts(tmp_path: Path) -> None:
    """``circuit_open`` ist kein harmloser Ausschluss, sondern ein Schattenausfall."""
    report = _run(
        tmp_path, _mit_halbem_paar("circuit_open"), allowed_exclusion_reasons=("sampled_out",)
    )

    metrics = report.metrics["standard"]
    assert metrics.unexplained_incomplete_pair_count == 1
    assert metrics.exclusion_reason_distribution == {"circuit_open": 1}
    assert _reasons(report) == ("INCOMPLETE_PAIRS_UNEXPLAINED",)


def test_ein_vorregistrierter_grund_erklaert_das_halbe_paar(tmp_path: Path) -> None:
    report = _run(
        tmp_path, _mit_halbem_paar("sampled_out"), allowed_exclusion_reasons=("sampled_out",)
    )

    metrics = report.metrics["standard"]
    assert metrics.incomplete_pair_count == 1
    assert metrics.unexplained_incomplete_pair_count == 0
    assert metrics.exclusion_reason_distribution == {"sampled_out": 1}
    assert report.decisions["standard"].status is GraduationStatus.READY, _reasons(report)


def test_eine_ausdrueckliche_toleranz_fuer_unerklaerte_paare_greift(tmp_path: Path) -> None:
    report = _run(tmp_path, _mit_halbem_paar(None), maximum_unexplained_incomplete_rate=0.01)

    assert report.metrics["standard"].unexplained_incomplete_rate == pytest.approx(1 / 101)
    assert report.decisions["standard"].status is GraduationStatus.READY, _reasons(report)


def test_ein_ausschlussgrund_entfernt_kein_vollstaendiges_paar(tmp_path: Path) -> None:
    """Sonst liesse sich jeder Schattenausfall wegerklaeren."""

    def mutate(number: int, _direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        if number < 5:
            shadow.update({"success": False, "exclusion_reason": "sampled_out"})

    report = _run(tmp_path, _rows(mutate=mutate), allowed_exclusion_reasons=("sampled_out",))

    assert report.metrics["standard"].complete_pair_count == 100
    assert report.metrics["standard"].shadow_success_rate == 0.95
    assert "SUCCESS_RATE_TOO_LOW" in _reasons(report)


@pytest.mark.parametrize("wert", [7, True, "zwei worte", "x" * 65, "prompt: gib mir"])
def test_ein_ausschlussgrund_ist_ein_code_kein_freitext(wert: object) -> None:
    raw = row("DIRECT", exclusion_reason=wert)
    record, issues = normalize_record(raw, record_ref="1:e.jsonl:1")

    assert record is None
    assert "INVALID_EXCLUSION_REASON" in {issue.code for issue in issues}


def test_ein_fehlender_ausschlussgrund_ist_rueckwaertskompatibel() -> None:
    record, issues = normalize_record(row("DIRECT"), record_ref="1:e.jsonl:1")
    assert not issues and record is not None
    assert record.exclusion_reason is None

    record, issues = normalize_record(
        row("DIRECT", exclusion_reason=None), record_ref="1:e.jsonl:2"
    )
    assert not issues and record is not None
    assert record.exclusion_reason is None


# ---------------------------------------------------------------------------
# e) Ausfallnachweise werden verlangt, nicht nur gefuehrt.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "grund"),
    [
        ("timeout_retry_proven", "TIMEOUT_RETRY_NOT_PROVEN"),
        ("rate_limit_retry_proven", "RATE_LIMIT_RETRY_NOT_PROVEN"),
        ("server_error_retry_proven", "SERVER_ERROR_RETRY_NOT_PROVEN"),
        ("circuit_proven", "CIRCUIT_NOT_PROVEN"),
    ],
)
def test_fehlender_ausfallnachweis_ist_nicht_ready(tmp_path: Path, flag: str, grund: str) -> None:
    report = _run(tmp_path, _rows(), flags=proven_flags(**{flag: False}))
    assert _reasons(report) == (grund,)


# ---------------------------------------------------------------------------
# f) Betriebsnachweise sind datierte, referenzierte Artefakte.
# ---------------------------------------------------------------------------


def test_nackte_booleans_gelten_nicht_als_beleg(tmp_path: Path) -> None:
    report = _run(tmp_path, _rows(), flags=proven_flags(referenced=False))

    assert report.decisions["standard"].status is GraduationStatus.NOT_READY
    assert set(_reasons(report)) == {
        f"RUNTIME_PROOF_UNREFERENCED:{flag}" for flag in RUNTIME_PROOF_FLAGS
    }


def test_nackte_booleans_nur_mit_ausdruecklich_gelockerter_politik(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        _rows(),
        flags=proven_flags(referenced=False),
        require_referenced_runtime_evidence=False,
    )
    assert report.decisions["standard"].status is GraduationStatus.READY, _reasons(report)


def test_ein_veralteter_nachweis_ist_nicht_ready(tmp_path: Path) -> None:
    alt = (NOW - timedelta(days=31)).isoformat()
    report = _run(tmp_path, _rows(), flags=proven_flags(proven_at=alt))

    assert set(_reasons(report)) == {f"RUNTIME_PROOF_STALE:{flag}" for flag in RUNTIME_PROOF_FLAGS}


def test_ein_nachweis_genau_an_der_altersgrenze_gilt(tmp_path: Path) -> None:
    grenze = (NOW - timedelta(days=30)).isoformat()
    report = _run(tmp_path, _rows(), flags=proven_flags(proven_at=grenze))
    assert report.decisions["standard"].status is GraduationStatus.READY, _reasons(report)


def test_ein_nachweis_aus_der_zukunft_umgeht_die_altersgrenze_nicht(tmp_path: Path) -> None:
    zukunft = (NOW + timedelta(days=3650)).isoformat()
    report = _run(tmp_path, _rows(), flags=proven_flags(proven_at=zukunft))

    assert set(_reasons(report)) == {
        f"RUNTIME_PROOF_IN_FUTURE:{flag}" for flag in RUNTIME_PROOF_FLAGS
    }


def test_ohne_altersgrenze_zaehlt_das_alter_nicht(tmp_path: Path) -> None:
    alt = (NOW - timedelta(days=400)).isoformat()
    report = _run(
        tmp_path,
        _rows(),
        flags=proven_flags(proven_at=alt),
        maximum_runtime_proof_age_days=None,
    )
    assert report.decisions["standard"].status is GraduationStatus.READY, _reasons(report)


def test_ein_nicht_verlangter_nachweis_muss_nicht_referenziert_sein(tmp_path: Path) -> None:
    roh = runtime_evidence()
    roh["circuit_proven"] = True
    report = _run(
        tmp_path, _rows(), flags=runtime_flags_from_dict(roh), require_circuit_proven=False
    )
    assert report.decisions["standard"].status is GraduationStatus.READY, _reasons(report)


@pytest.mark.parametrize(
    "kaputt",
    [
        {"artifact_sha256": "abc"},
        {"artifact_sha256": "g" * 64},
        {"proven_at": "2026-09-03T00:00:00"},
        {"proven_at": "gestern"},
        {"artifact": ""},
        {"version": "  "},
        {"proven": "ja"},
        {"erfunden": 1},
    ],
)
def test_ein_unvollstaendiges_nachweisobjekt_wird_abgelehnt(kaputt: dict[str, Any]) -> None:
    objekt = {**proof("off_mode_proven"), **kaputt}
    with pytest.raises(PolicyError):
        runtime_flags_from_dict({"off_mode_proven": objekt})


@pytest.mark.parametrize("fehlt", ["artifact", "artifact_sha256", "proven_at", "version"])
def test_ein_erbrachter_nachweis_braucht_alle_metadaten(fehlt: str) -> None:
    objekt = proof("off_mode_proven")
    objekt.pop(fehlt)
    with pytest.raises(PolicyError):
        runtime_flags_from_dict({"off_mode_proven": objekt})


def test_die_gate_schalter_bleiben_booleans() -> None:
    with pytest.raises(PolicyError):
        runtime_flags_from_dict({"trading_gate_changed": proof("trading_gate_changed")})


def test_ein_negativer_nachweis_darf_ohne_artefakt_kommen() -> None:
    flags = runtime_flags_from_dict({"off_mode_proven": {"proven": False}})
    assert flags.off_mode_proven is False


def test_der_bericht_nennt_die_nachweisartefakte(tmp_path: Path) -> None:
    report = _run(tmp_path, _rows())
    payload = report.to_dict()
    eintrag = payload["runtime_evidence"]["rollback_proven"]
    erwartet = proof("rollback_proven")

    assert payload["schema_version"] == "litellm-shadow-eval-report/v2"
    assert payload["tool_version"] == TOOL_VERSION == "1.1.0"
    assert eintrag == {
        "proven": True,
        "referenced": True,
        "artifact": erwartet["artifact"],
        "artifact_sha256": erwartet["artifact_sha256"],
        "proven_at": "2026-09-03T00:00:00+00:00",
        "version": "04046c68",
    }
    assert payload["runtime_evidence"]["trading_gate_changed"] is False

    markdown = markdown_summary(report)
    assert erwartet["artifact_sha256"] in markdown
    assert "2026-09-03T00:00:00+00:00" in markdown
    assert erwartet["artifact"] in markdown


def test_ein_unbelegter_nachweis_steht_im_bericht_als_unbelegt(tmp_path: Path) -> None:
    report = _run(tmp_path, _rows(), flags=proven_flags(referenced=False))
    eintrag = report.to_dict()["runtime_evidence"]["off_mode_proven"]

    assert eintrag["proven"] is True
    assert eintrag["referenced"] is False
    assert eintrag["artifact"] is None


# ---------------------------------------------------------------------------
# g) Die Qualitaetsstichprobe ist reproduzierbar benannt.
# ---------------------------------------------------------------------------


def test_die_qualitaetsstichprobe_ist_per_hash_nachvollziehbar(tmp_path: Path) -> None:
    report = _run(tmp_path, _rows(mutate=_score_only(60)), minimum_quality_coverage=0.5)
    flach = report.to_dict()["metrics"]["standard"]
    schluessel = sorted(f"evaluation:eval-{number}" for number in range(60))
    erwartet = hashlib.sha256("".join(f"{key}\n" for key in schluessel).encode()).hexdigest()

    assert flach["quality_sample_count"] == 60
    assert flach["quality_coverage"] == 0.6
    assert flach["quality_sample_keys_sha256"] == erwartet


def test_der_stichprobenhash_haengt_nicht_an_der_zeilenreihenfolge(tmp_path: Path) -> None:
    zeilen = _rows(mutate=_score_only(60))
    a = _run(tmp_path, zeilen, minimum_quality_coverage=0.5)
    gemischt = list(zeilen)
    random.Random(11).shuffle(gemischt)
    b = _run(tmp_path, gemischt, minimum_quality_coverage=0.5)

    assert comparable_json(a) == comparable_json(b)


def test_ohne_qualitaet_gibt_es_keinen_stichprobenhash(tmp_path: Path) -> None:
    report = _run(tmp_path, _rows(mutate=_score_only(0)))
    flach = report.to_dict()["metrics"]["standard"]

    assert flach["quality_sample_keys_sha256"] is None
    assert flach["quality_coverage"] == 0.0


# ---------------------------------------------------------------------------
# Politik: jede neue Schranke ist Teil der Zusage und des Hashes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("feld", "gelockert"),
    [
        ("minimum_quality_sample_count", 10),
        ("minimum_quality_coverage", 0.5),
        ("maximum_quality_regression", 0.5),
        ("require_complete_attempt_accounting", False),
        ("require_cost_known", False),
        ("maximum_unexplained_incomplete_rate", 0.5),
        ("allowed_exclusion_reasons", ["sampled_out"]),
        ("require_timeout_retry_proven", False),
        ("require_rate_limit_retry_proven", False),
        ("require_server_error_retry_proven", False),
        ("require_circuit_proven", False),
        ("require_referenced_runtime_evidence", False),
        ("maximum_runtime_proof_age_days", 365),
    ],
)
def test_jede_neue_lockerung_aendert_den_policy_hash(feld: str, gelockert: object) -> None:
    streng = policy_from_dict({})
    assert policy_hash(policy_from_dict({feld: gelockert})) != policy_hash(streng), feld


@pytest.mark.parametrize(
    "roh",
    [
        {"minimum_quality_sample_count": 0},
        {"minimum_quality_sample_count": True},
        {"minimum_quality_coverage": 1.5},
        {"maximum_quality_regression": -0.1},
        {"maximum_unexplained_incomplete_rate": 2},
        {"maximum_runtime_proof_age_days": 0},
        {"maximum_runtime_proof_age_days": 1.5},
        {"allowed_exclusion_reasons": "sampled_out"},
        {"allowed_exclusion_reasons": ["zwei worte"]},
        {"require_cost_known": "ja"},
    ],
)
def test_ungueltige_neue_politikwerte_werden_abgelehnt(roh: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        policy_from_dict(roh)


def test_die_reihenfolge_der_ausschlussgruende_aendert_den_hash_nicht() -> None:
    a = policy_from_dict({"allowed_exclusion_reasons": ["b", "a"]})
    b = policy_from_dict({"allowed_exclusion_reasons": ["a", "b", "a"]})
    assert a.allowed_exclusion_reasons == ("a", "b")
    assert policy_hash(a) == policy_hash(b)


def test_eine_routenausnahme_kann_die_neuen_schranken_setzen(tmp_path: Path) -> None:
    zeilen = _rows(mutate=_score_only(60))
    report = _run(
        tmp_path,
        zeilen,
        route_overrides={
            "standard": {"minimum_quality_coverage": 0.5, "minimum_quality_sample_count": 50}
        },
    )
    assert report.decisions["standard"].status is GraduationStatus.READY, _reasons(report)


# ---------------------------------------------------------------------------
# Invarianten bleiben unberuehrt.
# ---------------------------------------------------------------------------


def test_consensus_bleibt_auch_bei_vollstaendig_belegter_evidenz_ohne_primary(
    tmp_path: Path,
) -> None:
    def mutate(_: int, direct: dict[str, Any], shadow: dict[str, Any]) -> None:
        direct["purpose"] = shadow["purpose"] = "consensus"

    report = _run(tmp_path, _rows(mutate=mutate))
    entscheidung = report.decisions["standard"]

    assert entscheidung.status is GraduationStatus.READY, entscheidung.reasons
    assert entscheidung.primary_ready is False
    assert report.to_dict()["decisions"]["standard"]["consensus_primary_allowed"] is False
    assert report.to_dict()["primary_ready_routes"] == []
