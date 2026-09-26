"""Beweispaket fuer Kaeufer: ein Verdict bis zum Bitcoin-Block (D-288, Befund 4).

``/oracle/verdicts`` nannte je Bericht nur den Attestations-Hash — ohne den
Bericht selbst, ohne den Ledger-Pfad und ohne den OTS-Anker. Ein Kaeufer konnte
"wurde nicht nachtraeglich veraendert" damit nicht selbst pruefen. Dieses Modul
baut das vollstaendige Paket und prueft es ohne KAI-Zustand:

1. ``sha256(kanonischer Bericht) == attestation_hash``
2. ein Ledger-Record traegt ihn als ``payload_hash``
3. ``record_hash``/``prev_hash`` verketten diesen Record bis zu einem VERANKERTEN Tip
4. die ``.ots`` des Tips stempelt genau diese ``record_hash``-Bytes
5. jede Bitcoin-Attestation des Proofs == Merkle-Root ihres Blocks
   (``header_source``: eigener bitcoind, oder unabhaengig z. B. mempool.space)

Ohne Bitcoin-Quelle ist das Paket NICHT gruen (``unverifiable``) — Schritt 5 ist
genau der Teil, der aus "Attestation vorhanden" einen Beweis macht.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from app.integrity.bitcoin_verify import (
    UNVERIFIABLE,
    VERIFIED,
    HeaderSource,
    read_verification,
    verify_timestamp,
)
from app.research.verdict_report import DEFAULT_VERDICTS_DIR
from app.truth.attestation import compute_attestation
from app.truth.ledger import DEFAULT_TRUTH_LEDGER_PATH

GENESIS_HASH = "0" * 64
BUNDLE_SCHEMA = "kai-truth-proof-bundle/v1"


def _find_report(verdicts_dir: Path, attestation_hash: str) -> dict[str, Any] | None:
    if not verdicts_dir.is_dir():
        return None
    for path in sorted(verdicts_dir.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(report.get("attestation", {}).get("hash", "")) == attestation_hash:
            return dict(report)
    return None


def _ledger(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build_verdict_bundle(
    attestation_hash: str,
    *,
    verdicts_dir: Path = DEFAULT_VERDICTS_DIR,
    ledger_path: Path = DEFAULT_TRUTH_LEDGER_PATH,
    proofs_dir: Path = Path("monitor/integrity"),
) -> dict[str, Any] | None:
    """Das Paket zu einem Verdict — ``None``, wenn Bericht, Record oder Anker fehlt."""
    report = _find_report(verdicts_dir, attestation_hash)
    if report is None:
        return None
    records = _ledger(ledger_path)
    start = next(
        (i for i, r in enumerate(records) if r.get("payload_hash") == attestation_hash), None
    )
    if start is None:
        return None
    for end in range(start, len(records)):
        tip_hash = str(records[end]["record_hash"])
        anchor_json = proofs_dir / f"truthledger-{tip_hash[:16]}.json"
        ots_path = proofs_dir / f"truthledger-{tip_hash[:16]}.ots"
        if not (anchor_json.is_file() and ots_path.is_file()):
            continue
        anchor = json.loads(anchor_json.read_text(encoding="utf-8"))
        if anchor.get("digest") != tip_hash:
            continue
        receipt_path = proofs_dir / "bitcoin_verified" / f"truthledger-{tip_hash[:16]}.json"
        receipt = (
            json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else None
        )
        return {
            "schema": BUNDLE_SCHEMA,
            "verdict_report": report,
            "ledger_segment": records[start : end + 1],
            "anchored_tip": {"seq": records[end]["seq"], "record_hash": tip_hash},
            "anchor": {
                "record": anchor,
                "ots_base64": base64.b64encode(ots_path.read_bytes()).decode("ascii"),
                "kai_bitcoin_verification": receipt
                or {"result": read_verification(proofs_dir, ots_path.name)},
            },
            "how_to_verify": [
                "1 sha256(canonical JSON of verdict_report.payload) == attestation.hash",
                "2 ledger_segment[0].payload_hash == attestation.hash",
                "3 each record_hash == sha256(canonical record without record_hash); "
                "prev_hash links each record to the previous one",
                "4 the .ots (ots_base64) stamps bytes.fromhex(anchored_tip.record_hash)",
                "5 ots verify / block explorer: each Bitcoin attestation equals the block "
                "merkle root (independent: scripts/verify_truth_bundle.py --mempool)",
            ],
        }
    return None


def _ledger_chain_ok(segment: list[dict[str, Any]]) -> bool:
    previous: str | None = None
    for record in segment:
        body = {k: v for k, v in record.items() if k != "record_hash"}
        if compute_attestation(body)["hash"] != record.get("record_hash"):
            return False
        if previous is not None and record.get("prev_hash") != previous:
            return False
        previous = str(record.get("record_hash"))
    return bool(segment)


async def verify_bundle(
    bundle: dict[str, Any], *, header_source: HeaderSource | None
) -> dict[str, Any]:
    """Alle fuenf Schritte pruefen — rein aus dem Paket, ohne KAI-Zustand."""
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    from opentimestamps.core.serialize import BytesDeserializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile

    report = bundle["verdict_report"]
    segment = list(bundle["ledger_segment"])
    tip = str(bundle["anchored_tip"]["record_hash"])
    checks: dict[str, bool] = {}
    checks["payload_hash"] = (
        compute_attestation(report["payload"])["hash"] == report["attestation"]["hash"]
    )
    checks["ledger_contains_report"] = bool(segment) and (
        segment[0].get("payload_hash") == report["attestation"]["hash"]
        and segment[0].get("payload") == report["payload"]
    )
    checks["ledger_chain"] = _ledger_chain_ok(segment)
    checks["segment_ends_at_tip"] = bool(segment) and segment[-1].get("record_hash") == tip

    detached = DetachedTimestampFile.deserialize(
        BytesDeserializationContext(base64.b64decode(bundle["anchor"]["ots_base64"]))
    )
    checks["ots_stamps_tip"] = detached.file_digest == bytes.fromhex(tip)

    claimed = sorted(
        {
            att.height
            for _msg, att in detached.timestamp.all_attestations()
            if isinstance(att, BitcoinBlockHeaderAttestation)
        }
    )
    if header_source is None:
        bitcoin: dict[str, Any] = {"result": UNVERIFIABLE, "claimed_heights": claimed}
    else:
        outcome = await verify_timestamp(detached.timestamp, header_source)
        bitcoin = {
            "result": outcome.result,
            "height": outcome.height,
            "block_hash": outcome.block_hash,
        }
    checks["bitcoin"] = bitcoin["result"] == VERIFIED
    return {"ok": all(checks.values()), "checks": checks, "bitcoin": bitcoin}


__all__ = ["BUNDLE_SCHEMA", "build_verdict_bundle", "verify_bundle"]
