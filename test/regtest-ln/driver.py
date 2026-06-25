#!/usr/bin/env python
"""PR7 regtest E2E driver — runs KAI's OWN Lightning code against a real (regtest) lnd.

Proves the node touches the in-process E2E mocks: a real invoices:write macaroon MINTS
(the readonly macaroon on the live node could not), bob PAYS, the invoice SETTLES, the
L402 token+preimage VERIFIES, and the settled invoice is BOOKED. Capital-free (regtest
coins). Usage: python driver.py <alice_invoices_macaroon_hex>
"""

from __future__ import annotations

import asyncio
import base64
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.lightning.client import LndRestClient
from app.lightning.earnings_ledger import read_recent_ln_earnings, record_settled_invoices
from app.lightning.l402 import mint_token, verify

_SECRET = "regtest-e2e-secret"
_BOB = ["docker", "exec", "kai-rt-bob", "lncli", "--lnddir=/home/lnd/.lnd", "-n", "regtest"]


async def main(mac_hex: str) -> int:
    alice = LndRestClient(base_url="https://127.0.0.1:8081", macaroon_hex=mac_hex, tls_cert_path="")

    # [1] MINT via KAI's client + the invoices:write macaroon (the live readonly couldn't)
    inv = await alice.add_invoice(value_sat=100, memo="kai-oracle:fee-series", expiry_seconds=600)
    pr, rhash_b64 = inv["payment_request"], inv["r_hash"]
    ph_hex = base64.b64decode(rhash_b64).hex()
    print(f"[1] MINT ok: payment_hash={ph_hex[:20]}... bolt11={pr[:28]}...")

    # [2] L402 token bound to the payment_hash
    token = mint_token(ph_hex, secret=_SECRET, scope="fee-series")

    # [3] PAY from bob (the payer node)
    r = subprocess.run(
        [*_BOB, "payinvoice", "--force", pr], capture_output=True, text=True, timeout=90
    )
    print(f"[3] bob payinvoice rc={r.returncode}")

    # [4] poll alice for the settled invoice + its preimage (what the payer learned)
    preimage_hex = ""
    for _ in range(20):
        for iv in await alice.list_invoices():
            if iv.get("r_hash") == rhash_b64 and iv.get("settled"):
                preimage_hex = base64.b64decode(iv["r_preimage"]).hex()
                break
        if preimage_hex:
            break
        time.sleep(1)
    assert preimage_hex, "invoice never settled — payment did not complete"
    print(f"[4] SETTLE ok: preimage={preimage_hex[:20]}...")

    # [5] L402 round-trip verify with the real preimage
    v = verify(token, preimage_hex, secret=_SECRET)
    assert v.valid and v.scope == "fee-series", f"L402 verify FAILED: {v}"
    print(f"[5] L402 VERIFY ok: valid={v.valid} scope={v.scope}")

    # [6] BOOK the settled oracle invoice via KAI's earnings ledger
    oracle = [
        i for i in await alice.list_invoices() if str(i.get("memo", "")).startswith("kai-oracle:")
    ]
    earn = Path(tempfile.mkdtemp()) / "earn.jsonl"
    booked = record_settled_invoices(oracle, source="oracle-l402", path=earn)
    rows = read_recent_ln_earnings(earn, limit=0)
    assert booked >= 1 and rows and rows[0]["amount_sat"] == 100, f"booking FAILED: {rows}"
    print(f"[6] BOOK ok: booked={booked} amount={rows[0]['amount_sat']}sat")

    print("=== REGTEST E2E PASS: mint -> pay -> settle -> L402-verify -> book ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1])))
