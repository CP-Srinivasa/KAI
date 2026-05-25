"""D-142 Bearish-Disabled Safety-Lock-Test (Forensik 2026-05-25).

Beziehung zu Memos:
- [[kai-dispatch-filter-root-befund-20260524]] §6 (F2-Sprint)
- artifacts/operator_memos/f2_d142_bearish_reeval_2026-05-24.md (Sign-off)
- Recalc 2026-05-25: bearish 2/15 (precision 11.8%, Wilson Lower 95 = 3.3%)
  bei n=17. Schwelle für Re-Aktivierung: >=30% Wilson Lower oder >=20% mit n>=30.

Dieser Test ist ein **Safety-Lock**: er verhindert, dass D-142 versehentlich
auf False kippt ohne dass der Recalc-Threshold beleg-baselined neu validiert ist.
Wer D-142 deaktivieren will, muss zusätzlich diesen Test anpassen UND ein
neues F2-Memo mit aktuellem Recalc-Beleg + Operator-Sign-off im Repo ablegen.
"""

from __future__ import annotations

from app.alerts import eligibility


def test_d142_bearish_directional_disabled_remains_active() -> None:
    """Safety-Lock: D-142 bleibt aktiv bis F2-Re-Eval frühestens 2026-06-15.

    Empirie 2026-05-25 (Pi-Recalc):
      - bearish n=17, hit=2, miss=15
      - precision=11.8%, Wilson Lower 95%=3.3%
      - bearish p>=10: 1 hit / 6 miss → 14.3%
      - Schwelle für Re-Aktivierung: >=30% Wilson Lower oder >=20% mit n>=30.
        Beide nicht erfüllt — n liegt bei 17, Wilson Lower bei 3.3%.

    Wer diesen Test ändert, muss zusätzlich:
      1) artifacts/operator_memos/f2_d142_bearish_reeval_*.md mit
         aktuellem Recalc-Beleg (Wilson Lower >= Schwelle, n >= 30) anlegen
      2) Operator-Sign-off explizit dokumentieren
      3) Memo [[kai-dispatch-filter-root-befund-20260524]] §6 aktualisieren
    """
    assert eligibility.BEARISH_DIRECTIONAL_DISABLED is True, (
        "D-142 ist Safety-Block. Recalc 2026-05-25 (Wilson Lower 3.3% bei n=17) "
        "unterschreitet die Reaktivierungs-Schwelle (>=30% / n>=30) deutlich. "
        "Re-Eval frühestens 2026-06-15 wenn Outcomes-Coverage gestiegen ist."
    )


def test_d142_block_reason_string_is_stable() -> None:
    """Block-Reason-String ist stabiler Auditierungs-Schlüssel.

    Dashboard + Daily-Strategy + paper-pipeline-status zählen darüber.
    Umbenennen würde alle Counter und alle blocked_alerts.jsonl-Historie brechen.
    """
    assert eligibility.BLOCK_REASON_BEARISH_DISABLED == "bearish_directional_disabled"


def test_bearish_signal_emits_block_decision_not_silent_drop() -> None:
    """Bearish-Signal wird BLOCKIERT, nicht silenziös verworfen.

    Sichert: die Signal-Erfassung läuft weiter (Pipeline-Stage 1), das Signal
    erreicht das Dispatch-Gate, das Gate emittiert eine sichtbare Block-Decision
    mit Reason=bearish_directional_disabled. Auditierungs-Voraussetzung.
    """
    decision = eligibility.evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC/USDT"],
        sentiment_score=-0.80,
        impact_score=0.80,
        directional_confidence=0.95,
        priority=10,
        actionable=True,
        title="Bitcoin hard-money thesis colliding with 5% Treasury yields",
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == eligibility.BLOCK_REASON_BEARISH_DISABLED


def test_d142_only_blocks_bearish_not_bullish() -> None:
    """D-142 darf bullish-Pfad NICHT versehentlich blocken."""
    decision = eligibility.evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.80,
        impact_score=0.70,
        directional_confidence=0.85,
        priority=9,
        actionable=True,
        title="BlackRock files for new Bitcoin ETF",
    )
    assert decision.is_directional is True
    assert decision.directional_eligible is True
    assert decision.directional_block_reason is None
