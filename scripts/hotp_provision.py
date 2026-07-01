#!/usr/bin/env python
"""Provision the HOTP seed for the LN value-layer confirm (2FA) — capital-free.

Generates a random base32 seed, writes it to the configured seed path (mode 600,
refuses overwrite without ``--force``), and prints the seed + an ``otpauth://`` URI
to load into an authenticator app (Aegis / FreeOTP in HOTP / counter mode, start
counter 0, 6 digits — matching :class:`app.security.hotp_auth.HotpVerifier`).

The seed NEVER moves funds; it only signs the confirm on an ALREADY-gated spend
(``pay_enabled`` + policy envelope). Run on the Pi with ``.venv/bin/python``.
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
from pathlib import Path

import pyotp

from app.core.settings import get_settings


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision the LN confirm HOTP seed (capital-free)")
    ap.add_argument(
        "--seed-path", default=None, help="Seed file path (default: APP_LN_HOTP_SEED_PATH)"
    )
    ap.add_argument("--issuer", default="KAI")
    ap.add_argument("--account", default="ln-cockpit")
    ap.add_argument("--force", action="store_true", help="Overwrite an existing seed file")
    args = ap.parse_args()

    seed_path_str = args.seed_path or get_settings().lightning.hotp_seed_path
    if not seed_path_str:
        print("no seed path: set APP_LN_HOTP_SEED_PATH or pass --seed-path", file=sys.stderr)
        return 2
    seed_path = Path(seed_path_str)
    if seed_path.exists() and not args.force:
        print(f"refusing to overwrite existing seed: {seed_path} (use --force)", file=sys.stderr)
        return 2

    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii")  # 32 base32 chars
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    # Restrictive mode from creation (0600); chmod after as a belt-and-braces on
    # platforms where the open-mode is not honoured.
    fd = os.open(seed_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(secret + "\n")
    try:
        os.chmod(seed_path, 0o600)
    except OSError:
        pass

    uri = pyotp.HOTP(secret, digits=6).provisioning_uri(
        name=args.account, issuer_name=args.issuer, initial_count=0
    )
    print(f"HOTP seed written -> {seed_path} (mode 600)")
    print(f"base32 secret: {secret}")
    print(f"otpauth URI:   {uri}")
    print("Load into an authenticator in HOTP/counter mode (start counter 0, 6 digits).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
