#!/usr/bin/env python3
"""Ein KAI-Beweispaket unabhaengig pruefen (D-288, Befund 4).

Aufruf auf einem beliebigen Geraet mit Python + ``opentimestamps``::

    python -m scripts.verify_truth_bundle bundle.json --mempool

Das Paket kommt von ``GET /oracle/verdicts/proof?attestation_hash=...``. Geprueft
werden Bericht-Hash, Ledger-Kette bis zum verankerten Tip, die ``.ots``-Bindung an
den Tip und — mit ``--mempool`` — jede Bitcoin-Attestation gegen die Merkle-Root,
die mempool.space fuer diesen Block nennt (NICHT gegen KAIs eigenen Node).
Ohne Bitcoin-Quelle bleibt das Ergebnis offen: Exit 1, mit den behaupteten Hoehen.

Exit: 0 = alle Pruefungen bestanden · 1 = eine Pruefung nicht bestanden oder offen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from app.truth.proof_bundle import verify_bundle

DEFAULT_MEMPOOL = "https://mempool.space/api"


def mempool_source(base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> Any:
    """Header-Quelle ueber die mempool.space-API (unabhaengig vom KAI-Node)."""

    async def source(height: int) -> tuple[str, bytes] | None:
        kwargs: dict[str, Any] = {"timeout": 15.0}
        if transport is not None:
            kwargs["transport"] = transport
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                block_hash = (await client.get(f"{base_url}/block-height/{height}")).text.strip()
                block = (await client.get(f"{base_url}/block/{block_hash}")).json()
        except (httpx.HTTPError, ValueError):
            return None
        root = str(block.get("merkle_root", ""))
        if len(block_hash) != 64 or len(root) != 64:
            return None
        return block_hash, bytes.fromhex(root)[::-1]  # Anzeige ist byte-verdreht

    return source


async def run(
    bundle_path: Path,
    *,
    mempool_url: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    source = mempool_source(mempool_url, transport=transport) if mempool_url else None
    report = await verify_bundle(bundle, header_source=source)
    for name, ok in report["checks"].items():
        print(f"{'OK  ' if ok else 'FAIL'} {name}")
    bitcoin = report["bitcoin"]
    if bitcoin["result"] == "verified":
        print(f"VERIFIED bitcoin height={bitcoin['height']} block={bitcoin['block_hash']}")
    else:
        print(f"bitcoin: {bitcoin['result']} claimed={bitcoin.get('claimed_heights', '')}")
    print("RESULT:", "PASS" if report["ok"] else "NOT PROVEN")
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--mempool", action="store_true", help=f"gegen {DEFAULT_MEMPOOL} pruefen")
    parser.add_argument("--mempool-url", default=DEFAULT_MEMPOOL)
    args = parser.parse_args(argv)
    return asyncio.run(run(args.bundle, mempool_url=args.mempool_url if args.mempool else None))


if __name__ == "__main__":
    sys.exit(main())
