"""Ledger ↔ Reifeblick: jeder versiegelte Claim erscheint genau einmal, wahr.

Live gemessen am 2026-08-26 (Pi, ``kai trading prereg-maturity --json``):

* ``prereg_ledger.jsonl`` fuehrt **19** versiegelte Prae-Regs, der Reifeblick
  zeigte **14** Zeilen. Die fuenf fehlenden (``f676bcf5``, ``5872f817``,
  ``722f1593``, ``6e23c682``, ``9cab81fa``) tragen ALLE ein terminales Verdikt
  in der Truth-Kette — und waren trotzdem unsichtbar, weil nur Spec-Zeilen
  und unbeobachtete Claims gerendert wurden. „Entschieden" sah aus wie
  „existiert nicht".
* ``0879a65c`` (LN) stand als ``UNWATCHED`` mit ``offchain_verdict: False``,
  obwohl ``ln_reconciliation_verdict.jsonl`` ein ``PASS`` traegt: die
  Seitenablage-Suche kannte nur ``prereg_verdicts.jsonl``. Der Reifeblick
  behauptete „kein Verdikt", wo eines lag — die falsche Handlung
  („Regel anwenden" statt „attestieren").
* ``prereg-list --json`` hatte kein ``state``-Feld — die Ledger-Sicht und die
  Reife-Sicht liessen sich nicht programmatisch abgleichen.

Die Invariante ist ein Abgleich, keine neue Wahrheitsquelle: terminal bleibt
allein die verifizierte Truth-Kette; ein Off-Chain-Verdikt aendert den
Zustand nur von „unbeobachtet" zu „unattestiert".
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.research import prereg_reconciliation
from app.research.prereg_maturity import (
    STATE_RESOLVED,
    STATE_UNWATCHED,
    STATE_VERDICT_UNATTESTED,
    TRUTH_LEDGER_RELPATH,
    build_maturity_alert,
    compute_maturity,
    find_unwatched_preregs,
)
from app.research.prereg_reconciliation import (
    RECON_STATE_RESOLVED,
    RECON_STATE_UNWATCHED,
    RECON_STATE_VERDICT_UNATTESTED,
    RECON_STATE_WATCHED,
    classify_ledger_entries,
    reconcile_ledger_view,
)
from app.truth.attestation import compute_attestation
from app.truth.ledger import append_attestation

_NOW = datetime(2026, 8, 26, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_ambient_supervision_register(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein Test liest versehentlich das echte Aufsichtsregister aus dem Repo.

    Der Default ist CWD-relativ (Haus-Stil, wie ``DEFAULT_PREREG_LEDGER_PATH``),
    also haengt er am Arbeitsverzeichnis des Testlaufs. Tests, die einen
    Zustand behaupten, muessen ihr Register selbst mitbringen.
    """
    monkeypatch.setattr(
        prereg_reconciliation,
        "DEFAULT_SUPERVISION_REGISTER",
        tmp_path_factory.mktemp("no-register") / "absent.json",
    )


def test_the_production_default_points_at_the_repo_register() -> None:
    """Positivkontrolle zur Fixture: der echte Default darf nicht ins Leere zeigen."""
    with_default = importlib.reload(prereg_reconciliation).DEFAULT_SUPERVISION_REGISTER
    assert with_default == Path("config/prereg_supervision.json")
    assert Path(__file__).resolve().parents[2].joinpath(with_default).is_file()


_RESOLVED = "f676bcf5a7a1bfb6"  # attestiert NOT_MET, in keiner Wachliste
_WATCHED = "00c75a76a2b0e78b"  # Deadline-Spec, kein Verdikt
_LN = "0879a65c5fd01f65"  # PASS nur in ln_reconciliation_verdict.jsonl
_SEC = "6751bc3364d39ec2"  # NOT_MET nur in prereg_verdicts.jsonl
# Synthetische ID: der Fall "weder Spec noch Verdikt" muss unabhaengig davon
# testbar bleiben, welche Claims die produktive Wachliste gerade fuehrt.
# Vorher stand hier c489079289070a8c — der bekam am 2026-08-27 per STAB-06a
# einen Deadline-Spec (M3-Frist 29.09.) und ist seitdem WATCHED, worauf der
# CLI-Test brach. Ein Fixture, das an der Doktrin haengt, misst die Doktrin,
# nicht die Zustandsfunktion.
_NAKED = "deadbeef00000001"

