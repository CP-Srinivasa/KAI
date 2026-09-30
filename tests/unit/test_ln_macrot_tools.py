"""Werkzeug fuer den Neuaufbau aller lnd-Macaroons (D-294), versioniert heisst geprueft.

Operator-Bedingung vom 29.09.2026: kein Satoshi darf sich bewegen. Diese Tests halten
fest, dass die Skripte keinen Geldbefehl enthalten, dass die Abbruchbedingungen
greifen, dass der Bilanzvergleich jede Abnahme als BEFUND meldet und dass die
Rechtepruefung der neuen Macaroons keine Ausweitung durchlaesst.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "scripts" / "workstation" / "ln-macrot"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Kein __pycache__ neben den Betriebsskripten (Installer zaehlt jede Datei).
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = before
    return mod


# ---------------------------------------------------------------- kein Geldbefehl

_MONEY = re.compile(
    r"sendpayment|payinvoice|sendtoroute|sendcoins|sendmany|openchannel|closechannel|"
    r"abandonchannel|fundpsbt|bumpfee|changepassword|accounts\s+update|wallet\s+send",
    re.I,
)


@pytest.mark.parametrize("name", sorted(p.name for p in TOOLS.iterdir() if p.is_file()))
def test_no_money_command_outside_comments(name: str) -> None:
    for number, line in enumerate((TOOLS / name).read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        assert not _MONEY.search(code), f"{name}:{number}: {line.strip()}"


# ---------------------------------------------------------------- mac_ops


def _pb(field: int, payload: bytes) -> bytes:
    return bytes([(field << 3) | 2, len(payload)]) + payload


def _macaroon(root: str, ops: list[tuple[str, list[str]]]) -> bytes:
    ident = b"\x03" + _pb(1, b"n" * 16) + _pb(2, root.encode())
    for entity, actions in ops:
        body = _pb(1, entity.encode()) + b"".join(_pb(2, a.encode()) for a in actions)
        ident += _pb(3, body)
    # V2: Version, Feld 2 (Identifier), EOS, EOS (keine Caveats), Feld 6 (Signatur)
    return b"\x02" + bytes([2, len(ident)]) + ident + b"\x00\x00" + bytes([6, 32]) + b"s" * 32


def test_mac_ops_reads_root_and_permissions(tmp_path: Path) -> None:
    mo = _load("mac_ops")
    f = tmp_path / "kai-invoice.macaroon"
    f.write_bytes(_macaroon("102", [("invoices", ["read", "write"]), ("info", ["read"])]))
    assert mo.parse(str(f)) == ("102", "info:read invoices:read,write")


def test_mac_ops_expect_rejects_wider_rights(tmp_path: Path, capsys) -> None:
    mo = _load("mac_ops")
    f = tmp_path / "kai-readonly.macaroon"
    f.write_bytes(_macaroon("101", [("info", ["read"]), ("offchain", ["read", "write"])]))
    sys_argv = ["mac_ops.py", "--expect", "101", "info:read offchain:read", str(f)]
    old, sys.argv = sys.argv, sys_argv
    try:
        assert mo.main() == 1
    finally:
        sys.argv = old
    assert "ABWEICHUNG" in capsys.readouterr().out


def test_mac_ops_account_root_is_the_litd_prefix(tmp_path: Path) -> None:
    mo = _load("mac_ops")
    account_root = str((0xFFEEDDCC << 32) | 0x3C64C562)
    good = tmp_path / "acc.macaroon"
    good.write_bytes(_macaroon(account_root, [("offchain", ["read", "write"])]))
    bad = tmp_path / "root0.macaroon"
    bad.write_bytes(_macaroon("0", [("offchain", ["read", "write"])]))
    for path, want in ((good, 0), (bad, 1)):
        old, sys.argv = (
            sys.argv,
            ["mac_ops.py", "--expect", "account", "offchain:read,write", str(path)],
        )
        try:
            assert mo.main() == want
        finally:
            sys.argv = old


# ---------------------------------------------------------------- macrot_snap


def _snap(**over) -> dict:
    s = {
        "info": {
            "pubkey": "02aa",
            "synced_to_chain": True,
            "block_height": 1,
            "active_channels": 1,
        },
        "wallet": {
            "total_balance": 1000,
            "confirmed_balance": 1000,
            "unconfirmed_balance": 0,
            "locked_balance": 0,
        },
        "channelbalance": {
            "local_balance": 500,
            "remote_balance": 50,
            "unsettled_local_balance": 0,
            "pending_open_local_balance": 0,
        },
        "channels": [
            {
                "chan_point": "cp:0",
                "capacity": 600,
                "local": 500,
                "remote": 50,
                "unsettled": 0,
                "pending_htlcs": 0,
                "active": True,
            }
        ],
        "pending": {
            "pending_open": 0,
            "pending_closing": 0,
            "waiting_close": 0,
            "force_closing": [
                {"chan_point": "old:0", "limbo": 7, "recovered": 0, "pending_htlcs": 0}
            ],
        },
        "payments": {"checked": 5, "in_flight": 0},
        "accounts": [{"id": "ab", "label": "kai", "balance": 4878}],
        "boltz": {
            "mode": "boltz.db (boltzd ohne Backend)",
            "pending_swaps": 0,
            "autoswap": "not configured (kein Backend)",
        },
        "loop": {"autoloop": False, "open_swaps": 0},
        "pool": {"open_accounts": 0},
        "macaroon_ids": ["0"],
    }
    s.update(over)
    return s


def test_guards_go_on_a_quiet_node() -> None:
    assert _load("macrot_snap").guard_reasons(_snap()) == []


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"payments": {"checked": 5, "in_flight": 1}}, "IN_FLIGHT"),
        (
            {"boltz": {"mode": "boltzcli", "pending_swaps": 1, "autoswap": "not configured"}},
            "Boltz-Swaps",
        ),
        (
            {"boltz": {"mode": "boltzcli", "pending_swaps": 0, "autoswap": "Status: enabled"}},
            "Autoswap",
        ),
        ({"loop": {"autoloop": True, "open_swaps": 0}}, "Autoloop"),
        ({"accounts": []}, "'kai'"),
        ({"boltz": {"error": "boltz.db nicht lesbar"}}, "nicht lesbar"),
    ],
)
def test_guards_abort(override: dict, needle: str) -> None:
    reasons = _load("macrot_snap").guard_reasons(_snap(**override))
    assert any(needle in r for r in reasons), reasons


def test_guards_abort_on_htlcs_even_in_a_force_close() -> None:
    snap = _snap()
    snap["pending"]["force_closing"][0]["pending_htlcs"] = 1
    assert any("HTLC" in r for r in _load("macrot_snap").guard_reasons(snap))


def test_diff_identical_and_decrease_is_a_finding() -> None:
    ms = _load("macrot_snap")
    assert ms.diff(_snap(), _snap()) == []
    after = _snap()
    after["channels"][0]["local"] = 499
    assert any(x.startswith("BEFUND") and "Abnahme" in x for x in ms.diff(_snap(), after))


def test_diff_routing_gain_is_only_a_note() -> None:
    after = _snap()
    after["channels"][0]["local"] = 503
    lines = _load("macrot_snap").diff(_snap(), after)
    assert lines and all(x.startswith("HINWEIS") for x in lines)


def test_diff_flags_budget_and_channel_changes() -> None:
    ms = _load("macrot_snap")
    after = _snap(accounts=[{"id": "cd", "label": "kai", "balance": 100}], channels=[])
    lines = ms.diff(_snap(), after)
    assert any("KAI-Budget" in x for x in lines)
    assert any("Kanalliste" in x for x in lines)


def test_boltz_final_states_treat_errors_as_open() -> None:
    # ERROR/SERVER_ERROR koennen noch einen Refund brauchen -> nicht abgeschlossen.
    assert _load("macrot_snap").BOLTZ_FINAL_STATES == (1, 4, 5)


# ---------------------------------------------------------------- Shell-Syntax


def _bash() -> str | None:
    if sys.platform == "win32":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        return str(git_bash) if git_bash.exists() else None
    return shutil.which("bash")


@pytest.mark.skipif(_bash() is None, reason="bash nicht verfuegbar")
@pytest.mark.parametrize("name", ["macrot_node.sh", "macrot_pi.sh"])
def test_shell_scripts_parse(name: str) -> None:
    bash = _bash()
    assert bash is not None
    res = subprocess.run(
        [bash, "-n", str(TOOLS / name)], capture_output=True, text=True, timeout=30
    )
    assert res.returncode == 0, res.stderr


def test_node_script_refuses_to_run_over_an_unfinished_rotation() -> None:
    src = (TOOLS / "macrot_node.sh").read_text(encoding="utf-8")
    assert "existiert schon (frueherer Lauf)" in src
    assert "confirm ROTIEREN" in src and "confirm AUFRAEUMEN" in src
