"""Beweispaket fuer Kaeufer (D-288, Befund 4 Teil 2).

Ein Kaeufer soll OHNE KAI pruefen koennen: Bericht -> Ledger-Record -> Kette ->
verankerter Tip -> OTS-Proof -> Bitcoin-Block. Veraenderter Inhalt oder ein
falscher Anker muss scheitern.
"""

from __future__ import annotations

import base64
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.research.verdict_report import build_verdict_report, write_verdict_report
from app.truth import proof_bundle as pb
from app.truth.ledger import append_attestation

HEIGHT = 900_000
BLOCK_HASH = "00" * 31 + "cd"


def _write_ots(path: Path, digest: bytes) -> None:
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    from opentimestamps.core.op import OpSHA256
    from opentimestamps.core.serialize import BytesSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

    ts = Timestamp(digest)
    ts.attestations.add(BitcoinBlockHeaderAttestation(HEIGHT))
    ctx = BytesSerializationContext()
    DetachedTimestampFile(OpSHA256(), ts).serialize(ctx)
    path.write_bytes(ctx.getbytes())


def _world(tmp_path: Path, *, ots_digest: bytes | None = None) -> dict[str, Any]:
    verdicts = tmp_path / "verdicts"
    ledger = tmp_path / "truth" / "attestation_ledger.jsonl"
    proofs = tmp_path / "proofs"
    proofs.mkdir(parents=True)
    report = build_verdict_report(
        hypothesis="h_test",
        prereg_id="prereg-1",
        verdict="FAILED at pre-registered horizon",
        result={"n": 10},
        params={"h": 24},
        code_version="abc123",
        generated_at=datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
    )
    write_verdict_report(report, verdicts)
    append_attestation("verdict", None, report["payload"], path=ledger, mirror_audit=False)
    tip = append_attestation("prereg", None, {"later": True}, path=ledger, mirror_audit=False)
    tip_hash = tip["record_hash"]
    (proofs / f"truthledger-{tip_hash[:16]}.json").write_text(
        json.dumps({"digest": tip_hash, "prefix": "truthledger"}), encoding="utf-8"
    )
    _write_ots(proofs / f"truthledger-{tip_hash[:16]}.ots", ots_digest or bytes.fromhex(tip_hash))
    return {
        "hash": report["attestation"]["hash"],
        "tip": tip_hash,
        "kw": {"verdicts_dir": verdicts, "ledger_path": ledger, "proofs_dir": proofs},
    }


def _headers(tip: str):  # type: ignore[no-untyped-def]
    async def source(height: int) -> tuple[str, bytes] | None:
        return (BLOCK_HASH, bytes.fromhex(tip)) if height == HEIGHT else None

    return source


async def test_ein_vollstaendiges_paket_besteht_jede_pruefung(tmp_path: Path) -> None:
    world = _world(tmp_path)
    bundle = pb.build_verdict_bundle(world["hash"], **world["kw"])
    assert bundle is not None
    assert bundle["anchored_tip"]["record_hash"] == world["tip"]
    assert [r["kind"] for r in bundle["ledger_segment"]] == ["verdict", "prereg"]
    assert base64.b64decode(bundle["anchor"]["ots_base64"])

    report = await pb.verify_bundle(bundle, header_source=_headers(world["tip"]))
    assert report["ok"] is True, report
    assert report["bitcoin"] == {"result": "verified", "height": HEIGHT, "block_hash": BLOCK_HASH}


async def test_ein_veraenderter_bericht_faellt_durch(tmp_path: Path) -> None:
    world = _world(tmp_path)
    bundle = pb.build_verdict_bundle(world["hash"], **world["kw"])
    forged = copy.deepcopy(bundle)
    forged["verdict_report"]["payload"]["verdict"] = "PASSED"  # nachtraeglich geschoent
    report = await pb.verify_bundle(forged, header_source=_headers(world["tip"]))
    assert report["ok"] is False
    assert report["checks"]["payload_hash"] is False


async def test_ein_veraenderter_ledger_record_faellt_durch(tmp_path: Path) -> None:
    world = _world(tmp_path)
    bundle = pb.build_verdict_bundle(world["hash"], **world["kw"])
    forged = copy.deepcopy(bundle)
    forged["ledger_segment"][1]["payload"]["later"] = False
    report = await pb.verify_bundle(forged, header_source=_headers(world["tip"]))
    assert report["ok"] is False
    assert report["checks"]["ledger_chain"] is False


async def test_ein_anker_fuer_einen_anderen_digest_faellt_durch(tmp_path: Path) -> None:
    world = _world(tmp_path, ots_digest=b"\x44" * 32)
    bundle = pb.build_verdict_bundle(world["hash"], **world["kw"])
    report = await pb.verify_bundle(bundle, header_source=_headers(world["tip"]))
    assert report["ok"] is False
    assert report["checks"]["ots_stamps_tip"] is False


async def test_ohne_bitcoin_quelle_bleibt_der_beweis_offen_nicht_gruen(tmp_path: Path) -> None:
    world = _world(tmp_path)
    bundle = pb.build_verdict_bundle(world["hash"], **world["kw"])
    report = await pb.verify_bundle(bundle, header_source=None)
    assert report["ok"] is False
    assert report["bitcoin"]["result"] == "unverifiable"
    assert report["bitcoin"]["claimed_heights"] == [HEIGHT]


def test_ein_unbekannter_hash_liefert_kein_paket(tmp_path: Path) -> None:
    world = _world(tmp_path)
    assert pb.build_verdict_bundle("ff" * 32, **world["kw"]) is None