_SPEC: dict[str, Any] = {
    "name": "k1_channel_audit_resonance",
    "prereg_id": _WATCHED,
    "kind": "deadline",
    "since_utc": "2026-07-04T12:51:11.469459+00:00",
    "window_end_utc": "2026-08-03T12:51:11.469459+00:00",
    "n_target": 0,
}


_SEALED_AT = "2026-07-01T00:00:00+00:00"


def _seal(root: Path, prereg_id: str, name: str, *, created: str = _SEALED_AT) -> None:
    path = root / "research" / "prereg_ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema": "prereg/v1",
        "prereg_id": prereg_id,
        "name": name,
        "direction": "neutral",
        "horizon": "30d",
        "success_criteria": "irrelevant fuer diesen Test",
        "sample_size_target": 5,
        "created_at_utc": created,
        "gate": None,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _attest(root: Path, prereg_id: str, verdict: str) -> None:
    payload = {"schema_version": 1, "prereg_id": prereg_id, "verdict": verdict}
    append_attestation(
        "verdict",
        compute_attestation(payload)["hash"],
        payload,
        path=root / TRUTH_LEDGER_RELPATH,
        mirror_audit=False,
        attested_at_utc="2026-08-06T03:00:00+00:00",
    )


def _offchain(root: Path, relname: str, *rows: dict[str, Any]) -> None:
    path = root / "research" / relname
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _pi_like_artifacts(root: Path) -> None:
    """Fuenf Claims, fuenf verschiedene Wahrheitslagen — wie live am 26.08."""
    _seal(root, _RESOLVED, "funding_premium_meanrev_1h")
    _seal(root, _WATCHED, "k1_channel_audit_resonance", created=_SPEC["since_utc"])
    _seal(root, _LN, "ln_reconciliation_shadow_integrity_v1")
    _seal(root, _SEC, "sec_filing_timing")
    _seal(root, _NAKED, "synthetischer_claim_ohne_aufsicht")
    _attest(root, _RESOLVED, "NOT_MET at pre-registered gate (n=308>=200)")
    _offchain(
        root,
        "ln_reconciliation_verdict.jsonl",
        {"prereg_id": _LN, "verdict": "IMMATURE", "passed": False},
        {"prereg_id": _LN, "verdict": "PASS", "passed": True},
    )
    _offchain(root, "prereg_verdicts.jsonl", {"prereg_id": _SEC, "verdict": "NOT_MET"})


# --- classify_ledger_entries: EINE Zustandsfunktion fuer Ledger, Reifeblick, Health ---


def test_jeder_ledger_eintrag_bekommt_genau_einen_abgleichszustand(tmp_path: Path) -> None:
    _pi_like_artifacts(tmp_path)

    rows = classify_ledger_entries(tmp_path, specs=(_SPEC,))

    by_id = {r["prereg_id"]: r for r in rows}
    assert list(by_id) == [_RESOLVED, _WATCHED, _LN, _SEC, _NAKED], "Ledger-Reihenfolge, je einmal"
    assert by_id[_RESOLVED]["state"] == RECON_STATE_RESOLVED
    assert by_id[_RESOLVED]["verdict_class"] == "NOT_MET"
    assert by_id[_WATCHED]["state"] == RECON_STATE_WATCHED
    assert by_id[_LN]["state"] == RECON_STATE_VERDICT_UNATTESTED
    assert by_id[_LN]["offchain_verdicts"] == [
        {"source": "research/ln_reconciliation_verdict.jsonl", "verdict_class": "MET"}
    ]
    assert by_id[_SEC]["state"] == RECON_STATE_VERDICT_UNATTESTED
    assert by_id[_SEC]["offchain_verdicts"] == [
        {"source": "research/prereg_verdicts.jsonl", "verdict_class": "NOT_MET"}
    ]
    assert by_id[_NAKED]["state"] == RECON_STATE_UNWATCHED
    assert by_id[_NAKED]["offchain_verdicts"] == []


