"""Die beiden reinen lnd-Wire-Funktionen — an ihrer neuen, gemeinsamen Heimat.

Sie lagen in ``app/lightning/ops_ledger.py``, dem Modul, das mit dem Altpfad
faellt (ADR 0018 §12, PR 1). Beide sind rein: kein Node, keine Datei, kein Env.
Sie werden von BEIDEN Seiten gebraucht — vom Payment Control Plane
(``payments.rails.lightning_mapping``) und vom Empfangs-Audit
(``lightning.receive_ledger``). Jede Heimat in einem der beiden Pakete waere
eine Importkante, die der Richtungs-Test verbietet; deshalb ``app/core``.

Die Tests sind woertlich aus ``test_ln_ops_ledger_write.py`` uebernommen, damit
der Umzug nachweislich nichts an der Semantik aendert.
"""

from __future__ import annotations

import pytest

from app.core.bolt11 import bolt11_amount_sat, normalize_payment_hash


class TestBolt11AmountSat:
    @pytest.mark.parametrize(
        ("request_str", "expected"),
        [
            ("lnbc1500n1p...", 150),
            ("lnbc10u1p...", 1_000),
            ("lnbc1m1p...", 100_000),
            ("lnbc1p...", 0),  # kein HRP-Betrag -> amountless
            ("", 0),
            ("not-an-invoice", 0),
        ],
    )
    def test_hrp_amount(self, request_str: str, expected: int) -> None:
        assert bolt11_amount_sat(request_str) == expected

    def test_rounds_up_never_down(self) -> None:
        """Ein Cap darf nie durch Abrunden unterlaufen werden."""
        # 1 nano-BTC = 100 msat = 0,1 sat -> aufgerundet 1 sat.
        assert bolt11_amount_sat("lnbc1n1p...") == 1

    def test_pico_unit_is_ceiled_to_msat_first(self) -> None:
        assert bolt11_amount_sat("lnbc1p1p...") == 1

    def test_testnet_and_regtest_prefixes(self) -> None:
        assert bolt11_amount_sat("lntb10u1p...") == 1_000
        assert bolt11_amount_sat("lnbcrt10u1p...") == 1_000


class TestNormalizePaymentHash:
    def test_hex_is_lowercased(self) -> None:
        value = "AB" * 32
        assert normalize_payment_hash(value) == "ab" * 32

    def test_base64_is_decoded_to_hex(self) -> None:
        import base64

        raw = bytes(range(32))
        assert normalize_payment_hash(base64.b64encode(raw).decode()) == raw.hex()

    def test_urlsafe_base64_is_decoded(self) -> None:
        import base64

        raw = bytes(range(200, 232))
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        assert normalize_payment_hash(encoded) == raw.hex()

    def test_empty_stays_empty(self) -> None:
        assert normalize_payment_hash(None) == ""
        assert normalize_payment_hash("  ") == ""

    def test_unnormalisable_is_kept_verbatim(self) -> None:
        """Wegwerfen wuerde die Dedup still entwaffnen — lieber unveraendert."""
        assert normalize_payment_hash("not-a-hash") == "not-a-hash"


def test_module_is_a_leaf() -> None:
    """Die Heimat traegt sich selbst: kein ``app.*``-Import.

    Sonst waere sie kein neutraler Boden, sondern eine Bruecke, ueber die die
    verbotene Richtung ``lightning -> payments`` doch wieder zurueckfaende.
    """
    import ast
    from pathlib import Path

    source = Path("app/core/bolt11.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not [name for name in imported if name.startswith("app.")]
