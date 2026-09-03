"""Deprecated re-export — der Strom ist nach ``app.payments.input_rejections`` umgezogen.

ADR 0018 §2/§12: der Umzug dreht die Kante ``audit -> lightning`` zu
``audit -> payments`` und bricht damit den Importzyklus
``audit -> lightning -> truth -> audit``. Dieser Pfad bleibt 7 Tage lesbar
(Dual-Read-Fenster) und wird danach geloescht — er darf niemals eigene Logik
tragen, das prueft ``tests/unit/test_payment_dependency_direction.py``.
"""

from __future__ import annotations

from app.payments.input_rejections import (
    LN_INPUT_REJECTIONS_FILENAME,
    MoneyInputRejectionAuditError,
    append_money_input_rejection,
)

__all__ = [
    "LN_INPUT_REJECTIONS_FILENAME",
    "MoneyInputRejectionAuditError",
    "append_money_input_rejection",
]
