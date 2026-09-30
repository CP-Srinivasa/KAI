"""Geklaerte Force-Close-Altfaelle: lnd fuehrt sie weiter, das Geld ist nachweislich zurueck.

Operator-Entscheid 2026-09-30: den lnd-Eintrag NICHT per ``abandonchannel`` entfernen
(irreversibel, rein kosmetisch), sondern in KAI als geklaerten Altfall kennzeichnen.

Regeln:
- Ein Eintrag gilt nur als geklaert, wenn ``channel_point``, ``closing_txid`` UND der
  Limbo-Betrag exakt dem hier belegten Stand entsprechen. Aendert lnd daran etwas, ist
  der Fall wieder ein aktiver Befund und die Warnung bleibt sichtbar.
- Neue Force-Closes stehen nie hier. Die Liste waechst nur per Review (PR) mit Beleg.
- Der Betrag wird weder zum Vermoegen addiert (er steckt schon im Walletbestand) noch
  vom Walletbestand abgezogen.
"""

from __future__ import annotations

from dataclasses import dataclass

DISPLAY_TITLE = "Historischer Force-Close"
DISPLAY_TEXT = (
    "Rückführung in die Wallet bestätigt. lnd führt den alten Eintrag weiterhin. "
    "Kein Handlungsbedarf."
)


@dataclass(frozen=True)
class ReconciledForceClose:
    """Ein belegter Altfall: welche lnd-Zeile, welche Rueckfuehrung, wann geprueft."""

    channel_point: str
    closing_txid: str
    limbo_sat: int
    recovery_txid: str
    recovery_height: int
    verified_at: str  # Datum der letzten unabhaengigen Pruefung am Node
    decision: str
    evidence: str


RECONCILED_FORCE_CLOSES: tuple[ReconciledForceClose, ...] = (
    # D-287: Der Sweep 2fd51c3d gibt 58ae2f35:0 (25 815 sat, to_remote) zusammen mit
    # 94f9d604:1 an eine eigene Adresse aus. Am 2026-09-30 erneut am Node geprueft:
    # Wallet-Transaktion +845 499 sat auf Hoehe 953 849, Eingaenge 94f9d604:1 und 58ae2f35:0.
    ReconciledForceClose(
        channel_point="cf5fe0585efd98789aa8e8c5d9298729666aade45bb02f2946cb0b2cda0f9011:0",
        closing_txid="58ae2f35d12170e4a5b62cc8b6f35eff52f8d7319464e2853dc0c1bc30747a8d",
        limbo_sat=25_815,
        recovery_txid="2fd51c3d8ab8fc8b0ec2a3757083f85513d925071b7fd937e4d1282011ee6f02",
        recovery_height=953_849,
        verified_at="2026-09-30",
        decision="D-287",
        evidence="docs/evidence/ln_node_forensics_20260926.md",
    ),
)


def match(channel_point: str, closing_txid: str, limbo_sat: int) -> ReconciledForceClose | None:
    """Den belegten Altfall zu genau dieser lnd-Zeile finden, sonst ``None``."""
    for rec in RECONCILED_FORCE_CLOSES:
        if (
            rec.channel_point == channel_point
            and rec.closing_txid == closing_txid
            and rec.limbo_sat == limbo_sat
        ):
            return rec
    return None


def display(rec: ReconciledForceClose) -> dict[str, object]:
    """Anzeige fuer ``Historie / geklaerte Vorgaenge`` samt Nachweis."""
    return {
        "title": DISPLAY_TITLE,
        "text": DISPLAY_TEXT,
        "channel_point": rec.channel_point,
        "closing_txid": rec.closing_txid,
        "limbo_sat": rec.limbo_sat,
        "recovery_txid": rec.recovery_txid,
        "recovery_height": rec.recovery_height,
        "verified_at": rec.verified_at,
        "decision": rec.decision,
        "evidence": rec.evidence,
    }


__all__ = ["RECONCILED_FORCE_CLOSES", "ReconciledForceClose", "display", "match"]
