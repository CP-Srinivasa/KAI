"""Der Beleg einer Invalidierung muss existieren, passen und sich nicht widersprechen.

Am 2026-09-01 wurde der zweite G8-Akt abgebrochen, weil das Messinstrument selbst
defekt war. Der Beleg dazu war sachlich richtig und **formal unwahr**: er führte

    not_done.emitted_count_inspected = false
    not_done.note = "No count of any kind was read."

und im selben Dokument

    post_t0_emissions_observed = 15

worauf sich die Begründung stützte. Ein Instrumentendefekt lässt sich nur
nachweisen, indem man ansieht, was das Instrument ausgesendet hat — das ist
legitim. Die Behauptung, nichts gelesen zu haben, war es nicht: sie ist genau die
Hälfte, mit der ein Kritiker die ganze Invalidierung kippt.

Der Wächter dagegen ist **fail-closed**. Er prüft nicht, ob jemand eine schöne
Formulierung gewählt hat, sondern ob zwei Felder desselben Dokuments einander
widersprechen — und er behandelt einen fehlenden Beleg als Fehler, nicht als
„nichts zu prüfen". Der Vorgänger tat Letzteres: er übersprang sich selbst, wenn
die Datei fehlte, und die Datei lag ausserhalb des Repos. Er war grün durch
Abwesenheit.

Rein bis auf das Lesen der Belegdatei; die Regeln selbst kennen keine Uhr.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

#: Schema des kanonischen Invalidierungsbelegs.
EVIDENCE_SCHEMA: Final = "kai_prereg_invalidation/v1"

#: Der einzige zulässige Einsichts-Umfang. Ein freier Text wäre eine Einladung,
#: sich nachträglich einen passenden Grund zu formulieren.
INSPECTION_SCOPE_DEFECT_PROOF: Final = "instrument_defect_proof_only"

#: Felder, die IMMER ``false`` sein müssen. Wer eines davon auf ``true`` setzt,
#: hat das Ergebnis angesehen — dann ist es keine Invalidierung mehr, sondern
#: ein Abbruch nach Blick auf den Zwischenstand.
_MUST_BE_FALSE: Final = (
    "evaluator_executed",
    "acted_count_inspected",
    "outcome_inspected",
    "interim_result_taken",
    "substantive_outcome_evaluated",
)

PROBLEM_PATH_MISSING: Final = "EVIDENCE_PATH_MISSING"
PROBLEM_PIN_MISSING: Final = "EVIDENCE_PIN_MISSING"
PROBLEM_ARTIFACT_MISSING: Final = "EVIDENCE_ARTIFACT_MISSING"
PROBLEM_SHA_MISMATCH: Final = "EVIDENCE_SHA_MISMATCH"
PROBLEM_UNREADABLE: Final = "EVIDENCE_UNREADABLE"
PROBLEM_SCHEMA: Final = "EVIDENCE_SCHEMA_UNEXPECTED"
PROBLEM_NOT_DONE_MISSING: Final = "EVIDENCE_NOT_DONE_MISSING"
PROBLEM_OUTCOME_INSPECTED: Final = "EVIDENCE_OUTCOME_INSPECTED"
PROBLEM_SCOPE_MISSING: Final = "EVIDENCE_INSPECTION_SCOPE_MISSING"
PROBLEM_CONTRADICTS_COUNT: Final = "EVIDENCE_INSPECTION_CONTRADICTS_COUNT"
PROBLEM_COUNT_MISSING: Final = "EVIDENCE_OBSERVED_COUNT_MISSING"
PROBLEM_COUNT_MISMATCH: Final = "EVIDENCE_OBSERVED_COUNT_MISMATCH"
PROBLEM_MIRROR_PIN_MISMATCH: Final = "EVIDENCE_MIRROR_PIN_MISMATCH"
PROBLEM_SUBSTANTIVE_VERDICT: Final = "EVIDENCE_SUBSTANTIVE_VERDICT_PRESENT"
PROBLEM_ID_MISMATCH: Final = "EVIDENCE_PREREG_ID_MISMATCH"


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_evidence(path: Path) -> tuple[dict[str, Any] | None, str, str | None]:
    """``(dokument, sha256, problem)`` — jede Stufe kann fehlschlagen."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "", PROBLEM_ARTIFACT_MISSING
    digest = sha256_of_bytes(raw)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, digest, PROBLEM_UNREADABLE
    if not isinstance(doc, dict):
        return None, digest, PROBLEM_UNREADABLE
    return doc, digest, None


