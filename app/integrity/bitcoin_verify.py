"""OTS-Proofs gegen den echten Bitcoin-Blockheader pruefen (D-288, Befund 4).

:func:`app.integrity.upgrade.classify_timestamp` meldet ``confirmed``, sobald
irgendeine ``BitcoinBlockHeaderAttestation`` im Proof steht. Das ist
**"Attestation vorhanden"** — die Attestation selbst behauptet nur eine
Blockhoehe. Ob die Nachricht, die der Proof dort ausrechnet, wirklich die
Merkle-Root dieses Blocks IST, prueft erst ein Vergleich mit dem Header. Genau
das macht ``ots verify`` mit einem Bitcoin-Knoten, und genau das macht dieses
Modul gegen den eigenen bitcoind (``app.chain.client``).

Ergebnis je Proof (``VERIFIED`` / ``MISMATCH`` / ``UNVERIFIABLE`` /
``NOT_ATTESTED``) landet als kleiner Beleg unter ``<proofs_dir>/bitcoin_verified/``
— NICHT neben den ``.ots``/``audit-*.json``, damit kein anderer Scanner ihn
fuer einen Anker oder Proof haelt. ``MISMATCH`` ist ein Befund: ein Proof, der
eine Bitcoin-Verankerung behauptet, die der Block nicht traegt.

Rein lesend gegenueber Node und Proofs; kein Kapitalpfad.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VERIFIED = "verified"
MISMATCH = "mismatch"
UNVERIFIABLE = "unverifiable"  # Header nicht beschaffbar (bitcoind weg/kein Block)
NOT_ATTESTED = "not_attested"  # keine Bitcoin-Attestation (noch pending)
UNVERIFIED = "unverified"  # noch nie geprueft (kein Beleg)
VERIFIED_DIR = "bitcoin_verified"

#: ``height -> (block_hash, merkle_root in interner Byte-Reihenfolge)`` oder ``None``.
HeaderSource = Callable[[int], Awaitable[tuple[str, bytes] | None]]


@dataclass(frozen=True)
class BitcoinVerification:
    result: str
    height: int | None = None
    block_hash: str = ""


@dataclass(frozen=True)
class VerifyReport:
    scanned: int = 0
    verified: int = 0
    mismatch: int = 0
    unverifiable: int = 0
    not_attested: int = 0
    skipped: int = 0


async def verify_timestamp(ts: Any, header_source: HeaderSource) -> BitcoinVerification:
    """Jede Bitcoin-Attestation gegen ihren Blockheader halten.

    Eine einzige Abweichung macht den Proof zum ``MISMATCH`` — auch wenn eine
    andere Attestation passt: ein Proof, der etwas Falsches behauptet, ist
    kein Beleg. Sonst gewinnt die niedrigste verifizierte Hoehe.
    """
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation

    verified: BitcoinVerification | None = None
    saw_attestation = False
    unverifiable = False
    for msg, att in ts.all_attestations():
        if not isinstance(att, BitcoinBlockHeaderAttestation):
            continue
        saw_attestation = True
        header = await header_source(att.height)
        if header is None:
            unverifiable = True
            continue
        block_hash, merkle_root = header
        if msg != merkle_root:
            return BitcoinVerification(MISMATCH, att.height, block_hash)
        if verified is None or att.height < (verified.height or att.height):
            verified = BitcoinVerification(VERIFIED, att.height, block_hash)
    if verified is not None:
        return verified
    if saw_attestation and unverifiable:
        return BitcoinVerification(UNVERIFIABLE)
    return BitcoinVerification(NOT_ATTESTED)


def _proof_files(proofs_dir: Path) -> list[Path]:
    """Dieselbe Auswahl wie der Upgrader: alle ``.ots`` ausser ``work/``."""
    return sorted(
        path
        for path in proofs_dir.rglob("*.ots")
        if "work" not in path.relative_to(proofs_dir).parts
        and VERIFIED_DIR not in path.relative_to(proofs_dir).parts
    )


def _receipt_path(proofs_dir: Path, proof_rel: str) -> Path:
    """Beleg je Proof; der relative Pfad geht in den Namen ein (UC3 liegt in Unterordnern)."""
    stem = Path(proof_rel).with_suffix("").as_posix().replace("/", "__")
    return proofs_dir / VERIFIED_DIR / f"{stem}.json"


def read_verification(proofs_dir: Path, proof_rel: str) -> str:
    """Ergebnis fuer einen Proof (Pfad relativ zu ``proofs_dir``); nie geprueft: ``UNVERIFIED``."""
    path = _receipt_path(proofs_dir, proof_rel)
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("result", UNVERIFIED))
    except (OSError, ValueError):
        return UNVERIFIED


def _write_receipt(proofs_dir: Path, proof: Path, outcome: BitcoinVerification) -> None:
    target = _receipt_path(proofs_dir, proof.relative_to(proofs_dir).as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "proof": str(proof.relative_to(proofs_dir)),
        "result": outcome.result,
        "height": outcome.height,
        "block_hash": outcome.block_hash,
        "checked_at": datetime.now(UTC).isoformat(),
        "method": "merkle_root == attestation message (own bitcoind getblockheader)",
    }
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


async def verify_proofs_dir(proofs_dir: Path, header_source: HeaderSource) -> VerifyReport:
    """Alle noch nicht verifizierten Proofs pruefen und Belege schreiben.

    Ein bereits ``VERIFIED`` Beleg wird nicht erneut abgefragt (Bitcoin-Bloecke
    aendern sich nach Bestaetigung nicht). Alles andere wird wiederholt:
    ``UNVERIFIABLE`` war ein Ausfall, ``NOT_ATTESTED`` ist ein noch nicht
    aufgewerteter Proof, ``MISMATCH`` soll bei jedem Lauf wieder auffallen.
    """
    from app.integrity.upgrade import _load_detached

    counts = {VERIFIED: 0, MISMATCH: 0, UNVERIFIABLE: 0, NOT_ATTESTED: 0}
    scanned = skipped = 0
    for proof in _proof_files(proofs_dir):
        scanned += 1
        if read_verification(proofs_dir, proof.relative_to(proofs_dir).as_posix()) == VERIFIED:
            skipped += 1
            continue
        try:
            detached = _load_detached(proof)
        except Exception as exc:  # noqa: BLE001 - ein kaputter Proof nimmt den Lauf nicht mit
            logger.info("[ots-verify] unreadable proof %s: %s", proof.name, type(exc).__name__)
            continue
        outcome = await verify_timestamp(detached.timestamp, header_source)
        counts[outcome.result] += 1
        if outcome.result == MISMATCH:
            logger.error(
                "[ots-verify] MISMATCH %s: attestation at height %s does not match the block",
                proof.name,
                outcome.height,
            )
        _write_receipt(proofs_dir, proof, outcome)
    return VerifyReport(
        scanned=scanned,
        verified=counts[VERIFIED],
        mismatch=counts[MISMATCH],
        unverifiable=counts[UNVERIFIABLE],
        not_attested=counts[NOT_ATTESTED],
        skipped=skipped,
    )


def header_source_from(client: Any) -> HeaderSource:
    """Eine gecachte Header-Quelle ueber ``BitcoindRpcClient.get_block_merkle_root``."""
    cache: dict[int, tuple[str, bytes] | None] = {}

    async def source(height: int) -> tuple[str, bytes] | None:
        if height not in cache:
            try:
                cache[height] = await client.get_block_merkle_root(height)
            except Exception as exc:  # noqa: BLE001 - ein Ausfall ist UNVERIFIABLE, kein Absturz
                logger.warning("[ots-verify] header %s unavailable: %s", height, type(exc).__name__)
                cache[height] = None
        return cache[height]

    return source


__all__ = [
    "MISMATCH",
    "NOT_ATTESTED",
    "UNVERIFIABLE",
    "UNVERIFIED",
    "VERIFIED",
    "VERIFIED_DIR",
    "BitcoinVerification",
    "VerifyReport",
    "header_source_from",
    "read_verification",
    "verify_proofs_dir",
    "verify_timestamp",
]