def test_offchain_immature_allein_ist_kein_verdikt(tmp_path: Path) -> None:
    """Nur das JUENGSTE Verdikt je Seitenablage zaehlt — und nur ein terminales."""
    _seal(tmp_path, _LN, "ln_reconciliation_shadow_integrity_v1")
    _offchain(
        tmp_path,
        "ln_reconciliation_verdict.jsonl",
        {"prereg_id": _LN, "verdict": "IMMATURE", "passed": False},
    )

    (row,) = classify_ledger_entries(tmp_path, specs=())

    assert row["state"] == RECON_STATE_UNWATCHED
    assert row["offchain_verdicts"] == []


def test_truth_kette_schlaegt_seitenablage(tmp_path: Path) -> None:
    """Ein attestiertes Verdikt schliesst — auch wenn die Seitenablage widerspricht."""
    _seal(tmp_path, _SEC, "sec_filing_timing")
    _attest(tmp_path, _SEC, "NOT_MET at registered gate")
    _offchain(tmp_path, "prereg_verdicts.jsonl", {"prereg_id": _SEC, "verdict": "MET"})

    (row,) = classify_ledger_entries(tmp_path, specs=())

    assert row["state"] == RECON_STATE_RESOLVED
    assert row["verdict_class"] == "NOT_MET"


def test_doppelte_registrierung_kollabiert_auf_eine_zeile(tmp_path: Path) -> None:
    _seal(tmp_path, _NAKED, "synthetischer_claim_ohne_aufsicht")
    _seal(tmp_path, _NAKED, "synthetischer_claim_ohne_aufsicht")

    rows = classify_ledger_entries(tmp_path, specs=())

    assert [r["prereg_id"] for r in rows] == [_NAKED]


# --- compute_maturity: der Reifeblick ist VOLLSTAENDIG ---


async def test_reifeblick_zeigt_jeden_versiegelten_claim_genau_einmal(tmp_path: Path) -> None:
    _pi_like_artifacts(tmp_path)

    rows = await compute_maturity(
        None,  # type: ignore[arg-type] -- Deadline-Spec beruehrt die Session nie
        specs=(_SPEC,),
        artifacts_dir=tmp_path,
        now=_NOW,
    )

    ids = [r["prereg_id"] for r in rows]
    assert sorted(ids) == sorted([_RESOLVED, _WATCHED, _LN, _SEC, _NAKED])
    assert len(ids) == len(set(ids))
    by_id = {r["prereg_id"]: r for r in rows}
    resolved = by_id[_RESOLVED]
    assert resolved["state"] == STATE_RESOLVED
    assert resolved["state_source"] == "truth_ledger"
    assert resolved["due"] is False
    assert resolved["resolution"]["verdict_class"] == "NOT_MET"
    assert resolved["name"] == "funding_premium_meanrev_1h"
    assert by_id[_LN]["state"] == STATE_VERDICT_UNATTESTED
    assert by_id[_LN]["per_source"]["offchain_verdict"] is True
    assert by_id[_SEC]["state"] == STATE_VERDICT_UNATTESTED
    assert by_id[_NAKED]["state"] == STATE_UNWATCHED


async def test_abgleich_invariante_meldet_luecken_und_dubletten(tmp_path: Path) -> None:
    _pi_like_artifacts(tmp_path)
    view = await compute_maturity(
        None,  # type: ignore[arg-type]
        specs=(_SPEC,),
        artifacts_dir=tmp_path,
        now=_NOW,
    )

    ok = reconcile_ledger_view(tmp_path, view)
    assert ok["ok"] is True
    assert ok["missing_from_view"] == []
    assert ok["duplicated_in_view"] == []
    assert ok["not_in_ledger"] == []
    assert ok["ledger_count"] == 5 and ok["view_count"] == 5

    broken = reconcile_ledger_view(tmp_path, [r for r in view if r["prereg_id"] != _RESOLVED])
    assert broken["ok"] is False
    assert broken["missing_from_view"] == [_RESOLVED]

    doubled = reconcile_ledger_view(tmp_path, [*view, view[0]])
    assert doubled["ok"] is False
    assert doubled["duplicated_in_view"] == [view[0]["prereg_id"]]

    foreign = reconcile_ledger_view(tmp_path, [*view, {"prereg_id": "deadbeefdeadbeef"}])
    assert foreign["ok"] is False
    assert foreign["not_in_ledger"] == ["deadbeefdeadbeef"]


