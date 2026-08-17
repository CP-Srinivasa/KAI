"""Familienweites α-Budget über die pra-registrierte Claim-Familie.

``prereg_gate.check_gate`` urteilt je Claim ISOLIERT gegen dessen versiegeltes
``p_min``. Das ist richtig so — das Gate IST das Vorab-Kriterium und darf
nachträglich nicht verschärft werden. Es beantwortet aber eine engere Frage,
als beim Lesen entsteht: nicht „ist dieser Effekt echt", sondern „hält dieser
eine Claim seine eigene Schranke".

Werden mehrere Claims parallel gefahren, wächst die Wahrscheinlichkeit, dass
IRGENDEINER von ihnen zufällig besteht. Bei m unabhängigen Claims mit
individuellen α_i ist die obere Schranke 1 - Π(1 - α_i). Diese Zahl steht
nirgends, solange jeder Claim nur sich selbst kennt — der erste PASS läse sich
dann als „Edge gefunden", obwohl er das Familienrisiko nicht bestanden hat.

Dieses Modul rechnet das Budget aus und liefert die BH-Schranke, die ein
erster PASS klären müsste. Es ist AUSDRÜCKLICH NICHT GATEND: kein versiegeltes
Kriterium wird verändert, kein Verdikt gekippt. Es ordnet ein.

Live-Stand bei Einführung (Pi, 2026-08-17): 19 registrierte Claims, davon 4 mit
maschinellem ``p_min`` (m=4 ⇒ 26,9 % obere Schranke). Die übrigen 15 tragen
``gate: null`` und sind gar nicht maschinell urteilbar — sie werden getrennt
ausgewiesen statt still mitgezählt.
"""

from __future__ import annotations

from typing import Any

_NOTE = (
    "NICHT GATEND. Die versiegelten p_min je Claim bleiben unverändert und "
    "allein maßgeblich. Diese Zahlen ordnen einen PASS ein: bei m parallelen "
    "Claims ist ein einzelner PASS schwächere Evidenz als sein eigenes alpha "
    "suggeriert. Ein erster PASS, der die BH-Schranke nicht klärt, ist als "
    "Kandidat zu zitieren — nie als gefundener Edge."
)


def _machine_alpha(claim: dict[str, Any]) -> float | None:
    """α = 1 - p_min, oder ``None`` wenn der Claim kein maschinelles Gate trägt."""
    gate = claim.get("gate")
    if not isinstance(gate, dict):
        return None
    p_min = gate.get("p_min")
    if p_min is None:
        return None
    try:
        value = float(p_min)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= value < 1.0:
        return None
    return 1.0 - value


def _familywise(alphas: list[float]) -> float:
    """1 - Π(1 - α_i): obere Schranke für >=1 Falsch-PASS unter H0."""
    if not alphas:
        return 0.0
    product = 1.0
    for alpha in alphas:
        product *= 1.0 - alpha
    return 1.0 - product


def family_alpha_budget(
    claims: list[dict[str, Any]],
    *,
    resolved_ids: set[str],
) -> dict[str, Any]:
    """Familienweites α-Budget über registrierte Claims (read-only, nicht gatend).

    Args:
        claims: Zeilen des Prä-Reg-Ledgers.
        resolved_ids: ``prereg_id`` der bereits terminal entschiedenen Claims.
            Sie zählen in die Gesamtfamilie (garden of forking paths), aber
            nicht mehr in das OFFENE Budget.

    Returns:
        Dict mit Gesamt- und Offen-Sicht, den nicht maschinell gateten Claims
        und der BH-Schranke, die ein erster PASS klären müsste.
    """
    alphas: list[float] = []
    open_alphas: list[float] = []
    ungated: list[str] = []

    for claim in claims:
        alpha = _machine_alpha(claim)
        name = str(claim.get("name") or claim.get("prereg_id") or "<unnamed>")
        if alpha is None:
            ungated.append(name)
            continue
        alphas.append(alpha)
        if str(claim.get("prereg_id") or "") not in resolved_ids:
            open_alphas.append(alpha)

    m = len(alphas)
    # Ein einzelner PASS ist der Rang-1-Fall in BH: er muss die schärfste
    # Schranke der Familie klären, (1/m)*alpha_min — nicht sein eigenes alpha.
    bh_threshold = (min(alphas) / m) if m else None

    return {
        "schema": "prereg/alpha_budget/v1",
        "gating": False,
        "m_registered": len(claims),
        "m_machine_gated": m,
        "m_open": len(open_alphas),
        "alphas": sorted(round(a, 6) for a in alphas),
        "familywise_error_upper_bound": round(_familywise(alphas), 6),
        "familywise_error_open": round(_familywise(open_alphas), 6),
        "claims_without_machine_gate": ungated,
        "n_without_machine_gate": len(ungated),
        "bh_threshold_for_next_pass": (
            round(bh_threshold, 9) if bh_threshold is not None else None
        ),
        "bh_p_positive_for_next_pass": (
            round(1.0 - bh_threshold, 9) if bh_threshold is not None else None
        ),
        "note": _NOTE,
    }


__all__ = ["family_alpha_budget"]
