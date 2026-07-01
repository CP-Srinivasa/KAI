"""Tests for the HOTP seed provisioning script (capital-free 2FA setup)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pyotp
import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hotp_provision.py"
_BASE32_RE = re.compile(r"^[A-Z2-7]+=*$")


def _load() -> object:
    spec = importlib.util.spec_from_file_location("hotp_provision", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def test_writes_valid_base32_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_path = tmp_path / "hotp_seed.b32"
    monkeypatch.setattr(sys, "argv", ["hotp_provision.py", "--seed-path", str(seed_path)])
    assert mod.main() == 0
    secret = seed_path.read_text(encoding="ascii").strip()
    assert _BASE32_RE.match(secret), secret
    # The printed otpauth URI must round-trip through pyotp with the SAME seed,
    # so the operator's authenticator and HotpVerifier agree at counter 0.
    out = capsys.readouterr().out
    assert secret in out
    assert "otpauth://hotp/" in out
    # Sanity: pyotp can produce a code for counter 0 from the written seed.
    pyotp.HOTP(secret, digits=6).at(0)


def test_refuses_overwrite_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_path = tmp_path / "hotp_seed.b32"
    seed_path.write_text("EXISTINGSEED234567\n", encoding="ascii")
    monkeypatch.setattr(sys, "argv", ["hotp_provision.py", "--seed-path", str(seed_path)])
    assert mod.main() == 2
    # Untouched.
    assert seed_path.read_text(encoding="ascii").strip() == "EXISTINGSEED234567"


def test_force_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_path = tmp_path / "hotp_seed.b32"
    seed_path.write_text("EXISTINGSEED234567\n", encoding="ascii")
    monkeypatch.setattr(
        sys, "argv", ["hotp_provision.py", "--seed-path", str(seed_path), "--force"]
    )
    assert mod.main() == 0
    secret = seed_path.read_text(encoding="ascii").strip()
    assert secret != "EXISTINGSEED234567"
    assert _BASE32_RE.match(secret), secret