def test_unattestiertes_verdikt_ist_ein_eigener_zustand_kein_unwatched(tmp_path: Path) -> None:
    _seal(tmp_path, _LN, "ln_reconciliation_shadow_integrity_v1")
    _offchain(
        tmp_path,
        "ln_reconciliation_verdict.jsonl",
        {"prereg_id": _LN, "verdict": "PASS", "passed": True},
    )

    (row,) = find_unwatched_preregs(tmp_path, specs=(), resolutions={})

    assert row["state"] == STATE_VERDICT_UNATTESTED
    assert row["due"] is True, "attestieren ist eine Handlung — kein Reifegrad"
    assert row["per_source"]["offchain_verdict"] is True
    assert row["per_source"]["offchain_sources"] == ["research/ln_reconciliation_verdict.jsonl"]
    assert "attestieren" in row["note"]
    alert = build_maturity_alert([row])
    assert alert is not None
    assert STATE_VERDICT_UNATTESTED in alert
    assert "NICHT attestiert" in alert


# --- Health-Befund: Zerlegung nach Zustand, nie nur eine Summe ---


def test_health_befund_zerlegt_nach_zustand_und_nennt_ids(tmp_path: Path) -> None:
    from app.alerts.health_check import _check_prereg_reconciliation

    _pi_like_artifacts(tmp_path)

    issues = _check_prereg_reconciliation(tmp_path, specs=(_SPEC,))

    assert [i.component for i in issues] == ["prereg_reconciliation"]
    issue = issues[0]
    assert issue.severity == "warning"
    for token in (
        "ledger=5",
        "RESOLVED=1",
        "WATCHED=1",
        "VERDICT_UNATTESTED=2",
        "UNWATCHED=1",
        _LN[:16],
        _SEC[:16],
        _NAKED[:16],
    ):
        assert token in issue.message, token


def test_health_befund_schweigt_wenn_alles_beobachtet_oder_entschieden(tmp_path: Path) -> None:
    from app.alerts.health_check import _check_prereg_reconciliation

    _seal(tmp_path, _RESOLVED, "funding_premium_meanrev_1h")
    _seal(tmp_path, _WATCHED, "k1_channel_audit_resonance")
    _attest(tmp_path, _RESOLVED, "NOT_MET at gate")

    assert _check_prereg_reconciliation(tmp_path, specs=(_SPEC,)) == []


def test_health_befund_ohne_ledger_ist_kein_befund(tmp_path: Path) -> None:
    """Existenz des Ledgers wacht ``prereg_ledger_presence`` — nicht doppelt melden."""
    from app.alerts.health_check import _check_prereg_reconciliation

    assert _check_prereg_reconciliation(tmp_path, specs=(_SPEC,)) == []


def test_health_befund_wachliste_ohne_ledger_eintrag_ist_kritisch(tmp_path: Path) -> None:
    """Ein Spec fuer eine nie versiegelte ID ist Wachlisten-Drift — fail-loud."""
    from app.alerts.health_check import _check_prereg_reconciliation

    _seal(tmp_path, _RESOLVED, "funding_premium_meanrev_1h")
    _attest(tmp_path, _RESOLVED, "NOT_MET at gate")
    ghost = {**_SPEC, "prereg_id": "deadbeefdeadbeef"}

    issues = _check_prereg_reconciliation(tmp_path, specs=(ghost,))

    assert [i.severity for i in issues] == ["critical"]
    assert "deadbeefdeadbeef" in issues[0].message


def test_kaputte_truth_kette_ist_kritisch_nicht_stumm(tmp_path: Path) -> None:
    from app.alerts.health_check import _check_prereg_reconciliation

    _seal(tmp_path, _RESOLVED, "funding_premium_meanrev_1h")
    _attest(tmp_path, _RESOLVED, "NOT_MET at gate")
    truth = tmp_path / TRUTH_LEDGER_RELPATH
    truth.write_text(truth.read_text(encoding="utf-8").replace("NOT_MET", "MET"), encoding="utf-8")

    issues = _check_prereg_reconciliation(tmp_path, specs=())

    assert [i.severity for i in issues] == ["critical"]
    assert "invalid_ledger" in issues[0].message


