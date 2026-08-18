"""Ein Fenster-Claim hat keinen Zaehler — das Board darf keinen behaupten.

Sichtbar geworden am 2026-08-18, als ``k1_channel_audit_resonance``
(``00c75a76a2b0e78b``) erstmals unter Aufsicht kam. Das Board zeigte:

    eval_check | exakten Evaluator fahren — Ziel-n nur im Upper-Bound-Proxy
                 erreicht, das ist KEIN Urteil und kein PASS.

Jedes Wort davon ist fuer diesen Claim falsch. K1 ist ``kind="deadline"``:
Reife ist das versiegelte **Fensterende**, kein n. Es gibt keinen Proxy, der
etwas erreicht haette, und keinen exakten Evaluator, den man fahren koennte —
die versiegelte Regel verlangt das Auszaehlen qualifizierter Anfragen im
Posteingang, und das kann nur der Operator.

Der Board-Zustand bleibt ``eval_check`` (Handlung noetig, kein Urteil). Nur der
Handlungstext trennt jetzt nach ``kind``.
"""

from __future__ import annotations

from typing import Any

from app.observability.operator_board_live import STATE_EVAL_CHECK, build_live_board

_K1 = "00c75a76a2b0e78b"


def _board(kind: str, note: str | None = None) -> dict[str, Any]:
    ledger = [
        {
            "schema": "prereg/v1",
            "prereg_id": _K1,
            "name": "k1_channel_audit_resonance",
            "created_at_utc": "2026-07-04T12:51:11.469459+00:00",
            "sample_size_target": 5,
        }
    ]
    maturity = [
        {
            "name": "k1_channel_audit_resonance",
            "prereg_id": _K1,
            "kind": kind,
            "state": "EVAL_CHECK_DUE",
            "due": True,
            "n_proxy": 0,
            "n_target": 5,
            "note": note,
            "window_end_utc": "2026-08-03T12:51:11.469459+00:00",
            "per_source": {"window_end_utc": "2026-08-03T12:51:11.469459+00:00"},
        }
    ]
    return build_live_board(ledger=ledger, verdicts=[], maturity_rows=maturity)


def test_fenster_claim_behauptet_keinen_evaluator() -> None:
    row = _board("deadline")["open_preregs"][0]
    assert row["state"] == STATE_EVAL_CHECK, "Handlungsbedarf bleibt bestehen"
    action = row["action"]
    # Geprueft wird die BEHAUPTUNG, nicht das Wort: "kein Proxy" ist ehrlich,
    # "Ziel-n nur im Upper-Bound-Proxy erreicht" ist die falsche Aussage.
    assert "Evaluator fahren" not in action, f"behauptet einen Evaluator: {action}"
    assert "Proxy erreicht" not in action, f"behauptet einen erreichten Proxy: {action}"
    assert "Ziel-n" not in action, f"behauptet ein Ziel-n: {action}"
    assert "Fenster" in action
    assert "2026-08-03" in action


def test_n_basierter_claim_behaelt_seinen_text() -> None:
    """Die alte, korrekte Formulierung darf fuer n-Claims nicht verschwinden."""
    row = _board("documents")["open_preregs"][0]
    assert "exakten Evaluator fahren" in row["action"]


def test_spec_notiz_wird_mitgegeben() -> None:
    """Bei K1 steht die entscheidende Einschraenkung im ``note`` — nicht im Kopf
    des Operators: die Zaehlung ist nicht maschinell."""
    row = _board("deadline", note="Zaehlung ist NICHT maschinell: nur der Operator zaehlt aus.")[
        "open_preregs"
    ][0]
    assert "nicht maschinell" in row["action"].lower()
