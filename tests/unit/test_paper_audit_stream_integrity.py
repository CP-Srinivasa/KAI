"""Integrität des Paper-Evidenz-Streams: ein Schreiber, eine Leseregel.

Drei Defekte, die dieselbe Datei betreffen und sich gegenseitig verstärken:

1. **Zweiter Close-Writer ohne Stempel.** ``target_completion_reconciler``
   schreibt ``position_closed`` MIT ``trade_pnl_usd``, aber OHNE
   ``schema_version``. Der Dashboard-Leser gatet auf ``schema_version == "v2"``
   und rekonstruiert diese Zeilen deshalb brutto aus Preis×Menge — obwohl der
   exakte Netto-Wert danebensteht. Auf dem Prod-Stream sind das 74 von 139
   Closes (53 %).

2. **Leseregel weicht zwischen drei Pfaden ab.** ``analytics_db`` prüft
   ``schema_version='v2' OR trade_pnl_usd IS NOT NULL`` und behandelt Shorts
   seitenbewusst; ``dashboard`` und ``portfolio_read`` tun beides nicht. Ein
   Short-Close im v1-Zweig geht mit invertiertem Vorzeichen in win_rate und
   expectancy ein.

3. **Die Test-Suite schreibt in den Produktions-Stream.** ``PaperExecutionEngine``
   ohne ``audit_log_path`` fällt auf ``artifacts/paper_execution_audit.jsonl``
   zurück. Auf dem lokalen Stand stehen dadurch 230 Zeilen mit Fixture-Symbolen
   (TIGHT/WIN/WIDE/FOO/BAR/XYZ/BIRB/USDT) in der Datei, aus der Verdikte
   gelesen werden.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.execution.paper_engine import _AUDIT_LOG, PaperExecutionEngine


def _close(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_type": "position_closed",
        "symbol": "BTC/USDT",
        "entry_price": 100.0,
        "exit_price": 110.0,
        "quantity": 2.0,
        "trade_pnl_usd": 19.5,  # netto, inkl. Close-Fee
        "position_side": "long",
    }
    row.update(over)
    return row


class TestReconcilerStamp:
    """Der zweite Writer muss denselben Stempel tragen wie der erste."""

    def test_reconciler_close_traegt_schema_version_v2(self) -> None:
        from app.execution.target_completion_reconciler import PAPER_CLOSE_SCHEMA_VERSION

        # Der Reconciler schreibt trade_pnl_usd und position_side — damit ist die
        # Zeile inhaltlich v2 und muss auch so gestempelt sein, sonst liest der
        # Dashboard-Pfad sie brutto.
        assert PAPER_CLOSE_SCHEMA_VERSION == "v2"


class TestCloseReadRule:
    """Alle Lesepfade müssen dieselbe Regel anwenden wie analytics_db."""

    def test_trade_pnl_schlaegt_fehlenden_schema_stempel(self) -> None:
        from app.execution.close_pnl import close_pnl

        # Kein schema_version, aber exakter Netto-Wert vorhanden: der Wert
        # gewinnt. Vorher gewann die Rekonstruktion (110-100)*2 = 20.0.
        row = _close()
        row.pop("position_side", None)
        assert close_pnl(row) == 19.5

    def test_v2_stempel_bleibt_kanonisch(self) -> None:
        from app.execution.close_pnl import close_pnl

        assert close_pnl(_close(schema_version="v2")) == 19.5

    def test_short_ohne_trade_pnl_wird_seitenbewusst_rekonstruiert(self) -> None:
        from app.execution.close_pnl import close_pnl

        # Short: Einstieg 110, Ausstieg 100 ⇒ +20 Gewinn, nicht −20.
        row = _close(entry_price=110.0, exit_price=100.0, position_side="short")
        row.pop("trade_pnl_usd")
        assert close_pnl(row) == 20.0

    def test_long_ohne_trade_pnl_bleibt_unveraendert(self) -> None:
        from app.execution.close_pnl import close_pnl

        row = _close()
        row.pop("trade_pnl_usd")
        assert close_pnl(row) == 20.0

    def test_fehlende_felder_ergeben_null_statt_absturz(self) -> None:
        from app.execution.close_pnl import close_pnl

        assert close_pnl({"event_type": "position_closed"}) == 0.0


class TestSuiteWritesNowhereNearProduction:
    """Die Suite darf den Evidenz-Stream nicht anfassen — der Wächter beweist es."""

    def test_pfadkonstante_bleibt_relativ(self) -> None:
        # Die Isolation dieser Suite laeuft ueber ``monkeypatch.chdir(tmp_path)``
        # und haengt daran, dass der Default RELATIV ist. Wird er absolut,
        # schreibt der Code an der Test-Isolation vorbei — genau daran sind
        # fuenf Tests gescheitert, als hier ein Redirect stand.
        assert not _AUDIT_LOG.is_absolute()
        assert _AUDIT_LOG == Path("artifacts/paper_execution_audit.jsonl")

    def test_engine_ohne_pfad_folgt_dem_cwd(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        engine = PaperExecutionEngine(initial_equity=10_000.0)

        # Unter chdir liegt der Stream im tmp-Baum; genau so isolieren die
        # bestehenden Paper-Tests.
        assert engine._audit_path.resolve().is_relative_to(tmp_path.resolve())

    def test_waechter_ist_scharf(self) -> None:
        """Der autouse-Wächter muss existieren, sonst faellt Kontamination wieder auf."""
        from tests.conftest import _paper_audit_fingerprint

        fp = _paper_audit_fingerprint()
        assert isinstance(fp, tuple) and len(fp) == 3