# --- prereg-list --json: die Ledger-Sicht traegt denselben Zustand ---


def test_prereg_list_json_traegt_abgleichszustand(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from app.cli.commands.trading import trading_app

    _pi_like_artifacts(tmp_path)
    ledger = tmp_path / "research" / "prereg_ledger.jsonl"

    result = CliRunner().invoke(
        trading_app,
        ["prereg-list", "--ledger-path", str(ledger), "--artifacts-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    states = {r["prereg_id"]: r["state"] for r in rows}
    assert states == {
        _RESOLVED: RECON_STATE_RESOLVED,
        _WATCHED: RECON_STATE_WATCHED,
        _LN: RECON_STATE_VERDICT_UNATTESTED,
        _SEC: RECON_STATE_VERDICT_UNATTESTED,
        _NAKED: RECON_STATE_UNWATCHED,
    }
    by_id = {r["prereg_id"]: r for r in rows}
    assert by_id[_RESOLVED]["verdict_class"] == "NOT_MET"
    assert by_id[_LN]["offchain_verdicts"][0]["source"].endswith("ln_reconciliation_verdict.jsonl")
    assert by_id[_WATCHED]["watched"] is True


def test_prereg_list_json_ledger_ausserhalb_der_artefakte_faellt_ehrlich_zurueck(
    tmp_path: Path,
) -> None:
    """Ein fremder ``--ledger-path`` hat keine Truth-Kette: nichts ist RESOLVED."""
    from typer.testing import CliRunner

    from app.cli.commands.trading import trading_app

    ledger = tmp_path / "copy" / "prereg_ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    _seal(tmp_path / "elsewhere", _RESOLVED, "funding_premium_meanrev_1h")
    ledger.write_text(
        (tmp_path / "elsewhere" / "research" / "prereg_ledger.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    args = ["prereg-list", "--ledger-path", str(ledger), "--json"]
    result = CliRunner().invoke(trading_app, args)

    assert result.exit_code == 0, result.output
    (row,) = json.loads(result.output)
    assert row["state"] == RECON_STATE_UNWATCHED
    assert row["verdict_class"] is None


# --- Board: unattestiert ist so dringlich wie unbeobachtet ---


def test_board_stuft_unattestiertes_verdikt_wie_unbeobachtet_ein() -> None:
    from app.observability.operator_board_live import STATE_UNWATCHED as BOARD_UNWATCHED
    from app.observability.operator_board_live import _board_state

    assert _board_state({"state": STATE_VERDICT_UNATTESTED, "due": True}) == BOARD_UNWATCHED


# ---------------------------------------------------------------------------
# Aufsichtsregister — Befund 2026-08-31
#
# ``config/prereg_supervision.json`` traegt seit dem 2026-08-27 eine
# Operator-Aufsichtsentscheidung je Claim. Der Waechter kannte die Datei nicht
# und meldete ``6751bc33`` taeglich als „in KEINER Wachliste, kein Verdikt" —
# waehrend das Register fuer genau diesen Claim MANUAL_SCHEDULED_REVIEW mit
# Termin 2026-09-15 und Entscheidungsfrage fuehrt. Der Alarm war unwahr, nicht
# nur unschoen: er behauptete eine Aufsichtsluecke, wo eine Aufsicht steht
# ([[feedback_watchlists_must_reconcile_against_source]]).
#
# Die Gegenprobe ist Teil des Auftrags: das Register darf KEIN Stummschalter
# werden. Ein faelliger Termin bleibt faellig, und ein Registereintrag mit
# unbekanntem oder leerem Zustand zaehlt weiter als Aufsichtsluecke.
# ---------------------------------------------------------------------------

from app.research.prereg_reconciliation import (  # noqa: E402
    RECON_STATE_SUPERVISED,
    load_supervision_register,
)

_SUPERVISED = "6751bc3364d39ec2"


def _register(path: Path, *entries: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "prereg_supervision/v1", "entries": list(entries)}),
        encoding="utf-8",
    )
    return path


def test_a_registered_claim_is_supervised_not_an_oversight_gap(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _seal(root, _SUPERVISED, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {
            "prereg_id": _SUPERVISED,
            "name": "sec_filing_timing",
            "decision_state": "MANUAL_SCHEDULED_REVIEW",
            "owner": "operator",
            "next_review_utc": "2026-09-15T00:00:00+00:00",
            "decision_question": "Frist rueckwirkend setzen oder Population verbreitern?",
        },
    )
    (row,) = classify_ledger_entries(root, specs=(), supervision_register=reg)
    assert row["state"] == RECON_STATE_SUPERVISED
    assert row["supervision"]["decision_state"] == "MANUAL_SCHEDULED_REVIEW"
    assert row["supervision"]["owner"] == "operator"
    assert row["supervision"]["due"] is False


def test_without_the_register_the_same_claim_is_still_an_oversight_gap(tmp_path: Path) -> None:
    """Positivkontrolle: der neue Zustand kommt aus dem Register, nicht aus Nachsicht."""
    root = tmp_path / "artifacts"
    _seal(root, _SUPERVISED, "sec_filing_timing")
    (row,) = classify_ledger_entries(
        root, specs=(), supervision_register=tmp_path / "config" / "missing.json"
    )
    assert row["state"] == RECON_STATE_UNWATCHED
    assert row["supervision"] is None


def test_an_overdue_review_stays_due_the_register_is_no_mute_button(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _seal(root, _SUPERVISED, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {
            "prereg_id": _SUPERVISED,
            "name": "sec_filing_timing",
            "decision_state": "MANUAL_IMMEDIATE_VERDICT",
            "owner": "operator",
            "next_review_utc": "DUE_NOW",
        },
    )
    (row,) = classify_ledger_entries(root, specs=(), supervision_register=reg, now=_NOW)
    assert row["state"] == RECON_STATE_SUPERVISED
    assert row["supervision"]["due"] is True


def test_a_past_review_date_is_due_as_well(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _seal(root, _SUPERVISED, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {
            "prereg_id": _SUPERVISED,
            "name": "sec_filing_timing",
            "decision_state": "MANUAL_SCHEDULED_REVIEW",
            "owner": "operator",
            "next_review_utc": "2026-08-01T00:00:00+00:00",
        },
    )
    (row,) = classify_ledger_entries(root, specs=(), supervision_register=reg, now=_NOW)
    assert row["supervision"]["due"] is True


def test_an_unknown_decision_state_does_not_count_as_supervision(tmp_path: Path) -> None:
    """Fail-closed: was der Waechter nicht versteht, ist keine Aufsicht."""
    root = tmp_path / "artifacts"
    _seal(root, _SUPERVISED, "sec_filing_timing")
    for bad in ("UNWATCHED", "UNRESOLVED", "", "irgendwas"):
        reg = _register(
            tmp_path / "config" / "prereg_supervision.json",
            {"prereg_id": _SUPERVISED, "decision_state": bad, "owner": "operator"},
        )
        (row,) = classify_ledger_entries(root, specs=(), supervision_register=reg)
        assert row["state"] == RECON_STATE_UNWATCHED, bad


def test_a_verdict_outranks_the_register(tmp_path: Path) -> None:
    """Wahrheitsordnung unveraendert: die Truth-Kette schlaegt jede Aufsichtsnotiz."""
    root = tmp_path / "artifacts"
    _seal(root, _SUPERVISED, "sec_filing_timing")
    _attest(root, _SUPERVISED, "NOT_MET - x")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {"prereg_id": _SUPERVISED, "decision_state": "MANUAL_SCHEDULED_REVIEW"},
    )
    (row,) = classify_ledger_entries(root, specs=(), supervision_register=reg)
    assert row["state"] == RECON_STATE_RESOLVED


def test_a_maturity_spec_outranks_the_register(tmp_path: Path) -> None:
    """Ein laufender Zaehler ist die staerkere Aussage als eine Terminnotiz."""
    root = tmp_path / "artifacts"
    _seal(root, _WATCHED, "k1_channel_audit_resonance", created=_SPEC["since_utc"])
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {"prereg_id": _WATCHED, "decision_state": "WATCH"},
    )
    (row,) = classify_ledger_entries(root, specs=(_SPEC,), supervision_register=reg)
    assert row["state"] == RECON_STATE_WATCHED


def test_a_corrupt_register_never_crashes_the_reconciliation(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _seal(root, _SUPERVISED, "sec_filing_timing")
    bad = tmp_path / "config" / "prereg_supervision.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ not json", encoding="utf-8")
    (row,) = classify_ledger_entries(root, specs=(), supervision_register=bad)
    assert row["state"] == RECON_STATE_UNWATCHED
    assert load_supervision_register(bad) == {}


def test_a_register_entry_for_an_unsealed_claim_is_drift_not_supervision(tmp_path: Path) -> None:
    """Spiegelbild von ghost_specs: das Register darf nicht auf Phantome zeigen."""
    root = tmp_path / "artifacts"
    _seal(root, _SUPERVISED, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {"prereg_id": _SUPERVISED, "decision_state": "WATCH"},
        {"prereg_id": "deadbeef00000009", "decision_state": "WATCH"},
    )
    loaded = load_supervision_register(reg)
    assert set(loaded) == {_SUPERVISED, "deadbeef00000009"}
    rows = classify_ledger_entries(root, specs=(), supervision_register=reg)
    assert [r["prereg_id"] for r in rows] == [_SUPERVISED]


def test_health_nennt_einen_faelligen_aufsichtstermin_keine_luecke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Alarm darf die Frist zeigen, aber nicht mehr 'Aufsichtsluecke' sagen."""
    from app.alerts.health_check import _check_prereg_reconciliation

    _seal(tmp_path, _SUPERVISED, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {
            "prereg_id": _SUPERVISED,
            "decision_state": "MANUAL_IMMEDIATE_VERDICT",
            "owner": "operator",
            "next_review_utc": "DUE_NOW",
        },
    )
    monkeypatch.setattr(prereg_reconciliation, "DEFAULT_SUPERVISION_REGISTER", reg)

    (issue,) = _check_prereg_reconciliation(tmp_path, specs=())

    assert "SUPERVISED=1" in issue.message
    assert "Aufsichtstermin faellig" in issue.message
    assert _SUPERVISED in issue.message
    assert "Aufsichtsluecke" not in issue.message


def test_health_schweigt_bei_einem_termin_in_der_zukunft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein bewusst nach vorn gelegter Termin ist kein taeglicher Befund."""
    from app.alerts.health_check import _check_prereg_reconciliation

    _seal(tmp_path, _SUPERVISED, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {
            "prereg_id": _SUPERVISED,
            "decision_state": "MANUAL_SCHEDULED_REVIEW",
            "owner": "operator",
            "next_review_utc": "2099-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(prereg_reconciliation, "DEFAULT_SUPERVISION_REGISTER", reg)

    assert _check_prereg_reconciliation(tmp_path, specs=()) == []


def test_health_meldet_ein_register_das_auf_ein_phantom_zeigt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spiegelbild zu ghost_specs: Aufsicht ueber eine nie versiegelte ID ist Drift."""
    from app.alerts.health_check import _check_prereg_reconciliation

    _seal(tmp_path, _SUPERVISED, "sec_filing_timing")
    reg = _register(
        tmp_path / "config" / "prereg_supervision.json",
        {
            "prereg_id": _SUPERVISED,
            "decision_state": "MANUAL_SCHEDULED_REVIEW",
            "next_review_utc": "2099-01-01T00:00:00+00:00",
        },
        {"prereg_id": "deadbeef00000009", "decision_state": "WATCH"},
    )
    monkeypatch.setattr(prereg_reconciliation, "DEFAULT_SUPERVISION_REGISTER", reg)

    issues = _check_prereg_reconciliation(tmp_path, specs=())

    assert [i.severity for i in issues] == ["critical"]
    assert "supervision drift" in issues[0].message
    assert "deadbeef00000009" in issues[0].message
