"""OTS-Proofs gegen den echten Bitcoin-Blockheader pruefen (D-288, Befund 4).

``classify_timestamp`` meldet ``confirmed``, sobald IRGENDEINE Bitcoin-Attestation
im Proof steht. Das ist "Attestation vorhanden", nicht "gegen Bitcoin
verifiziert": eine synthetische Attestation mit beliebigem Digest wurde ohne
Bitcoin-Kontakt als ``confirmed`` gefuehrt. Diese Tests binden die Aussage an
die Merkle-Root des Blocks, den der eigene bitcoind liefert.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.chain.client import BitcoindRpcClient
from app.integrity import bitcoin_verify as bv

HEIGHT = 862_481
BLOCK_HASH = "00" * 31 + "ab"
MERKLE = bytes(range(32))  # interne Byte-Reihenfolge, wie OTS sie vergleicht


def _timestamp(msg: bytes, *, height: int | None = HEIGHT, pending: bool = False):  # type: ignore[no-untyped-def]
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
    from opentimestamps.core.timestamp import Timestamp

    ts = Timestamp(msg)
    if height is not None:
        ts.attestations.add(BitcoinBlockHeaderAttestation(height))
    if pending:
        ts.attestations.add(PendingAttestation("https://calendar.example"))
    return ts


def _source(answers: dict[int, tuple[str, bytes] | None]):  # type: ignore[no-untyped-def]
    calls: list[int] = []

    async def header(height: int) -> tuple[str, bytes] | None:
        calls.append(height)
        return answers.get(height)

    header.calls = calls  # type: ignore[attr-defined]
    return header


async def test_eine_passende_attestation_ist_verifiziert() -> None:
    result = await bv.verify_timestamp(_timestamp(MERKLE), _source({HEIGHT: (BLOCK_HASH, MERKLE)}))
    assert (result.result, result.height, result.block_hash) == (bv.VERIFIED, HEIGHT, BLOCK_HASH)


async def test_eine_synthetische_attestation_mit_beliebigem_digest_faellt_durch() -> None:
    """Genau der Befund: vorhanden ist nicht verifiziert."""
    forged = _timestamp(b"\x11" * 32)
    result = await bv.verify_timestamp(forged, _source({HEIGHT: (BLOCK_HASH, MERKLE)}))
    assert result.result == bv.MISMATCH


async def test_ohne_blockheader_ist_nichts_verifiziert() -> None:
    result = await bv.verify_timestamp(_timestamp(MERKLE), _source({}))
    assert result.result == bv.UNVERIFIABLE


async def test_ein_nur_ausstehender_proof_ist_nicht_attestiert() -> None:
    result = await bv.verify_timestamp(_timestamp(MERKLE, height=None, pending=True), _source({}))
    assert result.result == bv.NOT_ATTESTED


def _write_proof(path: Path, msg: bytes) -> None:
    from opentimestamps.core.op import OpSHA256
    from opentimestamps.core.serialize import BytesSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile

    detached = DetachedTimestampFile(OpSHA256(), _timestamp(msg))
    ctx = BytesSerializationContext()
    detached.serialize(ctx)
    path.write_bytes(ctx.getbytes())


async def test_der_verzeichnislauf_schreibt_belege_und_zaehlt(tmp_path: Path) -> None:
    _write_proof(tmp_path / "audit-good.ots", MERKLE)
    _write_proof(tmp_path / "audit-forged.ots", b"\x22" * 32)
    (tmp_path / "work").mkdir()
    _write_proof(tmp_path / "work" / "audit-temp.ots", MERKLE)  # temporaer: nie pruefen

    report = await bv.verify_proofs_dir(tmp_path, _source({HEIGHT: (BLOCK_HASH, MERKLE)}))

    assert (report.verified, report.mismatch, report.unverifiable) == (1, 1, 0)
    good = json.loads((tmp_path / bv.VERIFIED_DIR / "audit-good.json").read_text("utf-8"))
    assert good["result"] == bv.VERIFIED and good["block_hash"] == BLOCK_HASH
    assert bv.read_verification(tmp_path, "audit-forged.ots") == bv.MISMATCH
    assert bv.read_verification(tmp_path, "audit-unknown.ots") == bv.UNVERIFIED
    assert not (tmp_path / bv.VERIFIED_DIR / "audit-temp.json").exists()


async def test_ein_verifizierter_beleg_wird_nicht_erneut_abgefragt(tmp_path: Path) -> None:
    _write_proof(tmp_path / "audit-good.ots", MERKLE)
    source = _source({HEIGHT: (BLOCK_HASH, MERKLE)})
    await bv.verify_proofs_dir(tmp_path, source)
    await bv.verify_proofs_dir(tmp_path, source)
    assert source.calls == [HEIGHT]  # type: ignore[attr-defined]


async def test_der_client_liefert_die_merkle_root_in_ots_reihenfolge() -> None:
    """bitcoind zeigt ``merkleroot`` byte-verdreht an; OTS vergleicht intern."""
    displayed = MERKLE[::-1].hex()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "getblockhash":
            assert body["params"] == [HEIGHT]
            return httpx.Response(200, json={"result": BLOCK_HASH, "error": None})
        assert body["method"] == "getblockheader"
        assert body["params"] == [BLOCK_HASH, True]
        return httpx.Response(200, json={"result": {"merkleroot": displayed}, "error": None})

    client = BitcoindRpcClient(
        base_url="http://127.0.0.1:8332",
        rpc_user="u",
        rpc_password="p",
        transport=httpx.MockTransport(handler),
    )
    assert await client.get_block_merkle_root(HEIGHT) == (BLOCK_HASH, MERKLE)


def _receipt(proofs_dir: Path, name: str, result: str, *, bound: bool = True) -> None:
    """Ein Beleg wie ``_write_receipt`` — optional ohne ``proof_sha256`` (Altbeleg)."""
    body: dict[str, object] = {"result": result}
    if bound:
        body["proof_sha256"] = hashlib.sha256((proofs_dir / name).read_bytes()).hexdigest()
    (proofs_dir / bv.VERIFIED_DIR).mkdir(exist_ok=True)
    stem = Path(name).with_suffix("").as_posix()
    (proofs_dir / bv.VERIFIED_DIR / f"{stem}.json").write_text(json.dumps(body), encoding="utf-8")


@pytest.mark.parametrize("sidecar", [bv.MISMATCH, bv.VERIFIED])
def test_status_liest_das_pruefergebnis(tmp_path: Path, sidecar: str) -> None:
    _write_proof(tmp_path / "audit-x.ots", MERKLE)
    _receipt(tmp_path, "audit-x.ots", sidecar)
    assert bv.read_verification(tmp_path, "audit-x.ots") == sidecar


# --- Audit A3: der Beleg gilt nur fuer genau den Proof-Inhalt, den er geprueft hat ---


async def test_der_beleg_traegt_den_hash_des_gepruefte_proofs(tmp_path: Path) -> None:
    _write_proof(tmp_path / "audit-good.ots", MERKLE)
    await bv.verify_proofs_dir(tmp_path, _source({HEIGHT: (BLOCK_HASH, MERKLE)}))

    receipt = json.loads((tmp_path / bv.VERIFIED_DIR / "audit-good.json").read_text("utf-8"))
    expected = hashlib.sha256((tmp_path / "audit-good.ots").read_bytes()).hexdigest()
    assert receipt["proof_sha256"] == expected
    assert bv.read_verification(tmp_path, "audit-good.ots") == bv.VERIFIED


async def test_ein_geaenderter_proof_verliert_seinen_beleg_und_wird_neu_geprueft(
    tmp_path: Path,
) -> None:
    proof = tmp_path / "audit-good.ots"
    _write_proof(proof, MERKLE)
    source = _source({HEIGHT: (BLOCK_HASH, MERKLE)})
    await bv.verify_proofs_dir(tmp_path, source)
    assert bv.read_verification(tmp_path, "audit-good.ots") == bv.VERIFIED

    _write_proof(proof, b"\x44" * 32)  # neuer Inhalt unter altem Namen
    assert bv.read_verification(tmp_path, "audit-good.ots") == bv.UNVERIFIED

    report = await bv.verify_proofs_dir(tmp_path, source)
    assert report.skipped == 0 and report.mismatch == 1
    assert source.calls == [HEIGHT, HEIGHT]  # type: ignore[attr-defined]
    assert bv.read_verification(tmp_path, "audit-good.ots") == bv.MISMATCH


async def test_ein_altbeleg_ohne_proof_hash_gilt_als_ungeprueft(tmp_path: Path) -> None:
    _write_proof(tmp_path / "audit-old.ots", MERKLE)
    _receipt(tmp_path, "audit-old.ots", bv.VERIFIED, bound=False)
    assert bv.read_verification(tmp_path, "audit-old.ots") == bv.UNVERIFIED

    source = _source({HEIGHT: (BLOCK_HASH, MERKLE)})
    report = await bv.verify_proofs_dir(tmp_path, source)
    assert (report.verified, report.skipped) == (1, 0)
    assert bv.read_verification(tmp_path, "audit-old.ots") == bv.VERIFIED


def test_ein_beleg_ohne_proof_datei_gilt_nicht(tmp_path: Path) -> None:
    _write_proof(tmp_path / "audit-gone.ots", MERKLE)
    _receipt(tmp_path, "audit-gone.ots", bv.VERIFIED)
    (tmp_path / "audit-gone.ots").unlink()
    assert bv.read_verification(tmp_path, "audit-gone.ots") == bv.UNVERIFIED
    assert bv.read_receipt(tmp_path, "audit-gone.ots") is None


def test_status_zeigt_die_bitcoin_pruefung_des_letzten_proofs(tmp_path: Path) -> None:
    from app.core.integrity_settings import IntegritySettings
    from app.integrity.status import get_integrity_status

    digest = "ab" * 32
    (tmp_path / f"audit-{digest[:16]}.json").write_text(
        json.dumps({"digest": digest, "ts": "2026-09-26T00:00:00Z"}), encoding="utf-8"
    )
    _write_proof(tmp_path / f"audit-{digest[:16]}.ots", MERKLE)
    _receipt(tmp_path, f"audit-{digest[:16]}.ots", bv.VERIFIED)
    status = get_integrity_status(
        IntegritySettings(enabled=True, stamper="opentimestamps", proofs_dir=str(tmp_path))
    )
    assert status.proof_state == "confirmed"
    assert status.bitcoin_verification == bv.VERIFIED


async def test_gleichnamige_proofs_in_unterordnern_kollidieren_nicht(tmp_path: Path) -> None:
    (tmp_path / "uc3_timestamp_jobs").mkdir()
    _write_proof(tmp_path / "audit-same.ots", MERKLE)
    _write_proof(tmp_path / "uc3_timestamp_jobs" / "audit-same.ots", b"\x33" * 32)

    await bv.verify_proofs_dir(tmp_path, _source({HEIGHT: (BLOCK_HASH, MERKLE)}))

    assert bv.read_verification(tmp_path, "audit-same.ots") == bv.VERIFIED
    assert bv.read_verification(tmp_path, "uc3_timestamp_jobs/audit-same.ots") == bv.MISMATCH
