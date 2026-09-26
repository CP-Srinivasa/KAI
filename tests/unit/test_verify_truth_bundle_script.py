"""scripts/verify_truth_bundle.py — ein Kaeufer prueft ein Paket auf einem zweiten Geraet.

``--mempool`` holt die Merkle-Root von mempool.space statt vom KAI-Node: eine
unabhaengige Bitcoin-Quelle. mempool.space zeigt ``merkle_root`` byte-verdreht an
(wie bitcoind); das Skript dreht sie fuer den OTS-Vergleich um.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from scripts import verify_truth_bundle as vtb

from tests.unit.test_truth_proof_bundle import BLOCK_HASH, HEIGHT, _world


def _mempool(tip: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/block-height/{HEIGHT}"):
            return httpx.Response(200, text=BLOCK_HASH)
        if request.url.path.endswith(f"/block/{BLOCK_HASH}"):
            return httpx.Response(200, json={"merkle_root": bytes.fromhex(tip)[::-1].hex()})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _bundle_file(tmp_path: Path) -> tuple[Path, str]:
    from app.truth.proof_bundle import build_verdict_bundle

    world = _world(tmp_path)
    bundle = build_verdict_bundle(world["hash"], **world["kw"])
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path, world["tip"]


def test_ein_echtes_paket_besteht_gegen_mempool(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    path, tip = _bundle_file(tmp_path)
    rc = asyncio.run(vtb.run(path, mempool_url="https://mempool.test/api", transport=_mempool(tip)))
    assert rc == 0
    assert "VERIFIED" in capsys.readouterr().out


def test_ein_manipuliertes_paket_faellt_durch(tmp_path: Path) -> None:
    path, tip = _bundle_file(tmp_path)
    bundle = json.loads(path.read_text(encoding="utf-8"))
    bundle["verdict_report"]["payload"]["verdict"] = "PASSED"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    rc = asyncio.run(vtb.run(path, mempool_url="https://mempool.test/api", transport=_mempool(tip)))
    assert rc == 1


def test_ohne_bitcoin_quelle_ist_das_ergebnis_offen(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    path, _tip = _bundle_file(tmp_path)
    rc = asyncio.run(vtb.run(path, mempool_url=None))
    assert rc == 1
    assert "claimed" in capsys.readouterr().out
