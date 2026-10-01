"""KAI -> KAI-Pay: signierte Zahlungsvorschlaege (D-297, ADR 0021 I3/I4/I5)."""

from __future__ import annotations

import ast
import os
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from typer.testing import CliRunner

from app.kai_pay_bridge.keys import KeyMissingError, create_key, load_key
from app.kai_pay_bridge.proposal import (
    MAX_SAT,
    ProposalError,
    encode_source,
    fingerprint,
    key_id,
    safe_text,
    sign_proposal,
    spki_of,
    unb64url,
    verify_proposal,
)
from app.kai_pay_bridge.settings import KaiPayProposalSettings
from app.kai_pay_bridge.telegram import NO_KEY, USAGE, handle_vorschlag

# Vom Wallet-Code (kai-pay packages/proposal/proposal.ts) signiert - nur oeffentliche Daten.
# Belegt, dass KAI und Wallet dasselbe Format sprechen (Gegenrichtung: Vektor im kai-pay-Repo).
JS_SPKI = (
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEhK5yXGcP7XATnJwU0OTtMtmuYLwVCGmxagMQovLWgPw9FGi1oeUH7HJD"
    "iBk8hAbHPRXtASXgw2zCvZyD7ToYsw"
)
JS_TOKEN = (
    "kaiprop1.eyJ2IjoxLCJpZCI6IlVPZ21sZ21tYlNWVEdQUWV0cVlldlEiLCJzcmMiOiI0M2MwMjJkN2VlOGVkOGVkYTI3ZjBiODUzN2Y4NzVlMSIsImlh"
    "dCI6MTc5MDgwMDAwMCwiZXhwIjoxNzkwODAzNjAwLCJ0byI6ImthaXBheXRlc3RtdWk2MW1vcEBicmVlei50aXBzIiwic2F0IjoyMTAwLCJwdXJwb3Nl"
    "IjoiR2VnZW5wcm9iZSBad2VjayDDpMO2w7wifQ.hmRv4mVu8gHz2_6b_zc5HTlV4FOXyP_lUqtSkmbS-fjVIvfAu1-WS-VSBQ6rH3Ykfaqee3ROCq525ZR3mtT-oQ"
)
NOW = 1_790_800_000


@pytest.fixture()
def key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture()
def settings(tmp_path: Path) -> KaiPayProposalSettings:
    return KaiPayProposalSettings(key_path=str(tmp_path / "kai-pay" / "proposal-source.pem"))


def test_wallet_vector_verifies_in_python() -> None:
    p = verify_proposal(JS_TOKEN, unb64url(JS_SPKI))
    assert (p.sat, p.to, p.purpose) == (
        2100,
        "kaipaytestmui61mop@breez.tips",
        "Gegenprobe Zweck äöü",
    )
    assert p.src == key_id(unb64url(JS_SPKI))


def test_sign_verify_roundtrip_and_tamper(key: ec.EllipticCurvePrivateKey) -> None:
    tok = sign_proposal(key, to="shop@example.com", sat=1000, purpose="Server Oktober", now=NOW)
    p = verify_proposal(tok, spki_of(key))
    assert (p.sat, p.exp - p.iat, len(p.src)) == (1000, 24 * 3600, 32)
    head, sig = tok.split(".")[1:]
    flipped = f"kaiprop1.{head}.{sig[:-2]}{'AA' if not sig.endswith('AA') else 'AB'}"
    with pytest.raises(ProposalError):
        verify_proposal(flipped, spki_of(key))
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(ProposalError, match="source"):
        verify_proposal(tok, spki_of(other))


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"sat": 0}, "sat"),
        ({"sat": MAX_SAT + 1}, "sat"),
        ({"sat": True}, "sat"),
        ({"purpose": ""}, "purpose"),
        ({"purpose": "a​b"}, "purpose"),
        ({"purpose": "a؜b"}, "purpose"),
        ({"purpose": "a b"}, "purpose"),
        ({"purpose": "a\U000e0041b"}, "purpose"),
        ({"purpose": "aㅤb"}, "purpose"),
        ({"purpose": "x" * 141}, "purpose"),
        ({"to": "a b"}, "to"),
        ({"ttl_s": 8 * 24 * 3600}, "ttl"),
    ],
)
def test_rejects_what_the_wallet_would_reject(
    key: ec.EllipticCurvePrivateKey, kwargs: dict[str, object], reason: str
) -> None:
    args: dict[str, object] = {
        "to": "shop@example.com",
        "sat": 1,
        "purpose": "p",
        "now": NOW,
        **kwargs,
    }
    with pytest.raises(ProposalError, match=reason):
        sign_proposal(key, **args)  # type: ignore[arg-type]


def test_text_length_counts_like_javascript() -> None:
    # Emoji = 2 UTF-16-Einheiten (wie String.length in der Wallet)
    assert safe_text("\U0001f600" * 70, 140) and not safe_text("\U0001f600" * 71, 140)


