"""Ein versiegelter Claim darf nicht ausserhalb jeder Aufsicht liegen.

Befund 2026-08-18 (live gegen den Pi gemessen):

* ``artifacts/research/prereg_ledger.jsonl`` fuehrt **19** versiegelte Prae-Regs.
* ``MATURITY_SPECS`` — die handgepflegte Wachliste — kennt **6** davon.
* Die verifizierte Truth-Kette traegt fuer **9** ein terminales Verdikt.
* Uebrig bleiben **8 Claims, die weder beobachtet noch entschieden sind**.

Einer davon ist ``00c75a76a2b0e78b`` (k1_channel_audit_resonance): Fenster seit
dem 03.08. zu, kein Verdikt, und kein Mechanismus, der das je gemeldet haette.
Er fiel nur auf, weil die oeffentliche ``/paper``-Seite ihn zufaellig rendert
(#714). Die anderen sieben fielen gar nicht auf.

Das ist dieselbe Familie wie „Monitoring wacht ueber Ausgaenge, nicht
Eingaenge": die Wachliste beobachtete sich selbst statt die Quelle der
Wahrheit. Was niemand von Hand eintrug, existierte fuer den Waechter nicht.

Der Fix ist ein Abgleich, keine neue Wahrheitsquelle: jeder Eintrag im
versiegelten Ledger ist entweder beobachtet ODER terminal entschieden —
alles andere ist ein Befund und geht in den Operator-Kanal.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.research.prereg_maturity import (
    STATE_UNWATCHED,
    build_maturity_alert,
    find_unwatched_preregs,
)


def _seal(tmp: Path, *, prereg_id: str, name: str, created: str, horizon: str = "30d") -> None:
    path = tmp / "research" / "prereg_ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema": "prereg/v1",
        "prereg_id": prereg_id,
        "name": name,
        "direction": "neutral",
        "horizon": horizon,
        "success_criteria": "irrelevant fuer diesen Test",
        "sample_size_target": 5,
        "created_at_utc": created,
        "gate": None,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_versiegelter_claim_ohne_spec_und_ohne_verdikt_ist_ein_befund(tmp_path: Path) -> None:
    _seal(
        tmp_path,
        prereg_id="00c75a76a2b0e78b",
        name="k1_channel_audit_resonance",
        created="2026-07-04T12:51:11.469459+00:00",
    )
    rows = find_unwatched_preregs(tmp_path, specs=(), resolutions={})
    assert [r["prereg_id"] for r in rows] == ["00c75a76a2b0e78b"]
    assert rows[0]["state"] == STATE_UNWATCHED
    assert rows[0]["due"] is True
    assert rows[0]["name"] == "k1_channel_audit_resonance"


def test_beobachteter_claim_erzeugt_keinen_befund(tmp_path: Path) -> None:
    _seal(
        tmp_path,
        prereg_id="fd6f5f7842f49244",
        name="technical_paper_precision_fwd_v1",
        created="2026-07-20T00:00:00+00:00",
    )
    specs = ({"name": "technical_paper_precision_fwd_v1", "prereg_id": "fd6f5f7842f49244"},)
    assert find_unwatched_preregs(tmp_path, specs=specs, resolutions={}) == []


def test_terminal_entschiedener_claim_erzeugt_keinen_befund(tmp_path: Path) -> None:
    _seal(
        tmp_path,
        prereg_id="f676bcf5a7a1bfb6",
        name="funding_premium_meanrev_1h",
        created="2026-07-01T00:00:00+00:00",
    )
    resolutions = {"f676bcf5a7a1bfb6": {"status": "resolved", "verdict_class": "NOT_MET"}}
    assert find_unwatched_preregs(tmp_path, specs=(), resolutions=resolutions) == []


def test_beschaedigte_resolution_zaehlt_nicht_als_abschluss(tmp_path: Path) -> None:
    """``untrusted_attestation``/``conflict`` sind kein Abschluss — fail-closed."""
    _seal(
        tmp_path, prereg_id="aaaaaaaaaaaaaaaa", name="kaputt", created="2026-07-01T00:00:00+00:00"
    )
    resolutions = {
        "aaaaaaaaaaaaaaaa": {"status": "conflict", "verdict_classes": ["MET", "NOT_MET"]}
    }
    rows = find_unwatched_preregs(tmp_path, specs=(), resolutions=resolutions)
    assert [r["prereg_id"] for r in rows] == ["aaaaaaaaaaaaaaaa"]


def test_fehlendes_ledger_ist_kein_absturz(tmp_path: Path) -> None:
    assert find_unwatched_preregs(tmp_path, specs=(), resolutions={}) == []


def test_alert_benennt_den_unbeobachteten_claim() -> None:
    """Der Operator-Kanal muss den Befund tragen, nicht nur die Datenstruktur."""
    rows = [
        {
            "name": "k1_channel_audit_resonance",
            "prereg_id": "00c75a76a2b0e78b",
            "kind": "unwatched",
            "state": STATE_UNWATCHED,
            "due": True,
            "n_target": 5,
            "n_proxy": 0,
            "n_exact": None,
            "per_source": {"sealed_at_utc": "2026-07-04T12:51:11.469459+00:00", "horizon": "30d"},
            "window_end_utc": None,
            "timed_out": False,
            "resolution": None,
        }
    ]
    alert = build_maturity_alert(rows)
    assert alert is not None
    assert "00c75a76a2b0e78b" in alert
    assert "k1_channel_audit_resonance" in alert
    assert STATE_UNWATCHED in alert


def test_offchain_verdikt_wird_als_diagnose_markiert_aber_schliesst_nicht(tmp_path: Path) -> None:
    """Ein Verdikt in der Seitenablage ist kein Abschluss — nur ein anderer Auftrag.

    ``0879a65c…`` (LN) traegt sein Verdikt in ``ln_reconciliation_verdict.jsonl``,
    ``6751bc33…`` (SEC) in ``prereg_verdicts.jsonl`` — beide nicht in der
    signaturverketteten Truth-Kette. Der Claim bleibt offen; die Handlung
    heisst aber "attestieren", nicht "auswerten".
    """
    _seal(
        tmp_path,
        prereg_id="6751bc3364d39ec2",
        name="sec_filing_timing",
        created="2026-07-01T12:32:46.801599+00:00",
        horizon="24h",
    )
    vpath = tmp_path / "research" / "prereg_verdicts.jsonl"
    vpath.write_text(
        json.dumps({"prereg_id": "6751bc3364d39ec2", "verdict": "NOT_MET"}) + "\n",
        encoding="utf-8",
    )
    rows = find_unwatched_preregs(tmp_path, specs=(), resolutions={})
    assert len(rows) == 1
    assert rows[0]["per_source"]["offchain_verdict"] is True
    alert = build_maturity_alert(rows)
    assert alert is not None and "NICHT attestiert" in alert


def test_k1_steht_jetzt_unter_aufsicht() -> None:
    """Regression: der bekannte Zombie muss in der Wachliste stehen.

    ``00c75a76a2b0e78b`` lag bis 2026-08-18 in keiner Zeile — das Fenster war
    seit dem 03.08. zu, ohne dass irgendetwas das gemeldet haette.
    """
    from app.research.prereg_maturity import MATURITY_SPECS

    spec = next((s for s in MATURITY_SPECS if s.get("prereg_id") == "00c75a76a2b0e78b"), None)
    assert spec is not None, "K1 fehlt wieder in der Wachliste."
    assert spec["kind"] == "deadline"
    assert spec["window_end_utc"] == "2026-08-03T12:51:11.469459+00:00"


def test_board_nennt_unbeobachtet_beim_namen_statt_evaluator_faellig() -> None:
    """Auf dem Operator-Board darf UNWATCHED nicht als "Evaluator fahren" erscheinen.

    ``_board_state`` fiel fuer unbekannte Zustaende auf das ``due``-Bit zurueck
    und haette damit "Ziel-n nur im Proxy erreicht" behauptet — fuer einen
    Claim, der ueberhaupt keinen Zaehler hat. Der schwaechere Zustand ist hier
    nicht der konservative: unbeobachtet ist dringlicher als reifend.
    """
    from app.observability.operator_board_live import STATE_UNWATCHED, build_live_board

    ledger = [
        {
            "schema": "prereg/v1",
            "prereg_id": "c489079289070a8c",
            "name": "m3_external_validation_first_signal",
            "created_at_utc": "2026-07-04T09:15:41.100686+00:00",
            "sample_size_target": 1,
        }
    ]
    maturity = [
        {
            "name": "m3_external_validation_first_signal",
            "prereg_id": "c489079289070a8c",
            "kind": "unwatched",
            "state": "UNWATCHED",
            "due": True,
            "n_proxy": None,
            "n_target": 1,
            "per_source": {"sealed_at_utc": "2026-07-04T09:15:41.100686+00:00", "horizon": "90d"},
        }
    ]
    board = build_live_board(ledger=ledger, verdicts=[], maturity_rows=maturity)
    row = board["open_preregs"][0]
    assert row["state"] == STATE_UNWATCHED
    assert "UNBEOBACHTET" in row["action"]
    assert "Evaluator fahren" not in row["action"]
    assert board["unwatched_count"] == 1
    assert board["eval_check_count"] == 0
