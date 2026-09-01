"""TL-013: ein Abschluss OHNE Sachverdikt darf keinen Sachentscheid mitführen.

Operator-Auflage 2026-08-31 zu K1: „Ich würde ausdrücklich verhindern, dass aus
dem manuellen Abschluss später rückwirkend ein Messfehler oder eine
Unmessbarkeit konstruiert wird."

Die Gefahr ist real und hat eine bekannte Form: die Verdikt-KLASSE steht im
Text (führendes Token), das Ergebnis-Feld daneben ist frei. Trägt ein
``CLOSED_NO_VERDICT``-Record im ``result`` ein ``substantive_verdict`` wie
``NOT_MET``, dann behaupten Klasse und Feld Gegenteiliges — und jeder spätere
Leser darf sich aussuchen, welches gilt.

Bewusst NICHT als Wortsuche im Verdikt-Text gebaut: der K1-Entwurf sagt
ausdrücklich „weder MET noch NOT_MET sind hiermit behauptet". Eine Textsuche
würde genau die ehrliche Klarstellung bestrafen — derselbe Fehlertyp wie bei
den bit-genauen Detektoren, die an runden Zahlen dreimal falsch anschlugen.
Geprüft wird deshalb das strukturierte Feld, nicht die Prosa.

Fail-soft nach unten: der H2-Record vom 2026-08-08
(``execution_translation_hit_to_win_v1``) trägt das Feld gar nicht. Das ist
eine historische Baseline mit erwartetem Neuzuwachs 0 — INFO, kein Daueralarm
(dieselbe Behandlung wie TL-008).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.truth.attestation import compute_attestation
from app.truth.lint import run_lint


def _artifacts(tmp_path: Path) -> Path:
    (tmp_path / "research" / "verdicts").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _verdict(
    root: Path,
    name: str,
    verdict: str,
    result: dict[str, Any] | None = None,
) -> None:
    payload = {
        "schema_version": 1,
        "hypothesis": name,
        "prereg_id": "00c75a76a2b0e78b",
        "verdict": verdict,
        "params": {},
        "result": result if result is not None else {},
        "code_version": "abc1234",
        "generated_at_utc": "2026-08-31T20:00:00+00:00",
    }
    report = {"payload": payload, "attestation": compute_attestation(payload)}
    (root / "research" / "verdicts" / f"{name}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )


def _tl013(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [v for v in result["violations"] if v["invariant_id"] == "TL-013"]


def test_ein_widerspruch_zwischen_klasse_und_feld_ist_eine_verletzung(tmp_path: Path) -> None:
    """Der eigentliche Zweck: Klasse sagt 'kein Sachverdikt', Feld sagt NOT_MET."""
    art = _artifacts(tmp_path)
    _verdict(
        art,
        "k1_widerspruch",
        "INCONCLUSIVE_BY_TIMEOUT - Fenster zu, kein Sachverdikt.",
        {"substantive_verdict": "NOT_MET"},
    )

    (violation,) = _tl013(run_lint(art))

    assert violation["severity"] == "ERROR"
    assert "k1_widerspruch" in str(violation["evidence"])
    assert "NOT_MET" in violation["message"] or "NOT_MET" in str(violation["evidence"])


def test_none_ist_der_erwartete_fall_und_schweigt(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _verdict(
        art,
        "k1_sauber",
        "INCONCLUSIVE_BY_TIMEOUT - Fenster zu, kein Sachverdikt.",
        {"substantive_verdict": "NONE"},
    )

    assert _tl013(run_lint(art)) == []


def test_ein_echtes_sachverdikt_wird_nicht_angefasst(tmp_path: Path) -> None:
    """Positivkontrolle: MET/NOT_MET-Records sind nicht Gegenstand dieser Regel."""
    art = _artifacts(tmp_path)
    _verdict(art, "echtes_met", "MET - 13/13", {"substantive_verdict": "MET"})
    _verdict(art, "echtes_not_met", "NOT_MET at gate", {"substantive_verdict": "NOT_MET"})

    assert _tl013(run_lint(art)) == []


def test_fehlendes_feld_ist_info_und_kein_daueralarm(tmp_path: Path) -> None:
    """Der H2-Record von 2026-08-08 traegt das Feld nicht — historische Baseline."""
    art = _artifacts(tmp_path)
    _verdict(art, "h2_historisch", "CLOSED_UNMEASURABLE bei n=14/50", {})

    (violation,) = _tl013(run_lint(art))

    assert violation["severity"] == "INFO"
    assert "erwarteter Neuzuwachs 0" in violation["message"]


def test_der_verdikt_text_darf_die_klarstellung_enthalten(tmp_path: Path) -> None:
    """Eine Wortsuche haette genau die ehrliche Formulierung bestraft.

    Der K1-Entwurf sagt ausdruecklich, dass weder MET noch NOT_MET behauptet
    sind. Das MUSS erlaubt bleiben — geprueft wird das Feld, nicht die Prosa.
    """
    art = _artifacts(tmp_path)
    _verdict(
        art,
        "k1_entwurf",
        "INCONCLUSIVE_BY_TIMEOUT - Fenster zu. Weder MET noch NOT_MET, weder "
        "CLOSED_UNMEASURABLE noch SUPERSEDED sind hiermit behauptet oder impliziert.",
        {"substantive_verdict": "NONE"},
    )

    assert _tl013(run_lint(art)) == []


def test_mehrere_widersprueche_werden_einzeln_ausgewiesen(tmp_path: Path) -> None:
    """Kein Aggregat ohne Zerlegung — eine Summe verstecke, welcher Record faul ist."""
    art = _artifacts(tmp_path)
    _verdict(art, "a", "INCONCLUSIVE_BY_TIMEOUT - x", {"substantive_verdict": "MET"})
    _verdict(art, "b", "CLOSED_UNMEASURABLE - y", {"substantive_verdict": "NOT_MET"})

    (violation,) = _tl013(run_lint(art))

    evidence = str(violation["evidence"])
    assert "a" in evidence and "b" in evidence
    assert violation["evidence"]["count"] == 2


def test_kaputte_datei_bricht_den_lint_nicht(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    (art / "research" / "verdicts" / "kaputt.json").write_text("{ nope", encoding="utf-8")
    _verdict(art, "ok", "INCONCLUSIVE_BY_TIMEOUT - x", {"substantive_verdict": "NONE"})

    assert _tl013(run_lint(art)) == []


def test_tl013_steht_in_der_registry(tmp_path: Path) -> None:
    from app.truth.lint import REGISTRY

    entry = next(i for i in REGISTRY if i.invariant_id == "TL-013")
    assert entry.status == "active"
    assert "research/verdicts" in entry.betroffene_daten