def test_source_code_and_fingerprint(key: ec.EllipticCurvePrivateKey) -> None:
    src = encode_source("KAI", spki_of(key))
    assert src.startswith("kaisrc1.")
    fp = fingerprint(key_id(spki_of(key)))
    assert len(fp.split("-")) == 8 and all(len(g) == 4 for g in fp.split("-"))
    with pytest.raises(ProposalError):
        encode_source("KAI‮X", spki_of(key))


def test_key_created_0600_never_overwritten(settings: KaiPayProposalSettings) -> None:
    k = create_key(settings.key_path)
    p = Path(settings.key_path)
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
        assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    with pytest.raises(FileExistsError):
        create_key(settings.key_path)
    assert key_id(spki_of(load_key(settings.key_path))) == key_id(spki_of(k))


def test_load_errors_do_not_leak_file_content(tmp_path: Path) -> None:
    bad = tmp_path / "k.pem"
    bad.write_text("-----BEGIN PRIVATE KEY-----\nGEHEIMESMATERIAL\n-----END PRIVATE KEY-----\n")
    with pytest.raises(ValueError) as exc:
        load_key(bad)
    assert "GEHEIM" not in str(exc.value) and exc.value.__cause__ is None
    with pytest.raises(KeyMissingError):
        load_key(tmp_path / "fehlt.pem")


def test_telegram_without_key_and_help(settings: KaiPayProposalSettings) -> None:
    assert handle_vorschlag("", settings) == USAGE
    assert handle_vorschlag("hilfe", settings) == USAGE
    assert handle_vorschlag("quelle", settings) == NO_KEY


def test_telegram_source_and_proposal_links(settings: KaiPayProposalSettings) -> None:
    k = create_key(settings.key_path)
    src = handle_vorschlag("quelle", settings)
    assert "https://app.kai-pay.net/#kaisrc=kaisrc1." in src
    assert fingerprint(key_id(spki_of(k))) in src
    out = handle_vorschlag("kaipaytestmui61mop@breez.tips 2100 Server Oktober", settings, now=NOW)
    assert "https://app.kai-pay.net/#kaiprop=kaiprop1." in out and "2.100 sat" in out
    token = out.split("`")[1]
    assert verify_proposal(token, spki_of(k)).purpose == "Server Oktober"
    assert handle_vorschlag("x 0 y", settings).startswith("Betrag ungueltig")
    assert handle_vorschlag("x zwei y", settings) == USAGE


def test_i4_private_key_never_in_any_output(settings: KaiPayProposalSettings) -> None:
    """ADR 0021 I4: Schluessel nie in Ausgaben (Telegram-Antworten, CLI)."""
    k = create_key(settings.key_path)
    pem = k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    secret_body = "".join(pem.splitlines()[1:-1])
    scalar_hex = format(k.private_numbers().private_value, "064x")
    outputs = [
        handle_vorschlag("quelle", settings),
        handle_vorschlag("a@b.tld 5 test", settings, now=NOW),
        handle_vorschlag("", settings),
    ]
    runner = CliRunner()
    from app.cli.commands.kaipay_proposal import kaipay_proposal_app

    env = {"APP_KAIPAY_PROPOSAL_KEY_PATH": settings.key_path}
    for cmd in (["show"], ["new", "a@b.tld", "5", "test"], ["init"]):
        outputs.append(runner.invoke(kaipay_proposal_app, cmd, env=env).output)
    for out in outputs:
        assert "PRIVATE KEY" not in out
        assert secret_body[:40] not in out.replace("\n", "")
        assert scalar_hex not in out.lower()


def test_cli_init_show(tmp_path: Path) -> None:
    from app.cli.commands.kaipay_proposal import kaipay_proposal_app

    runner = CliRunner()
    env = {"APP_KAIPAY_PROPOSAL_KEY_PATH": str(tmp_path / "s" / "k.pem")}
    assert runner.invoke(kaipay_proposal_app, ["show"], env=env).exit_code == 1
    r = runner.invoke(kaipay_proposal_app, ["init"], env=env)
    assert r.exit_code == 0 and "Fingerabdruck" in r.output
    assert (
        runner.invoke(kaipay_proposal_app, ["init"], env=env).exit_code == 1
    )  # nie ueberschreiben
    assert "#kaisrc=kaisrc1." in runner.invoke(
        kaipay_proposal_app, ["show"], env=env
    ).output.replace("\n", "")


def test_bridge_never_imports_the_payment_core() -> None:
    """ADR 0021 I3: kein Code-Pfad nach app.payments / app.pay / app.lightning."""
    forbidden = ("app.payments", "app.pay", "app.lightning")
    pkg = Path(__file__).resolve().parents[2] / "app" / "kai_pay_bridge"
    files = [*pkg.glob("*.py"), pkg.parents[0] / "cli" / "commands" / "kaipay_proposal.py"]
    for f in files:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for n in names:
                assert not any(n == m or n.startswith(m + ".") for m in forbidden), (
                    f"{f.name} importiert {n}"
                )


def test_bot_registers_vorschlag_and_help_lists_it() -> None:
    from app.messaging import telegram_bot, telegram_help

    src = Path(telegram_bot.__file__).read_text(encoding="utf-8")
    assert '"vorschlag": self._cmd_vorschlag' in src
    assert "/vorschlag" in telegram_help.HELP_TEXT