def verify_invalidation_evidence(repo_root: Path, entry: Mapping[str, Any]) -> list[str]:
    """Alle Verstösse eines Registereintrags gegen seinen Beleg — nie nur der erste.

    Leere Liste heisst: der Beleg existiert am gepinnten Pfad, sein SHA-256 trifft
    den Pin im Register, und seine Aussagen widersprechen einander nicht.
    """
    problems: list[str] = []

    rel = entry.get("audit_artifact")
    pin = entry.get("audit_artifact_sha256")
    if not isinstance(rel, str) or not rel:
        problems.append(PROBLEM_PATH_MISSING)
    if not isinstance(pin, str) or len(pin) != 64:
        problems.append(PROBLEM_PIN_MISSING)
    if problems:
        return problems

    assert isinstance(rel, str) and isinstance(pin, str)  # noqa: S101 — durch die Pruefung oben
    doc, digest, problem = _read_evidence(repo_root / rel)
    if problem:
        return [problem]
    if digest != pin:
        problems.append(PROBLEM_SHA_MISMATCH)
    assert doc is not None  # noqa: S101 — ``problem`` deckt den None-Fall ab

    if doc.get("schema") != EVIDENCE_SCHEMA:
        problems.append(PROBLEM_SCHEMA)

    claimed_id = entry.get("prereg_id")
    if claimed_id and doc.get("prereg_id") != claimed_id:
        problems.append(PROBLEM_ID_MISMATCH)

    if str(doc.get("substantive_verdict", "")).upper() != "NONE":
        problems.append(PROBLEM_SUBSTANTIVE_VERDICT)

    not_done = doc.get("not_done")
    if not isinstance(not_done, dict):
        problems.append(PROBLEM_NOT_DONE_MISSING)
        return problems

    for field in _MUST_BE_FALSE:
        if not_done.get(field) is not False:
            problems.append(PROBLEM_OUTCOME_INSPECTED)
            break

    observed = _observed_emissions(doc)
    inspected = not_done.get("emitted_count_inspected")

    # Der Kern: die beiden Felder muessen einander tragen. Ein Dokument, das eine
    # Emissionszahl nennt und zugleich behauptet, nichts gelesen zu haben, ist
    # unwahr — egal welche Haelfte stimmt.
    if inspected is True:
        if not_done.get("emitted_inspection_scope") != INSPECTION_SCOPE_DEFECT_PROOF:
            problems.append(PROBLEM_SCOPE_MISSING)
        if observed is None or observed <= 0:
            problems.append(PROBLEM_COUNT_MISSING)
    elif observed is not None and observed > 0:
        problems.append(PROBLEM_CONTRADICTS_COUNT)

    # Die Zahl selbst ist gepinnt, nicht nur ihre Anwesenheit. Ohne Pin liesse
    # sich der Beleg auf eine bequemere Emissionszahl umschreiben, ohne dass ein
    # Waechter es merkt — der Registereintrag haelt sie fest, nicht dieser Code.
    expected_count = entry.get("expected_post_t0_emissions_observed")
    if isinstance(expected_count, int) and not isinstance(expected_count, bool):
        if observed != expected_count:
            problems.append(PROBLEM_COUNT_MISMATCH)

    # Der Spiegel ist Audit-Kopie, kein zweiter Beleg: sein Pin MUSS derselbe
    # sein. Ein abweichender Pin waere eine zweite Wahrheit mit gleichem Namen.
    mirror_pin = entry.get("audit_artifact_mirror_sha256")
    if mirror_pin is not None and mirror_pin != pin:
        problems.append(PROBLEM_MIRROR_PIN_MISMATCH)

    return problems


def _observed_emissions(doc: Mapping[str, Any]) -> int | None:
    """Die im Beleg genannte Zahl beobachteter Emissionen, falls vorhanden."""
    evidence = doc.get("evidence")
    if not isinstance(evidence, dict):
        return None
    for block in evidence.values():
        if not isinstance(block, dict):
            continue
        value = block.get("post_t0_emissions_observed")
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None


__all__ = [
    "EVIDENCE_SCHEMA",
    "INSPECTION_SCOPE_DEFECT_PROOF",
    "PROBLEM_ARTIFACT_MISSING",
    "PROBLEM_CONTRADICTS_COUNT",
    "PROBLEM_COUNT_MISMATCH",
    "PROBLEM_COUNT_MISSING",
    "PROBLEM_MIRROR_PIN_MISMATCH",
    "PROBLEM_ID_MISMATCH",
    "PROBLEM_NOT_DONE_MISSING",
    "PROBLEM_OUTCOME_INSPECTED",
    "PROBLEM_PATH_MISSING",
    "PROBLEM_PIN_MISSING",
    "PROBLEM_SCHEMA",
    "PROBLEM_SCOPE_MISSING",
    "PROBLEM_SHA_MISMATCH",
    "PROBLEM_SUBSTANTIVE_VERDICT",
    "PROBLEM_UNREADABLE",
    "sha256_of_bytes",
    "verify_invalidation_evidence",
]
